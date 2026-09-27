"""The credential seam.

Every write needs a producer and a credential; every read needs a principal.
`TokenVerifier` is the seam that lets Firebase, OIDC and local passwords arrive
later without forking the request path -- Phase 1 implements the API-key case
only, and that is the point of building it now.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

import asyncpg

from .ids import new_id, ulid

KEY_PREFIX = "mdk"

# Capability scopes. `admin:*` is a grant within an org, never across orgs.
DATA_READ = "data:read"
DATA_WRITE = "data:write"
CONFIG_WRITE = "config:write"
ADMIN = "admin:*"


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Principal:
    """Who is acting, and what this credential is permitted to do.

    Identity and capability are separate on purpose: a Firebase login and an API
    key belonging to the same person land on the same `user_id` with the same
    ACLs, and differ only in scope.
    """

    user_id: str
    org_id: str
    capabilities: frozenset[str]
    key_id: str | None = None
    project_id: str | None = None
    mode: str = "user"
    groups: frozenset[str] = field(default_factory=frozenset)

    def can(self, capability: str) -> bool:
        return capability in self.capabilities or ADMIN in self.capabilities

    def require(self, capability: str) -> None:
        if not self.can(capability):
            raise AuthError(f"credential lacks {capability}", status=403)

    def acl_principals(self) -> list[str]:
        """Principals resolved at query time -- so revocation takes effect now."""
        return (
            [f"user:{self.user_id}", f"org:{self.org_id}", "public"]
            + [f"group:{g}" for g in sorted(self.groups)]
            + ([f"project:{self.project_id}"] if self.project_id else [])
        )


class TokenVerifier(Protocol):
    async def verify(self, token: str) -> Principal: ...


def mint_key() -> tuple[str, str, bytes]:
    """Returns (token, prefix, hash). The token is never stored or re-shown."""
    prefix = f"{KEY_PREFIX}_{ulid()[:10]}"
    token = f"{prefix}.{secrets.token_urlsafe(32)}"
    return token, prefix, hashlib.sha256(token.encode()).digest()


class ApiKeyVerifier:
    """Lookup by display prefix, then constant-time compare of the hash.

    Hashing is SHA-256 rather than Argon2 by intent: an API key is a
    high-entropy random token, so a slow KDF buys nothing and costs on every
    request. Passwords are the other threat model and get Argon2id.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def verify(self, token: str) -> Principal:
        prefix, _, _ = token.partition(".")
        if not prefix.startswith(f"{KEY_PREFIX}_"):
            raise AuthError("malformed credential")
        row = await self._pool.fetchrow(
            """
            SELECT key_id, user_id, org_id, project_id, key_hash, capabilities,
                   expires_at, revoked_at
            FROM api_keys WHERE prefix = $1
            """,
            prefix,
        )
        digest = hashlib.sha256(token.encode()).digest()
        if row is None or not hmac.compare_digest(bytes(row["key_hash"]), digest):
            raise AuthError("invalid credential")
        now = datetime.now(timezone.utc)
        if row["revoked_at"] is not None:
            raise AuthError("credential revoked")
        if row["expires_at"] is not None and row["expires_at"] <= now:
            raise AuthError("credential expired")

        # Membership-coupled: leaving the org revokes the key's org access, so
        # offboarding cannot leak through a key nobody remembered to delete.
        member = await self._pool.fetchval(
            "SELECT 1 FROM memberships WHERE user_id = $1 AND org_id = $2",
            row["user_id"],
            row["org_id"],
        )
        if not member:
            raise AuthError("credential holder is not a member of the organization", 403)

        groups = await self._pool.fetch(
            """
            SELECT g.group_id FROM group_members gm
            JOIN groups g ON g.group_id = gm.group_id
            WHERE gm.user_id = $1 AND g.org_id = $2
            """,
            row["user_id"],
            row["org_id"],
        )
        await self._pool.execute(
            "UPDATE api_keys SET last_used_at = now() WHERE key_id = $1", row["key_id"]
        )
        return Principal(
            user_id=row["user_id"],
            org_id=row["org_id"],
            project_id=row["project_id"],
            key_id=row["key_id"],
            capabilities=frozenset(row["capabilities"]),
            groups=frozenset(r["group_id"] for r in groups),
        )


async def issue_key(
    pool: asyncpg.Pool,
    *,
    user_id: str,
    org_id: str,
    capabilities: list[str],
    project_id: str | None = None,
    name: str = "",
    expires_at: datetime | None = None,
) -> str:
    token, prefix, digest = mint_key()
    await pool.execute(
        """
        INSERT INTO api_keys (key_id, user_id, org_id, project_id, name, prefix,
                              key_hash, capabilities, expires_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
        new_id("key"),
        user_id,
        org_id,
        project_id,
        name,
        prefix,
        digest,
        capabilities,
        expires_at,
    )
    return token
