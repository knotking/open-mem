"""Provider adapters -- where each integration's quirks live, and nowhere else.

Every provider signs differently, retries differently, and calls its fields
something different. Spreading that across the receive path would mean every new
integration edits shared code; keeping it here means adding one is adding a
dictionary entry.

Three things vary and all three matter:

**The signed string.** Slack signs `v0:{timestamp}:{body}`, GitHub signs the
bare body, Stripe signs `{t}.{payload}`. Getting this wrong fails closed and
looks exactly like a misconfigured secret, which is a bad afternoon.

**The handshake.** Slack will not accept an endpoint until it has POSTed a
challenge and had it echoed back -- so registration is impossible without it.

**What a message is.** A Slack event envelope is mostly routing; the message is
three fields deep, and the thread id is worth keeping because it turns a
conversation into a container without anyone configuring one.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import parse_qsl

SIGNATURE_WINDOW_SECONDS = 300


@dataclass(frozen=True)
class Request:
    """What a provider might have signed.

    Some sign the body. Twilio signs the URL plus the sorted parameters, which
    means the receive path has to hand the adapter more than the bytes -- and
    discovering that after building the registry around `raw_body` alone is the
    kind of rewrite worth avoiding by passing the whole thing from the start.
    """

    raw_body: bytes
    headers: dict
    url: str = ""
    query: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SignatureScheme:
    """How to reconstruct what the provider actually signed, and in what form.

    Four things vary and every one of them fails closed when wrong, so a
    mistake here is indistinguishable from a bad secret at 3am.
    """

    signature_headers: tuple[str, ...]
    timestamp_headers: tuple[str, ...] = ()
    # Given (timestamp, request) -> the exact bytes the provider hashed.
    base: Callable[[str | None, "Request"], bytes] = lambda ts, req: (
        req.raw_body if ts is None else f"{ts}.".encode() + req.raw_body
    )
    # Shopify and Twilio send base64; most send hex.
    encoding: str = "hex"
    # Twilio still uses SHA-1. Not our choice.
    algorithm: str = "sha256"
    # Providers prefix their digests -- `v0=`, `sha256=`. Strip before comparing.
    strip_prefix: bool = True
    # And send it back when *we* are the one signing, so a test delivery is
    # shaped like the real thing rather than merely acceptable to `verify`.
    prefix: str = ""
    # Some providers do not sign at all and use a shared secret in the body.
    shared_secret_path: str | None = None


@dataclass(frozen=True)
class Handshake:
    """A reply the provider requires before it will accept the endpoint.

    `plain_text` matters: Microsoft Graph rejects a JSON-wrapped echo, and
    `needs_secret` matters because Zoom's reply is computed from the secret
    rather than echoed.
    """

    body: dict | str
    plain_text: bool = False


@dataclass(frozen=True)
class Provider:
    name: str
    signature: SignatureScheme
    # (payload, request, secrets) -> a reply, or None to carry on receiving.
    handshake: Callable[..., Handshake | None] | None = None
    delivery_id: Callable[[Any, dict], Any] | None = None
    mapping: dict = field(default_factory=dict)
    ignore: Callable[[Any], bool] | None = None
    # Not every provider sends JSON. Twilio posts a form.
    decode: Callable[[bytes], Any] | None = None
    # Whether the handshake can be answered before authentication.
    #
    # For Graph it must be: the validation request carries no body and no
    # clientState, so there is nothing to verify, and echoing back a token the
    # caller supplied reveals nothing they did not already have.
    #
    # For Slack and Zoom it must NOT be: their challenge is signed, and
    # answering an unverified one would let anyone claim the endpoint.
    handshake_unauthenticated: bool = False


def _slack_base(timestamp: str | None, req: "Request") -> bytes:
    # The version prefix is part of the signed string, not decoration.
    return b"v0:" + (timestamp or "").encode() + b":" + req.raw_body


def _twilio_base(timestamp: str | None, req: "Request") -> bytes:
    """Twilio signs the full URL with the POST parameters appended in sorted
    key order -- not the body. A registry built only around raw bytes cannot
    express this, which is why Request carries the URL."""
    params = dict(parse_qsl(req.raw_body.decode("utf-8", errors="replace")))
    joined = "".join(f"{k}{params[k]}" for k in sorted(params))
    return (req.url + joined).encode()


def _decode_form(raw: bytes) -> Any:
    return dict(parse_qsl(raw.decode("utf-8", errors="replace")))


def _slack_handshake(payload: Any, req: "Request", secrets: list) -> Handshake | None:
    """Slack will not save an endpoint until it echoes this back.

    It is signed like any other request, so verification still runs first --
    answering an unverified challenge would let anyone claim the endpoint.
    """
    if isinstance(payload, dict) and payload.get("type") == "url_verification":
        return Handshake({"challenge": payload.get("challenge", "")})
    return None


def _graph_handshake(payload: Any, req: "Request", secrets: list) -> Handshake | None:
    """Microsoft Graph validates a subscription with a query parameter and
    demands the token back as **plain text**. A JSON-wrapped echo is rejected,
    and the subscription silently never activates."""
    token = req.query.get("validationToken")
    if token:
        return Handshake(token, plain_text=True)
    return None


def _zoom_handshake(payload: Any, req: "Request", secrets: list) -> Handshake | None:
    """Zoom's challenge is computed, not echoed: it wants an HMAC of the token
    it sent, which proves we hold the secret rather than merely received the
    request."""
    if not isinstance(payload, dict) or payload.get("event") != "endpoint.url_validation":
        return None
    plain = (payload.get("payload") or {}).get("plainToken", "")
    if not secrets:
        return None
    encrypted = hmac.new(secrets[0], plain.encode(), hashlib.sha256).hexdigest()
    return Handshake({"plainToken": plain, "encryptedToken": encrypted})


def _slack_ignore(payload: Any) -> bool:
    """Bot echoes, edits and joins.

    Without this, anything the platform itself posts back into a channel is
    ingested as new content -- a loop that grows a corpus on its own.
    """
    if not isinstance(payload, dict):
        return False
    if payload.get("type") not in {"event_callback"}:
        return True
    event = payload.get("event") or {}
    if event.get("bot_id"):
        return True
    return event.get("subtype") in {
        "bot_message", "message_changed", "message_deleted", "channel_join", "channel_leave",
    }


def _stripe_signature(headers: dict) -> tuple:
    """`Stripe-Signature: t=1700000000,v1=abc...` -- a list, not a value."""
    raw = headers.get("stripe-signature", "")
    parts = dict(piece.split("=", 1) for piece in raw.split(",") if "=" in piece)
    return parts.get("t"), parts.get("v1")


PROVIDERS: dict = {
    "slack": Provider(
        name="slack",
        signature=SignatureScheme(
            signature_headers=("x-slack-signature",),
            timestamp_headers=("x-slack-request-timestamp",),
            base=_slack_base,
            prefix="v0=",
        ),
        handshake=_slack_handshake,
        # Slack retries on any non-2xx and repeats event_id -- this is what
        # turns a retry storm into one item.
        delivery_id=lambda payload, headers: (
            payload.get("event_id") if isinstance(payload, dict) else None
        ),
        ignore=_slack_ignore,
        mapping={
            "items_path": "event",
            "external_id_path": "client_msg_id",
            "text_path": "text",
            "event_time_path": "ts",
            "source_type": "chat",
            # A thread becomes a conversation memory with nothing configured:
            # the thread id *is* the natural key the write path already upserts on.
            "memory_key_path": "thread_ts",
            "memory_fallback_key_path": "ts",
            "memory_type": "conversation",
            "tags": ["source:slack"],
        },
    ),
    "github": Provider(
        name="github",
        signature=SignatureScheme(
            signature_headers=("x-hub-signature-256",),
            # No timestamp is sent, so the signature covers the body alone and
            # replay protection has to come from the delivery id.
            base=lambda ts, req: req.raw_body,
            prefix="sha256=",
        ),
        delivery_id=lambda payload, headers: headers.get("x-github-delivery"),
        mapping={"source_type": "event", "tags": ["source:github"]},
    ),
    "stripe": Provider(
        name="stripe",
        signature=SignatureScheme(
            signature_headers=("stripe-signature",),
            timestamp_headers=(),
        ),
        delivery_id=lambda payload, headers: (
            payload.get("id") if isinstance(payload, dict) else None
        ),
        mapping={"external_id_path": "id", "source_type": "event",
                 "tags": ["source:stripe"]},
    ),
    "linear": Provider(
        name="linear",
        signature=SignatureScheme(
            signature_headers=("linear-signature",),
            base=lambda ts, req: req.raw_body,
        ),
        delivery_id=lambda payload, headers: (
            payload.get("webhookId") if isinstance(payload, dict) else None
        ),
        mapping={"items_path": "data", "external_id_path": "id",
                 "text_path": "description", "source_type": "issue",
                 "tags": ["source:linear"]},
    ),
    "shopify": Provider(
        name="shopify",
        signature=SignatureScheme(
            signature_headers=("x-shopify-hmac-sha256",),
            base=lambda ts, req: req.raw_body,
            # Base64, not hex. A hex comparison against a base64 digest fails
            # in a way that looks exactly like a wrong secret.
            encoding="base64",
            strip_prefix=False,
        ),
        delivery_id=lambda payload, headers: headers.get("x-shopify-webhook-id"),
        mapping={"external_id_path": "id", "source_type": "order",
                 "tags": ["source:shopify"]},
    ),
    "twilio": Provider(
        name="twilio",
        signature=SignatureScheme(
            signature_headers=("x-twilio-signature",),
            base=_twilio_base,
            encoding="base64",
            algorithm="sha1",       # still SHA-1. Not our choice.
            strip_prefix=False,
        ),
        decode=_decode_form,        # a form post, not JSON
        delivery_id=lambda payload, headers: (
            payload.get("MessageSid") if isinstance(payload, dict) else None
        ),
        mapping={"external_id_path": "MessageSid", "text_path": "Body",
                 "source_type": "chat",
                 # An SMS conversation is keyed by the other party's number, so
                 # a thread of messages collects itself.
                 "memory_key_path": "From", "memory_type": "conversation",
                 "tags": ["source:twilio"]},
    ),
    "microsoft_graph": Provider(
        name="microsoft_graph",
        signature=SignatureScheme(
            signature_headers=(),
            # Graph does not sign notifications; it returns the clientState it
            # was given at subscription time. Weaker than a signature, and the
            # only thing on offer.
            shared_secret_path="value.0.clientState",
        ),
        handshake=_graph_handshake,
        handshake_unauthenticated=True,
        delivery_id=lambda payload, headers: (
            _dig(payload, "value.0.subscriptionId") if isinstance(payload, dict) else None
        ),
        mapping={"items_path": "value", "external_id_path": "resource",
                 "source_type": "event", "tags": ["source:microsoft"]},
    ),
    "zoom": Provider(
        name="zoom",
        signature=SignatureScheme(
            signature_headers=("x-zm-signature",),
            timestamp_headers=("x-zm-request-timestamp",),
            base=_slack_base,       # same v0:{ts}:{body} shape as Slack
        ),
        handshake=_zoom_handshake,
        delivery_id=lambda payload, headers: (
            (payload.get("payload") or {}).get("object", {}).get("uuid")
            if isinstance(payload, dict) else None
        ),
        mapping={"items_path": "payload", "source_type": "event",
                 "tags": ["source:zoom"]},
    ),
    "generic": Provider(
        name="generic",
        signature=SignatureScheme(
            signature_headers=("x-signature",),
            timestamp_headers=("x-signature-timestamp",),
        ),
        delivery_id=lambda payload, headers: headers.get("x-delivery-id"),
    ),
}


def get(name) -> Provider:
    return PROVIDERS.get((name or "generic").lower(), PROVIDERS["generic"])


def _digest(secret: bytes, signed: bytes, scheme: SignatureScheme) -> str:
    algorithm = hashlib.sha1 if scheme.algorithm == "sha1" else hashlib.sha256
    mac = hmac.new(secret, signed, algorithm)
    return (
        base64.b64encode(mac.digest()).decode()
        if scheme.encoding == "base64"
        else mac.hexdigest()
    )


def sign(provider: Provider, *, request: Request, secret: bytes,
         timestamp: str | None = None) -> dict:
    """The headers this provider would send for these bytes. Mirror of `verify`.

    It lives here, beside the scheme it uses, because the alternative is a
    second copy of every provider's signing rule somewhere else -- and a test
    delivery signed by the copy tests the copy. `test-delivery` did exactly
    that: it hand-rolled the *generic* scheme for every producer, so the
    console's "send as the provider would" button answered 401 for Slack,
    Zoom, Linear, Shopify, Twilio, Stripe and Graph alike. A wrongly-signed
    test is indistinguishable from a wrong secret, which is the one thing this
    button exists to tell apart.
    """
    scheme = provider.signature
    # Nothing is signed: the secret travels in the body, and the caller has
    # already put it there or the delivery is not a valid one to send.
    if scheme.shared_secret_path:
        return {}

    stamp = timestamp or str(int(time.time()))
    carries_time = bool(scheme.timestamp_headers) or provider.name == "stripe"
    signed = scheme.base(stamp if carries_time else None, request)
    digest = scheme.prefix + _digest(secret, signed, scheme)

    if provider.name == "stripe":
        # One header carrying both halves, the shape `_stripe_signature` parses.
        return {"stripe-signature": f"t={stamp},v1={digest}"}

    headers = {scheme.signature_headers[0]: digest}
    if scheme.timestamp_headers:
        headers[scheme.timestamp_headers[0]] = stamp
    return headers


def verify(provider: Provider, *, request: Request, secrets: list) -> bool:
    """Verify against this provider's scheme, inside the replay window."""
    scheme = provider.signature

    # A few providers do not sign at all: they hand back a secret they were
    # given at subscription time. Weaker, but refusing to support it would
    # mean refusing the provider.
    if scheme.shared_secret_path:
        payload = _safe_json(request.raw_body)
        claimed = _dig(payload, scheme.shared_secret_path)
        return any(
            hmac.compare_digest(str(claimed or ""), secret.decode(errors="replace"))
            for secret in secrets
        )

    if not secrets:
        return False

    if provider.name == "stripe":
        timestamp, provided = _stripe_signature(request.headers)
    else:
        provided = next(
            (request.headers[h] for h in scheme.signature_headers if h in request.headers), None
        )
        timestamp = next(
            (request.headers[h] for h in scheme.timestamp_headers if h in request.headers), None
        )
    if not provided:
        return False

    if timestamp is not None:
        try:
            if abs(time.time() - float(timestamp)) > SIGNATURE_WINDOW_SECONDS:
                return False   # verifiable but stale: a captured request replayed
        except ValueError:
            return False

    candidate = provided.split("=")[-1].strip() if scheme.strip_prefix else provided.strip()
    signed = scheme.base(timestamp, request)
    return any(
        hmac.compare_digest(_digest(secret, signed, scheme), candidate) for secret in secrets
    )


def _safe_json(raw: bytes) -> Any:
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        return {}


def _dig(payload: Any, path: str) -> Any:
    """Walk a dotted path, indexing into lists by number.

    Graph puts its shared secret at `value.0.clientState` -- a path that has to
    cross a list to reach anything.
    """
    current = payload
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
