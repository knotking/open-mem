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
from .telemetry import record, span
from . import providers as provider_registry
from .contracts import Inline, MemoryRef, WriteItem, WriteOptions, WriteRequest
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
    # A reply the provider requires before it will accept the endpoint.
    handshake: object | None = None


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
    selected = _dig(payload, collection_path) if collection_path else None
    if isinstance(selected, list):
        records = selected
    elif isinstance(selected, dict):
        # A path can select one record as easily as many -- Slack's `event` is a
        # single object. Falling back to the whole envelope here silently made
        # every other field path miss, which looked like a mapping that did
        # nothing rather than one pointed at the wrong level.
        records = [selected]
    else:
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

        # A conversation container, derived rather than configured: a Slack
        # thread id is already a stable natural key, and the write path upserts
        # on it -- so a thread collects itself into one memory with nothing set
        # up by anyone.
        memory = None
        key_path = mapping.get("memory_key_path")
        if key_path:
            key = _dig(record, key_path) or _dig(
                record, mapping.get("memory_fallback_key_path", "")
            )
            if key:
                memory = MemoryRef(key=str(key), type=mapping.get("memory_type", "conversation"))

        items.append(WriteItem(
            external_id=external_id[:400],
            content=Inline(text=text),
            source_type=mapping.get("source_type"),
            event_time=event_time,
            tags=mapping.get("tags", []),
            memory=memory,
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
    url: str = "",
    query: dict | None = None,
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
        # Each provider signs a different string. Dispatching here rather than
        # trying every scheme means a wrong secret and a wrong provider fail
        # distinguishably instead of both looking like "bad signature".
        provider = provider_registry.get(dict(producer["inbound_mapping"]).get("provider"))
        request = provider_registry.Request(
            raw_body=raw_body, headers=headers, url=url, query=query or {}
        )
        if not provider_registry.verify(provider, request=request, secrets=secrets):
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
    url: str = "",
    query: dict | None = None,
) -> Delivery:
    """The whole inbound path: authenticate, dedupe, map, write."""
    with span("webhook.receive", producer_id=producer_id, bytes=len(raw_body)) as current:
        delivery = await _receive(
            pool, queue, blobs, settings, envelope, producer_id=producer_id,
            raw_body=raw_body, headers=headers, url=url, query=query,
        )
        # Set after the fact rather than guessed up front: the outcome is the
        # attribute worth filtering traces on, and it is not known until here.
        current.set_attribute("status", delivery.status)
        current.set_attribute("items", delivery.items)
        if delivery.reason:
            current.set_attribute("reason", delivery.reason)
        return delivery


async def _receive(
    pool: asyncpg.Pool,
    queue,
    blobs,
    settings,
    envelope,
    *,
    producer_id: str,
    raw_body: bytes,
    headers: dict[str, str],
    url: str = "",
    query: dict | None = None,
) -> Delivery:
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

    mapping_early = dict(producer["inbound_mapping"])
    provider_early = provider_registry.get(mapping_early.get("provider"))

    # A subscription handshake that carries nothing to verify has to be
    # answered before authentication, or the subscription can never be set up.
    # Which providers those are is their decision, declared on the adapter.
    if provider_early.handshake is not None and provider_early.handshake_unauthenticated:
        answer = provider_early.handshake(
            None,
            provider_registry.Request(raw_body=raw_body, headers=headers,
                                      url=url, query=query or {}),
            [],
        )
        if answer is not None:
            await _record_delivery(pool, producer, None, "accepted", False, 0,
                                   len(raw_body), "subscription validation")
            return Delivery("accepted", 0, [], "subscription validation", handshake=answer)

    try:
        signature_verified, key_id = await authenticate(
            pool, envelope, producer, raw_body=raw_body, headers=headers,
            url=url, query=query,
        )
    except WebhookError as exc:
        # Counted here because authentication fails *before* a delivery row
        # exists, so these never reach the recorder every other path funnels
        # through. A spike in signature failures is the clearest signal of
        # either a rotated secret or someone probing the endpoint, and it
        # would otherwise be invisible in every metric.
        record("inbound_rejected", 1,
               provider=(dict(producer["inbound_mapping"]) or {}).get("provider") or "generic",
               reason="auth", status=exc.status)
        raise

    mapping = dict(producer["inbound_mapping"])
    provider = provider_registry.get(mapping.get("provider"))

    if provider.decode is not None:
        # Not every provider sends JSON. Twilio posts a form.
        payload = provider.decode(raw_body)
    else:
        try:
            payload = json.loads(raw_body or b"{}")
        except ValueError:
            # Keep it as text -- discarding a payload because it surprised us
            # is how integrations lose data silently.
            payload = {"raw": raw_body.decode("utf-8", errors="replace")}

    # The handshake runs *after* verification: answering an unverified
    # challenge would let anyone claim the endpoint.
    if provider.handshake is not None:
        secrets = []
        for column in ("signing_secret_ct", "previous_signing_secret_ct"):
            blob = producer[column]
            if blob:
                secrets.append(envelope.decrypt(bytes(blob), aad=producer["org_id"].encode()))
        answer = provider.handshake(
            payload,
            provider_registry.Request(raw_body=raw_body, headers=headers,
                                      url=url, query=query or {}),
            secrets,
        )
        if answer is not None:
            await _record_delivery(pool, producer, None, "accepted", signature_verified,
                                   0, len(raw_body), "handshake")
            return Delivery("accepted", 0, [], "handshake", handshake=answer)

    delivery_key = None
    if provider.delivery_id is not None:
        delivery_key = provider.delivery_id(payload, headers)
    delivery_key = delivery_key or (
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

    if provider.ignore is not None and provider.ignore(payload):
        # Bot echoes and edit envelopes. Without this, anything the platform
        # posts back into a channel is ingested as new content -- a loop that
        # grows a corpus on its own.
        await _record_delivery(pool, producer, delivery_key, "accepted", signature_verified,
                               0, len(raw_body), "ignored by provider rules")
        return Delivery("accepted", 0, [], "ignored by provider rules")

    # The provider's preset supplies the shape; anything configured on the
    # producer wins, because two workspaces of the same provider can
    # legitimately differ.
    items = map_payload(payload, {**provider.mapping, **mapping})
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

    provider = (dict(producer["inbound_mapping"]) or {}).get("provider") or "generic"
    record("inbound_deliveries", 1, provider=provider, status=status,
           signature_verified=signature_verified)
    if status == "dropped":
        # Its own counter, not a status label on an error metric. A disabled
        # webhook answers 200 and drops, so the error rate is correctly zero
        # while data goes nowhere -- the thing to alert on does not look like
        # a failure.
        record("ingest_dropped", 1, provider=provider, reason=reason or "unknown")
    elif status == "rejected":
        record("inbound_rejected", 1, provider=provider, reason=reason or "unknown")

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
