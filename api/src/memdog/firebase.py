"""Firebase / Identity Platform tokens, behind the existing seam.

This is what the `TokenVerifier` protocol was for. Adding a hosted identity
provider is an implementation of one interface and rows in `identities` -- not a
fork of the request path, and not a second notion of who a user is.

The point of `identities` being the permanent design rather than migration
scaffolding: a Firebase UID is a 28-character string and existing user ids are
prefixed ULIDs. Without the join table this would be a rewrite; with it, it is
an insert.

Verification is done locally against Google's published keys. Calling an
identity API on every request would put a network round trip in front of every
read, and its outage would become ours.
"""

from __future__ import annotations

import time

import asyncpg
import httpx
import jwt
from jwt import PyJWKClient

from .auth import AuthError, Principal
from .ids import new_id

# Google publishes the signing certificates here; they rotate, so they are
# cached with a TTL rather than pinned.
CERT_URL = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
ISSUER_PREFIX = "https://securetoken.google.com/"


class FirebaseVerifier:
    """Verifies an Identity Platform ID token and resolves it to a local user.

    Auto-provisioning is deliberate and bounded: a verified token from the
    configured project creates a user and an identity row on first sight, but
    **never a membership**. Signing in gets you an account; belonging to an
    organization is something an admin grants.
    """

    def __init__(self, pool: asyncpg.Pool, project_id: str, default_org_id: str | None = None) -> None:
        self._pool = pool
        self._project_id = project_id
        self._default_org_id = default_org_id
        self._certs: dict[str, str] = {}
        self._fetched_at = 0.0

    async def _certificates(self) -> dict[str, str]:
        # A five-minute cache: long enough that verification is local, short
        # enough that a rotation is picked up without a restart.
        if self._certs and time.time() - self._fetched_at < 300:
            return self._certs
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(CERT_URL)
            response.raise_for_status()
            self._certs = response.json()
        self._fetched_at = time.time()
        return self._certs

    async def verify(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthError("malformed identity token") from exc

        certificates = await self._certificates()
        certificate = certificates.get(header.get("kid", ""))
        if certificate is None:
            raise AuthError("identity token signed by an unknown key")

        from cryptography.x509 import load_pem_x509_certificate

        public_key = load_pem_x509_certificate(certificate.encode()).public_key()
        try:
            claims = jwt.decode(
                token,
                public_key,
                algorithms=["RS256"],
                audience=self._project_id,
                issuer=f"{ISSUER_PREFIX}{self._project_id}",
                # Expiry, audience and issuer are all checked. An ID token
                # minted for a different project is a valid Google token and
                # must not be a valid credential here.
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(f"invalid identity token: {exc}") from exc

        uid, email = claims["sub"], claims.get("email")
        if claims.get("email") and not claims.get("email_verified", False):
            # Unverified email means the address proves nothing, and the
            # address is what an admin invites.
            raise AuthError("email address is not verified", status=403)

        return await self._principal_for(uid, email)

    async def _principal_for(self, uid: str, email: str | None) -> Principal:
        row = await self._pool.fetchrow(
            """
            SELECT u.user_id FROM identities i JOIN users u ON u.user_id = i.user_id
            WHERE i.provider = 'firebase' AND i.external_id = $1
            """,
            uid,
        )
        if row is None:
            # The registration gate. Anyone who can authenticate used to get a
            # `users` row here -- harmless in that membership is what grants
            # capability, and still an account-creation side effect nobody asked
            # for in a system holding other people's records.
            await self._admit(email)
            user_id = await self._link_or_create(uid, email)
        else:
            user_id = row["user_id"]

        membership = await self._pool.fetchrow(
            """
            SELECT org_id, role FROM memberships WHERE user_id = $1
            ORDER BY created_at LIMIT 1
            """,
            user_id,
        )
        if membership is None:
            raise AuthError(
                "this account is not a member of any organization", status=403
            )

        groups = await self._pool.fetch(
            """
            SELECT g.group_id FROM group_members gm
            JOIN groups g ON g.group_id = gm.group_id
            WHERE gm.user_id = $1 AND g.org_id = $2
            """,
            user_id, membership["org_id"],
        )
        # A signed-in human gets the capabilities their org role implies. This
        # is where identity and capability meet: the same person holding an
        # API key may be scoped down further, but never up.
        capabilities = {"data:read", "data:write"}
        if membership["role"] in ("owner", "admin"):
            capabilities |= {"config:write"}
        if membership["role"] == "viewer":
            capabilities = {"data:read"}

        return Principal(
            user_id=user_id,
            org_id=membership["org_id"],
            capabilities=frozenset(capabilities),
            groups=frozenset(r["group_id"] for r in groups),
        )

    async def _admit(self, email: str | None) -> None:
        """May this identity become a user at all?

        `open` admits anyone who can authenticate; they still hold no
        membership, so they can still do nothing until invited. `disabled`
        refuses outright. `invite_only` -- the default -- admits an address that
        holds a live invite, and an address that is already a user, which is the
        admin who added a member directly.

        The refusal is the same sentence either way. "You were not invited" and
        "this deployment is closed" are both true and neither is worth telling a
        stranger precisely.
        """
        from . import invites

        mode = await invites.registration_mode(self._pool)
        if mode == "open":
            return
        if mode != "disabled":
            known = email and await self._pool.fetchval(
                "SELECT 1 FROM users WHERE lower(email) = lower($1)", email
            )
            if known or await invites.pending_for_email(self._pool, email):
                return
        raise AuthError(
            "this deployment does not accept new accounts without an invitation",
            status=403,
        )

    async def _link_or_create(self, uid: str, email: str | None) -> str:
        """Link to an existing account by verified email, or make a new one.

        Linking matters: an admin invites `dana@acme.com` before Dana has ever
        signed in, and Dana signing in with Google must land on *that* account
        rather than a second empty one.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            user_id = None
            if email:
                user_id = await conn.fetchval(
                    "SELECT user_id FROM users WHERE email = $1", email
                )
            if user_id is None:
                user_id = new_id("usr")
                await conn.execute(
                    "INSERT INTO users (user_id, email, display_name) VALUES ($1, $2, $2)",
                    user_id, email or uid,
                )
            await conn.execute(
                """
                INSERT INTO identities (identity_id, user_id, provider, external_id)
                VALUES ($1, $2, 'firebase', $3)
                ON CONFLICT (provider, external_id) DO NOTHING
                """,
                new_id("idn"), user_id, uid,
            )
        return user_id


class CompositeVerifier:
    """API key or identity token, one seam.

    Which one a credential is, is decided by its shape rather than by a
    separate endpoint: `mdk_` prefixed strings are API keys, anything with two
    dots is a JWT. Two auth *paths* would mean two places to get authorization
    wrong.
    """

    def __init__(self, api_keys, firebase=None) -> None:
        self._api_keys = api_keys
        self._firebase = firebase

    async def verify(self, token: str) -> Principal:
        if token.startswith("mdk_"):
            return await self._api_keys.verify(token)
        if self._firebase is not None and token.count(".") == 2:
            return await self._firebase.verify(token)
        raise AuthError("unrecognised credential")
