"""The control plane.

Everything settable in the UI is settable through the API, at the same
granularity and with the same validation. Two consequences: an embedding host
can build its own settings surface, and the surface is testable without a
browser.

The rule that shapes every handler here: **org roles and platform grants are
orthogonal.** `owner`/`admin`/`member`/`viewer` are org-scoped; a platform grant
confers nothing inside an organization its holder is not a member of.
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from .audit import record_audit
from .auth import ADMIN, CONFIG_WRITE, Principal, issue_key
from .ids import new_id
from .memories import ensure_shipped_types

ROLES = ("owner", "admin", "member", "viewer")
# Who may change org-level things. A member manages their own data and keys;
# they do not add members or create projects.
ADMIN_ROLES = ("owner", "admin")


class ControlError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


async def _role(pool: asyncpg.Pool, principal: Principal, org_id: str) -> str:
    role = await pool.fetchval(
        "SELECT role FROM memberships WHERE user_id = $1 AND org_id = $2",
        principal.user_id, org_id,
    )
    if role is None:
        # Indistinguishable from "no such org": membership is not discoverable
        # by probing.
        raise ControlError("organization not found", status=404)
    return role


async def _require_admin(pool: asyncpg.Pool, principal: Principal, org_id: str) -> str:
    role = await _role(pool, principal, org_id)
    if role not in ADMIN_ROLES and not principal.can(ADMIN):
        raise ControlError("requires an org owner or admin", status=403)
    return role


# ------------------------------------------------------------------ projects


async def list_projects(pool: asyncpg.Pool, principal: Principal) -> list[dict]:
    await _role(pool, principal, principal.org_id)
    rows = await pool.fetch(
        """
        SELECT p.project_id, p.name, p.answer_storage, p.created_at,
               (SELECT count(*) FROM data_items d
                 WHERE d.project_id = p.project_id AND d.deleted_at IS NULL) AS items
        FROM projects p WHERE p.org_id = $1 ORDER BY p.created_at
        """,
        principal.org_id,
    )
    return [dict(r) for r in rows]


async def create_project(
    pool: asyncpg.Pool, principal: Principal, *, name: str
) -> dict:
    await _require_admin(pool, principal, principal.org_id)
    principal.require(CONFIG_WRITE)
    project_id = new_id("prj")
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO projects (project_id, org_id, name) VALUES ($1, $2, $3)",
            project_id, principal.org_id, name,
        )
        await record_audit(
            conn, principal, action="project.created", project_id=project_id,
            target_type="project", target_id=project_id, detail={"name": name},
        )
    # A project without memory types cannot route its first write.
    await ensure_shipped_types(pool, project_id, principal.org_id)
    return {"project_id": project_id, "name": name}


# ------------------------------------------------------------------- members


async def list_members(pool: asyncpg.Pool, principal: Principal) -> list[dict]:
    await _role(pool, principal, principal.org_id)
    rows = await pool.fetch(
        """
        SELECT u.user_id, u.email, u.display_name, m.role, m.created_at
        FROM memberships m JOIN users u ON u.user_id = m.user_id
        WHERE m.org_id = $1 ORDER BY m.created_at
        """,
        principal.org_id,
    )
    return [dict(r) for r in rows]


async def add_member(
    pool: asyncpg.Pool, principal: Principal, *, email: str, role: str
) -> dict:
    await _require_admin(pool, principal, principal.org_id)
    principal.require(CONFIG_WRITE)
    if role not in ROLES:
        raise ControlError(f"role must be one of {', '.join(ROLES)}")

    async with pool.acquire() as conn, conn.transaction():
        user_id = await conn.fetchval("SELECT user_id FROM users WHERE email = $1", email)
        if user_id is None:
            user_id = new_id("usr")
            await conn.execute(
                "INSERT INTO users (user_id, email, display_name) VALUES ($1, $2, $2)",
                user_id, email,
            )
            await conn.execute(
                """
                INSERT INTO identities (identity_id, user_id, provider, external_id)
                VALUES ($1, $2, 'local', $3)
                """,
                new_id("idn"), user_id, email,
            )
        await conn.execute(
            """
            INSERT INTO memberships (user_id, org_id, role) VALUES ($1, $2, $3)
            ON CONFLICT (user_id, org_id) DO UPDATE SET role = EXCLUDED.role
            """,
            user_id, principal.org_id, role,
        )
        await record_audit(
            conn, principal, action="member.added", target_type="user", target_id=user_id,
            detail={"email": email, "role": role},
        )
    return {"user_id": user_id, "email": email, "role": role}


async def remove_member(pool: asyncpg.Pool, principal: Principal, user_id: str) -> dict:
    """Removing a member revokes their keys in the same transaction.

    Membership-coupling is checked at verify time too, so this is belt and
    braces -- but leaving live keys behind on a departed member is exactly the
    offboarding leak the design calls out.
    """
    await _require_admin(pool, principal, principal.org_id)
    principal.require(CONFIG_WRITE)
    if user_id == principal.user_id:
        raise ControlError("an admin cannot remove themselves", status=409)

    async with pool.acquire() as conn, conn.transaction():
        deleted = await conn.execute(
            "DELETE FROM memberships WHERE user_id = $1 AND org_id = $2",
            user_id, principal.org_id,
        )
        revoked = await conn.fetchval(
            """
            WITH r AS (
              UPDATE api_keys SET revoked_at = now()
              WHERE user_id = $1 AND org_id = $2 AND revoked_at IS NULL
              RETURNING 1)
            SELECT count(*) FROM r
            """,
            user_id, principal.org_id,
        )
        await record_audit(
            conn, principal, action="member.removed", target_type="user", target_id=user_id,
            detail={"keys_revoked": revoked},
        )
    if deleted.endswith("0"):
        raise ControlError("not a member", status=404)
    return {"user_id": user_id, "keys_revoked": revoked}


# -------------------------------------------------------------------- groups


async def list_groups(pool: asyncpg.Pool, principal: Principal) -> list[dict]:
    await _role(pool, principal, principal.org_id)
    rows = await pool.fetch(
        """
        SELECT g.group_id, g.name, g.managed_by,
               coalesce(array_agg(gm.user_id) FILTER (WHERE gm.user_id IS NOT NULL), '{}') AS members
        FROM groups g LEFT JOIN group_members gm ON gm.group_id = g.group_id
        WHERE g.org_id = $1 GROUP BY g.group_id, g.name, g.managed_by ORDER BY g.name
        """,
        principal.org_id,
    )
    return [dict(r) for r in rows]


async def create_group(pool: asyncpg.Pool, principal: Principal, *, name: str) -> dict:
    await _require_admin(pool, principal, principal.org_id)
    principal.require(CONFIG_WRITE)
    group_id = new_id("grp")
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO groups (group_id, org_id, name) VALUES ($1, $2, $3)",
            group_id, principal.org_id, name,
        )
        await record_audit(conn, principal, action="group.created",
                           target_type="group", target_id=group_id, detail={"name": name})
    return {"group_id": group_id, "name": name}


async def set_group_members(
    pool: asyncpg.Pool, principal: Principal, group_id: str, user_ids: list[str]
) -> dict:
    await _require_admin(pool, principal, principal.org_id)
    principal.require(CONFIG_WRITE)
    owner_org = await pool.fetchval("SELECT org_id FROM groups WHERE group_id = $1", group_id)
    if owner_org != principal.org_id:
        raise ControlError("group not found", status=404)

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("DELETE FROM group_members WHERE group_id = $1", group_id)
        # Only actual members of this org: a group is a principal for sharing,
        # so a non-member in one would be a grant to an outsider.
        await conn.executemany(
            """
            INSERT INTO group_members (group_id, user_id)
            SELECT $1, $2 WHERE EXISTS (
              SELECT 1 FROM memberships WHERE user_id = $2 AND org_id = $3)
            """,
            [(group_id, u, principal.org_id) for u in user_ids],
        )
        await record_audit(conn, principal, action="group.members_set",
                           target_type="group", target_id=group_id,
                           detail={"count": len(user_ids)})
    rows = await pool.fetch("SELECT user_id FROM group_members WHERE group_id = $1", group_id)
    return {"group_id": group_id, "members": [r["user_id"] for r in rows]}


# ------------------------------------------------------------------ api keys


async def list_keys(pool: asyncpg.Pool, principal: Principal) -> list[dict]:
    """Prefix and last-used only. The key itself is never re-displayed."""
    rows = await pool.fetch(
        """
        SELECT key_id, name, prefix, capabilities, project_id, expires_at,
               last_used_at, revoked_at, created_at
        FROM api_keys WHERE user_id = $1 AND org_id = $2 ORDER BY created_at DESC
        """,
        principal.user_id, principal.org_id,
    )
    return [dict(r) for r in rows]


async def create_key(
    pool: asyncpg.Pool,
    principal: Principal,
    *,
    name: str,
    capabilities: list[str],
    project_id: str | None = None,
) -> dict:
    """A key can never grant more than the credential that created it.

    Otherwise capability scoping is decorative: any `data:read` key could mint
    itself an `admin:*` one.
    """
    for capability in capabilities:
        if not principal.can(capability):
            raise ControlError(
                f"cannot issue a key with {capability}: this credential does not hold it",
                status=403,
            )
    token = await issue_key(
        pool, user_id=principal.user_id, org_id=principal.org_id,
        project_id=project_id, capabilities=capabilities, name=name,
    )
    await record_audit(
        pool, principal, action="apikey.issued", target_type="api_key",
        target_id=token.split(".")[0], detail={"name": name, "capabilities": capabilities},
    )
    return {"token": token, "prefix": token.split(".")[0], "capabilities": capabilities}


async def revoke_key(pool: asyncpg.Pool, principal: Principal, key_id: str) -> dict:
    updated = await pool.execute(
        """
        UPDATE api_keys SET revoked_at = now()
        WHERE key_id = $1 AND user_id = $2 AND revoked_at IS NULL
        """,
        key_id, principal.user_id,
    )
    if updated.endswith("0"):
        raise ControlError("key not found", status=404)
    await record_audit(pool, principal, action="apikey.revoked",
                       target_type="api_key", target_id=key_id)
    return {"key_id": key_id, "revoked": True}


# --------------------------------------------------- producers & connections


async def list_producers(pool: asyncpg.Pool, principal: Principal) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT p.producer_id, p.type, p.project_id, p.status, p.inbound_auth,
               p.connection_id, c.scope AS connection_scope, p.last_item_at,
               -- The highest-value freshness detector: it catches a webhook
               -- that stopped, a crawler whose selector broke, and a client
               -- that quietly died, with one query.
               extract(epoch FROM now() - p.last_item_at)::int AS seconds_since_last_item
        FROM producers p LEFT JOIN connections c ON c.connection_id = p.connection_id
        WHERE p.org_id = $1 ORDER BY p.created_at
        """,
        principal.org_id,
    )
    return [dict(r) for r in rows]


async def create_producer(
    pool: asyncpg.Pool,
    principal: Principal,
    *,
    project_id: str,
    producer_type: str,
    connection_id: str | None = None,
) -> dict:
    principal.require(CONFIG_WRITE)
    prefixes = {"client": "key", "webhook": "whk", "crawler": "crw",
                "upload": "upl", "agent": "key"}
    if producer_type not in prefixes:
        raise ControlError(f"type must be one of {', '.join(prefixes)}")
    owner_org = await pool.fetchval(
        "SELECT org_id FROM projects WHERE project_id = $1", project_id
    )
    if owner_org != principal.org_id:
        raise ControlError("project not found", status=404)

    producer_id = new_id(prefixes[producer_type])
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO producers (producer_id, type, user_id, org_id, project_id,
                                   connection_id, status, inbound_auth)
            VALUES ($1, $2, $3, $4, $5, $6, 'enabled', 'none')
            """,
            producer_id, producer_type, principal.user_id, principal.org_id,
            project_id, connection_id,
        )
        await record_audit(conn, principal, action="producer.created",
                           project_id=project_id, target_type="producer",
                           target_id=producer_id, detail={"type": producer_type})
    return {"producer_id": producer_id, "type": producer_type, "project_id": project_id}


async def set_producer_status(
    pool: asyncpg.Pool, principal: Principal, producer_id: str, status: str
) -> dict:
    principal.require(CONFIG_WRITE)
    if status not in ("draft", "enabled", "disabled"):
        raise ControlError("status must be draft, enabled or disabled")
    updated = await pool.execute(
        "UPDATE producers SET status = $3 WHERE producer_id = $1 AND org_id = $2",
        producer_id, principal.org_id, status,
    )
    if updated.endswith("0"):
        raise ControlError("producer not found", status=404)
    await record_audit(pool, principal, action="producer.status_set",
                       target_type="producer", target_id=producer_id,
                       detail={"status": status})
    return {"producer_id": producer_id, "status": status}


async def set_connection_scope(
    pool: asyncpg.Pool, principal: Principal, connection_id: str, scope: str
) -> dict:
    """The ACL-inheritance root.

    Changing it does not retroactively re-file what was already written: those
    items were assigned an ACL at write time and rewriting history here would
    silently change who can see existing data.
    """
    principal.require(CONFIG_WRITE)
    if scope not in ("personal", "shared"):
        raise ControlError("scope must be personal or shared")
    updated = await pool.execute(
        "UPDATE connections SET scope = $3 WHERE connection_id = $1 AND org_id = $2",
        connection_id, principal.org_id, scope,
    )
    if updated.endswith("0"):
        raise ControlError("connection not found", status=404)
    await record_audit(pool, principal, action="connection.scope_set",
                       target_type="connection", target_id=connection_id,
                       detail={"scope": scope, "applies_to": "future writes only"})
    return {"connection_id": connection_id, "scope": scope,
            "applies_to": "future writes only"}
