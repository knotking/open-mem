"""Memories -- typed containers with a lifecycle.

A memory answers "how long does this matter?". A case answers "what is this
about?". A conversation expires; a patient does not.
"""

from __future__ import annotations

import pytest

from memdog.contracts import Inline, MemoryRef, WriteItem, WriteRequest, WriteOptions
from memdog import memories
from memdog.memories import effective_expiry, ensure_shipped_types
from memdog.retrieval import item_memories, list_memories, memory_members
from memdog.write import write_items

pytestmark = pytest.mark.asyncio


async def _write(pool, queue, blobs, settings, actor, producer_id, items):
    return await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=producer_id, items=items,
                        options=WriteOptions(enrich=True)),
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


# ------------------------------------------------- create, then fill


async def test_a_memory_can_be_created_before_anything_is_in_it(
    pool, tenant, principal_for
):
    """The producer's path is writing an item with a key. This is the person's
    path: make the container, then fill it deliberately."""
    from memdog.memories import create_memory

    actor = await principal_for(tenant.api_key)
    memory = await create_memory(
        pool, actor, project_id=tenant.project_id, type_name="session",
        memory_key="onboarding-review", title="Onboarding review",
    )
    assert memory["memory_id"].startswith("mem_")

    listed = await list_memories(pool, actor, tenant.project_id)
    mine = next(m for m in listed if m["memory_id"] == memory["memory_id"])
    # Empty, but visible to its owner -- an empty container you cannot see is
    # a container you cannot fill.
    assert mine["members"] == 0 and mine["title"] == "Onboarding review"


async def test_an_unknown_type_is_refused(pool, tenant, principal_for):
    from memdog.memories import MemoryError, create_memory

    actor = await principal_for(tenant.api_key)
    with pytest.raises(MemoryError) as exc:
        await create_memory(pool, actor, project_id=tenant.project_id,
                            type_name="not-a-type")
    assert exc.value.status == 404


async def test_existing_items_can_be_added_and_removed_after_the_write(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Membership is mutable after write -- the whole point of a container you
    can organise."""
    from memdog.memories import add_members, create_memory, remove_member

    actor = await principal_for(tenant.api_key)
    written = await _write(
        pool, queue, blobs, settings, actor, tenant.producer_id,
        [WriteItem(external_id="loose-1", content=Inline(text="An existing item."))],
    )
    data_id = written.results[0].data_id

    memory = await create_memory(
        pool, actor, project_id=tenant.project_id, type_name="factual",
    ) if False else await create_memory(
        pool, actor, project_id=tenant.project_id, type_name="session",
        memory_key="curated",
    )
    result = await add_members(pool, actor, memory["memory_id"], [data_id])
    assert result["added"] == [data_id] and result["skipped"] == []

    members = await memory_members(pool, actor, memory["memory_id"])
    assert [m["data_id"] for m in members] == [data_id]
    assert members[0]["added_by"] == "explicit"     # a person did it, not a rule

    removed = await remove_member(pool, actor, memory["memory_id"], data_id)
    assert removed["deleted"] is False              # unmapping is not deletion
    assert await memory_members(pool, actor, memory["memory_id"]) == []
    # The item itself is untouched.
    assert await pool.fetchval(
        "SELECT content_text FROM data_items WHERE data_id = $1", data_id
    ) == "An existing item."


async def test_removing_the_last_membership_lands_the_item_in_default(
    pool, queue, blobs, settings, tenant, principal_for
):
    """An item in no memory is invisible from the memory side entirely, which
    is the hole `default` exists to close."""
    from memdog.memories import add_members, create_memory, remove_member

    actor = await principal_for(tenant.api_key)
    written = await _write(
        pool, queue, blobs, settings, actor, tenant.producer_id,
        [WriteItem(external_id="curated-1", content=Inline(text="Curated."),
                   memory=MemoryRef(key="thread-x", type="conversation"))],
    )
    data_id = written.results[0].data_id

    result = await remove_member(
        pool, actor,
        (await item_memories(pool, actor, data_id))["memberships"][0]["memory_id"],
        data_id,
    )
    assert result["landed_in"] is not None

    memberships = (await item_memories(pool, actor, data_id))["memberships"]
    assert [m["type"] for m in memberships] == ["default"]
    assert memberships[0]["memory_key"] == tenant.user_id


async def test_adding_an_item_you_cannot_see_is_not_an_oracle(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Adding to a memory must not become a way to observe that something
    exists."""
    from memdog.auth import DATA_READ, DATA_WRITE, issue_key
    from memdog.bootstrap import create_user
    from memdog.memories import add_members, create_memory

    owner = await principal_for(tenant.api_key)
    written = await _write(
        pool, queue, blobs, settings, owner, tenant.producer_id,
        [WriteItem(external_id="hidden-1", content=Inline(text="Private."))],
    )
    other_id = await create_user(pool, "curator@example.com")
    await pool.execute(
        "INSERT INTO memberships (user_id, org_id, role) VALUES ($1, $2, 'member')",
        other_id, tenant.org_id,
    )
    token = await issue_key(pool, user_id=other_id, org_id=tenant.org_id,
                            capabilities=[DATA_READ, DATA_WRITE])
    curator = await principal_for(token)

    memory = await create_memory(
        pool, curator, project_id=tenant.project_id, type_name="session",
        memory_key="theirs",
    )
    result = await add_members(pool, curator, memory["memory_id"],
                               [written.results[0].data_id])
    # Skipped and named, which is indistinguishable from a typo -- deliberately.
    assert result["added"] == []
    assert result["skipped"] == [written.results[0].data_id]


async def test_retyping_previews_what_a_shorter_ttl_would_expire(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Moving to a shorter TTL can expire members, and finding that out
    afterwards is not acceptable."""
    from memdog.memories import create_type, retype_memory

    actor = await principal_for(tenant.api_key)
    await create_type(pool, actor, project_id=tenant.project_id, name="ephemeral",
                      ttl_seconds=1, on_expiry="orphan_delete")
    written = await _write(
        pool, queue, blobs, settings, actor, tenant.producer_id,
        [WriteItem(external_id="ttl-1", content=Inline(text="Held briefly."),
                   memory=MemoryRef(key="short", type="conversation"))],
    )
    memory_id = (await item_memories(pool, actor, written.results[0].data_id))[
        "memberships"][0]["memory_id"]

    await pool.execute(
        "UPDATE memory_members SET added_at = now() - interval '1 hour' WHERE memory_id = $1",
        memory_id,
    )
    preview = await retype_memory(pool, actor, memory_id, "ephemeral", preview=True)
    assert preview["applied"] is False and preview["would_expire"] == 1

    applied = await retype_memory(pool, actor, memory_id, "ephemeral")
    assert applied["applied"] is True
    # The type is mutable and not encoded in the id: same memory, new lifecycle.
    assert await pool.fetchval(
        "SELECT type FROM memories WHERE memory_id = $1", memory_id
    ) == "ephemeral"


async def test_a_custom_type_is_a_name_a_ttl_and_a_policy(pool, tenant, principal_for):
    from memdog.memories import create_type, list_types

    actor = await principal_for(tenant.api_key)
    await create_type(pool, actor, project_id=tenant.project_id, name="procedural",
                      ttl_seconds=None, on_expiry="keep_members")
    types = {t["name"]: t for t in await list_types(pool, tenant.project_id)}
    assert types["procedural"]["ttl_seconds"] is None
    assert types["procedural"]["on_expiry"] == "keep_members"


# ------------------------------------------------------ deleting one


async def test_deleting_a_memory_never_deletes_an_item_another_memory_holds(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Deleting a member because one of its containers went away destroys data
    a permanent memory still depends on."""
    from memdog.memories import add_members, create_memory, delete_memory

    actor = await principal_for(tenant.api_key)
    written = await _write(
        pool, queue, blobs, settings, actor, tenant.producer_id,
        [WriteItem(external_id="shared-item", content=Inline(text="Held twice."),
                   memory=MemoryRef(key="thread-a", type="conversation"))],
    )
    data_id = written.results[0].data_id
    keeper = await create_memory(pool, actor, project_id=tenant.project_id,
                                  type_name="session", memory_key="keeper")
    await add_members(pool, actor, keeper["memory_id"], [data_id])

    conversation = next(
        m for m in (await item_memories(pool, actor, data_id))["memberships"]
        if m["type"] == "conversation"
    )
    preview = await delete_memory(pool, actor, queue, conversation["memory_id"], preview=True)
    assert preview["would_delete"] == 0
    assert preview["retained_because_held_elsewhere"] == 1

    await delete_memory(pool, actor, queue, conversation["memory_id"])
    # The item survives, still held by the other memory.
    assert await pool.fetchval(
        "SELECT deleted_at IS NULL FROM data_items WHERE data_id = $1", data_id
    )
    remaining = (await item_memories(pool, actor, data_id))["memberships"]
    assert [m["memory_id"] for m in remaining] == [keeper["memory_id"]]


async def test_orphan_delete_erases_what_only_this_memory_held(
    pool, queue, blobs, settings, tenant, principal_for
):
    """And it goes through the ordinary cascade, not a second implementation."""
    from memdog.memories import delete_memory

    actor = await principal_for(tenant.api_key)
    written = await _write(
        pool, queue, blobs, settings, actor, tenant.producer_id,
        [WriteItem(external_id="sole-item", content=Inline(text="Held once."),
                   memory=MemoryRef(key="thread-b", type="conversation"))],
    )
    data_id = written.results[0].data_id
    memory_id = (await item_memories(pool, actor, data_id))["memberships"][0]["memory_id"]

    result = await delete_memory(pool, actor, queue, memory_id)
    assert result["deleted_items"] == 1
    assert result["deletion_run_id"] is not None    # the shared run entity

    row = await pool.fetchrow(
        "SELECT deleted_at, deletion_reason FROM data_items WHERE data_id = $1", data_id
    )
    assert row["deleted_at"] is not None
    assert "orphan_delete" in row["deletion_reason"]


async def test_keep_members_refiles_orphans_instead_of_deleting_them(
    pool, queue, blobs, settings, tenant, principal_for
):
    from memdog.memories import create_memory, create_type, delete_memory

    actor = await principal_for(tenant.api_key)
    await create_type(pool, actor, project_id=tenant.project_id, name="kept",
                      ttl_seconds=None, on_expiry="keep_members")
    written = await _write(
        pool, queue, blobs, settings, actor, tenant.producer_id,
        [WriteItem(external_id="kept-1", content=Inline(text="Survives."),
                   memory=MemoryRef(key="kept-thread", type="kept"))],
    )
    data_id = written.results[0].data_id
    memory_id = (await item_memories(pool, actor, data_id))["memberships"][0]["memory_id"]

    result = await delete_memory(pool, actor, queue, memory_id)
    assert result["deleted_items"] == 0
    assert await pool.fetchval(
        "SELECT deleted_at IS NULL FROM data_items WHERE data_id = $1", data_id
    )
    # Re-filed rather than left unattached.
    memberships = (await item_memories(pool, actor, data_id))["memberships"]
    assert [m["type"] for m in memberships] == ["default"]


async def test_the_default_memory_cannot_be_deleted(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Removing it would recreate the hole it exists to close on the very next
    write."""
    from memdog.memories import MemoryError, delete_memory

    actor = await principal_for(tenant.api_key)
    written = await _write(
        pool, queue, blobs, settings, actor, tenant.producer_id,
        [WriteItem(external_id="unattached-x", content=Inline(text="No memory asked for."))],
    )
    memory_id = (await item_memories(
        pool, actor, written.results[0].data_id
    ))["memberships"][0]["memory_id"]

    with pytest.raises(MemoryError) as exc:
        await delete_memory(pool, actor, queue, memory_id)
    assert exc.value.status == 409


# --- links -------------------------------------------------------------------


async def _two_memories(pool, tenant):
    from memdog.auth import ApiKeyVerifier

    principal = await ApiKeyVerifier(pool).verify(tenant.api_key)
    a = await memories.create_memory(
        pool, principal, project_id=tenant.project_id, type_name="default",
        memory_key="today")
    b = await memories.create_memory(
        pool, principal, project_id=tenant.project_id, type_name="default",
        memory_key="yesterday")
    return principal, a["memory_id"], b["memory_id"]


async def test_a_link_is_directional_and_readable_both_ways(pool, tenant):
    """`memory_links` was declared in the first memories migration and reached
    by nothing — no function, no endpoint, no reader. A memory could never be
    said to continue another, which is most of what the relations exist for."""
    principal, today, yesterday = await _two_memories(pool, tenant)

    await memories.link(pool, principal, from_memory=today,
                        to_memory=yesterday, relation="continues")

    forward = await memories.links_for(pool, principal, today)
    assert [l["relation"] for l in forward["outgoing"]] == ["continues"]
    assert forward["incoming"] == []

    # The same link from the other end, and the direction is preserved: today
    # continues yesterday, never the reverse.
    backward = await memories.links_for(pool, principal, yesterday)
    assert backward["outgoing"] == []
    assert [l["from_memory"] for l in backward["incoming"]] == [today]


async def test_an_inference_never_overwrites_a_statement(pool, tenant):
    """The distinction case membership already draws, for the same reason: a
    guess that cannot be told from a claim quietly becomes one."""
    principal, summary, source = await _two_memories(pool, tenant)

    await memories.link(pool, principal, from_memory=summary, to_memory=source,
                        relation="derived_from", created_by="explicit")
    # An agent re-asserting it must not demote what a person stated.
    await memories.link(pool, principal, from_memory=summary, to_memory=source,
                        relation="derived_from", created_by="agent",
                        confidence=0.6)

    links = (await memories.links_for(pool, principal, summary))["outgoing"]
    assert links[0]["created_by"] == "explicit"
    assert links[0]["confidence"] is None


async def test_a_person_promotes_what_an_agent_guessed(pool, tenant):
    principal, summary, source = await _two_memories(pool, tenant)

    await memories.link(pool, principal, from_memory=summary, to_memory=source,
                        relation="derived_from", created_by="agent", confidence=0.6)
    await memories.link(pool, principal, from_memory=summary, to_memory=source,
                        relation="derived_from", created_by="explicit")

    links = (await memories.links_for(pool, principal, summary))["outgoing"]
    assert links[0]["created_by"] == "explicit"
    # The confidence goes with the guess it belonged to.
    assert links[0]["confidence"] is None


async def test_an_explicit_link_refuses_a_confidence(pool, tenant):
    """A statement is not 80% true, and a number here would invite a reader to
    weigh it the way they weigh a guess."""
    principal, a, b = await _two_memories(pool, tenant)
    with pytest.raises(memories.MemoryError):
        await memories.link(pool, principal, from_memory=a, to_memory=b,
                            relation="about", created_by="explicit", confidence=0.9)


async def test_a_memory_cannot_be_linked_to_itself(pool, tenant):
    principal, a, _ = await _two_memories(pool, tenant)
    with pytest.raises(memories.MemoryError):
        await memories.link(pool, principal, from_memory=a, to_memory=a,
                            relation="part_of")


async def test_an_unknown_relation_is_refused(pool, tenant):
    principal, a, b = await _two_memories(pool, tenant)
    with pytest.raises(memories.MemoryError):
        await memories.link(pool, principal, from_memory=a, to_memory=b,
                            relation="reminds_me_of")


async def test_linking_across_organizations_is_not_found(pool, tenant, other_tenant):
    """"Not found" rather than "not yours": the second sentence confirms it
    exists."""
    from memdog.auth import ApiKeyVerifier

    principal, mine, _ = await _two_memories(pool, tenant)
    theirs_principal = await ApiKeyVerifier(pool).verify(other_tenant.api_key)
    theirs = await memories.create_memory(
        pool, theirs_principal, project_id=other_tenant.project_id,
        type_name="default", memory_key="not-yours")

    with pytest.raises(memories.MemoryError) as exc:
        await memories.link(pool, principal, from_memory=mine,
                            to_memory=theirs["memory_id"], relation="about")
    assert exc.value.status == 404


async def test_unlinking(pool, tenant):
    principal, a, b = await _two_memories(pool, tenant)
    await memories.link(pool, principal, from_memory=a, to_memory=b, relation="about")
    await memories.unlink(pool, principal, from_memory=a, to_memory=b, relation="about")
    assert (await memories.links_for(pool, principal, a))["outgoing"] == []

    with pytest.raises(memories.MemoryError):
        await memories.unlink(pool, principal, from_memory=a, to_memory=b,
                              relation="about")


async def test_a_link_is_audited(pool, tenant):
    principal, a, b = await _two_memories(pool, tenant)
    await memories.link(pool, principal, from_memory=a, to_memory=b,
                        relation="supersedes")
    row = await pool.fetchrow(
        "SELECT action, detail FROM audit_events WHERE action = 'memory.linked'"
    )
    assert row["detail"]["relation"] == "supersedes"
    assert row["detail"]["to_memory"] == b
