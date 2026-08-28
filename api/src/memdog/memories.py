"""Memories -- typed containers with a lifecycle.

Two rules carry most of the weight:

**Nothing is orphaned.** An item written with no memory and no matching rule
lands in a `default` memory. Without it, unattached data is invisible from the
memory side entirely -- a hole in exactly the view memories exist to provide.

**The default follows the ACL's scope, not the connector's owner.** A
`shared`-scope connection produces org-visible items, so filing them in the
connecting user's personal container would put team data in one person's
memory -- and then account deletion has an awkward case where the data is
retained but the container belonged to someone who left.
"""

from __future__ import annotations

import asyncpg

from .ids import new_id

# Shipped types. An organisation adds its own; these exist so a fresh install
# works and so `default` is always available to catch unattached writes.
SHIPPED_TYPES: list[tuple[str, int | None, str]] = [
    ("default", None, "keep_members"),
    ("conversation", 3600, "orphan_delete"),
    ("session", 86_400, "archive"),
    ("tracing", 259_200, "orphan_delete"),
    ("activity", 7_776_000, "orphan_delete"),
]


async def ensure_shipped_types(pool: asyncpg.Pool, project_id: str, org_id: str) -> None:
    for name, ttl, on_expiry in SHIPPED_TYPES:
        await pool.execute(
            """
            INSERT INTO memory_types (type_id, org_id, project_id, name, ttl_seconds, on_expiry)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (project_id, name) DO NOTHING
            """,
            new_id("mty"), org_id, project_id, name, ttl, on_expiry,
        )


async def upsert_memory(
    conn,
    *,
    org_id: str,
    project_id: str,
    type_name: str,
    memory_key: str | None,
    owner_id: str | None = None,
    title: str | None = None,
) -> str:
    """Upsert by (project, type, key).

    A producer writing many messages from one thread collects them into one
    memory without tracking session state or pre-creating anything -- the
    natural key does it, from any producer, after any restart.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO memories (memory_id, org_id, project_id, type, memory_key, title, owner_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (project_id, type, memory_key)
        DO UPDATE SET updated_at = now()
        RETURNING memory_id
        """,
        new_id("mem"), org_id, project_id, type_name, memory_key, title, owner_id,
    )
    return row["memory_id"]


async def add_member(conn, memory_id: str, data_id: str, added_by: str) -> None:
    await conn.execute(
        """
        INSERT INTO memory_members (memory_id, data_id, added_by)
        VALUES ($1, $2, $3)
        ON CONFLICT (memory_id, data_id) DO NOTHING
        """,
        memory_id, data_id, added_by,
    )


async def route_write(
    conn,
    *,
    org_id: str,
    project_id: str,
    data_id: str,
    owner_id: str,
    connection_scope: str | None,
    requested_type: str | None,
    requested_key: str | None,
) -> list[str]:
    """Attach an item to its memories at write time. Returns the memory ids.

    The caller should not have to query to discover where their own item went,
    which is why the write response reports this (FR-MEMT-8).
    """
    memories: list[str] = []
    if requested_type or requested_key:
        memory_id = await upsert_memory(
            conn,
            org_id=org_id,
            project_id=project_id,
            type_name=requested_type or "default",
            memory_key=requested_key,
            owner_id=owner_id,
        )
        await add_member(conn, memory_id, data_id, "explicit")
        memories.append(memory_id)
        return memories

    # Unattached. The default memory's key is what scopes it: a user id for
    # personal data, the project id for shared -- the same rule as the ACL, so
    # account deletion falls out cleanly.
    shared = connection_scope == "shared"
    memory_id = await upsert_memory(
        conn,
        org_id=org_id,
        project_id=project_id,
        type_name="default",
        memory_key=project_id if shared else owner_id,
        owner_id=None if shared else owner_id,
        title="Project default" if shared else "My unattached items",
    )
    await add_member(conn, memory_id, data_id, "routed")
    memories.append(memory_id)
    return memories


async def memories_for_item(pool: asyncpg.Pool, data_id: str) -> list[dict]:
    """Both directions of the mapping are exposed; this is the item's side.

    `expires_at` is **computed**, never stored. Effective expiry is the maximum
    TTL across memberships, so an item in an hour-long conversation and a
    permanent factual memory does not expire in an hour.
    """
    rows = await pool.fetch(
        """
        SELECT m.memory_id, m.type, m.memory_key, mm.added_by, mm.added_at,
               t.ttl_seconds, t.on_expiry,
               CASE WHEN t.ttl_seconds IS NULL THEN NULL
                    ELSE mm.added_at + make_interval(secs => t.ttl_seconds)
               END AS expires_at
        FROM memory_members mm
        JOIN memories m ON m.memory_id = mm.memory_id
        LEFT JOIN memory_types t
               ON t.project_id = m.project_id AND t.name = m.type
        WHERE mm.data_id = $1 AND m.deleted_at IS NULL
        ORDER BY mm.added_at
        """,
        data_id,
    )
    return [dict(r) for r in rows]


def effective_expiry(memberships: list[dict]):
    """The MAXIMUM ttl across memberships, and null wins outright.

    Taking the earliest would delete data a permanent memory still depends on --
    the orphan_delete bug in another form.
    """
    if not memberships:
        return None
    if any(m["expires_at"] is None for m in memberships):
        return None
    return max(m["expires_at"] for m in memberships)
