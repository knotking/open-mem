"""Credentials that are exchanged rather than presented.

This file exists because of a correction. "Google and Microsoft need OAuth" was
said here for weeks, and it is only true of one thing: a *person* connecting
their own account, which needs a browser, a redirect and a consent screen. An
organization connecting its own data uses a grant with no human in it — a POST
that trades a stored secret for a token good for an hour.

That is what a connector actually needs, and it is a fraction of the work.

Two grants cover almost everything:

**Client credentials.** Microsoft Graph, Salesforce, Zoom, Xero. The connection
holds `client_id:client_secret`; the exchange posts it to a token endpoint with
the scopes and gets a bearer token back.

**Google's service-account assertion.** A JWT the caller signs with the service
account's private key, presented as a grant. Domain-wide delegation rides on the
`sub` claim, which is how one credential reads many mailboxes without any of
their owners doing anything.

## Tokens are cached, and the cache is in-process

Deliberately, and for the same reason `routing.Breaker` is: shared state would
make a crawl depend on infrastructure it does not otherwise need, and losing the
cache costs one extra POST. What it must not do is refresh on a timer — a token
is fetched when the one in hand is stale, so an idle deployment makes no
requests at all.

**The clock skew margin is not decoration.** A token treated as valid until the
instant it expires produces a request that leaves here fine and arrives expired,
and the failure looks like a permissions problem rather than a timing one.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

# Refresh this far before expiry. A request that leaves valid and arrives
# expired reads as a permissions failure, which sends whoever debugs it
# somewhere else entirely.
SKEW_SECONDS = 120

# A signed assertion is good for an hour at most, and Google rejects longer.
ASSERTION_LIFETIME = 3600


class GrantError(Exception):
    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Token:
    value: str
    expires_at: float

    @property
    def stale(self) -> bool:
        return time.time() >= self.expires_at - SKEW_SECONDS


# Keyed on the connection, because that is what determines the token. Not on the
# org: two connections in one org may hold different credentials, and one cache
# entry serving both would hand a caller somebody else's access.
_cache: dict[str, Token] = {}


def forget(connection_id: str) -> None:
    """Drop a cached token.

    Called when a source answers 401. The cache holds a token until shortly
    before it expires, so a credential that started being refused -- consent
    revoked, scope changed, secret rotated at the provider -- would go on being
    refused with the same dead token for up to an hour after somebody fixed it.
    """
    _cache.pop(connection_id, None)


async def token_for(
    connection_id: str, *, style: str, secret: str, config: dict
) -> str:
    """A live bearer token for this connection, exchanged if the cache is stale."""
    cached = _cache.get(connection_id)
    if cached is not None and not cached.stale:
        return cached.value

    if style == "client_credentials":
        token = await _client_credentials(secret, config)
    elif style == "google_service_account":
        token = await _google_service_account(secret, config)
    else:
        raise GrantError(f"{style!r} is not an exchanged credential", status=500)

    _cache[connection_id] = token
    return token.value


async def _client_credentials(secret: str, config: dict) -> Token:
    """The ordinary two-legged grant.

    The credential is stored as `client_id:client_secret` — one field, because
    the pair is useless split and storing them apart would mean two things to
    rotate rather than one.
    """
    token_url = config.get("token_url")
    if not token_url:
        raise GrantError("this connection has no token_url", status=400)
    client_id, _, client_secret = secret.partition(":")
    if not client_id or not client_secret:
        raise GrantError(
            "a client-credentials secret is stored as client_id:client_secret",
            status=400,
        )

    form = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if config.get("scope"):
        form["scope"] = config["scope"]
    if config.get("audience"):
        form["audience"] = config["audience"]
    return await _post_for_token(token_url, form)


async def _google_service_account(secret: str, config: dict) -> Token:
    """Google's JWT-bearer grant, signed with the service account's key.

    `subject` is domain-wide delegation: the assertion says "acting as this
    user", which is how one credential reads many mailboxes without any of their
    owners doing anything. It is also why that setting is worth stating out
    loud — a connection with a `subject` is impersonating somebody.
    """
    import json

    import jwt

    try:
        account = json.loads(secret)
    except ValueError as exc:
        raise GrantError(
            "a Google service-account credential is the JSON key file, stored "
            "whole", status=400,
        ) from exc

    for field in ("client_email", "private_key"):
        if not account.get(field):
            raise GrantError(f"the service-account key has no {field}", status=400)
    if not config.get("scope"):
        raise GrantError("a Google connection needs the scopes it is for", status=400)

    token_url = account.get("token_uri") or "https://oauth2.googleapis.com/token"
    now = int(time.time())
    claims = {
        "iss": account["client_email"],
        "scope": config["scope"],
        "aud": token_url,
        "iat": now,
        "exp": now + ASSERTION_LIFETIME,
    }
    if config.get("subject"):
        claims["sub"] = config["subject"]

    try:
        assertion = jwt.encode(claims, account["private_key"], algorithm="RS256")
    except Exception as exc:  # noqa: BLE001
        # A malformed key is a configuration problem, and saying so beats a
        # signature error from a library the operator has never heard of.
        raise GrantError(
            f"could not sign with this service-account key: {exc}", status=400
        ) from exc

    return await _post_for_token(token_url, {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
    })


async def _post_for_token(url: str, form: dict) -> Token:
    """One exchange. The response body never reaches a log or an error message.

    A token endpoint answers a bad secret with a body that quotes what it was
    sent. Passing that through would put the credential in whatever caught the
    exception, which is the ordinary way a secret ends up somewhere durable.
    """
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(url, data=form)
    except httpx.HTTPError as exc:
        raise GrantError(
            f"the token endpoint could not be reached ({exc.__class__.__name__})"
        ) from exc

    if response.status_code >= 400:
        raise GrantError(
            f"the token endpoint refused this credential ({response.status_code})",
            status=401 if response.status_code in (400, 401) else 502,
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise GrantError("the token endpoint did not return JSON") from exc

    access = body.get("access_token")
    if not access:
        raise GrantError("the token endpoint returned no access_token")
    # A provider that omits `expires_in` is treated as short-lived rather than
    # eternal: re-exchanging needlessly costs one request, and holding a dead
    # token costs a failure nobody can explain.
    lifetime = int(body.get("expires_in") or 600)
    return Token(access, time.time() + lifetime)
