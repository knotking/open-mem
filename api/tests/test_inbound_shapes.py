"""Every provider's signature, signed by one side and verified by the other.

`test_webhooks.py` already checks several providers, and checks them well. What
it cannot check is *coverage*: each of those tests hand-rolls one scheme inline,
so a provider added to the registry without a test is indistinguishable from one
that is covered, and nothing fails.

`tools/fake_inbound.py` is a signer written from each provider's published
scheme. Pairing it with `providers.verify()` turns the registry itself into the
subject: the loop below iterates `PROVIDERS`, so **a new provider with no
signing rule fails here on the day it is added** rather than the day somebody
points a real subscription at it.

The limit, stated because it is easy to overstate this: signer and verifier were
both read off the same documentation, so this catches drift between them and a
mistake in one — not a shared misreading of the provider. Only a real delivery
catches that.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memdog import providers                                   # noqa: E402
from memdog.providers import SIGNATURE_WINDOW_SECONDS          # noqa: E402
from tools.fake_inbound import body_for, handshake_body, sign  # noqa: E402

SECRET = "s3cret-provider-signing-key"

# `microsoft_graph` does not sign at all -- it returns the secret it was handed
# at subscription time. It is exercised separately below rather than skipped
# quietly, because "no signature" is a real scheme with a real failure mode.
SIGNED = [name for name in providers.PROVIDERS if name != "microsoft_graph"]


def _request(provider: str, raw: bytes, headers: dict, url: str = "") -> providers.Request:
    return providers.Request(raw_body=raw, headers=headers, url=url, query={})


@pytest.mark.parametrize("name", SIGNED)
def test_a_signature_this_provider_would_send_verifies(name):
    """The pairing test, run across the whole registry.

    Nine providers, nine different signed strings, two encodings and two hash
    algorithms — and every one of them fails closed when wrong, which means a
    mistake here is indistinguishable from a bad secret at three in the morning.
    """
    url = "https://memdog.example/hooks/prd_test"
    raw = body_for(name, SECRET)
    headers = sign(name, SECRET, raw, url=url)
    if name == "twilio":
        # Twilio signs the URL plus form parameters, and posts a form.
        from urllib.parse import urlencode
        raw = urlencode(json.loads(raw)).encode()

    assert providers.verify(providers.get(name),
                            request=_request(name, raw, headers, url),
                            secrets=[SECRET.encode()]), (
        f"{name}: the signer and the verifier disagree about what gets signed")


@pytest.mark.parametrize("name", SIGNED)
def test_the_wrong_secret_is_refused(name):
    """The check that stops the test above passing vacuously. If `verify`
    returned True regardless, both tests would look identical from the outside
    and only this one would fail."""
    url = "https://memdog.example/hooks/prd_test"
    raw = body_for(name, SECRET)
    headers = sign(name, "not-the-secret", raw, url=url)
    if name == "twilio":
        from urllib.parse import urlencode
        raw = urlencode(json.loads(raw)).encode()

    assert not providers.verify(providers.get(name),
                                request=_request(name, raw, headers, url),
                                secrets=[SECRET.encode()])


@pytest.mark.parametrize("name", [n for n in SIGNED
                                  if providers.PROVIDERS[n].signature.timestamp_headers
                                  or n == "stripe"])
def test_a_valid_signature_outside_the_window_is_still_a_replay(name):
    """A captured delivery, resent later, verifies perfectly — the digest was
    real when it was made. Only the clock distinguishes it, which is why the
    providers that send a timestamp must be checked against it and why the ones
    that do not (GitHub, Linear, Shopify) need a delivery id instead."""
    stale = int(time.time()) - SIGNATURE_WINDOW_SECONDS - 60
    raw = body_for(name, SECRET)
    headers = sign(name, SECRET, raw, timestamp=stale)

    assert not providers.verify(providers.get(name),
                                request=_request(name, raw, headers),
                                secrets=[SECRET.encode()]), (
        f"{name}: a signature {SIGNATURE_WINDOW_SECONDS}s old was accepted")


def test_every_provider_in_the_registry_has_a_signing_rule():
    """The reason this file iterates the registry rather than listing names.

    A provider added to `PROVIDERS` with no rule in the simulator is a provider
    nothing can rehearse — and the gap would be invisible, because the other
    eight would still pass.
    """
    for name in providers.PROVIDERS:
        if name == "microsoft_graph":
            continue
        headers = sign(name, SECRET, b"{}", url="https://x.example/h")
        assert headers, f"{name} produced no headers"


def test_a_rotated_secret_keeps_the_previous_one_working():
    """Rotation is a window, not an instant. The provider is still signing with
    the old secret at the moment the new one is stored, so a rotation that
    accepted only the new value would drop every delivery in flight."""
    raw = body_for("github", SECRET)
    headers = sign("github", "the-previous-secret", raw)

    assert providers.verify(
        providers.get("github"), request=_request("github", raw, headers),
        secrets=[SECRET.encode(), b"the-previous-secret"]), (
        "a delivery signed with the previous secret was refused mid-rotation")


def test_graph_authenticates_with_a_secret_in_the_body_not_a_signature():
    """Microsoft Graph does not sign. It echoes back the `clientState` it was
    given at subscription time — weaker, and refusing to support it would mean
    refusing the provider, so the failure mode is worth having a test for."""
    good = body_for("microsoft_graph", SECRET)
    assert providers.verify(providers.get("microsoft_graph"),
                            request=_request("microsoft_graph", good, {}),
                            secrets=[SECRET.encode()])

    wrong = body_for("microsoft_graph", "someone-elses-state")
    assert not providers.verify(providers.get("microsoft_graph"),
                                request=_request("microsoft_graph", wrong, {}),
                                secrets=[SECRET.encode()])


@pytest.mark.parametrize("name", ["slack", "zoom"])
def test_a_handshake_is_signed_like_any_other_delivery(name):
    """Both challenges arrive signed, and answering an unverified one would let
    anyone claim the endpoint. So the handshake payload has to survive the same
    verification as content — which is only testable if the simulator can sign
    a handshake, and it is the delivery nobody thinks to rehearse."""
    raw = handshake_body(name)
    assert raw is not None
    headers = sign(name, SECRET, raw)

    assert providers.verify(providers.get(name),
                            request=_request(name, raw, headers),
                            secrets=[SECRET.encode()])

    reply = providers.get(name).handshake(json.loads(raw), _request(name, raw, headers),
                                          [SECRET.encode()])
    assert reply is not None, f"{name} did not recognise its own challenge"


def test_zoom_computes_its_challenge_rather_than_echoing_it():
    """The distinction that makes Zoom's handshake worth its own test: the reply
    proves we hold the secret, so echoing the token back is a wrong answer that
    looks like a right one."""
    raw = handshake_body("zoom")
    headers = sign("zoom", SECRET, raw)
    reply = providers.get("zoom").handshake(json.loads(raw), _request("zoom", raw, headers),
                                            [SECRET.encode()])

    plain = json.loads(raw)["payload"]["plainToken"]
    assert reply.body["plainToken"] == plain
    assert reply.body["encryptedToken"] != plain, "echoed the token instead of signing it"


def test_slack_ignores_what_it_posted_itself():
    """Not a signature concern, but the same class of problem: without it,
    anything the platform posts back into a channel is ingested as new content,
    and the corpus grows on its own."""
    payload = json.loads(body_for("slack"))
    assert not providers.get("slack").ignore(payload), "a real message was ignored"

    payload["event"]["bot_id"] = "B0SELF"
    assert providers.get("slack").ignore(payload), "a bot echo would have been ingested"


def test_the_signed_url_is_the_one_the_provider_sent_to():
    """The bug a unit test structurally cannot find, so it gets one anyway.

    Twilio signs the URL. Cloud Run terminates TLS and forwards over plain
    HTTP, so `request.url` inside the container reconstructs as `http://` while
    the provider signed `https://` — and every delivery is refused as
    `signature verification failed`, which reads as a wrong secret.

    It survived every existing test because a test hands the *same* URL to the
    signer and the verifier. It was found by firing `tools/fake_inbound.py` at
    the deployed service, where the two URLs are not the same.
    """
    from memdog.app import _public_url

    class _Req:
        def __init__(self, url, headers):
            from starlette.datastructures import URL
            self.url = URL(url)
            self.headers = headers

    behind_proxy = _Req("http://memdog.internal/webhooks/whk_1",
                        {"x-forwarded-proto": "https"})
    assert _public_url(behind_proxy) == "https://memdog.internal/webhooks/whk_1"

    # A proxy chain sends a list, and the first entry is the original client.
    chained = _Req("http://memdog.internal/webhooks/whk_1",
                   {"x-forwarded-proto": "https, http"})
    assert _public_url(chained).startswith("https://")

    # Nothing in front: the request's own scheme is the only truth there is.
    direct = _Req("http://localhost:8080/webhooks/whk_1", {})
    assert _public_url(direct) == "http://localhost:8080/webhooks/whk_1"
