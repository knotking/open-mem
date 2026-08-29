"""Connections: a credential, and how a source wants it presented.

A crawler reaching an authenticated API needs two things — the secret, and the
way that particular API expects to receive it. The first was already modelled:
`connections.credential_ct` is envelope-encrypted and fails closed. The second
was not modelled at all, so the only place to say "this one wants
`X-Api-Key`" was the crawler's own config, in the clear.

**Six styles, closed, in two kinds.** Four are *presented* as stored — bearer, a
named header, a query parameter, basic auth. Two are *exchanged* before use:
client credentials and a Google service-account assertion trade the stored secret
for a token that lives an hour.

That second kind is why "Google and Microsoft need OAuth" was wrong here for
weeks. Interactive OAuth is how a *person* connects their own account; an
organization connecting its own data uses a grant with no human in it at all,
which is a POST. See `grants.py`.

Keeping the set closed is what stops a secret drifting back into a templated
header, where it would be readable by anyone who can read a config.

**A credential is written and never read back.** `list_connections` reports
whether one is held, never a prefix of it. There is no endpoint that returns a
credential, because the only party that needs the plaintext is the crawler, and
it gets it from the envelope at the moment it makes the request.
"""

from __future__ import annotations

import asyncpg

from .audit import record_audit
from .auth import CONFIG_WRITE, AuthError, Principal
from .crypto import CryptoUnavailable, Envelope
from .ids import new_id

# Presented as stored, then exchanged for a short-lived token.
PRESENTED = ("bearer", "header", "query", "basic")
EXCHANGED = ("client_credentials", "google_service_account")
AUTH_STYLES = PRESENTED + EXCHANGED

# Styles that are meaningless without somewhere to put the value.
NEEDS_NAME = {"header", "query"}

# What each exchanged style needs in `auth_config`, which holds no secret.
NEEDS_CONFIG = {
    "client_credentials": ("token_url",),
    "google_service_account": ("scope",),
}


class ConnectionError_(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


async def create(
    pool: asyncpg.Pool,
    principal: Principal,
    envelope: Envelope,
    *,
    project_id: str,
    provider: str,
    credential: str | None,
    auth_style: str = "bearer",
    auth_name: str | None = None,
    auth_config: dict | None = None,
    scope: str = "personal",
) -> dict:
    """Register a credential for a project.

    Refuses rather than storing a secret in plaintext when there is no root key,
    which is the same rule `register_engine` follows and the reason the envelope
    exists before anything could store one.
    """
    principal.require(CONFIG_WRITE)
    if auth_style not in AUTH_STYLES:
        raise ConnectionError_(
            f"auth_style must be one of {', '.join(AUTH_STYLES)}"
        )
    if auth_style in NEEDS_NAME and not auth_name:
        # Refused rather than defaulted. Guessing `X-Api-Key` would send the
        # secret to a header the source ignores, and the failure would look
        # like a wrong credential rather than a wrong configuration.
        raise ConnectionError_(
            f"auth_style {auth_style!r} needs auth_name -- the header or query "
            "parameter this source expects the credential in"
        )
    auth_config = auth_config or {}
    missing = [k for k in NEEDS_CONFIG.get(auth_style, ()) if not auth_config.get(k)]
    if missing:
        # Refused rather than defaulted, for the same reason a header name is:
        # a guessed token endpoint fails as a rejected credential rather than as
        # the misconfiguration it is.
        raise ConnectionError_(
            f"auth_style {auth_style!r} needs {', '.join(missing)} in auth_config"
        )
    if scope not in ("personal", "shared"):
        raise ConnectionError_("scope must be personal or shared")

    ciphertext = None
    if credential:
        try:
            ciphertext = envelope.encrypt(
                credential.encode(), aad=principal.org_id.encode()
            )
        except CryptoUnavailable as exc:
            raise ConnectionError_(
                "cannot store a credential: encryption is not configured",
                status=503,
            ) from exc

    connection_id = new_id("conn")
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO connections (connection_id, org_id, project_id, user_id,
                                     provider, scope, credential_ct,
                                     auth_style, auth_name, auth_config)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            connection_id, principal.org_id, project_id, principal.user_id,
            provider, scope, ciphertext, auth_style, auth_name, auth_config,
        )
        await record_audit(
            conn, principal, action="connection.created", project_id=project_id,
            target_type="connection", target_id=connection_id,
            # Never the credential, and never a prefix of it. Which provider and
            # how it is presented are the reviewable parts.
            detail={"provider": provider, "scope": scope,
                    "auth_style": auth_style, "auth_name": auth_name,
                    # `auth_config` holds no secret by construction, so it is
                    # reviewable in full -- which is the point of keeping the
                    # token endpoint out of the encrypted blob.
                    "auth_config": auth_config,
                    "has_credential": bool(credential)},
        )
    return {
        "connection_id": connection_id, "provider": provider, "scope": scope,
        "auth_style": auth_style, "auth_name": auth_name,
        "auth_config": auth_config, "has_credential": bool(credential),
    }


async def listing(pool: asyncpg.Pool, principal: Principal,
                  project_id: str | None = None) -> list[dict]:
    """Connections in this org. Whether a credential is held, never what it is."""
    rows = await pool.fetch(
        """
        SELECT connection_id, project_id, provider, scope, auth_style, auth_name,
               auth_config, created_at, (credential_ct IS NOT NULL) AS has_credential
        FROM connections
        WHERE org_id = $1 AND ($2::text IS NULL OR project_id = $2)
        ORDER BY created_at DESC
        """,
        principal.org_id, project_id,
    )
    return [dict(r) for r in rows]


async def authorize(
    pool: asyncpg.Pool, envelope: Envelope, connection_id: str, org_id: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """The headers and query parameters this connection adds to a request.

    Returned as two dicts for the caller to merge rather than a mutated request,
    so the credential exists in one expression and nothing holds it afterwards.

    An unreadable credential raises. A crawler that carried on unauthenticated
    would get a 401 from the source and report it as the source's problem, which
    sends whoever is debugging it in exactly the wrong direction.
    """
    row = await pool.fetchrow(
        """
        SELECT credential_ct, auth_style, auth_name, auth_config
        FROM connections WHERE connection_id = $1 AND org_id = $2
        """,
        connection_id, org_id,
    )
    if row is None:
        raise ConnectionError_("no such connection", status=404)
    if not row["credential_ct"]:
        return {}, {}

    try:
        secret = envelope.decrypt(
            bytes(row["credential_ct"]), aad=org_id.encode()
        ).decode()
    except CryptoUnavailable as exc:
        raise ConnectionError_(
            "the credential cannot be decrypted: encryption is not configured",
            status=503,
        ) from exc

    style, name = row["auth_style"], row["auth_name"]

    if style in EXCHANGED:
        # The stored secret is not what goes to the source. It buys a token
        # that does, and the token is cached until shortly before it expires.
        from .grants import GrantError, token_for

        try:
            access = await token_for(
                connection_id, style=style, secret=secret,
                config=dict(row["auth_config"] or {}),
            )
        except GrantError as exc:
            raise ConnectionError_(str(exc), status=exc.status) from exc
        return {"Authorization": f"Bearer {access}"}, {}

    if style == "bearer":
        return {"Authorization": f"Bearer {secret}"}, {}
    if style == "header":
        return {name: secret}, {}
    if style == "query":
        return {}, {name: secret}
    if style == "basic":
        import base64

        # The credential is stored as `user:password`; encoding it here rather
        # than at rest keeps one representation in the database.
        encoded = base64.b64encode(secret.encode()).decode()
        return {"Authorization": f"Basic {encoded}"}, {}
    raise ConnectionError_(f"unsupported auth_style {style!r}", status=500)


async def attach(
    pool: asyncpg.Pool, principal: Principal, crawler_id: str,
    connection_id: str | None,
) -> dict:
    """Point a crawler at a connection, or detach it.

    Scoped to the caller's org in the statement rather than fetched and checked,
    so an id from another organization is a no-op rather than a 404 that
    confirms it exists.
    """
    principal.require(CONFIG_WRITE)
    if connection_id is not None:
        owned = await pool.fetchval(
            "SELECT 1 FROM connections WHERE connection_id = $1 AND org_id = $2",
            connection_id, principal.org_id,
        )
        if not owned:
            raise ConnectionError_("no such connection", status=404)

    async with pool.acquire() as conn, conn.transaction():
        updated = await conn.fetchval(
            """
            UPDATE crawlers SET connection_id = $3
            WHERE crawler_id = $1 AND org_id = $2
            RETURNING crawler_id
            """,
            crawler_id, principal.org_id, connection_id,
        )
        if updated is None:
            raise ConnectionError_("no such crawler", status=404)
        await record_audit(
            conn, principal, action="crawler.connection_set",
            target_type="crawler", target_id=crawler_id,
            detail={"connection_id": connection_id},
        )
    return {"crawler_id": crawler_id, "connection_id": connection_id}
