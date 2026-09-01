"""Nine providers, signing the way they actually sign.

Inbound is the half of ingestion that nothing could rehearse. Crawling reaches
out and can be pointed at a simulator; a webhook arrives, and until now the only
way to produce one was to construct the headers by hand inside a test — which
meant every scenario worth checking (a retry storm, a rotation window, a replay,
a burst against a live deployment) cost more to set up than it was worth.

**This is written from each provider's published scheme, not from
`providers.py`.** That independence is the entire point. A test that signs with
`_digest()` and verifies with `verify()` proves the module is self-consistent,
which it would also be if the scheme were wrong. Signing here and verifying
there means the two have to agree, and a change to either breaks the pair.

The honest limit: both sides were read off the same documentation by the same
person, so this catches a *drift* between signer and verifier and a mistake in
one of them — it does not catch a shared misreading. Only a real delivery does
that, and that needs somebody's account.

Nine schemes, and no two are the same:

| Provider   | Signs                       | Encoding | Notes                       |
|------------|-----------------------------|----------|-----------------------------|
| slack      | `v0:{ts}:{body}`            | hex      | header value prefixed `v0=` |
| github     | the bare body               | hex      | no timestamp at all         |
| stripe     | `{ts}.{body}`               | hex      | `t=` and `v1=` in one header|
| linear     | the bare body               | hex      |                             |
| shopify    | the bare body               | base64   |                             |
| twilio     | URL + sorted form params    | base64   | SHA-1, and a form body      |
| ms graph   | nothing — a shared secret   | —        | `clientState` in the body   |
| zoom       | `v0:{ts}:{body}`            | hex      | challenge is computed       |
| generic    | `{ts}.{body}`               | hex      |                             |

Fire one at a running deployment:

    python -m tools.fake_inbound github http://localhost:8000/hooks/<producer_id> <secret>
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import time
import uuid
from urllib.parse import urlencode

# --------------------------------------------------------------- signing


def _hex(secret: bytes, signed: bytes, algorithm=hashlib.sha256) -> str:
    return hmac.new(secret, signed, algorithm).hexdigest()


def _b64(secret: bytes, signed: bytes, algorithm=hashlib.sha256) -> str:
    return base64.b64encode(hmac.new(secret, signed, algorithm).digest()).decode()


def sign(provider: str, secret: str, body: bytes, *,
         url: str = "", timestamp: int | None = None) -> dict[str, str]:
    """The headers this provider would send with this body.

    `timestamp` is a parameter rather than always `now` so a replay is one line
    to express: sign with a timestamp outside the window and the delivery must
    be refused even though the digest is perfectly valid.
    """
    key = secret.encode()
    ts = str(timestamp if timestamp is not None else int(time.time()))
    headers = {"content-type": "application/json"}

    if provider == "slack":
        headers["x-slack-request-timestamp"] = ts
        headers["x-slack-signature"] = "v0=" + _hex(key, b"v0:" + ts.encode() + b":" + body)

    elif provider == "github":
        # No timestamp is sent, so the signature covers the body alone and
        # replay protection has to come from the delivery id instead.
        headers["x-hub-signature-256"] = "sha256=" + _hex(key, body)
        headers["x-github-delivery"] = str(uuid.uuid4())
        headers["x-github-event"] = "issues"

    elif provider == "stripe":
        headers["stripe-signature"] = f"t={ts},v1=" + _hex(key, ts.encode() + b"." + body)

    elif provider == "linear":
        headers["linear-signature"] = _hex(key, body)

    elif provider == "shopify":
        headers["x-shopify-hmac-sha256"] = _b64(key, body)

    elif provider == "twilio":
        # Twilio signs the URL with the POST parameters appended in sorted key
        # order -- not the body -- and the body is a form, not JSON.
        params = json.loads(body)
        joined = "".join(f"{k}{params[k]}" for k in sorted(params))
        headers["content-type"] = "application/x-www-form-urlencoded"
        headers["x-twilio-signature"] = _b64(key, (url + joined).encode(), hashlib.sha1)

    elif provider == "microsoft_graph":
        # Graph does not sign. It returns the secret it was handed at
        # subscription time, inside the notification.
        pass

    elif provider == "zoom":
        headers["x-zm-request-timestamp"] = ts
        headers["x-zm-signature"] = "v0=" + _hex(key, b"v0:" + ts.encode() + b":" + body)

    elif provider == "generic":
        headers["x-signature-timestamp"] = ts
        headers["x-signature"] = _hex(key, ts.encode() + b"." + body)

    else:
        raise ValueError(f"no signing rule for {provider!r}")

    return headers


def body_for(provider: str, secret: str = "") -> bytes:
    """A payload shaped the way this provider sends them.

    Realistic rather than minimal: the mapping in `providers.py` reads specific
    paths (`event.text`, `value[0].clientState`, `payload.object.uuid`), so a
    stub payload would verify and then map to nothing, which is a pass that
    proves less than it appears to.
    """
    if provider == "slack":
        return json.dumps({
            "type": "event_callback",
            "event_id": f"Ev{uuid.uuid4().hex[:10].upper()}",
            "event": {
                "type": "message", "client_msg_id": str(uuid.uuid4()),
                "text": "Procurement will not sign until the security review "
                        "closes. Legal is still on the indemnity cap.",
                "ts": f"{time.time():.6f}", "thread_ts": "1756600000.123456",
                "channel": "C0ACME", "user": "U0DANA",
            },
        }).encode()

    if provider == "github":
        return json.dumps({
            "action": "opened",
            "issue": {"number": 412, "title": "Crawl re-reads the whole source",
                      "body": "Every run pulls all records. Looks like no "
                              "incremental clause on the template.",
                      "updated_at": "2026-08-31T22:05:00Z"},
            "repository": {"full_name": "acme/widgets"},
        }).encode()

    if provider == "stripe":
        return json.dumps({
            "id": f"evt_{uuid.uuid4().hex[:16]}", "type": "invoice.paid",
            "data": {"object": {"id": "in_1ACME", "amount_paid": 41000,
                                "currency": "usd"}},
        }).encode()

    if provider == "linear":
        return json.dumps({
            "action": "update", "type": "Issue",
            "data": {"id": str(uuid.uuid4()), "title": "Renewal risk: champion moving teams",
                     "description": "Champion moves in November; renewal is August."},
        }).encode()

    if provider == "shopify":
        return json.dumps({
            "id": 8801234, "email": "dana@northwind.example",
            "total_price": "412.00", "line_items": [{"title": "Depot kit", "quantity": 11}],
        }).encode()

    if provider == "twilio":
        # Signed as a form; kept as JSON here so one signature helper can read
        # the parameters, and encoded on the way out.
        return json.dumps({
            "MessageSid": f"SM{uuid.uuid4().hex[:16]}", "From": "+15550100",
            "To": "+15550199", "Body": "Confirming Thursday 14:00 for the review.",
        }).encode()

    if provider == "microsoft_graph":
        return json.dumps({"value": [{
            "subscriptionId": str(uuid.uuid4()), "clientState": secret,
            "changeType": "created",
            "resource": "users/dana@acme.example/messages/AAMk",
            "resourceData": {"id": "AAMk", "@odata.type": "#Microsoft.Graph.Message"},
        }]}).encode()

    if provider == "zoom":
        return json.dumps({
            "event": "recording.completed",
            "payload": {"object": {
                "uuid": base64.b64encode(uuid.uuid4().bytes).decode(),
                "topic": "Northwind — security review",
                "recording_files": [{"file_type": "TRANSCRIPT",
                                     "download_url": "https://zoom.invalid/rec/t"}],
            }},
        }).encode()

    return json.dumps({"id": str(uuid.uuid4()), "text": "A generic delivery."}).encode()


# ------------------------------------------------------------- handshakes


def handshake_body(provider: str) -> bytes | None:
    """The one delivery that is not content.

    Every provider that has a handshake refuses to activate the subscription
    until it is answered correctly, and each wants something different back --
    an echo, a computed HMAC, or plain text. A wrong answer is not an error
    anywhere; the subscription simply never starts delivering.
    """
    if provider == "slack":
        return json.dumps({"type": "url_verification",
                           "challenge": uuid.uuid4().hex}).encode()
    if provider == "zoom":
        return json.dumps({"event": "endpoint.url_validation",
                           "payload": {"plainToken": uuid.uuid4().hex}}).encode()
    return None


# ------------------------------------------------------------------ send


def deliver(provider: str, url: str, secret: str, *, body: bytes | None = None,
            timestamp: int | None = None) -> tuple[int, str]:
    """POST a real, signed request. Used against a running deployment.

    Nothing else in the repository sends one, so the HTTP layer of the inbound
    path -- routing, header casing, body handling -- has only ever been reached
    by a real provider or not at all.
    """
    import urllib.error
    import urllib.request

    raw = body if body is not None else body_for(provider, secret)
    headers = sign(provider, secret, raw, url=url, timestamp=timestamp)
    if provider == "twilio":
        raw = urlencode(json.loads(raw)).encode()

    request = urllib.request.Request(url, data=raw, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read().decode(errors="replace")
    except urllib.error.HTTPError as error:
        # A refusal is a result, not a failure -- 401 is what this tool exists
        # to provoke half the time.
        return error.code, error.read().decode(errors="replace")
    except urllib.error.URLError as error:
        # Not reaching the deployment at all is the most common way this is run
        # wrong, and a stack trace is a worse answer than a sentence.
        return 0, f"could not reach {url}: {error.reason}"


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        print("\nproviders: slack github stripe linear shopify twilio "
              "microsoft_graph zoom generic", file=sys.stderr)
        return 2
    provider, url, secret = argv[1], argv[2], argv[3]
    status, text = deliver(provider, url, secret)
    print(f"{provider} -> {status or 'no response'}")
    print(text[:600])
    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
