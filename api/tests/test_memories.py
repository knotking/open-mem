"""Memories -- typed containers with a lifecycle.

A memory answers "how long does this matter?". A case answers "what is this
about?". A conversation expires; a patient does not.
"""

from __future__ import annotations

import pytest

from memdog.contracts import Inline, MemoryRef, WriteItem, WriteRequest
from memdog.memories import effective_expiry, ensure_shipped_types
from memdog.retrieval import item_memories, list_memories, memory_members
from memdog.write import write_items

pytestmark = pytest.mark.asyncio


async def _write(pool, queue, blobs, settings, actor, producer_id, items):
    return await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=producer_id, items=items),
    )


async def test_nothing_is_orphaned_and_the_default_is_per_user(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A project-wide default is a junk drawer shared by strangers."""
    actor = await principal_for(tenant.api_key)
    written = await _write(
        pool, queue, blobs, settings, actor, tenant.producer_id,
        [WriteItem(external_id="unattached-1", content=Inline(text="No memory was asked for."))],
    )
    result = written.results[0]
    # The response reports where it went; the caller should not have to query.
    assert len(result.memories) == 1

    memberships = (await item_memories(pool, actor, result.data_id))["memberships"]
    assert memberships[0]["type"] == "default"
    assert memberships[0]["added_by"] == "routed"
    # Keyed on the user, so it is that person's container, not the project's.
    assert memberships[0]["memory_key"] == tenant.user_id


async def test_a_shared_connection_defaults_to_the_projects_memory(
    pool, queue, blobs, settings, principal_for
):
    """The default memory follows the same scope as the ACL, so account
    deletion does not strand team data in a departed user's container."""
    from memdog.bootstrap import bootstrap_tenant

    shared = await bootstrap_tenant(pool, org_name="team-co", email="team@example.com",
                                    connection_scope="shared")
    actor = await principal_for(shared.api_key)
    written = await _write(
        pool, queue, blobs, settings, actor, shared.producer_id,
        [WriteItem(external_id="team-1", content=Inline(text="A team document."))],
    )
    memberships = (await item_memories(pool, actor, written.results[0].data_id))["memberships"]
    assert memberships[0]["memory_key"] == shared.project_id


async def test_the_natural_key_collects_a_thread_without_session_state(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A producer writing many messages from one thread collects them into one
    memory without pre-creating anything."""
    actor = await principal_for(tenant.api_key)
    for i in range(3):
        await _write(
            pool, queue, blobs, settings, actor, tenant.producer_id,
            [WriteItem(external_id=f"msg-{i}", content=Inline(text=f"Message {i}"),
                       memory=MemoryRef(key="thread-8841", type="conversation"))],
        )
    memories = await list_memories(pool, actor, tenant.project_id)
    conversation = [m for m in memories if m["type"] == "conversation"]
    assert len(conversation) == 1              # one memory, not three
    assert conversation[0]["members"] == 3
    assert conversation[0]["ttl_seconds"] == 3600

    members = await memory_members(pool, actor, conversation[0]["memory_id"])
    assert len(members) == 3
    assert {m["added_by"] for m in members} == {"explicit"}


async def test_effective_expiry_is_the_longest_ttl_not_the_shortest(
    pool, queue, blobs, settings, tenant, principal_for
):
    """An item in an hour-long conversation and a permanent factual memory does
    not expire in an hour. Taking the earliest deletes data a permanent memory
    still depends on -- the orphan_delete bug in another form.
    """
    from memdog.ids import new_id
    from memdog.memories import add_member, upsert_memory

    actor = await principal_for(tenant.api_key)
    written = await _write(
        pool, queue, blobs, settings, actor, tenant.producer_id,
        [WriteItem(external_id="both-1", content=Inline(text="Held by two memories."),
                   memory=MemoryRef(key="thread-1", type="conversation"))],
    )
    data_id = written.results[0].data_id

    await pool.execute(
        """
        INSERT INTO memory_types (type_id, org_id, project_id, name, ttl_seconds, on_expiry)
        VALUES ($1, $2, $3, 'factual', NULL, 'keep_members')
        """,
        new_id("mty"), tenant.org_id, tenant.project_id,
    )
    async with pool.acquire() as conn:
        memory_id = await upsert_memory(
            conn, org_id=tenant.org_id, project_id=tenant.project_id,
            type_name="factual", memory_key="permanent-facts",
        )
        await add_member(conn, memory_id, data_id, "explicit")

    state = await item_memories(pool, actor, data_id)
    assert len(state["memberships"]) == 2
    assert any(m["expires_at"] is not None for m in state["memberships"])
    # A null TTL wins outright.
    assert state["effective_expiry"] is None


async def test_effective_expiry_is_computed_not_stored():
    """Any stored answer is wrong the moment someone adds or removes a member,
    which is why there is no expires_at column to read."""
    import datetime as dt

    early = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    late = dt.datetime(2027, 1, 1, tzinfo=dt.timezone.utc)
    assert effective_expiry([{"expires_at": early}, {"expires_at": late}]) == late
    assert effective_expiry([{"expires_at": early}, {"expires_at": None}]) is None
    assert effective_expiry([]) is None


async def test_a_memory_you_can_see_may_hold_items_you_cannot(
    pool, queue, blobs, settings, tenant, principal_for
):
    """The ACL applies to members, not the container, so counts must reflect
    what the caller could actually open."""
    from memdog.auth import DATA_READ, issue_key
    from memdog.bootstrap import create_user

    owner = await principal_for(tenant.api_key)
    await _write(
        pool, queue, blobs, settings, owner, tenant.producer_id,
        [WriteItem(external_id="private-mem-1", content=Inline(text="Private."),
                   memory=MemoryRef(key="shared-thread", type="conversation"))],
    )

    other_id = await create_user(pool, "outsider2@example.com")
    await pool.execute(
        "INSERT INTO memberships (user_id, org_id, role) VALUES ($1, $2, 'member')",
        other_id, tenant.org_id,
    )
    token = await issue_key(pool, user_id=other_id, org_id=tenant.org_id,
                            capabilities=[DATA_READ])
    outsider = await principal_for(token)

    # Not listed at all, rather than listed as empty: a count of zero still
    # discloses that the container exists, and `shared-thread` is meaningful.
    theirs = await list_memories(pool, outsider, tenant.project_id)
    assert [m for m in theirs if m["type"] == "conversation"] == []

    # The owner still sees it, with its real count.
    mine = await list_memories(pool, owner, tenant.project_id)
    conversation = [m for m in mine if m["type"] == "conversation"]
    assert conversation and conversation[0]["members"] == 1

    # And addressing it directly reveals nothing either.
    assert await memory_members(pool, outsider, conversation[0]["memory_id"]) == []


async def test_shipped_types_exist_so_a_fresh_project_works(pool, tenant):
    await ensure_shipped_types(pool, tenant.project_id, tenant.org_id)
    rows = await pool.fetch(
        "SELECT name, ttl_seconds, on_expiry FROM memory_types WHERE project_id = $1",
        tenant.project_id,
    )
    shipped = {r["name"]: (r["ttl_seconds"], r["on_expiry"]) for r in rows}
    assert shipped["default"] == (None, "keep_members")
    assert shipped["conversation"] == (3600, "orphan_delete")
    assert shipped["session"][1] == "archive"
