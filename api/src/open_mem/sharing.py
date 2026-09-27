"""Public sharing -- the feature most likely to cause an accidental disclosure.

So it carries controls the others do not, and each exists because of a specific
way this goes wrong:

- **Off at org level by default.** Some organisations never enable it.
- **Expiry is required, not optional.** A link with no expiry is a permanent
  disclosure nobody revisits.
- **Revocable, and revocation is audited.**
- **An inventory.** The view that catches the mistake made six months ago.
- **Derived artifacts do not follow.** Sharing a document must not publish its
  summary, its entities or its extracted claims -- a summary spanning a public
  document and two private ones would leak two items to share one.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import asyncpg

from .audit import record_access, record_audit
from .auth import CONFIG_WRITE, Principal
from .ids import new_id
from .settings_store import resolve

MAX_TTL_DAYS = 90


class ShareError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


async def create_share(
    pool: asyncpg.Pool,
    principal: Principal,
    *,
    data_id: str | None = None,
    case_id: str | None = None,
    expires_in_days: int = 7,
    password: str | None = None,
) -> dict:
    if (data_id is None) == (case_id is None):
        raise ShareError("share exactly one of data_id or case_id")

    # The org gate. Checked here rather than trusted from the UI, because the
    # UI is a client of the API and never a privileged path.
    policy = await resolve(pool, "public_sharing", org_id=principal.org_id)
    if not policy.value:
        raise ShareError(
            "public sharing is disabled for this organization", status=403
        )
    if expires_in_days <= 0 or expires_in_days > MAX_TTL_DAYS:
        raise ShareError(f"expiry must be between 1 and {MAX_TTL_DAYS} days")

    # You may only share what you can already see. Sharing is not a way to
    # widen your own access.
    from .acl import visibility_params, visibility_sql

    org_id, user_id, principals = visibility_params(principal)
    if data_id:
        predicate = visibility_sql("d", 2, 3, 4)
        row = await pool.fetchrow(
            f"SELECT d.project_id FROM data_items d WHERE d.data_id = $1 AND {predicate}",
            data_id, org_id, user_id, principals,
        )
    else:
        row = await pool.fetchrow(
            "SELECT project_id FROM cases WHERE case_id = $1 AND org_id = $2 AND deleted_at IS NULL",
            case_id, org_id,
        )
    if row is None:
        raise ShareError("not found", status=404)

    share_id = new_id("shr")
    token = f"{share_id}.{secrets.token_urlsafe(32)}"
    expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO share_links (share_id, org_id, project_id, data_id, case_id,
                token_hash, prefix, created_by, expires_at, password_hash)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            share_id, principal.org_id, row["project_id"], data_id, case_id,
            hashlib.sha256(token.encode()).digest(), share_id, principal.user_id,
            expires_at,
            hashlib.sha256(password.encode()).digest() if password else None,
        )
        await record_audit(
            conn, principal, action="share.created", project_id=row["project_id"],
            target_type="share", target_id=share_id,
            detail={"data_id": data_id, "case_id": case_id,
                    "expires_at": expires_at.isoformat(), "password": bool(password)},
        )
    return {"share_id": share_id, "token": token, "expires_at": expires_at.isoformat()}


async def resolve_share(
    pool: asyncpg.Pool, token: str, password: str | None = None
) -> dict:
    """Read through a share link.

    Returns the item and **nothing derived from it**. A public share exposes
    the item and, optionally and explicitly, a purpose-built public rendering --
    never the internal derived layer.
    """
    share_id = token.split(".")[0]
    row = await pool.fetchrow("SELECT * FROM share_links WHERE share_id = $1", share_id)
    if row is None or not hmac.compare_digest(
        bytes(row["token_hash"]), hashlib.sha256(token.encode()).digest()
    ):
        raise ShareError("not found", status=404)
    if row["revoked_at"] is not None:
        raise ShareError("this link has been revoked", status=410)
    if row["expires_at"] <= datetime.now(timezone.utc):
        raise ShareError("this link has expired", status=410)
    if row["password_hash"] is not None:
        if password is None or not hmac.compare_digest(
            bytes(row["password_hash"]), hashlib.sha256(password.encode()).digest()
        ):
            raise ShareError("password required", status=401)

    await pool.execute(
        """
        UPDATE share_links SET access_count = access_count + 1, last_accessed_at = now()
        WHERE share_id = $1
        """,
        share_id,
    )
    # Every access is logged, not just creation and revocation.
    await pool.execute(
        """
        INSERT INTO access_log (access_id, org_id, project_id, principal, action, data_id, detail)
        VALUES ($1, $2, $3, 'public', 'share.read', $4, $5)
        """,
        new_id("acc"), row["org_id"], row["project_id"], row["data_id"],
        {"share_id": share_id},
    )

    if row["data_id"]:
        item = await pool.fetchrow(
            """
            SELECT data_id, external_id, mime_type, data_type, event_time, state,
                   coalesce(content_text, extracted_text) AS text
            FROM data_items WHERE data_id = $1 AND deleted_at IS NULL
            """,
            row["data_id"],
        )
        if item is None:
            raise ShareError("not found", status=404)
        return {"kind": "data", "item": dict(item), "share_id": share_id}

    case = await pool.fetchrow(
        "SELECT case_id, case_type, external_id, title FROM cases WHERE case_id = $1",
        row["case_id"],
    )
    return {"kind": "case", "case": dict(case) if case else None, "share_id": share_id}


async def inventory(pool: asyncpg.Pool, principal: Principal) -> list[dict]:
    """Everything currently shared publicly.

    Owner and admin can both list it, because the value is catching a share
    made six months ago by someone who has since forgotten it.
    """
    rows = await pool.fetch(
        """
        SELECT s.share_id, s.data_id, s.case_id, s.created_by, s.created_at,
               s.expires_at, s.revoked_at, s.access_count, s.last_accessed_at,
               (s.password_hash IS NOT NULL) AS password_protected,
               (s.revoked_at IS NULL AND s.expires_at > now()) AS live
        FROM share_links s WHERE s.org_id = $1
        ORDER BY s.created_at DESC LIMIT 500
        """,
        principal.org_id,
    )
    return [dict(r) for r in rows]


async def revoke_share(pool: asyncpg.Pool, principal: Principal, share_id: str) -> dict:
    row = await pool.fetchrow(
        "SELECT created_by, project_id FROM share_links WHERE share_id = $1 AND org_id = $2",
        share_id, principal.org_id,
    )
    if row is None:
        raise ShareError("not found", status=404)
    # The creator, or an admin cleaning up after someone who left.
    if row["created_by"] != principal.user_id and not principal.can(CONFIG_WRITE):
        raise ShareError("only the creator or an admin can revoke a share", status=403)

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "UPDATE share_links SET revoked_at = now() WHERE share_id = $1 AND revoked_at IS NULL",
            share_id,
        )
        await record_audit(
            conn, principal, action="share.revoked", project_id=row["project_id"],
            target_type="share", target_id=share_id,
        )
    return {"share_id": share_id, "revoked": True}
