"""How the second user arrives.

Registration is `invite_only` by default. `open` exists for deployments that
want self-service and `disabled` for a closed appliance where accounts are
provisioned out of band, but the default is closed -- because opening
registration and closing it later leaves everyone who signed up in between
already inside, and closing it does not remove them.

An invite is a bearer credential, so it is created, stored, shown and revoked
the way an API key is. What is specific to invites is the redemption, and two
rules shape it:

**Redemption discloses nothing.** Every failure returns the same sentence. An
endpoint that distinguishes "no such organization" from "wrong token for this
organization" is an org-enumeration oracle, and it is unauthenticated by
necessity -- the person redeeming has no account yet, which is the point.

**Redemption is single-use under contention.** The update that marks an invite
redeemed is conditional on it not already being redeemed, so two people racing
the same forwarded link produce one member and one refusal. Checking first and
writing second would produce two members and no error.

The role travels on the invite, never in the redemption request. A redeemer who
could name their own role would be an unauthenticated privilege escalation.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import asyncpg

from .audit import record_audit
from .auth import ADMIN, CONFIG_WRITE, DATA_READ, DATA_WRITE, AuthError, Principal, issue_key
from .ids import new_id, ulid
from .settings_store import resolve

INVITE_PREFIX = "mdi"

MODES = ("invite_only", "open", "disabled")

# Deliberately one sentence for every failure. Expired, revoked, already
# redeemed, wrong address, never existed -- the caller learns only that this
# token will not work, because every finer distinction is information about an
# organization they have not proved any relationship to.
INVALID = "that invite is invalid or has expired"

ROLES = ("owner", "admin", "member", "viewer")

# The capabilities a redeemed key carries. An invite grants membership at a
# role, and the key issued on redemption must not exceed what that role implies
# -- the same mapping the identity verifier applies to a signed-in human, kept
# here rather than duplicated so the two cannot drift.
ROLE_CAPABILITIES: dict[str, list[str]] = {
    "owner": [DATA_READ, DATA_WRITE, CONFIG_WRITE],
    "admin": [DATA_READ, DATA_WRITE, CONFIG_WRITE],
    "member": [DATA_READ, DATA_WRITE],
    "viewer": [DATA_READ],
}


class InviteError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Created:
    invite_id: str
    prefix: str
    # Shown once, at creation, and never recoverable. Stored hashed.
    token: str
    role: str
    email: str | None
    expires_at: datetime


@dataclass(frozen=True)
class Redeemed:
    user_id: str
    org_id: str
    role: str
    api_key: str


def _mint() -> tuple[str, str, bytes]:
    prefix = f"{INVITE_PREFIX}_{ulid()[:10]}"
    token = f"{prefix}.{secrets.token_urlsafe(32)}"
    return token, prefix, hashlib.sha256(token.encode()).digest()


async def registration_mode(pool: asyncpg.Pool, org_id: str | None = None) -> str:
    resolved = await resolve(pool, "registration_mode", org_id=org_id)
    mode = resolved.value if resolved.value in MODES else "invite_only"
    return mode


async def _require_admin(pool: asyncpg.Pool, principal: Principal) -> None:
    if principal.can(ADMIN):
        return
    role = await pool.fetchval(
        "SELECT role FROM memberships WHERE user_id = $1 AND org_id = $2",
        principal.user_id, principal.org_id,
    )
    if role not in ("owner", "admin"):
        raise AuthError("only an organization admin may manage invites", status=403)


async def create(
    pool: asyncpg.Pool,
    principal: Principal,
    *,
    email: str | None = None,
    role: str = "member",
    expires_in_days: int | None = None,
    transferable: bool = False,
) -> Created:
    """Issue an invite. Audited on creation, and again on redemption.

    Both are recorded because they answer different questions -- *who invited
    them* and *who actually walked through the door* -- and an invite forwarded
    to somebody else answers only the second.
    """
    await _require_admin(pool, principal)

    if role not in ROLES:
        raise InviteError(f"role must be one of {', '.join(ROLES)}")
    mode = await registration_mode(pool, principal.org_id)
    if mode == "disabled":
        # Refused rather than issued-and-useless. An invite that cannot be
        # redeemed is worse than no invite: someone sends it and waits.
        raise InviteError(
            "registration is disabled for this deployment, so an invite could "
            "not be redeemed",
            status=409,
        )

    email = (email or "").strip().lower() or None
    if email is None and not transferable:
        # The default is the safe one, and opting out of it is explicit. A link
        # bound to nobody is a link anyone can forward.
        raise InviteError(
            "an invite needs an email address, or transferable=true to issue a "
            "link anyone holding it can redeem",
        )

    days = 7 if expires_in_days is None else int(expires_in_days)
    if not 1 <= days <= 90:
        raise InviteError("expiry must be between 1 and 90 days")

    token, prefix, digest = _mint()
    invite_id = new_id("inv")
    expires_at = datetime.now(timezone.utc) + timedelta(days=days)

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO invites (invite_id, org_id, prefix, token_hash, role,
                                 email, created_by, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            invite_id, principal.org_id, prefix, digest, role, email,
            principal.user_id, expires_at,
        )
        await record_audit(
            conn, principal, action="invite.created",
            target_type="invite", target_id=invite_id,
            # The address and role are the reviewable part. The token is not
            # recorded anywhere, including here.
            detail={"email": email, "role": role, "transferable": email is None,
                    "expires_at": expires_at.isoformat()},
        )
    return Created(invite_id, prefix, token, role, email, expires_at)


async def listing(pool: asyncpg.Pool, principal: Principal) -> list[dict]:
    """Outstanding invites, with their state. Never a token."""
    await _require_admin(pool, principal)
    rows = await pool.fetch(
        """
        SELECT invite_id, prefix, role, email, created_by, created_at, expires_at,
               redeemed_at, redeemed_by, revoked_at,
               CASE WHEN revoked_at IS NOT NULL THEN 'revoked'
                    WHEN redeemed_at IS NOT NULL THEN 'redeemed'
                    WHEN expires_at <= now() THEN 'expired'
                    ELSE 'pending' END AS status
        FROM invites WHERE org_id = $1 ORDER BY created_at DESC
        """,
        principal.org_id,
    )
    return [dict(r) for r in rows]


async def revoke(pool: asyncpg.Pool, principal: Principal, invite_id: str) -> dict:
    """Kill an invite before it is redeemed.

    Scoped to the caller's org in the same statement rather than fetched and
    checked, so an invite id from another organization is a no-op rather than a
    404 that confirms it exists.
    """
    await _require_admin(pool, principal)
    async with pool.acquire() as conn, conn.transaction():
        updated = await conn.fetchval(
            """
            UPDATE invites SET revoked_at = now()
            WHERE invite_id = $1 AND org_id = $2
              AND redeemed_at IS NULL AND revoked_at IS NULL
            RETURNING invite_id
            """,
            invite_id, principal.org_id,
        )
        if updated is None:
            raise InviteError("no pending invite with that id", status=404)
        await record_audit(
            conn, principal, action="invite.revoked",
            target_type="invite", target_id=invite_id,
        )
    return {"invite_id": invite_id, "status": "revoked"}


async def pending_for_email(pool: asyncpg.Pool, email: str | None) -> bool:
    """Is there a live invite for this address?

    This is what `invite_only` consults when a new identity signs in for the
    first time. It answers only yes or no, and the caller turns a no into the
    same refusal an unknown address gets.
    """
    if not email:
        return False
    return bool(await pool.fetchval(
        """
        SELECT 1 FROM invites
        WHERE lower(email) = lower($1)
          AND redeemed_at IS NULL AND revoked_at IS NULL AND expires_at > now()
        LIMIT 1
        """,
        email,
    ))


async def redeem(
    pool: asyncpg.Pool, *, token: str, email: str | None = None
) -> Redeemed:
    """Turn an invite into a membership. Unauthenticated by necessity.

    The invite *is* the credential -- the person presenting it has no account
    yet, which is the situation it exists for. What comes back is a durable one,
    because exchanging a single-use token for a long-lived key is the whole
    shape of the transaction, and the alternative leaves a new member holding
    nothing they can call the API with.

    Every failure below raises the same sentence. The distinctions are real and
    they are all disclosure.
    """
    prefix = token.split(".", 1)[0]
    digest = hashlib.sha256(token.encode()).digest()
    supplied = (email or "").strip().lower() or None

    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT * FROM invites WHERE prefix = $1", prefix
        )
        # Constant-time compare, so a valid prefix with a wrong secret cannot be
        # distinguished from an unknown prefix by timing.
        if row is None or not secrets.compare_digest(bytes(row["token_hash"]), digest):
            raise InviteError(INVALID, status=403)
        if row["revoked_at"] is not None or row["redeemed_at"] is not None:
            raise InviteError(INVALID, status=403)
        if row["expires_at"] <= datetime.now(timezone.utc):
            raise InviteError(INVALID, status=403)

        bound = row["email"]
        if bound is not None:
            # A forwarded link fails here, which is the behaviour the sender
            # intended when they addressed it to one person.
            if supplied is None or supplied != bound.lower():
                raise InviteError(INVALID, status=403)
        target_email = bound.lower() if bound else supplied
        if target_email is None:
            # A transferable invite still has to produce an identifiable member.
            raise InviteError(INVALID, status=403)

        # **Claim before provisioning.** Single-use is enforced by this update
        # rather than by the checks above: two people racing the same link both
        # pass every read, and only one of them changes a row.
        #
        # It happens first, before the account exists, because the other order
        # has both racers reach `INSERT INTO users` with the same address and
        # the loser dies on the unique constraint -- a 500 where the honest
        # answer is that the invite was already used.
        claimed = await conn.fetchval(
            """
            UPDATE invites SET redeemed_at = now()
            WHERE invite_id = $1 AND redeemed_at IS NULL AND revoked_at IS NULL
            RETURNING invite_id
            """,
            row["invite_id"],
        )
        if claimed is None:
            raise InviteError(INVALID, status=403)

        user_id = await conn.fetchval(
            "SELECT user_id FROM users WHERE lower(email) = $1", target_email
        )
        if user_id is None:
            user_id = new_id("usr")
            await conn.execute(
                "INSERT INTO users (user_id, email, display_name) VALUES ($1, $2, $2)",
                user_id, target_email,
            )
            await conn.execute(
                """
                INSERT INTO identities (identity_id, user_id, provider, external_id)
                VALUES ($1, $2, 'local', $3)
                ON CONFLICT (provider, external_id) DO NOTHING
                """,
                new_id("idn"), user_id, target_email,
            )
        await conn.execute(
            "UPDATE invites SET redeemed_by = $2 WHERE invite_id = $1",
            row["invite_id"], user_id,
        )

        await conn.execute(
            """
            INSERT INTO memberships (user_id, org_id, role) VALUES ($1, $2, $3)
            ON CONFLICT (user_id, org_id) DO NOTHING
            """,
            user_id, row["org_id"], row["role"],
        )

        # Audited as the redeemer, not as the inviter. Who walked through the
        # door is the question this record exists to answer.
        redeemer = Principal(
            user_id=user_id, org_id=row["org_id"], capabilities=frozenset()
        )
        await record_audit(
            conn, redeemer, action="invite.redeemed",
            target_type="invite", target_id=row["invite_id"],
            detail={"role": row["role"], "invited_by": row["created_by"],
                    "was_transferable": row["email"] is None},
        )

    api_key = await issue_key(
        pool,
        user_id=user_id,
        org_id=row["org_id"],
        capabilities=ROLE_CAPABILITIES[row["role"]],
        name="invite",
    )
    return Redeemed(user_id, row["org_id"], row["role"], api_key)
