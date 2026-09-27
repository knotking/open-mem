"""Credentials that are exchanged rather than presented.

"Google and Microsoft need OAuth" was said in this repository for weeks, and it
was only ever true of a person connecting their own account. An organization
connecting its own data uses a grant with no human in it — which is a POST, and
is what a connector actually needs.

The tests worth having are about what surrounds the exchange rather than the
exchange itself: that a stale token is refreshed and a fresh one is not, that
the margin against clock skew exists, and that a token endpoint's error body —
which quotes back what it was sent — never reaches a log or an exception.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

from open_mem import grants
from open_mem.grants import GrantError, Token

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def clean_cache():
    grants._cache.clear()
    yield
    grants._cache.clear()


def _transport(handler):
    """Swap httpx's network for a handler, so nothing here reaches a provider."""
    return httpx.MockTransport(handler)


@pytest.fixture
def token_endpoint(monkeypatch):
    """Records what was posted and answers like a token endpoint."""
    calls: list[dict] = []

    def install(*responses: httpx.Response):
        """One response, or a sequence consumed in order.

        The last one repeats rather than raising StopIteration — a test that
        makes an unexpected extra call should fail on its assertion, not on a
        confusing error from inside the transport.
        """
        queue = list(responses)

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append({
                "url": str(request.url),
                "form": dict(httpx.QueryParams(request.content.decode())),
            })
            return queue.pop(0) if len(queue) > 1 else queue[0]

        original = httpx.AsyncClient

        class Patched(original):
            def __init__(self, *a, **kw):
                kw["transport"] = _transport(handler)
                super().__init__(*a, **kw)

        monkeypatch.setattr(httpx, "AsyncClient", Patched)
        return calls

    return install


async def test_client_credentials_posts_the_grant_and_returns_the_token(
    token_endpoint
):
    calls = token_endpoint(httpx.Response(
        200, json={"access_token": "at-1", "expires_in": 3600}))

    value = await grants.token_for(
        "conn_1", style="client_credentials", secret="id-1:secret-1",
        config={"token_url": "https://login.example/token",
                "scope": "https://graph.microsoft.com/.default"},
    )
    assert value == "at-1"
    assert calls[0]["form"]["grant_type"] == "client_credentials"
    assert calls[0]["form"]["client_id"] == "id-1"
    assert calls[0]["form"]["scope"] == "https://graph.microsoft.com/.default"


async def test_a_fresh_token_is_not_re_exchanged(token_endpoint):
    calls = token_endpoint(httpx.Response(
        200, json={"access_token": "at-1", "expires_in": 3600}))

    for _ in range(3):
        await grants.token_for("conn_1", style="client_credentials",
                               secret="a:b", config={"token_url": "https://t/"})
    assert len(calls) == 1, "the cache is not holding"


async def test_a_stale_token_is_refreshed(token_endpoint):
    calls = token_endpoint(httpx.Response(
        200, json={"access_token": "at-2", "expires_in": 3600}))
    grants._cache["conn_1"] = Token("old", time.time() + 10)  # inside the margin

    assert await grants.token_for(
        "conn_1", style="client_credentials", secret="a:b",
        config={"token_url": "https://t/"},
    ) == "at-2"
    assert len(calls) == 1


async def test_the_skew_margin_exists_and_is_generous_enough_to_matter():
    """A token treated as valid until the instant it expires produces a request
    that leaves here fine and arrives expired — and that failure reads as a
    permissions problem, which sends whoever debugs it somewhere else."""
    assert grants.SKEW_SECONDS >= 60
    assert Token("x", time.time() + grants.SKEW_SECONDS - 1).stale
    assert not Token("x", time.time() + grants.SKEW_SECONDS + 60).stale


async def test_two_connections_never_share_a_token(token_endpoint):
    """Keyed on the connection, because that is what determines the token. One
    entry serving both would hand a caller somebody else's access."""
    token_endpoint(
        httpx.Response(200, json={"access_token": "for-a", "expires_in": 3600}),
        httpx.Response(200, json={"access_token": "for-b", "expires_in": 3600}),
    )
    a = await grants.token_for("conn_a", style="client_credentials",
                               secret="a:b", config={"token_url": "https://t/"})
    b = await grants.token_for("conn_b", style="client_credentials",
                               secret="c:d", config={"token_url": "https://t/"})
    assert a == "for-a" and b == "for-b"


async def test_a_refusal_never_carries_the_secret(token_endpoint):
    """A token endpoint answers a bad secret with a body quoting what it was
    sent. Passing that through puts the credential in whatever caught the
    exception, which is the ordinary way a secret ends up somewhere durable."""
    token_endpoint(httpx.Response(400, json={
        "error": "invalid_client",
        "error_description": "client_secret 'hunter2-the-actual-secret' is wrong",
    }))

    with pytest.raises(GrantError) as exc:
        await grants.token_for("conn_1", style="client_credentials",
                               secret="id:hunter2-the-actual-secret",
                               config={"token_url": "https://t/"})
    assert "hunter2" not in str(exc.value)
    assert exc.value.status == 401


async def test_a_malformed_client_credential_says_the_shape_it_wanted():
    with pytest.raises(GrantError) as exc:
        await grants.token_for("conn_1", style="client_credentials",
                               secret="just-a-token",
                               config={"token_url": "https://t/"})
    assert "client_id:client_secret" in str(exc.value)


async def test_a_missing_token_url_is_a_configuration_error():
    with pytest.raises(GrantError) as exc:
        await grants.token_for("conn_1", style="client_credentials",
                               secret="a:b", config={})
    assert exc.value.status == 400


async def test_a_provider_that_omits_expiry_is_treated_as_short_lived(
    token_endpoint
):
    """Re-exchanging needlessly costs one request. Holding a dead token costs a
    failure nobody can explain."""
    token_endpoint(httpx.Response(200, json={"access_token": "at-1"}))
    await grants.token_for("conn_1", style="client_credentials", secret="a:b",
                           config={"token_url": "https://t/"})
    assert grants._cache["conn_1"].expires_at < time.time() + 3600


# --- the Google assertion ----------------------------------------------------


def _service_account() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return json.dumps({
        "type": "service_account",
        "client_email": "reader@acme.iam.gserviceaccount.com",
        "private_key": pem,
        "token_uri": "https://oauth2.googleapis.com/token",
    })


async def test_the_google_assertion_is_signed_and_carries_the_scope(
    token_endpoint
):
    import jwt

    calls = token_endpoint(httpx.Response(
        200, json={"access_token": "goog-1", "expires_in": 3599}))

    value = await grants.token_for(
        "conn_g", style="google_service_account", secret=_service_account(),
        config={"scope": "https://www.googleapis.com/auth/drive.readonly"},
    )
    assert value == "goog-1"

    form = calls[0]["form"]
    assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
    claims = jwt.decode(form["assertion"], options={"verify_signature": False})
    assert claims["iss"] == "reader@acme.iam.gserviceaccount.com"
    assert claims["scope"] == "https://www.googleapis.com/auth/drive.readonly"
    assert claims["aud"] == "https://oauth2.googleapis.com/token"
    # No `sub` unless delegation was asked for: an assertion that impersonates
    # by default is one nobody chose.
    assert "sub" not in claims


async def test_delegation_is_explicit(token_endpoint):
    """A connection with a `subject` is impersonating somebody, which is worth
    having to type."""
    import jwt

    calls = token_endpoint(httpx.Response(
        200, json={"access_token": "goog-1", "expires_in": 3599}))
    await grants.token_for(
        "conn_g", style="google_service_account", secret=_service_account(),
        config={"scope": "https://www.googleapis.com/auth/gmail.readonly",
                "subject": "someone@acme.com"},
    )
    claims = jwt.decode(calls[0]["form"]["assertion"],
                        options={"verify_signature": False})
    assert claims["sub"] == "someone@acme.com"


async def test_a_credential_that_is_not_a_key_file_says_so():
    with pytest.raises(GrantError) as exc:
        await grants.token_for("conn_g", style="google_service_account",
                               secret="ya29.a-plain-token",
                               config={"scope": "x"})
    assert "JSON key file" in str(exc.value)


async def test_google_without_scopes_is_refused():
    with pytest.raises(GrantError) as exc:
        await grants.token_for("conn_g", style="google_service_account",
                               secret=_service_account(), config={})
    assert "scopes" in str(exc.value)


async def test_an_unknown_style_never_reaches_the_network():
    with pytest.raises(GrantError) as exc:
        await grants.token_for("conn_1", style="bearer", secret="x", config={})
    assert exc.value.status == 500


async def test_a_source_401_drops_the_cached_token():
    """The cache holds a token for an hour. A credential that starts being
    refused — consent revoked, scope changed, rotated at the provider — would
    go on being refused with the same dead token long after somebody fixed it,
    and the fix would look like it had not worked."""
    grants._cache["conn_1"] = Token("revoked", time.time() + 3600)
    grants.forget("conn_1")
    assert "conn_1" not in grants._cache
    grants.forget("conn_1")  # idempotent: a run can fail twice
