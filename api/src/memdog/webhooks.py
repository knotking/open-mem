"""Inbound webhooks -- the provider-facing surface.

This is a **translator in front of the write API**, not a second write path.
It accepts whatever shape a provider sends, maps it onto items, and calls
`write_items`. Admission control, ACL assignment, memory routing, events and
audit therefore happen exactly once, in the place they already happened.

Four things a webhook endpoint has to get right, and each is a way this goes
wrong in production:

**Authenticate by what the provider can actually do.** Some send a custom
header, some sign the payload, some just POST. The producer declares its method
rather than the platform assuming one.

**Verify the signature over the raw bytes.** Parsing and re-serialising JSON
changes the bytes, and the signature is over what was sent, not over what your
parser reconstructed.

**Be idempotent.** Providers retry aggressively and on any non-2xx. A duplicate
delivery must be a no-op, not a second copy.

**Answer quickly.** Providers time out in seconds and treat a slow 200 as a
failure. The write commits; nothing enriches on this path unless the producer
says so.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import asyncpg

from .audit import record_audit
from .contracts import Inline, WriteItem, WriteOptions, WriteRequest
from .ids import new_id

log = logging.getLogger(__name__)

# A signature older than this is a replay, whatever it verifies against.
SIGNATURE_WINDOW_SECONDS = 300
MAX_ITEMS_PER_DELIVERY = 200


class WebhookError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Delivery:
    status: str
    items: int
    data_ids: list[str]
    reason: str | None = None


def _dig(payload: Any, path: str) -> Any:
    """Follow a dotted path, tolerating anything missing.

    A provider's payload is not a contract we control: a mapping that raises on
    an absent field turns a schema change on their side into an outage on ours.
    """
    current = part = payload
    for part in path.split("."):
        if isinstance(current, list):
            try:
                current = current[int(part)]
                continue
            except (ValueError, IndexError):
                return None
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def verify_signature(
    *,
    raw_body: bytes,
    provided: str | None,
    secrets: list[bytes],
    timestamp: str | None,
    scheme: str = "hmac-sha256",
) -> bool:
    """Constant-time HMAC over the **raw bytes**, inside a time window.

    Several secrets are accepted so a rotation has an overlap window; without
    one, rotating a key is an outage for every delivery in flight.
    """
    if not provided or not secrets:
        return False

    if timestamp is not None:
        try:
            age = abs(time.time() - float(timestamp))
        except ValueError:
            return False
        if age > SIGNATURE_WINDOW_SECONDS:
            # Verifiable but stale: a captured request replayed later.
            return False

    signed = raw_body if timestamp is None else f"{timestamp}.".encode() + raw_body
    candidate = provided.split("=")[-1].strip()
    for secret in secrets:
        expected = hmac.new(secret, signed, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, candidate):
            return True
    return False


def map_payload(payload: Any, mapping: dict) -> list[WriteItem]:
    """Turn a provider payload into items.

    The mapping lives on the producer because it is a property of *this*
    integration: two workspaces of the same provider can map differently
    without either being wrong.

    With no mapping configured the whole body is stored as one JSON item. That
    is deliberately the default -- it loses nothing, is always correct, and can
    be re-parsed later once someone knows what the shape means.
    """
    collection_path = mapping.get("items_path")
    records = _dig(payload, collection_path) if collection_path else None
    if not isinstance(records, list):
        records = [payload]

    external_id_path = mapping.get("external_id_path")
    text_path = mapping.get("text_path")
    event_time_path = mapping.get("event_time_path")

    items: list[WriteItem] = []
    for index, record in enumerate(records[:MAX_ITEMS_PER_DELIVERY]):
        external_id = (
            str(_dig(record, external_id_path)) if external_id_path else None
        ) or f"hook-{new_id('dlv')}-{index}"

        text = _dig(record, text_path) if text_path else None
        if not isinstance(text, str) or not text.strip():
            # No text field, or it was not a string. The record is still worth
            # keeping -- as JSON, which the parser handles.
            text = json.dumps(record, indent=2, ensure_ascii=False, sort_keys=True)

        event_time = None
        if event_time_path:
            raw = _dig(record, event_time_path)
            event_time = _parse_time(raw)

        items.append(WriteItem(
            external_id=external_id[:400],
            content=Inline(text=text),
            source_type=mapping.get("source_type"),
            event_time=event_time,
            tags=mapping.get("tags", []),
        ))
    return items


def _parse_time(raw: Any):
    """Providers send epoch seconds, epoch millis, or ISO-8601. Take any of
    them, and take none of them rather than guessing wrong."""
    from datetime import datetime, timezone

    if raw is None:
        return None
    if isinstance(raw, (int, float)) or (isinstance(raw, str) and raw.replace(".", "", 1).isdigit()):
        value = float(raw)
        if value > 1e11:      # milliseconds
            value /= 1000
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


async def authenticate(
    pool: asyncpg.Pool,
    envelope,
    producer: asyncpg.Record,
    *,
    raw_body: bytes,
    headers: dict[str, str],
) -> tuple[bool, str | None]:
    """Apply the producer's declared inbound method.

    Returns `(signature_verified, key_id)`. The key id matters: the principal
    built from this must carry the credential that authenticated it, or the
    write path's own producer-binding check rejects a key it just verified.
    """
    method = producer["inbound_auth"]

    if method == "url_secret":
        # Reaching this handler already proves the caller knew the id. It is a
        # weak credential -- unrevocable without re-registering, and it leaks
        # through logs and referrers -- which is why it is last-resort.
        return False, None

    if method == "none":
        return False, None

    if method == "api_key":
        presented = headers.get("x-api-key") or ""
        prefix = presented.split(".")[0]
        row = await pool.fetchrow(
            "SELECT key_id, key_hash, revoked_at FROM api_keys WHERE prefix = $1", prefix
        )
        if row is None or row["revoked_at"] is not None or not hmac.compare_digest(
            bytes(row["key_hash"]), hashlib.sha256(presented.encode()).digest()
        ):
            raise WebhookError("invalid credential", status=401)
        if producer["api_key_id"] and row["key_id"] != producer["api_key_id"]:
            raise WebhookError("credential is not the key configured for this producer", 403)
        return False, row["key_id"]

    if method == "signature":
        secrets = []
        for column in ("signing_secret_ct", "previous_signing_secret_ct"):
            blob = producer[column]
            if blob:
                secrets.append(envelope.decrypt(bytes(blob), aad=producer["org_id"].encode()))
        provided = (
            headers.get("x-signature")
            or headers.get("x-hub-signature-256")
            or headers.get("x-slack-signature")
            or headers.get("stripe-signature")
        )
        timestamp = headers.get("x-signature-timestamp") or headers.get(
            "x-slack-request-timestamp"
        )
        if not verify_signature(
            raw_body=raw_body, provided=provided, secrets=secrets, timestamp=timestamp
        ):
            raise WebhookError("signature verification failed", status=401)
        return True, None

    raise WebhookError(f"unsupported inbound auth {method!r}", status=500)


async def receive(
    pool: asyncpg.Pool,
    queue,
    blobs,
    settings,
    envelope,
    *,
    producer_id: str,
    raw_body: bytes,
    headers: dict[str, str],
) -> Delivery:
    """The whole inbound path: authenticate, dedupe, map, write."""
    from .auth import Principal
    from .write import AdmissionError, write_items

    producer = await pool.fetchrow(
        """
        SELECT p.*, c.scope AS connection_scope
        FROM producers p LEFT JOIN connections c ON c.connection_id = p.connection_id
        WHERE p.producer_id = $1
        """,
        producer_id,
    )
    if producer is None or producer["type"] != "webhook":
        # Same answer for "no such producer" and "not a webhook": the id must
        # not be an oracle for what exists.
        raise WebhookError("not found", status=404)

    signature_verified, key_id = await authenticate(
        pool, envelope, producer, raw_body=raw_body, headers=headers
    )

    delivery_key = (
        headers.get("x-delivery-id")
        or headers.get("x-github-delivery")
        or headers.get("x-request-id")
        or headers.get("idempotency-key")
    )

    if delivery_key:
        existing = await pool.fetchrow(
            """
            SELECT status, items FROM webhook_deliveries
            WHERE producer_id = $1 AND external_delivery_id = $2
            """,
            producer_id, delivery_key,
        )
        if existing is not None:
            # A retry. Answering 200 is what stops the provider escalating a
            # duplicate into an endless redelivery loop.
            await _record_delivery(
                pool, producer, delivery_key, "duplicate", signature_verified,
                items=0, payload_bytes=len(raw_body), reason="already received",
                unique=False,
            )
            return Delivery("duplicate", existing["items"], [], "already received")

    if producer["status"] != "enabled":
        # Accept and drop, uniformly -- a provider must not be able to tell a
        # disabled integration from a working one.
        await _record_delivery(pool, producer, delivery_key, "dropped",
                               signature_verified, 0, len(raw_body),
                               f"producer is {producer['status']}")
        return Delivery("dropped", 0, [], f"producer is {producer['status']}")

    try:
        payload = json.loads(raw_body or b"{}")
    except ValueError:
        # Not JSON. Keep it anyway as text -- discarding a payload because it
        # surprised us is how integrations lose data silently.
        payload = {"raw": raw_body.decode("utf-8", errors="replace")}

    items = map_payload(payload, dict(producer["inbound_mapping"]))
    if not items:
        await _record_delivery(pool, producer, delivery_key, "accepted",
                               signature_verified, 0, len(raw_body), "no items in payload")
        return Delivery("accepted", 0, [], "no items in payload")

    # The producer's own owner is the principal: nothing writes anonymously,
    # and a webhook's writes belong to whoever registered the integration.
    principal = Principal(
        user_id=producer["user_id"],
        org_id=producer["org_id"],
        capabilities=frozenset({"data:read", "data:write"}),
        # The credential that actually authenticated, so the write path's
        # producer-binding check sees the same key it was configured with.
        key_id=key_id,
    )
    defaults = dict(producer["defaults"])
    try:
        response = await write_items(
            pool, queue, blobs, settings, principal,
            WriteRequest(
                producer_id=producer_id,
                items=items,
                # Enrichment stays opt-in here too, and per integration: a chatty
                # webhook that summarises every message is an unbounded bill.
                options=WriteOptions(enrich=bool(defaults.get("enrich", False))),
            ),
            idempotency_key=delivery_key,
        )
    except AdmissionError as exc:
        await _record_delivery(pool, producer, delivery_key, "rejected",
                               signature_verified, 0, len(raw_body), str(exc))
        raise WebhookError(str(exc), status=exc.status) from exc

    data_ids = [r.data_id for r in response.results if r.data_id]
    await _record_delivery(pool, producer, delivery_key, "accepted",
                           signature_verified, len(data_ids), len(raw_body), None)
    return Delivery("accepted", len(data_ids), data_ids)


async def _record_delivery(
    pool: asyncpg.Pool,
    producer: asyncpg.Record,
    delivery_key: str | None,
    status: str,
    signature_verified: bool,
    items: int,
    payload_bytes: int,
    reason: str | None,
    unique: bool = True,
) -> None:
    from .auth import Principal

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO webhook_deliveries (delivery_id, producer_id, org_id,
                external_delivery_id, signature_verified, status, reason, items, payload_bytes)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (producer_id, external_delivery_id) DO NOTHING
            """,
            new_id("dlv"), producer["producer_id"], producer["org_id"],
            delivery_key if unique else None,
            signature_verified, status, reason, items, payload_bytes,
        )
        await record_audit(
            conn,
            Principal(user_id=producer["user_id"], org_id=producer["org_id"],
                      capabilities=frozenset()),
            action=f"webhook.{status}",
            project_id=producer["project_id"],
            target_type="producer", target_id=producer["producer_id"],
            detail={"items": items, "bytes": payload_bytes,
                    "signature_verified": signature_verified, "reason": reason},
        )
