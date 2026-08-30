"""Outbound delivery: telling something outside that an event happened.

memdog has never had one. `webhooks.py` is inbound only by its own docstring,
and `/producers/{id}/test-delivery` signs a payload and posts it to memdog's own
receive path -- a self-test, not delivery. So this is new surface, and it
carries a control the inbound path never needed.

**The subscription URL is caller-supplied and we POST to it from inside the
VPC.** Cloud SQL sits at a private address reachable by direct VPC egress, and
the metadata server answers at 169.254.169.254. A subscriber pointed at either
is an authenticated request from a trusted position. `validate_url` refuses
both, and it runs on **every attempt** rather than only at registration --
a hostname that resolved to a public address when the subscription was created
can resolve to a private one later, and registration-time validation alone is
defeated by exactly that.

Unlike the crawler, deliveries **do not follow redirects at all**. Re-validating
each hop is defensible for something whose job is following links; for a signed
POST it is risk for no benefit, so a 3xx is a failed delivery.

Signing mirrors what memdog already asks providers to do inbound -- HMAC-SHA256
over `{timestamp}.{raw body}` -- so a subscriber verifies the same way, and the
rotation overlap means changing a secret is not an outage for everything in
flight.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time

import asyncpg
import httpx

from .fetching import FetchError, validate_url
from .ids import new_id
from .telemetry import span

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
TIMEOUT_SECONDS = 10.0
# Exponential, capped. A subscriber that has been down an hour should not be
# retried every ten seconds for the next one.
BACKOFF_SECONDS = (10, 60, 300, 1800)


def sign(secret: bytes, timestamp: str, body: bytes) -> str:
    return hmac.new(secret, f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def new_secret() -> str:
    """Shown once, at creation, and never again."""
    return "whsec_" + secrets.token_urlsafe(32)


async def enqueue(conn, event_id: str, *, project_id: str, alert_id: str) -> int:
    """Queue this event for every subscription that wants it.

    In the same transaction as the event, so "recorded but never queued" cannot
    happen. Whether it is *sent* is a separate question with its own status --
    a subscriber being down must not roll back the record of what happened.
    """
    rows = await conn.fetch(
        """
        INSERT INTO event_deliveries (delivery_id, subscription_id, event_id)
        SELECT 'wfx_' || substr(md5(random()::text || s.subscription_id), 1, 26),
               s.subscription_id, $1
          FROM event_subscriptions s
         WHERE s.project_id = $2 AND s.enabled
           AND (s.alert_id IS NULL OR s.alert_id = $3)
        ON CONFLICT (subscription_id, event_id) DO NOTHING
        RETURNING delivery_id
        """,
        event_id, project_id, alert_id,
    )
    return len(rows)


async def deliver_owed(pool: asyncpg.Pool, envelope, *, limit: int = 100) -> dict:
    """Send everything past its next attempt. Driven by the tick.

    Nothing else wakes on `next_attempt_at`, which is why a scheduled sweep is
    not optional here: a quiet project would otherwise retry never, and a dead
    subscriber's backlog would sit forever.
    """
    owed = await pool.fetch(
        """
        SELECT d.delivery_id, d.event_id, d.attempts, s.subscription_id, s.url,
               s.org_id, s.owner_id, s.signing_secret_ct
          FROM event_deliveries d
          JOIN event_subscriptions s ON s.subscription_id = d.subscription_id
         WHERE d.status = 'pending' AND d.next_attempt_at <= now() AND s.enabled
         ORDER BY d.next_attempt_at
         LIMIT $1
         FOR UPDATE OF d SKIP LOCKED
        """,
        limit,
    )
    sent, failed, dead = 0, 0, 0
    for row in owed:
        outcome = await _attempt(pool, envelope, row)
        sent += outcome == "delivered"
        failed += outcome == "failed"
        dead += outcome == "dead"
    return {"sent": sent, "failed": failed, "dead": dead}


async def _attempt(pool: asyncpg.Pool, envelope, row) -> str:
    body = await _payload(pool, row["event_id"], row["owner_id"])
    if body is None:
        # The reader lost sight of the subject between matching and sending --
        # revoked, re-scoped or erased. Not a failure to retry: the delivery is
        # simply no longer owed, and sending it anyway is the leak this whole
        # design exists to prevent.
        await pool.execute(
            "UPDATE event_deliveries SET status = 'dead', "
            "last_error = 'recipient can no longer see the subject' "
            "WHERE delivery_id = $1", row["delivery_id"])
        return "dead"

    raw = json.dumps(body, default=str, separators=(",", ":")).encode()
    ts = str(int(time.time()))
    secret = envelope.decrypt(bytes(row["signing_secret_ct"]), aad=row["org_id"].encode())
    headers = {
        "content-type": "application/json",
        "x-delivery-id": row["delivery_id"],
        "x-signature-timestamp": ts,
        "x-signature": sign(secret, ts, raw),
    }

    attempts = row["attempts"] + 1
    try:
        with span("alert.deliver", subscription=row["subscription_id"]):
            # Re-validated on every attempt, not just at registration: a host
            # that resolved public then can resolve to 10.x now.
            validate_url(row["url"])
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                response = await client.post(
                    row["url"], content=raw, headers=headers, follow_redirects=False)
        if 200 <= response.status_code < 300:
            await pool.execute(
                "UPDATE event_deliveries SET status = 'delivered', attempts = $2, "
                "response_status = $3, delivered_at = now() WHERE delivery_id = $1",
                row["delivery_id"], attempts, response.status_code)
            return "delivered"
        error, status = f"HTTP {response.status_code}", response.status_code
    except FetchError as exc:
        # Not retryable and not the network's fault: the URL is one we refuse to
        # reach. Retrying it for half an hour would only hide that.
        await pool.execute(
            "UPDATE event_deliveries SET status = 'dead', attempts = $2, "
            "last_error = $3 WHERE delivery_id = $1",
            row["delivery_id"], attempts, f"refused: {exc}"[:500])
        return "dead"
    except Exception as exc:  # noqa: BLE001
        error, status = str(exc)[:500], None

    if attempts >= MAX_ATTEMPTS:
        # Dead-lettered visibly. A subscriber that has been down for an hour has
        # to be findable in one query, not inferred from silence.
        await pool.execute(
            "UPDATE event_deliveries SET status = 'dead', attempts = $2, "
            "response_status = $3, last_error = $4 WHERE delivery_id = $1",
            row["delivery_id"], attempts, status, error)
        return "dead"

    delay = BACKOFF_SECONDS[min(attempts - 1, len(BACKOFF_SECONDS) - 1)]
    await pool.execute(
        "UPDATE event_deliveries SET attempts = $2, response_status = $3, "
        "last_error = $4, next_attempt_at = now() + make_interval(secs => $5) "
        "WHERE delivery_id = $1",
        row["delivery_id"], attempts, status, error, delay)
    return "failed"


async def _payload(pool: asyncpg.Pool, event_id: str, owner_id: str) -> dict | None:
    """The event, as its subscription's owner is entitled to see it *now*.

    Visibility is re-resolved here rather than trusted from match time. A
    subscription owner who lost access between the two must not be told, and a
    stored copy of the ACL could not express that.
    """
    from .alerts import poll_events
    from .auth import DATA_READ, Principal

    row = await pool.fetchrow(
        "SELECT o.sequence, o.org_id FROM observed_events o WHERE o.event_id = $1",
        event_id)
    if row is None:
        return None
    reader = Principal(
        user_id=owner_id, org_id=row["org_id"], capabilities=frozenset({DATA_READ}))
    visible = await poll_events(pool, reader, since=row["sequence"] - 1, limit=1)
    for event in visible["events"]:
        if event["event_id"] == event_id:
            return {"event": event["surface"], **{
                k: v for k, v in event.items() if k != "occurred_at"},
                "occurred_at": event["occurred_at"]}
    return None


# -- subscriptions ----------------------------------------------------------


async def create_subscription(
    pool: asyncpg.Pool, principal, envelope, *, project_id: str, url: str,
    alert_id: str | None = None,
) -> dict:
    """Register an endpoint. The secret is returned **once**.

    Refusing to show it again is the same rule `bootstrap-to-secret` follows:
    a credential that can be fetched back is a credential shared with everyone
    who can call the endpoint that fetches it.
    """
    from .auth import CONFIG_WRITE

    principal.require(CONFIG_WRITE)
    if not url.startswith("https://"):
        # The payload carries workflow and record state. http is refused rather
        # than warned about.
        raise DeliveryError("subscription urls must be https")
    validate_url(url)  # refused here as well as on every send

    secret = new_secret()
    row = await pool.fetchrow(
        """
        INSERT INTO event_subscriptions (subscription_id, org_id, project_id,
            alert_id, url, owner_id, signing_secret_ct)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING subscription_id, url, alert_id, created_at
        """,
        new_id("wfs"), principal.org_id, project_id, alert_id, url,
        principal.user_id,
        envelope.encrypt(secret.encode(), aad=principal.org_id.encode()),
    )
    return dict(row) | {
        "signing_secret": secret,
        "note": "shown once; it cannot be retrieved later, only rotated",
    }


async def rotate_secret(pool: asyncpg.Pool, principal, envelope, subscription_id: str) -> dict:
    """New secret, old one still accepted for the overlap.

    Without the overlap a rotation is an outage for every delivery already
    signed -- which is why producers grew the same pair in 0018.
    """
    from .auth import CONFIG_WRITE

    principal.require(CONFIG_WRITE)
    secret = new_secret()
    row = await pool.fetchrow(
        """
        UPDATE event_subscriptions
           SET previous_signing_secret_ct = signing_secret_ct,
               signing_secret_ct = $3,
               signing_secret_rotated_at = now(), updated_at = now()
         WHERE subscription_id = $1 AND org_id = $2
        RETURNING subscription_id, signing_secret_rotated_at
        """,
        subscription_id, principal.org_id,
        envelope.encrypt(secret.encode(), aad=principal.org_id.encode()),
    )
    if row is None:
        raise DeliveryError("subscription not found", status=404)
    return dict(row) | {"signing_secret": secret}


async def list_subscriptions(pool: asyncpg.Pool, principal, project_id: str) -> list[dict]:
    from .auth import CONFIG_WRITE

    principal.require(CONFIG_WRITE)
    rows = await pool.fetch(
        """
        SELECT s.subscription_id, s.url, s.alert_id, s.owner_id, s.enabled,
               s.signing_secret_rotated_at, s.created_at,
               count(d.delivery_id) FILTER (WHERE d.status = 'dead') AS dead,
               count(d.delivery_id) FILTER (WHERE d.status = 'pending') AS pending
          FROM event_subscriptions s
          LEFT JOIN event_deliveries d ON d.subscription_id = s.subscription_id
         WHERE s.project_id = $1 AND s.org_id = $2
         GROUP BY s.subscription_id
         ORDER BY s.created_at DESC
        """,
        project_id, principal.org_id,
    )
    # No secret in the projection, deliberately.
    return [dict(r) for r in rows]


async def deliveries_for(pool: asyncpg.Pool, principal, subscription_id: str,
                        limit: int = 50) -> list[dict]:
    from .auth import CONFIG_WRITE

    principal.require(CONFIG_WRITE)
    rows = await pool.fetch(
        """
        SELECT d.delivery_id, d.event_id, d.status, d.attempts, d.response_status,
               d.last_error, d.next_attempt_at, d.created_at, d.delivered_at
          FROM event_deliveries d
          JOIN event_subscriptions s ON s.subscription_id = d.subscription_id
         WHERE d.subscription_id = $1 AND s.org_id = $2
         ORDER BY d.created_at DESC LIMIT $3
        """,
        subscription_id, principal.org_id, limit,
    )
    return [dict(r) for r in rows]


async def replay_dead(pool: asyncpg.Pool, principal, subscription_id: str) -> dict:
    """Re-arm dead letters after the endpoint is fixed.

    The rows were kept rather than dropped for exactly this: a subscriber that
    was down for an hour can be caught up, instead of the hour being gone.
    """
    from .auth import CONFIG_WRITE

    principal.require(CONFIG_WRITE)
    result = await pool.execute(
        """
        UPDATE event_deliveries d
           SET status = 'pending', attempts = 0, next_attempt_at = now(),
               last_error = NULL
          FROM event_subscriptions s
         WHERE s.subscription_id = d.subscription_id
           AND d.subscription_id = $1 AND s.org_id = $2 AND d.status = 'dead'
        """,
        subscription_id, principal.org_id,
    )
    return {"subscription_id": subscription_id, "re_armed": result}


class DeliveryError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status
