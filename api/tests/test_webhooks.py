"""Inbound webhooks.

A translator in front of the write API, not a second one. Most of these test a
way this goes wrong in production rather than a feature.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

import pytest

from memdog.crypto import Envelope
from memdog.retrieval import get_item
from memdog.webhooks import WebhookError, map_payload, receive, verify_signature

pytestmark = pytest.mark.asyncio


@pytest.fixture
def envelope():
    return Envelope(os.urandom(32))


async def _webhook_producer(pool, tenant, envelope, *, auth="signature", mapping=None,
                            secret="shh-very-secret", defaults=None):
    from memdog.ids import new_id

    producer_id = new_id("whk")
    await pool.execute(
        """
        INSERT INTO producers (producer_id, type, user_id, org_id, project_id,
                               status, inbound_auth, signing_secret_ct,
                               inbound_mapping, defaults)
        VALUES ($1, 'webhook', $2, $3, $4, 'enabled', $5, $6, $7, $8)
        """,
        producer_id, tenant.user_id, tenant.org_id, tenant.project_id, auth,
        envelope.encrypt(secret.encode(), aad=tenant.org_id.encode()) if secret else None,
        mapping or {}, defaults or {},
    )
    return producer_id


def _sign(secret: str, body: bytes, timestamp: str | None = None) -> str:
    signed = body if timestamp is None else f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


# ------------------------------------------------------------- signatures


async def test_a_signature_is_verified_over_the_raw_bytes(
    pool, queue, blobs, settings, tenant, envelope
):
    """Parsing and re-serialising JSON changes the bytes. The signature is over
    what was sent, not over what our parser reconstructed."""
    producer_id = await _webhook_producer(pool, tenant, envelope)
    body = b'{"text":  "spaced  oddly",   "id": "evt-1"}'
    ts = str(int(time.time()))

    result = await receive(
        pool, queue, blobs, settings, envelope, producer_id=producer_id,
        raw_body=body,
        headers={"x-signature": _sign("shh-very-secret", body, ts),
                 "x-signature-timestamp": ts},
    )
    assert result.status == "accepted" and result.items == 1

    # The same payload, reformatted, does not verify -- as it must not.
    reserialised = json.dumps(json.loads(body)).encode()
    with pytest.raises(WebhookError) as exc:
        await receive(
            pool, queue, blobs, settings, envelope, producer_id=producer_id,
            raw_body=reserialised,
            headers={"x-signature": _sign("shh-very-secret", body, ts),
                     "x-signature-timestamp": ts},
        )
    assert exc.value.status == 401


async def test_a_stale_signature_is_a_replay(pool, queue, blobs, settings, tenant, envelope):
    """Verifiable but old: a captured request replayed later."""
    producer_id = await _webhook_producer(pool, tenant, envelope)
    body = b'{"id": "old"}'
    old = str(int(time.time()) - 4000)
    with pytest.raises(WebhookError) as exc:
        await receive(
            pool, queue, blobs, settings, envelope, producer_id=producer_id,
            raw_body=body,
            headers={"x-signature": _sign("shh-very-secret", body, old),
                     "x-signature-timestamp": old},
        )
    assert exc.value.status == 401


async def test_rotation_keeps_the_previous_secret_working(
    pool, queue, blobs, settings, tenant, envelope
):
    """Without an overlap, rotating a key is an outage for everything in
    flight."""
    producer_id = await _webhook_producer(pool, tenant, envelope, secret="new-secret")
    await pool.execute(
        "UPDATE producers SET previous_signing_secret_ct = $2 WHERE producer_id = $1",
        producer_id, envelope.encrypt(b"old-secret", aad=tenant.org_id.encode()),
    )
    body = b'{"id": "mid-rotation"}'
    ts = str(int(time.time()))
    result = await receive(
        pool, queue, blobs, settings, envelope, producer_id=producer_id, raw_body=body,
        headers={"x-signature": _sign("old-secret", body, ts), "x-signature-timestamp": ts},
    )
    assert result.status == "accepted"


async def test_an_unsigned_delivery_is_refused_when_signing_is_required(
    pool, queue, blobs, settings, tenant, envelope
):
    producer_id = await _webhook_producer(pool, tenant, envelope)
    with pytest.raises(WebhookError) as exc:
        await receive(pool, queue, blobs, settings, envelope,
                      producer_id=producer_id, raw_body=b"{}", headers={})
    assert exc.value.status == 401


async def test_signature_comparison_tolerates_provider_prefixes():
    body = b"payload"
    digest = hmac.new(b"k", body, hashlib.sha256).hexdigest()
    # GitHub sends `sha256=<hex>`; Slack sends `v0=<hex>`.
    assert verify_signature(raw_body=body, provided=f"sha256={digest}",
                            secrets=[b"k"], timestamp=None)
    assert not verify_signature(raw_body=body, provided="sha256=deadbeef",
                                secrets=[b"k"], timestamp=None)


# ---------------------------------------------------------- other methods


async def test_an_api_key_producer_checks_the_configured_key(
    pool, queue, blobs, settings, tenant, envelope
):
    from memdog.auth import DATA_WRITE, issue_key

    producer_id = await _webhook_producer(pool, tenant, envelope, auth="api_key", secret=None)
    token = await issue_key(pool, user_id=tenant.user_id, org_id=tenant.org_id,
                            capabilities=[DATA_WRITE])
    key_id = await pool.fetchval(
        "SELECT key_id FROM api_keys WHERE prefix = $1", token.split(".")[0]
    )
    await pool.execute("UPDATE producers SET api_key_id = $2 WHERE producer_id = $1",
                       producer_id, key_id)

    ok = await receive(pool, queue, blobs, settings, envelope, producer_id=producer_id,
                       raw_body=b'{"id": "k1"}', headers={"x-api-key": token})
    assert ok.status == "accepted"

    other = await issue_key(pool, user_id=tenant.user_id, org_id=tenant.org_id,
                            capabilities=[DATA_WRITE])
    with pytest.raises(WebhookError) as exc:
        await receive(pool, queue, blobs, settings, envelope, producer_id=producer_id,
                      raw_body=b'{"id": "k2"}', headers={"x-api-key": other})
    assert exc.value.status == 403


async def test_a_url_secret_producer_accepts_the_bare_post(
    pool, queue, blobs, settings, tenant, envelope
):
    """The weakest method, for providers that can do nothing else: knowing the
    id is the whole credential."""
    producer_id = await _webhook_producer(pool, tenant, envelope, auth="url_secret", secret=None)
    result = await receive(pool, queue, blobs, settings, envelope,
                           producer_id=producer_id, raw_body=b'{"id": "u1"}', headers={})
    assert result.status == "accepted"


# ---------------------------------------------------------------- delivery


async def test_a_retried_delivery_is_a_no_op(pool, queue, blobs, settings, tenant, envelope):
    """Providers retry aggressively, and on any non-2xx. A duplicate must not
    become a second copy."""
    producer_id = await _webhook_producer(pool, tenant, envelope, auth="url_secret", secret=None)
    headers = {"x-delivery-id": "delivery-42"}
    body = b'{"id": "once"}'

    first = await receive(pool, queue, blobs, settings, envelope,
                          producer_id=producer_id, raw_body=body, headers=headers)
    second = await receive(pool, queue, blobs, settings, envelope,
                           producer_id=producer_id, raw_body=body, headers=headers)

    assert first.status == "accepted"
    assert second.status == "duplicate"
    assert await pool.fetchval(
        "SELECT count(*) FROM data_items WHERE producer_id = $1", producer_id
    ) == 1


async def test_a_disabled_producer_accepts_and_drops(
    pool, queue, blobs, settings, tenant, envelope
):
    """A provider must not be able to tell a disabled integration from a
    working one."""
    producer_id = await _webhook_producer(pool, tenant, envelope, auth="url_secret", secret=None)
    await pool.execute("UPDATE producers SET status = 'disabled' WHERE producer_id = $1",
                       producer_id)
    result = await receive(pool, queue, blobs, settings, envelope,
                           producer_id=producer_id, raw_body=b'{"id": "x"}', headers={})
    assert result.status == "dropped"
    assert await pool.fetchval(
        "SELECT count(*) FROM data_items WHERE producer_id = $1", producer_id
    ) == 0


async def test_an_unknown_producer_looks_like_a_missing_one(
    pool, queue, blobs, settings, tenant, envelope
):
    with pytest.raises(WebhookError) as exc:
        await receive(pool, queue, blobs, settings, envelope,
                      producer_id="whk_does_not_exist", raw_body=b"{}", headers={})
    assert exc.value.status == 404


async def test_every_delivery_is_recorded_including_the_rejected_ones(
    pool, queue, blobs, settings, tenant, envelope
):
    """"We sent it, did you get it?" needs an answer that does not depend on
    whether it worked."""
    producer_id = await _webhook_producer(pool, tenant, envelope, auth="url_secret", secret=None)
    await receive(pool, queue, blobs, settings, envelope, producer_id=producer_id,
                  raw_body=b'{"id": "d1"}', headers={"x-delivery-id": "d1"})
    await pool.execute("UPDATE producers SET status = 'disabled' WHERE producer_id = $1",
                       producer_id)
    await receive(pool, queue, blobs, settings, envelope, producer_id=producer_id,
                  raw_body=b'{"id": "d2"}', headers={"x-delivery-id": "d2"})

    rows = await pool.fetch(
        "SELECT status, items FROM webhook_deliveries WHERE producer_id = $1 ORDER BY received_at",
        producer_id,
    )
    assert [r["status"] for r in rows] == ["accepted", "dropped"]


# ----------------------------------------------------------------- mapping


async def test_the_default_mapping_keeps_the_whole_payload():
    """It loses nothing, is always correct, and can be re-parsed later once
    someone knows what the shape means."""
    items = map_payload({"weird": {"nested": [1, 2]}}, {})
    assert len(items) == 1
    assert '"weird"' in items[0].content.text


async def test_a_mapping_can_fan_a_batch_into_items():
    payload = {"events": [
        {"id": "a", "body": "first", "ts": 1700000000},
        {"id": "b", "body": "second", "ts": 1700000060},
    ]}
    items = map_payload(payload, {
        "items_path": "events", "external_id_path": "id",
        "text_path": "body", "event_time_path": "ts",
    })
    assert [i.external_id for i in items] == ["a", "b"]
    assert items[0].content.text == "first"
    assert items[0].event_time.year == 2023


async def test_a_mapping_that_misses_still_keeps_the_record():
    """A mapping that raises on an absent field turns a schema change on their
    side into an outage on ours."""
    items = map_payload({"id": "x"}, {"text_path": "message.body.text"})
    assert len(items) == 1
    assert '"id": "x"' in items[0].content.text


async def test_a_non_json_body_is_kept_as_text(pool, queue, blobs, settings, tenant, envelope):
    """Discarding a payload because it surprised us is how integrations lose
    data silently."""
    producer_id = await _webhook_producer(pool, tenant, envelope, auth="url_secret", secret=None)
    result = await receive(pool, queue, blobs, settings, envelope, producer_id=producer_id,
                           raw_body=b"plain text, not json at all", headers={})
    assert result.status == "accepted" and result.items == 1

    from memdog.auth import Principal

    principal = Principal(user_id=tenant.user_id, org_id=tenant.org_id,
                          capabilities=frozenset({"data:read"}))
    item = await get_item(pool, principal, result.data_ids[0])
    assert "not json at all" in item["content_text"]


async def test_webhook_writes_go_through_the_ordinary_write_path(
    pool, queue, blobs, settings, tenant, envelope
):
    """One admission path, one place ACLs are derived, one set of events."""
    producer_id = await _webhook_producer(pool, tenant, envelope, auth="url_secret", secret=None)
    result = await receive(pool, queue, blobs, settings, envelope, producer_id=producer_id,
                           raw_body=b'{"id": "traced"}', headers={})

    from memdog.events import list_events

    events = await list_events(pool, tenant.org_id, data_id=result.data_ids[0])
    assert "data.recorded" in {e["event_type"] for e in events}
    # Enrichment is opt-in here too: a chatty webhook that summarises every
    # message is an unbounded bill.
    assert "enrichment.requested" not in {e["event_type"] for e in events}
