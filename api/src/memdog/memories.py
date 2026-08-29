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

    # Only *unattached* items go to default. An update to an item that is
    # already in a memory must not quietly add it to the writer's default as
    # well: re-writing a record is not a statement about where it belongs, and
    # the surprise shows up as items accumulating in a container nobody chose.
    already = await conn.fetchval(
        "SELECT 1 FROM memory_members WHERE data_id = $1 LIMIT 1", data_id
    )
    if already:
        return memories

    # The default memory's key is what scopes it: a user id for personal data,
    # the project id for shared -- the same rule as the ACL, so account deletion
    # falls out cleanly.
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


# --------------------------------------------------------- mutation


class MemoryError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


async def create_memory(
    pool: asyncpg.Pool,
    principal,
    *,
    project_id: str,
    type_name: str,
    memory_key: str | None = None,
    title: str | None = None,
) -> dict:
    """Create a memory before anything is in it.

    Writing an item with a `memory` key also creates one, and that is the path
    most producers use -- but a person organising a corpus needs to make the
    container first and fill it deliberately, which is a different motion.
    """
    from .audit import record_audit
    from .auth import DATA_WRITE

    principal.require(DATA_WRITE)
    known = await pool.fetchval(
        "SELECT 1 FROM memory_types WHERE project_id = $1 AND name = $2",
        project_id, type_name,
    )
    if not known:
        raise MemoryError(
            f"unknown memory type {type_name!r} in this project", status=404
        )

    async with pool.acquire() as conn, conn.transaction():
        memory_id = await upsert_memory(
            conn, org_id=principal.org_id, project_id=project_id,
            type_name=type_name, memory_key=memory_key,
            owner_id=principal.user_id, title=title,
        )
        await record_audit(
            conn, principal, action="memory.created", project_id=project_id,
            target_type="memory", target_id=memory_id,
            detail={"type": type_name, "memory_key": memory_key},
        )
    return {"memory_id": memory_id, "type": type_name, "memory_key": memory_key,
            "title": title}


async def add_members(
    pool: asyncpg.Pool, principal, memory_id: str, data_ids: list[str]
) -> dict:
    """Attach existing items. Recorded as `explicit`, because a person did it.

    Only items the caller can already see are attached: adding to a memory must
    not become a way to observe that something exists.
    """
    from .acl import visibility_params, visibility_sql
    from .audit import record_audit
    from .auth import DATA_WRITE

    principal.require(DATA_WRITE)
    memory = await pool.fetchrow(
        "SELECT project_id, org_id FROM memories WHERE memory_id = $1 AND deleted_at IS NULL",
        memory_id,
    )
    if memory is None or memory["org_id"] != principal.org_id:
        raise MemoryError("memory not found", status=404)

    org_id, user_id, principals = visibility_params(principal)
    predicate = visibility_sql("d", 2, 3, 4)
    visible = await pool.fetch(
        f"""
        SELECT d.data_id FROM data_items d
        WHERE d.data_id = ANY($1::text[]) AND {predicate} AND d.project_id = $5
        """,
        data_ids, org_id, user_id, principals, memory["project_id"],
    )
    allowed = [r["data_id"] for r in visible]

    async with pool.acquire() as conn, conn.transaction():
        for data_id in allowed:
            await add_member(conn, memory_id, data_id, "explicit")
        await record_audit(
            conn, principal, action="memory.members_added",
            project_id=memory["project_id"], target_type="memory", target_id=memory_id,
            detail={"added": len(allowed), "requested": len(data_ids)},
        )
    return {
        "memory_id": memory_id,
        "added": allowed,
        # Named rather than silently dropped, so a typo is visible.
        "skipped": sorted(set(data_ids) - set(allowed)),
    }


async def remove_member(
    pool: asyncpg.Pool, principal, memory_id: str, data_id: str
) -> dict:
    """Unmapping is not deletion.

    Removing a member never deletes the item. And removing its *last* membership
    files it in the applicable default memory rather than leaving it unattached
    -- an item in no memory is invisible from the memory side entirely, which is
    the hole `default` exists to close.
    """
    from .audit import record_audit
    from .auth import DATA_WRITE

    principal.require(DATA_WRITE)
    memory = await pool.fetchrow(
        "SELECT project_id, org_id FROM memories WHERE memory_id = $1", memory_id
    )
    if memory is None or memory["org_id"] != principal.org_id:
        raise MemoryError("memory not found", status=404)

    async with pool.acquire() as conn, conn.transaction():
        removed = await conn.execute(
            "DELETE FROM memory_members WHERE memory_id = $1 AND data_id = $2",
            memory_id, data_id,
        )
        if removed.endswith("0"):
            raise MemoryError("not a member of that memory", status=404)

        remaining = await conn.fetchval(
            "SELECT count(*) FROM memory_members WHERE data_id = $1", data_id
        )
        landed = None
        if remaining == 0:
            item = await conn.fetchrow(
                """
                SELECT d.owner_id, c.scope FROM data_items d
                LEFT JOIN connections c ON c.connection_id = d.connection_id
                WHERE d.data_id = $1
                """,
                data_id,
            )
            shared = item and item["scope"] == "shared"
            landed = await upsert_memory(
                conn, org_id=principal.org_id, project_id=memory["project_id"],
                type_name="default",
                memory_key=memory["project_id"] if shared else item["owner_id"],
                owner_id=None if shared else item["owner_id"],
                title="Project default" if shared else "My unattached items",
            )
            await add_member(conn, landed, data_id, "routed")

        await record_audit(
            conn, principal, action="memory.member_removed",
            project_id=memory["project_id"], target_type="memory", target_id=memory_id,
            detail={"data_id": data_id, "landed_in_default": landed is not None},
        )
    return {"memory_id": memory_id, "data_id": data_id, "deleted": False,
            "landed_in": landed}


async def retype_memory(
    pool: asyncpg.Pool, principal, memory_id: str, type_name: str, *, preview: bool = False
) -> dict:
    """Re-typing recomputes TTL, and a shorter TTL is previewed before it bites.

    The type is mutable and is deliberately not encoded in the identifier, so
    this is an update rather than a migration. But moving to a shorter TTL can
    expire members, and finding that out afterwards is not acceptable.
    """
    from .audit import record_audit
    from .auth import DATA_WRITE

    principal.require(DATA_WRITE)
    memory = await pool.fetchrow(
        "SELECT project_id, org_id, type FROM memories WHERE memory_id = $1", memory_id
    )
    if memory is None or memory["org_id"] != principal.org_id:
        raise MemoryError("memory not found", status=404)

    target = await pool.fetchrow(
        "SELECT ttl_seconds, on_expiry FROM memory_types WHERE project_id = $1 AND name = $2",
        memory["project_id"], type_name,
    )
    if target is None:
        raise MemoryError(f"unknown memory type {type_name!r}", status=404)

    # What would expire under the new TTL, counting only members this memory is
    # the sole holder of -- orphan_delete never touches what another memory
    # still holds.
    would_expire = 0
    if target["ttl_seconds"] is not None:
        would_expire = await pool.fetchval(
            """
            SELECT count(*) FROM memory_members mm
            WHERE mm.memory_id = $1
              AND mm.added_at + make_interval(secs => $2) < now()
              AND NOT EXISTS (
                SELECT 1 FROM memory_members other
                WHERE other.data_id = mm.data_id AND other.memory_id <> $1)
            """,
            memory_id, target["ttl_seconds"],
        )

    if preview:
        return {"memory_id": memory_id, "from": memory["type"], "to": type_name,
                "ttl_seconds": target["ttl_seconds"], "would_expire": would_expire,
                "applied": False}

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "UPDATE memories SET type = $2, updated_at = now() WHERE memory_id = $1",
            memory_id, type_name,
        )
        await record_audit(
            conn, principal, action="memory.retyped", project_id=memory["project_id"],
            target_type="memory", target_id=memory_id,
            detail={"from": memory["type"], "to": type_name, "would_expire": would_expire},
        )
    return {"memory_id": memory_id, "from": memory["type"], "to": type_name,
            "ttl_seconds": target["ttl_seconds"], "would_expire": would_expire,
            "applied": True}


RELATIONS = ("part_of", "derived_from", "about", "continues", "supersedes")

# Who says so. An explicit link is a claim a person will stand behind; a routed
# or agent link is an inference. Kept apart for the same reason case membership
# keeps asserted and inferred apart -- a guess that cannot be distinguished from
# a statement quietly becomes one.
ORIGINS = ("explicit", "routed", "agent")


async def link(
    pool: asyncpg.Pool,
    principal,
    *,
    from_memory: str,
    to_memory: str,
    relation: str,
    created_by: str = "explicit",
    confidence: float | None = None,
) -> dict:
    """Relate two memories.

    Both must be in the caller's org, checked in one statement rather than
    fetched and compared: a memory id from another organization is "not found"
    and not "not yours", because the second sentence confirms it exists.

    A link is not symmetric and the direction is the claim. `derived_from`
    pointing the wrong way says the conversations were derived from their
    summary, which is not merely wrong but backwards in a way a reader believes.
    """
    from .audit import record_audit
    from .auth import DATA_WRITE

    principal.require(DATA_WRITE)
    if relation not in RELATIONS:
        raise MemoryError(f"relation must be one of {', '.join(RELATIONS)}")
    if created_by not in ORIGINS:
        raise MemoryError(f"created_by must be one of {', '.join(ORIGINS)}")
    if from_memory == to_memory:
        # Not a philosophical objection: a self-link makes every traversal
        # cyclic and says nothing.
        raise MemoryError("a memory cannot be linked to itself")
    if created_by == "explicit" and confidence is not None:
        # An explicit link is not 80% true. A number here would invite a reader
        # to weigh a statement the way they weigh a guess.
        raise MemoryError("confidence belongs to a derived link, not an explicit one")

    found = await pool.fetchval(
        "SELECT count(*) FROM memories WHERE memory_id = ANY($1::text[]) AND org_id = $2",
        [from_memory, to_memory], principal.org_id,
    )
    if found != 2:
        raise MemoryError("no such memory", status=404)

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO memory_links (from_memory, to_memory, relation,
                                      created_by, confidence)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (from_memory, to_memory, relation) DO UPDATE
                -- A person restating what an agent guessed promotes it. The
                -- reverse never happens: an inference does not overwrite a
                -- statement somebody made.
                SET created_by = CASE
                        WHEN memory_links.created_by = 'explicit' THEN 'explicit'
                        ELSE EXCLUDED.created_by END,
                    -- Confidence follows the *resulting* row, not the incoming
                    -- one. Keying it off EXCLUDED left an agent's number on a
                    -- link that stayed explicit -- a statement wearing a guess's
                    -- probability, which is the confusion this column exists to
                    -- prevent.
                    confidence = CASE
                        WHEN memory_links.created_by = 'explicit'
                          OR EXCLUDED.created_by = 'explicit' THEN NULL
                        ELSE EXCLUDED.confidence END
            """,
            from_memory, to_memory, relation, created_by, confidence,
        )
        await record_audit(
            conn, principal, action="memory.linked",
            target_type="memory", target_id=from_memory,
            detail={"to_memory": to_memory, "relation": relation,
                    "created_by": created_by},
        )
    return {"from_memory": from_memory, "to_memory": to_memory,
            "relation": relation, "created_by": created_by,
            "confidence": confidence}


async def unlink(
    pool: asyncpg.Pool, principal, *,
    from_memory: str, to_memory: str, relation: str,
) -> dict:
    from .audit import record_audit
    from .auth import DATA_WRITE

    principal.require(DATA_WRITE)
    async with pool.acquire() as conn, conn.transaction():
        removed = await conn.fetchval(
            """
            DELETE FROM memory_links l USING memories m
            WHERE l.from_memory = $1 AND l.to_memory = $2 AND l.relation = $3
              AND m.memory_id = l.from_memory AND m.org_id = $4
            RETURNING l.from_memory
            """,
            from_memory, to_memory, relation, principal.org_id,
        )
        if removed is None:
            raise MemoryError("no such link", status=404)
        await record_audit(
            conn, principal, action="memory.unlinked",
            target_type="memory", target_id=from_memory,
            detail={"to_memory": to_memory, "relation": relation},
        )
    return {"from_memory": from_memory, "to_memory": to_memory,
            "relation": relation, "status": "removed"}


async def links_for(pool: asyncpg.Pool, principal, memory_id: str) -> dict:
    """Both directions, kept apart.

    "What is derived from this?" and "what is this derived from?" are different
    questions, and merging them into one list loses the direction -- which is
    the entire content of the claim.
    """
    from .auth import DATA_READ

    principal.require(DATA_READ)
    owned = await pool.fetchval(
        "SELECT 1 FROM memories WHERE memory_id = $1 AND org_id = $2",
        memory_id, principal.org_id,
    )
    if not owned:
        raise MemoryError("no such memory", status=404)

    rows = await pool.fetch(
        """
        SELECT l.from_memory, l.to_memory, l.relation, l.created_by, l.confidence,
               -- What to show: a memory has a key and an optional title,
               -- and no `name`. Coalescing keeps the display honest when only
               -- one of them was ever set.
               coalesce(f.title, f.memory_key) AS from_name,
               coalesce(t.title, t.memory_key) AS to_name
        FROM memory_links l
        JOIN memories f ON f.memory_id = l.from_memory
        JOIN memories t ON t.memory_id = l.to_memory
        WHERE l.from_memory = $1 OR l.to_memory = $1
        ORDER BY l.created_at
        """,
        memory_id,
    )
    return {
        "memory_id": memory_id,
        "outgoing": [dict(r) for r in rows if r["from_memory"] == memory_id],
        "incoming": [dict(r) for r in rows if r["to_memory"] == memory_id],
    }


async def list_types(pool: asyncpg.Pool, project_id: str) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT type_id, name, ttl_seconds, on_expiry, locked FROM memory_types
        WHERE project_id = $1 ORDER BY name
        """,
        project_id,
    )
    return [dict(r) for r in rows]


async def create_type(
    pool: asyncpg.Pool, principal, *, project_id: str, name: str,
    ttl_seconds: int | None, on_expiry: str = "orphan_delete",
) -> dict:
    """A type is a name, a TTL and an expiry policy. Deliberately three fields.

    Earlier drafts shipped ten types with semantics baked into each; nearly all
    of it turned out to be expressible as a TTL plus a policy, so the set is
    open and organisations define their own.
    """
    from .audit import record_audit
    from .auth import CONFIG_WRITE

    principal.require(CONFIG_WRITE)
    if on_expiry not in ("orphan_delete", "keep_members", "archive"):
        raise MemoryError("on_expiry must be orphan_delete, keep_members or archive")

    type_id = new_id("mty")
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO memory_types (type_id, org_id, project_id, name, ttl_seconds, on_expiry)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (project_id, name) DO UPDATE SET ttl_seconds = EXCLUDED.ttl_seconds,
                on_expiry = EXCLUDED.on_expiry
            """,
            type_id, principal.org_id, project_id, name, ttl_seconds, on_expiry,
        )
        await record_audit(
            conn, principal, action="memory_type.set", project_id=project_id,
            target_type="memory_type", target_id=name,
            detail={"ttl_seconds": ttl_seconds, "on_expiry": on_expiry},
        )
    return {"name": name, "ttl_seconds": ttl_seconds, "on_expiry": on_expiry}


async def delete_memory(
    pool: asyncpg.Pool,
    principal,
    queue,
    memory_id: str,
    *,
    preview: bool = False,
) -> dict:
    """Delete a container, applying the type's expiry policy to its contents.

    This is where `on_expiry` stops being metadata:

    - **`orphan_delete`** — remove membership, and delete an item **only if no
      other memory holds it**. Deleting a member because one of its containers
      went away destroys data a permanent memory still depends on. This is the
      default precisely because the failure mode of the conditional version is
      leaving something behind, and the failure mode of the unconditional one is
      losing something.
    - **`keep_members`** — the items survive. Any that lose their last
      membership are filed in the applicable default rather than left invisible.
    - **`archive`** — the memory is tombstoned and its members are untouched.

    The actual erasure runs through the ordinary deletion cascade rather than a
    second implementation, so items removed this way get the same tombstone,
    the same blob reclamation and the same audit trail.
    """
    from .audit import record_audit
    from .auth import DATA_WRITE
    from .deletion import request_deletion

    principal.require(DATA_WRITE)
    memory = await pool.fetchrow(
        """
        SELECT m.project_id, m.org_id, m.type, m.memory_key,
               coalesce(t.on_expiry, 'orphan_delete') AS on_expiry
        FROM memories m
        LEFT JOIN memory_types t ON t.project_id = m.project_id AND t.name = m.type
        WHERE m.memory_id = $1 AND m.deleted_at IS NULL
        """,
        memory_id,
    )
    if memory is None or memory["org_id"] != principal.org_id:
        raise MemoryError("memory not found", status=404)

    # A user's or project's default memory is not deletable: it is where
    # unattached items land, and removing it would recreate the hole it exists
    # to close on the very next write.
    if memory["type"] == "default":
        raise MemoryError(
            "the default memory cannot be deleted -- it is where unattached items land",
            status=409,
        )

    sole_held = [r["data_id"] for r in await pool.fetch(
        """
        SELECT mm.data_id FROM memory_members mm
        WHERE mm.memory_id = $1
          AND NOT EXISTS (SELECT 1 FROM memory_members other
                          WHERE other.data_id = mm.data_id AND other.memory_id <> $1)
        ORDER BY mm.data_id
        """,
        memory_id,
    )]
    also_held = await pool.fetchval(
        """
        SELECT count(*) FROM memory_members mm
        WHERE mm.memory_id = $1
          AND EXISTS (SELECT 1 FROM memory_members other
                      WHERE other.data_id = mm.data_id AND other.memory_id <> $1)
        """,
        memory_id,
    )

    policy = memory["on_expiry"]
    to_delete = sole_held if policy == "orphan_delete" else []

    if preview:
        return {
            "memory_id": memory_id,
            "on_expiry": policy,
            # Both numbers, separately: what goes, and what survives because
            # something else still holds it.
            "would_delete": len(to_delete),
            "retained_because_held_elsewhere": also_held,
            "would_refile_to_default": len(sole_held) if policy != "orphan_delete" else 0,
            "applied": False,
        }

    run_id = None
    if to_delete:
        deletion = await request_deletion(
            pool, queue, principal,
            selector={"project_id": memory["project_id"], "data_ids": to_delete},
            reason=f"memory {memory_id} deleted ({policy})",
        )
        run_id = deletion.run_id

    async with pool.acquire() as conn, conn.transaction():
        if policy != "orphan_delete":
            # Items that would otherwise be left unattached are re-filed before
            # the container goes, not after -- afterwards there would be no
            # membership row to tell us who owned them.
            for data_id in sole_held:
                item = await conn.fetchrow(
                    """
                    SELECT d.owner_id, c.scope FROM data_items d
                    LEFT JOIN connections c ON c.connection_id = d.connection_id
                    WHERE d.data_id = $1
                    """,
                    data_id,
                )
                if item is None:
                    continue
                shared = item["scope"] == "shared"
                landed = await upsert_memory(
                    conn, org_id=principal.org_id, project_id=memory["project_id"],
                    type_name="default",
                    memory_key=memory["project_id"] if shared else item["owner_id"],
                    owner_id=None if shared else item["owner_id"],
                    title="Project default" if shared else "My unattached items",
                )
                await add_member(conn, landed, data_id, "routed")

        if policy == "archive":
            # Tombstoned rather than removed: an archived memory is still the
            # answer to "what was this item in?".
            await conn.execute(
                "UPDATE memories SET deleted_at = now() WHERE memory_id = $1", memory_id
            )
        else:
            await conn.execute("DELETE FROM memories WHERE memory_id = $1", memory_id)

        await record_audit(
            conn, principal, action="memory.deleted", project_id=memory["project_id"],
            target_type="memory", target_id=memory_id,
            detail={"on_expiry": policy, "deleted_items": len(to_delete),
                    "retained": also_held, "run_id": run_id},
        )

    return {
        "memory_id": memory_id,
        "on_expiry": policy,
        "deleted_items": len(to_delete),
        "retained_because_held_elsewhere": also_held,
        "deletion_run_id": run_id,
        "applied": True,
    }
