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
from memdog.webhooks import WebhookError, map_payload, receive

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
    """Against the implementation the request path actually reaches.

    This previously exercised a copy in `webhooks.py` that nothing called: the
    live check moved to `providers.verify` when signing became per-provider, and
    the old one stayed behind with the tests still pointed at it. A passing test
    over dead code is worse than no test, because it reports on a scheme the
    service does not use.
    """
    from memdog import providers

    body = b"payload"
    digest = hmac.new(b"k", body, hashlib.sha256).hexdigest()
    github = providers.get("github")
    request = providers.Request(raw_body=body, headers={}, url="", query={})

    # GitHub sends `sha256=<hex>`; Slack sends `v0=<hex>`.
    assert providers.verify(
        github,
        request=providers.Request(raw_body=body, url="", query={},
                                  headers={"x-hub-signature-256": f"sha256={digest}"}),
        secrets=[b"k"],
    )
    assert not providers.verify(
        github,
        request=providers.Request(raw_body=body, url="", query={},
                                  headers={"x-hub-signature-256": "sha256=deadbeef"}),
        secrets=[b"k"],
    )
    assert not providers.verify(github, request=request, secrets=[b"k"])


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


async def test_a_test_delivery_goes_through_real_verification(
    pool, queue, blobs, settings, tenant, envelope
):
    """A test that skipped signature verification would pass for a producer
    whose signing is broken -- exactly the case worth catching before a
    provider is pointed at it."""
    import hashlib
    import hmac
    import json as jsonlib
    import time as timelib

    producer_id = await _webhook_producer(pool, tenant, envelope, secret="test-secret")
    raw = jsonlib.dumps({"hello": "world"}).encode()
    ts = str(int(timelib.time()))
    signature = hmac.new(b"test-secret", f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()

    result = await receive(
        pool, queue, blobs, settings, envelope, producer_id=producer_id, raw_body=raw,
        headers={"x-signature": signature, "x-signature-timestamp": ts,
                 "x-delivery-id": "test-1"},
    )
    assert result.status == "accepted" and result.items == 1

    # The same helper with a wrong secret must fail, or the test proves nothing.
    bad = hmac.new(b"wrong", f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    with pytest.raises(WebhookError):
        await receive(
            pool, queue, blobs, settings, envelope, producer_id=producer_id, raw_body=raw,
            headers={"x-signature": bad, "x-signature-timestamp": ts,
                     "x-delivery-id": "test-2"},
        )


# ------------------------------------------------------------- providers


def _slack_sign(secret: str, body: bytes, ts: str) -> str:
    import hashlib as h
    import hmac as m

    return "v0=" + m.new(secret.encode(), b"v0:" + ts.encode() + b":" + body, h.sha256).hexdigest()


async def test_slack_signs_a_different_string_than_the_generic_scheme(
    pool, queue, blobs, settings, tenant, envelope
):
    """Slack signs `v0:{ts}:{body}`. Getting this wrong fails closed and looks
    exactly like a misconfigured secret."""
    import json as jsonlib
    import time as timelib

    producer_id = await _webhook_producer(
        pool, tenant, envelope, secret="slack-secret", mapping={"provider": "slack"}
    )
    body = jsonlib.dumps({
        "type": "event_callback", "event_id": "Ev123",
        "event": {"type": "message", "text": "Deploy rolled back at 14:02.",
                  "client_msg_id": "cm-1", "ts": "1787900000.000100",
                  "thread_ts": "1787899000.000100", "channel": "C1"},
    }).encode()
    ts = str(int(timelib.time()))

    result = await receive(
        pool, queue, blobs, settings, envelope, producer_id=producer_id, raw_body=body,
        headers={"x-slack-signature": _slack_sign("slack-secret", body, ts),
                 "x-slack-request-timestamp": ts},
    )
    assert result.status == "accepted" and result.items == 1

    # The generic scheme's signature over the same bytes must not verify.
    generic = _sign("slack-secret", body, ts)
    with pytest.raises(WebhookError):
        await receive(
            pool, queue, blobs, settings, envelope, producer_id=producer_id, raw_body=body,
            headers={"x-slack-signature": f"v0={generic}", "x-slack-request-timestamp": ts},
        )


async def test_slack_url_verification_is_answered_but_only_when_signed(
    pool, queue, blobs, settings, tenant, envelope
):
    """Slack will not save an endpoint until it echoes this back -- and
    answering an unverified challenge would let anyone claim the endpoint."""
    import json as jsonlib
    import time as timelib

    producer_id = await _webhook_producer(
        pool, tenant, envelope, secret="slack-secret", mapping={"provider": "slack"}
    )
    body = jsonlib.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
    ts = str(int(timelib.time()))

    result = await receive(
        pool, queue, blobs, settings, envelope, producer_id=producer_id, raw_body=body,
        headers={"x-slack-signature": _slack_sign("slack-secret", body, ts),
                 "x-slack-request-timestamp": ts},
    )
    # The reply's shape is the provider's to decide -- Graph needs plain text,
    # Slack needs JSON -- so the handshake carries both the body and the form.
    assert result.handshake.body == {"challenge": "abc123"}
    assert result.handshake.plain_text is False
    assert result.items == 0

    with pytest.raises(WebhookError):
        await receive(
            pool, queue, blobs, settings, envelope, producer_id=producer_id, raw_body=body,
            headers={"x-slack-signature": "v0=forged", "x-slack-request-timestamp": ts},
        )


async def test_a_slack_thread_becomes_a_conversation_memory(
    pool, queue, blobs, settings, tenant, principal_for, envelope
):
    """The thread id is already a stable natural key, and the write path upserts
    on it -- so a thread collects itself with nothing configured."""
    import json as jsonlib
    import time as timelib

    from memdog.retrieval import item_memories

    producer_id = await _webhook_producer(
        pool, tenant, envelope, secret="s", mapping={"provider": "slack"}
    )
    actor = await principal_for(tenant.api_key)
    ids = []
    for n in (1, 2):
        body = jsonlib.dumps({
            "type": "event_callback", "event_id": f"Ev{n}",
            "event": {"type": "message", "text": f"message {n}",
                      "client_msg_id": f"cm-{n}", "ts": f"178790000{n}.0001",
                      "thread_ts": "1787899000.0001"},
        }).encode()
        ts = str(int(timelib.time()))
        r = await receive(pool, queue, blobs, settings, envelope, producer_id=producer_id,
                          raw_body=body,
                          headers={"x-slack-signature": _slack_sign("s", body, ts),
                                   "x-slack-request-timestamp": ts})
        ids.extend(r.data_ids)

    assert len(ids) == 2
    first = await item_memories(pool, actor, ids[0])
    second = await item_memories(pool, actor, ids[1])
    # Both landed in the same container, keyed on the thread.
    assert first["memberships"][0]["memory_key"] == "1787899000.0001"
    assert first["memberships"][0]["memory_id"] == second["memberships"][0]["memory_id"]
    assert first["memberships"][0]["type"] == "conversation"


async def test_slack_bot_echoes_are_ignored(pool, queue, blobs, settings, tenant, envelope):
    """Otherwise anything the platform posts back into a channel is ingested as
    new content -- a loop that grows a corpus on its own."""
    import json as jsonlib
    import time as timelib

    producer_id = await _webhook_producer(
        pool, tenant, envelope, secret="s", mapping={"provider": "slack"}
    )
    body = jsonlib.dumps({
        "type": "event_callback", "event_id": "EvBot",
        "event": {"type": "message", "text": "posted by us", "bot_id": "B123",
                  "ts": "1787900000.1"},
    }).encode()
    ts = str(int(timelib.time()))
    result = await receive(
        pool, queue, blobs, settings, envelope, producer_id=producer_id, raw_body=body,
        headers={"x-slack-signature": _slack_sign("s", body, ts),
                 "x-slack-request-timestamp": ts},
    )
    assert result.status == "accepted" and result.items == 0
    assert result.reason == "ignored by provider rules"


async def test_slack_retries_are_deduped_on_event_id(
    pool, queue, blobs, settings, tenant, envelope
):
    """Slack retries on any non-2xx and repeats event_id."""
    import json as jsonlib
    import time as timelib

    producer_id = await _webhook_producer(
        pool, tenant, envelope, secret="s", mapping={"provider": "slack"}
    )
    body = jsonlib.dumps({
        "type": "event_callback", "event_id": "EvDup",
        "event": {"type": "message", "text": "only once", "ts": "1787900000.2"},
    }).encode()
    ts = str(int(timelib.time()))
    headers = {"x-slack-signature": _slack_sign("s", body, ts),
               "x-slack-request-timestamp": ts}

    first = await receive(pool, queue, blobs, settings, envelope,
                          producer_id=producer_id, raw_body=body, headers=headers)
    second = await receive(pool, queue, blobs, settings, envelope,
                           producer_id=producer_id, raw_body=body, headers=headers)
    assert first.status == "accepted" and second.status == "duplicate"


async def test_github_signs_the_bare_body(pool, queue, blobs, settings, tenant, envelope):
    import hashlib as h
    import hmac as m

    producer_id = await _webhook_producer(
        pool, tenant, envelope, secret="gh", mapping={"provider": "github"}
    )
    body = b'{"action": "opened", "number": 42}'
    signature = "sha256=" + m.new(b"gh", body, h.sha256).hexdigest()
    result = await receive(
        pool, queue, blobs, settings, envelope, producer_id=producer_id, raw_body=body,
        headers={"x-hub-signature-256": signature, "x-github-delivery": "gh-1"},
    )
    assert result.status == "accepted" and result.items == 1


async def test_shopify_sends_base64_not_hex(pool, queue, blobs, settings, tenant, envelope):
    """A hex comparison against a base64 digest fails in a way that looks
    exactly like a wrong secret."""
    import base64 as b64
    import hashlib as h
    import hmac as m

    producer_id = await _webhook_producer(
        pool, tenant, envelope, secret="shop", mapping={"provider": "shopify"}
    )
    body = b'{"id": 4471, "total_price": "12.00"}'
    digest = b64.b64encode(m.new(b"shop", body, h.sha256).digest()).decode()

    result = await receive(
        pool, queue, blobs, settings, envelope, producer_id=producer_id, raw_body=body,
        headers={"x-shopify-hmac-sha256": digest, "x-shopify-webhook-id": "shop-1"},
    )
    assert result.status == "accepted"

    # The same digest in hex must not verify.
    with pytest.raises(WebhookError):
        await receive(
            pool, queue, blobs, settings, envelope, producer_id=producer_id, raw_body=body,
            headers={"x-shopify-hmac-sha256": m.new(b"shop", body, h.sha256).hexdigest(),
                     "x-shopify-webhook-id": "shop-2"},
        )


async def test_twilio_signs_the_url_and_posts_a_form(
    pool, queue, blobs, settings, tenant, envelope
):
    """Twilio signs the URL plus sorted parameters — not the body — and posts
    form-encoded. A registry built only around raw bytes cannot express it."""
    import base64 as b64
    import hashlib as h
    import hmac as m

    producer_id = await _webhook_producer(
        pool, tenant, envelope, secret="tw", mapping={"provider": "twilio"}
    )
    url = "https://example.com/hooks/whk_1"
    params = {"Body": "the deploy is rolled back", "From": "+15551234",
              "MessageSid": "SM123"}
    body = "&".join(f"{k}={v.replace('+', '%2B').replace(' ', '+')}"
                    for k, v in params.items()).encode()
    signed = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    signature = b64.b64encode(m.new(b"tw", signed.encode(), h.sha1).digest()).decode()

    result = await receive(
        pool, queue, blobs, settings, envelope, producer_id=producer_id,
        raw_body=body, headers={"x-twilio-signature": signature}, url=url,
    )
    assert result.status == "accepted" and result.items == 1

    from memdog.auth import Principal
    from memdog.retrieval import get_item

    principal = Principal(user_id=tenant.user_id, org_id=tenant.org_id,
                          capabilities=frozenset({"data:read"}))
    item = await get_item(pool, principal, result.data_ids[0])
    assert "deploy is rolled back" in item["content_text"]


async def test_graph_validates_with_a_query_parameter_and_plain_text(
    pool, queue, blobs, settings, tenant, envelope
):
    """Graph rejects a JSON-wrapped echo, and the subscription then silently
    never activates."""
    producer_id = await _webhook_producer(
        pool, tenant, envelope, secret="shared-123", mapping={"provider": "microsoft_graph"}
    )
    result = await receive(
        pool, queue, blobs, settings, envelope, producer_id=producer_id, raw_body=b"",
        headers={}, query={"validationToken": "token-abc"},
    )
    assert result.handshake.body == "token-abc"
    assert result.handshake.plain_text is True


async def test_graph_authenticates_with_the_client_state_it_was_given(
    pool, queue, blobs, settings, tenant, envelope
):
    """Graph does not sign notifications. Weaker than a signature, and the only
    thing on offer -- so it is supported and named for what it is."""
    import json as jsonlib

    producer_id = await _webhook_producer(
        pool, tenant, envelope, secret="shared-123", mapping={"provider": "microsoft_graph"}
    )
    body = jsonlib.dumps({"value": [
        {"clientState": "shared-123", "subscriptionId": "sub-1",
         "resource": "me/messages/AAA"},
    ]}).encode()
    result = await receive(
        pool, queue, blobs, settings, envelope, producer_id=producer_id,
        raw_body=body, headers={},
    )
    assert result.status == "accepted" and result.items == 1

    wrong = jsonlib.dumps({"value": [{"clientState": "not-it", "subscriptionId": "s"}]}).encode()
    with pytest.raises(WebhookError):
        await receive(pool, queue, blobs, settings, envelope, producer_id=producer_id,
                      raw_body=wrong, headers={})


async def test_zoom_computes_its_challenge_rather_than_echoing_it(
    pool, queue, blobs, settings, tenant, envelope
):
    """Zoom wants an HMAC of the token it sent, which proves we hold the secret
    rather than merely received the request."""
    import hashlib as h
    import hmac as m
    import json as jsonlib
    import time as timelib

    producer_id = await _webhook_producer(
        pool, tenant, envelope, secret="zoom-secret", mapping={"provider": "zoom"}
    )
    body = jsonlib.dumps({"event": "endpoint.url_validation",
                          "payload": {"plainToken": "abc"}}).encode()
    ts = str(int(timelib.time()))
    signature = "v0=" + m.new(b"zoom-secret", b"v0:" + ts.encode() + b":" + body,
                              h.sha256).hexdigest()

    result = await receive(
        pool, queue, blobs, settings, envelope, producer_id=producer_id, raw_body=body,
        headers={"x-zm-signature": signature, "x-zm-request-timestamp": ts},
    )
    assert result.handshake.body["plainToken"] == "abc"
    assert result.handshake.body["encryptedToken"] == m.new(
        b"zoom-secret", b"abc", h.sha256
    ).hexdigest()


async def test_a_dropped_delivery_is_counted_separately_from_a_failure(
    pool, queue, blobs, settings, envelope, tenant, monkeypatch
):
    """A disabled webhook answers 200 and drops the payload. The error rate is
    correctly zero while data goes nowhere, so `ingest.dropped` has to be its
    own counter -- the thing worth alerting on does not look like a failure.
    """
    import memdog.webhooks as webhooks_mod

    emitted: list[tuple] = []
    monkeypatch.setattr(webhooks_mod, "record",
                        lambda metric, value, **labels: emitted.append((metric, labels)))

    hook = await _webhook_producer(pool, tenant, envelope, auth="none")
    await pool.execute(
        "UPDATE producers SET status = 'disabled' WHERE producer_id = $1", hook
    )
    result = await receive(
        pool, queue, blobs, settings, envelope, producer_id=hook,
        raw_body=b'{"text": "dropped on the floor"}', headers={},
    )
    assert result.status == "dropped"
    names = {m for m, _ in emitted}
    assert "ingest_dropped" in names
    assert "inbound_deliveries" in names


async def test_a_signature_failure_is_counted_even_though_it_never_reaches_a_delivery_row(
    pool, queue, blobs, settings, envelope, tenant, monkeypatch
):
    """Authentication fails before a delivery row exists, so these bypass the
    recorder every other path funnels through. A spike in signature failures is
    the clearest sign of a rotated secret or someone probing the endpoint, and
    it would otherwise appear in no metric at all.
    """
    import memdog.webhooks as webhooks_mod

    emitted: list[tuple] = []
    monkeypatch.setattr(webhooks_mod, "record",
                        lambda metric, value, **labels: emitted.append((metric, labels)))

    hook = await _webhook_producer(pool, tenant, envelope, auth="signature")
    with pytest.raises(WebhookError):
        await receive(
            pool, queue, blobs, settings, envelope, producer_id=hook,
            raw_body=b'{"text": "forged"}',
            headers={"X-Hub-Signature-256": "sha256=nonsense"},
        )
    rejected = [labels for metric, labels in emitted if metric == "inbound_rejected"]
    assert rejected and rejected[0]["reason"] == "auth"
