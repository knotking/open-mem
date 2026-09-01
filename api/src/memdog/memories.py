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
    # A transcript is unedited speech that nobody reviewed before it was
    # stored, which makes retention a default rather than a preference here.
    # Archived rather than deleted at ninety days: out of the working set,
    # still answerable for.
    ("meeting", 7_776_000, "archive"),
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
    row = await conn.fetchrow(
        """
        INSERT INTO memory_members (memory_id, data_id, added_by)
        VALUES ($1, $2, $3)
        ON CONFLICT (memory_id, data_id) DO NOTHING
        RETURNING memory_id
        """,
        memory_id, data_id, added_by,
    )
    # Nothing on a conflict: re-adding an item already in a memory is not a
    # membership event, and an alert that fired on it would fire on every
    # rewrite of the same record.
    if row is None:
        return
    memory = await conn.fetchrow(
        "SELECT org_id, project_id, type FROM memories WHERE memory_id = $1", memory_id)
    if memory is None:
        return
    # Landing in the default memory is not a membership event. `default` is
    # where an item with no memory and no matching routing rule goes -- it is
    # the *absence* of a signal, and it happens on essentially every write, so
    # announcing it would put an event and a queue message on the write path
    # for nothing.
    # Every rollup built from this memory is now out of date. Marked before the
    # transition is emitted, so a consumer woken by the event that reads the
    # rollup finds it already flagged rather than racing the flag.
    await mark_ancestors_stale(conn, memory_id, reason="a member was added")

    if memory["type"] == "default":
        return
    from .alerts import emit_transition

    await emit_transition(
        conn, "memory.member_added", org_id=memory["org_id"],
        project_id=memory["project_id"], data_id=data_id,
        payload={"memory_id": memory_id, "data_id": data_id,
                 "memory_type": memory["type"], "added_by": added_by},
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
        # Removing is a change too, and the direction people forget: a rollup
        # that still describes a record the memory no longer holds is wrong in
        # the way that is hardest to notice, because it reads as complete.
        await mark_ancestors_stale(conn, memory_id, reason="a member was removed")
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
        # Promotion and demotion are the point of a mutable type -- a
        # conversation that turned out to hold durable facts, a working set gone
        # cold. Both are worth being told about, and neither leaves a trace once
        # the column has been overwritten.
        from .alerts import emit_transition

        await emit_transition(
            conn, "memory.retyped", org_id=memory["org_id"],
            project_id=memory["project_id"],
            payload={"memory_id": memory_id, "from_type": memory["type"],
                     "to_type": type_name, "would_expire": would_expire},
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

    # A cycle is refused at the edge that would close it, rather than survived
    # by every reader. Both the tree and the alert scope walk `part_of`
    # recursively, and a graph saying a memory contains its own container has
    # no meaning worth protecting.
    #
    # Per relation, because the relations are independent: a memory legitimately
    # derived from another can also be part of it, and rejecting that would be
    # refusing a true statement to prevent a loop that does not exist.
    closes = await pool.fetchval(
        """
        WITH RECURSIVE reachable(memory_id, depth) AS (
            SELECT to_memory, 1 FROM memory_links
            WHERE from_memory = $1 AND relation = $3
            UNION
            SELECT l.to_memory, r.depth + 1
            FROM memory_links l JOIN reachable r ON l.from_memory = r.memory_id
            WHERE l.relation = $3 AND r.depth < 64
        )
        SELECT 1 FROM reachable WHERE memory_id = $2 LIMIT 1
        """,
        to_memory, from_memory, relation,
    )
    if closes:
        raise MemoryError(
            f"that {relation} link would close a cycle: {to_memory} already leads back "
            f"to {from_memory}",
            status=409,
        )

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


# ------------------------------------------------------------------ expiry


async def due_for_expiry(
    pool: asyncpg.Pool,
    *,
    project_id: str | None = None,
    owner_id: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """Items every one of whose memberships has expired.

    The rule is `effective_expiry` expressed in SQL: an item is due when it has
    at least one membership, **none of its memberships is unbounded**, and the
    **latest** of them is in the past. Anything else would delete data a
    permanent memory still depends on -- an item in an hour-long conversation
    and in a factual memory is not an hour-old item, and expiring it per
    membership is the `orphan_delete` bug in another form.

    The governing policy is the one belonging to the membership that expired
    **last**, because that is the container that kept the item alive: a record
    held in a conversation for an hour and a case for a year is a case record by
    the time it expires, and the case's policy is the one that should decide.
    """
    rows = await pool.fetch(
        """
        WITH memberships AS (
            SELECT mm.data_id, m.project_id, m.org_id, d.owner_id, m.memory_id, m.type,
                   coalesce(t.on_expiry, 'orphan_delete') AS on_expiry,
                   CASE WHEN t.ttl_seconds IS NULL THEN NULL
                        ELSE mm.added_at + make_interval(secs => t.ttl_seconds)
                   END AS expires_at
            FROM memory_members mm
            JOIN memories m ON m.memory_id = mm.memory_id AND m.deleted_at IS NULL
            JOIN data_items d ON d.data_id = mm.data_id
            LEFT JOIN memory_types t
                   ON t.project_id = m.project_id AND t.name = m.type
            WHERE d.deleted_at IS NULL
              AND ($1::text IS NULL OR m.project_id = $1)
              AND ($3::text IS NULL OR d.owner_id = $3)
        ),
        rolled AS (
            SELECT data_id, project_id, org_id, owner_id,
                   count(*) FILTER (WHERE expires_at IS NULL) AS unbounded,
                   max(expires_at) AS expires_at
            FROM memberships GROUP BY data_id, project_id, org_id, owner_id
        )
        SELECT r.data_id, r.project_id, r.org_id, r.owner_id, r.expires_at,
               last.memory_id, last.type, last.on_expiry
        FROM rolled r
        JOIN LATERAL (
            SELECT memory_id, type, on_expiry FROM memberships m
            WHERE m.data_id = r.data_id AND m.expires_at = r.expires_at
            ORDER BY m.memory_id LIMIT 1
        ) last ON true
        WHERE r.unbounded = 0 AND r.expires_at < now()
        ORDER BY r.expires_at
        LIMIT $2
        """,
        project_id, limit, owner_id,
    )
    return [dict(r) for r in rows]


async def sweep_expired(
    pool: asyncpg.Pool,
    queue,
    principal,
    *,
    project_id: str | None = None,
    owner_id: str | None = None,
    limit: int = 500,
    dry_run: bool = False,
) -> dict:
    """Apply `on_expiry` to everything whose time is up.

    `ttl_seconds` and `on_expiry` were storable, editable and displayed for
    months while **nothing swept**: a `conversation` memory with a one-hour TTL
    was still there a year later, and a retention policy that does not run is a
    compliance claim rather than a control.

    Three policies, and none of them is "delete unconditionally":

    - **`orphan_delete`** — through the ordinary deletion cascade, so an expired
      item gets the same tombstone, blob reclamation, audit record and legal
      hold as any other. Expiry must not become a second erasure path that
      forgets one of them.
    - **`keep_members`** — the memberships go, the item is re-filed into the
      applicable default so it never becomes unreachable, and nothing is erased.
    - **`archive`** — `archived_at` is stamped and the item leaves the working
      set while staying readable, searchable with `include_archived` and
      citable. The same column compaction uses, because *out of the way* and
      *gone* are different states and only one of them is reversible.
    """
    from .audit import record_audit
    from .auth import DATA_WRITE
    from .deletion import request_deletion

    principal.require(DATA_WRITE)
    # Scoped to an owner when the caller is a scheduled pass, because the
    # deletion cascade selects under the caller's *visibility*: a private item
    # is invisible to anyone but its owner, so a single privileged-looking pass
    # would skip precisely the records with the tightest ACL and report a clean
    # sweep. There is no such thing here as an actor who can see everything.
    due = await due_for_expiry(
        pool, project_id=project_id, owner_id=owner_id, limit=limit)
    by_policy: dict[str, list[dict]] = {}
    for row in due:
        by_policy.setdefault(row["on_expiry"], []).append(row)

    result = {
        "considered": len(due),
        "deleted": len(by_policy.get("orphan_delete", [])),
        "archived": len(by_policy.get("archive", [])),
        "refiled": len(by_policy.get("keep_members", [])),
        "applied": not dry_run,
        # Enough to recognise the selection rather than trust it. A sweep that
        # reports only a count cannot be told apart from one that is about to
        # empty a memory nobody meant to expire.
        "samples": [
            {"data_id": r["data_id"], "memory_id": r["memory_id"], "type": r["type"],
             "on_expiry": r["on_expiry"], "expired_at": r["expires_at"].isoformat()}
            for r in due[:8]
        ],
        # A bounded sweep that says nothing about its bound reads as "that was
        # everything", and the next run would look like it did nothing.
        "capped": len(due) == limit,
        "run_id": None,
    }
    if dry_run or not due:
        return result

    to_delete = [r["data_id"] for r in by_policy.get("orphan_delete", [])]
    if to_delete:
        deletion = await request_deletion(
            pool, queue, principal,
            selector={"project_id": project_id, "data_ids": to_delete},
            reason="expired: ttl reached",
        )
        result["run_id"] = deletion.run_id

    async with pool.acquire() as conn, conn.transaction():
        for row in by_policy.get("archive", []):
            await conn.execute(
                "UPDATE data_items SET archived_at = now(), archived_by = 'expiry' "
                "WHERE data_id = $1 AND archived_at IS NULL",
                row["data_id"],
            )
        for row in by_policy.get("keep_members", []):
            item = await conn.fetchrow(
                """
                SELECT d.owner_id, c.scope FROM data_items d
                LEFT JOIN connections c ON c.connection_id = d.connection_id
                WHERE d.data_id = $1
                """,
                row["data_id"],
            )
            if item is None:
                continue
            shared = item["scope"] == "shared"
            landed = await upsert_memory(
                conn, org_id=principal.org_id, project_id=row["project_id"],
                type_name="default",
                memory_key=row["project_id"] if shared else item["owner_id"],
                owner_id=None if shared else item["owner_id"],
                title="Project default" if shared else "My unattached items",
            )
            # The expired memberships go first, or the item is immediately due
            # again on the next sweep and the default membership never saves it.
            await conn.execute(
                """
                DELETE FROM memory_members mm USING memories m
                WHERE mm.data_id = $1 AND m.memory_id = mm.memory_id
                  AND m.memory_id <> $2
                """,
                row["data_id"], landed,
            )
            await add_member(conn, landed, row["data_id"], "routed")

        await record_audit(
            conn, principal, action="memory.expired", project_id=project_id,
            target_type="run", target_id=result["run_id"],
            detail={"considered": result["considered"], "deleted": result["deleted"],
                    "archived": result["archived"], "refiled": result["refiled"]},
        )
    return result


async def sweep_all(pool: asyncpg.Pool, queue, *, limit: int = 500) -> dict:
    """Every project with something due, on the schedule that already runs.

    Grouped by project and swept under a principal scoped to that project's own
    org, rather than one privileged pass over everything: the deletion cascade
    audits whoever asked, and `expiry` acting as nobody in particular would put
    an unattributable actor on a record's erasure -- the one place attribution
    is most needed.
    """
    from .auth import DATA_READ, DATA_WRITE, Principal

    due = await due_for_expiry(pool, limit=limit)
    scopes = {(r["org_id"], r["project_id"], r["owner_id"]) for r in due}
    totals = {"scopes": len(scopes), "considered": 0, "deleted": 0,
              "archived": 0, "refiled": 0}
    for org_id, project_id, item_owner in sorted(scopes):
        principal = Principal(
            user_id=item_owner, org_id=org_id,
            capabilities=frozenset({DATA_WRITE, DATA_READ}),
            project_id=project_id, mode="expiry",
        )
        swept = await sweep_expired(
            pool, queue, principal, project_id=project_id, owner_id=item_owner,
            limit=limit)
        for key in ("considered", "deleted", "archived", "refiled"):
            totals[key] += swept[key]
    return totals


# --------------------------------------------------------------- hierarchy

# A walk has to stop somewhere, and the number has to be visible in what it
# returns. Silently truncating a tree produces the same shape as a tree that
# really is that deep, and the two are very different facts.
TREE_MAX_DEPTH = 12
TREE_MAX_NODES = 200


async def mark_ancestors_stale(conn, memory_id: str, *, reason: str) -> int:
    """A child changed, so every rollup built from it is now wrong.

    Deterministic and free: no model, no queue, and it cannot be mistaken. What
    it does **not** do is recompute -- that is deliberate, and it is the same
    correction the alert system had to make. Recomputing on write turns one bulk
    import into thousands of model calls nobody asked for, so the mark is the
    signal and the recompute is a decision somebody makes.

    Only `derived_from`. A `part_of` parent has no separate state: its members
    *are* its children's members, so there is nothing to be stale.

    Idempotent by construction -- `WHERE stale_since IS NULL` -- so forty
    children moving in one import costs one row write, and `stale_since` keeps
    the first change rather than the last. *How long has this been wrong* is the
    question; *when did it last get worse* is not.
    """
    marked = await conn.fetch(
        """
        WITH RECURSIVE ancestry(memory_id, depth) AS (
            SELECT l.from_memory, 1 FROM memory_links l
            WHERE l.to_memory = $1 AND l.relation = 'derived_from'
            UNION
            SELECT l.from_memory, a.depth + 1
            FROM memory_links l JOIN ancestry a ON l.to_memory = a.memory_id
            WHERE l.relation = 'derived_from' AND a.depth < $3
        )
        UPDATE memories m SET stale_since = now(), stale_reason = $2
        -- DISTINCT because a diamond is the requested shape, not an edge case:
        -- two children rolling into one parent means the parent is reachable by
        -- two paths, and an UPDATE joined against both would be told to write
        -- the same row twice.
        FROM (SELECT DISTINCT memory_id FROM ancestry) a
        WHERE m.memory_id = a.memory_id AND m.stale_since IS NULL
          AND m.deleted_at IS NULL
        RETURNING m.memory_id
        """,
        memory_id, reason, TREE_MAX_DEPTH,
    )
    return len(marked)


async def clear_stale(conn, memory_id: str) -> None:
    """Recomputed, so it is current again."""
    await conn.execute(
        "UPDATE memories SET stale_since = NULL, stale_reason = NULL WHERE memory_id = $1",
        memory_id,
    )


async def tree(
    pool: asyncpg.Pool, principal, memory_id: str, *, relation: str = "part_of",
) -> dict:
    """What this memory contains, and what contains it.

    Both directions, kept apart for the same reason `links_for` keeps them
    apart: *what rolls up into this* and *what does this roll up into* are
    different questions, and one merged list loses the direction that is the
    whole content of the claim.

    `part_of` by default because it is the only relation that means containment
    -- a parent's members *are* its children's members, so there is nothing to
    keep in sync and nothing to go stale. `derived_from` is a generated
    artifact and a different problem: a child changing does not change the
    parent, it makes the parent **wrong**.

    Depth-limited and node-limited, and the limits report themselves.
    """
    from .auth import DATA_READ

    principal.require(DATA_READ)
    if relation not in RELATIONS:
        raise MemoryError(f"relation must be one of {', '.join(RELATIONS)}")
    owned = await pool.fetchval(
        "SELECT 1 FROM memories WHERE memory_id = $1 AND org_id = $2 AND deleted_at IS NULL",
        memory_id, principal.org_id,
    )
    if owned is None:
        raise MemoryError("memory not found", status=404)

    async def walk(direction: str) -> list[dict]:
        # `descendants` walks against the arrow: a child says it is `part_of` a
        # parent, so the parent's children are the rows pointing *at* it.
        start, step_from, step_to = (
            ("to_memory", "to_memory", "from_memory") if direction == "down"
            else ("from_memory", "from_memory", "to_memory")
        )
        rows = await pool.fetch(
            f"""
            WITH RECURSIVE walk(memory_id, depth) AS (
                SELECT l.{step_to}, 1 FROM memory_links l
                WHERE l.{start} = $1 AND l.relation = $2
                UNION
                SELECT l.{step_to}, w.depth + 1
                FROM memory_links l JOIN walk w ON l.{step_from} = w.memory_id
                WHERE l.relation = $2 AND w.depth < $3
            )
            SELECT w.depth, m.memory_id, m.type, m.memory_key, m.title,
                   m.stale_since, m.stale_reason,
                   (SELECT count(*) FROM memory_members mm
                    WHERE mm.memory_id = m.memory_id) AS members
            FROM walk w JOIN memories m ON m.memory_id = w.memory_id
            WHERE m.org_id = $4 AND m.deleted_at IS NULL
            ORDER BY w.depth, m.memory_id
            LIMIT $5
            """,
            memory_id, relation, TREE_MAX_DEPTH, principal.org_id, TREE_MAX_NODES,
        )
        return [dict(r) for r in rows]

    ancestors = await walk("up")
    descendants = await walk("down")
    subject = await pool.fetchrow(
        "SELECT stale_since, stale_reason FROM memories WHERE memory_id = $1", memory_id)
    return {
        "memory_id": memory_id,
        "relation": relation,
        "stale_since": subject["stale_since"] if subject else None,
        "stale_reason": subject["stale_reason"] if subject else None,
        "ancestors": ancestors,
        "descendants": descendants,
        # Distinct memories, because a diamond -- two children of one parent
        # both rolling into a third -- would otherwise count a shared ancestor
        # twice and report more containers than exist.
        "descendant_members": sum(d["members"] for d in descendants),
        "max_depth": TREE_MAX_DEPTH,
        "truncated": len(ancestors) == TREE_MAX_NODES or len(descendants) == TREE_MAX_NODES,
    }


async def contained_memories(pool: asyncpg.Pool, memory_id: str) -> list[str]:
    """A memory and everything `part_of` it, transitively.

    The read side of the hierarchy, used where a scope means *this container
    and what is in it*. Nothing is written and nothing propagates: an alert on
    a parent sees a child's changes because the scope is resolved through the
    graph at match time, not because a parent transition was emitted for every
    child write. The alternative costs an event per level per item, has to be
    kept consistent, and is the mistake the alert system already made once and
    had to undo.
    """
    rows = await pool.fetch(
        """
        WITH RECURSIVE walk(memory_id, depth) AS (
            SELECT $1::text, 0
            UNION
            SELECT l.from_memory, w.depth + 1
            FROM memory_links l JOIN walk w ON l.to_memory = w.memory_id
            WHERE l.relation = 'part_of' AND w.depth < $2
        )
        SELECT DISTINCT memory_id FROM walk
        """,
        memory_id, TREE_MAX_DEPTH,
    )
    return [r["memory_id"] for r in rows]
