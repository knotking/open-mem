"""Memories that contain memories, and a change that is visible above.

`memory_links` has held `part_of` since 0006 and both directions have been
indexed since 0029, so nothing here needs a new table. What did not exist was
anything that *reads* the hierarchy: an alert on a parent never saw a child's
changes, and a scope that silently means less than it says is the same failure
as one that silently means more.

Nothing propagates. The graph is walked where it is used, which costs one query
at match time instead of an event per level per item that then has to be kept
consistent.
"""

from __future__ import annotations

import pytest

from memdog import memories
from memdog.contracts import Inline, MemoryRef, WriteItem, WriteOptions, WriteRequest
from memdog.memories import MemoryError, contained_memories, link, tree
from memdog.write import write_items

pytestmark = pytest.mark.asyncio


async def _memory(pool, actor, tenant, key: str) -> str:
    created = await memories.create_memory(
        pool, actor, project_id=tenant.project_id, type_name="factual", memory_key=key)
    return created["memory_id"]


async def _seed_types(pool, actor, tenant) -> None:
    await memories.create_type(pool, actor, project_id=tenant.project_id,
                               name="factual", ttl_seconds=None)


async def test_a_link_that_would_close_a_cycle_is_refused(
    pool, tenant, principal_for
):
    """Refused at the edge that closes it rather than survived by the readers:
    both the tree and the alert scope walk `part_of` recursively, and a graph
    saying a memory contains its own container has no meaning to protect."""
    actor = await principal_for(tenant.api_key)
    await _seed_types(pool, actor, tenant)
    a = await _memory(pool, actor, tenant, "a")
    b = await _memory(pool, actor, tenant, "b")
    c = await _memory(pool, actor, tenant, "c")

    await link(pool, actor, from_memory=a, to_memory=b, relation="part_of")
    await link(pool, actor, from_memory=b, to_memory=c, relation="part_of")
    with pytest.raises(MemoryError) as exc:
        await link(pool, actor, from_memory=c, to_memory=a, relation="part_of")
    assert exc.value.status == 409


async def test_a_cycle_in_one_relation_does_not_forbid_another(
    pool, tenant, principal_for
):
    """The relations are independent. A memory derived from another can also be
    part of it, and refusing that would be refusing a true statement to prevent
    a loop that does not exist."""
    actor = await principal_for(tenant.api_key)
    await _seed_types(pool, actor, tenant)
    a = await _memory(pool, actor, tenant, "a")
    b = await _memory(pool, actor, tenant, "b")

    await link(pool, actor, from_memory=a, to_memory=b, relation="part_of")
    await link(pool, actor, from_memory=b, to_memory=a, relation="derived_from")


async def test_the_tree_reads_both_directions_and_reports_its_limits(
    pool, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    await _seed_types(pool, actor, tenant)
    parent = await _memory(pool, actor, tenant, "roll-up")
    child = await _memory(pool, actor, tenant, "child")
    grandchild = await _memory(pool, actor, tenant, "grandchild")
    await link(pool, actor, from_memory=child, to_memory=parent, relation="part_of")
    await link(pool, actor, from_memory=grandchild, to_memory=child, relation="part_of")

    above = await tree(pool, actor, grandchild)
    assert [a["memory_id"] for a in above["ancestors"]] == [child, parent]
    assert above["descendants"] == []

    below = await tree(pool, actor, parent)
    assert {d["memory_id"] for d in below["descendants"]} == {child, grandchild}
    assert below["ancestors"] == []
    assert below["truncated"] is False
    assert below["max_depth"] == memories.TREE_MAX_DEPTH


async def test_an_alert_on_the_parent_sees_a_write_to_a_child(
    pool, queue, blobs, settings, tenant, principal_for
):
    """The behaviour the whole slice exists for, asserted end to end: two
    memories converge into a third, and a change in either is visible above."""
    from memdog.alerts import in_scope

    actor = await principal_for(tenant.api_key)
    await _seed_types(pool, actor, tenant)
    parent = await _memory(pool, actor, tenant, "programme")
    child = await _memory(pool, actor, tenant, "workstream")
    await link(pool, actor, from_memory=child, to_memory=parent, relation="part_of")

    written = await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(
            producer_id=tenant.producer_id,
            items=[WriteItem(external_id="in-child", content=Inline(text="a change"),
                             memory=MemoryRef(type="factual", key="workstream"))],
            options=WriteOptions(enrich=False),
        ),
    )
    data_id = written.results[0].data_id
    candidates = [({"sequence": 1, "data_id": data_id}, {})]

    # Scoped to the child: it is in there directly.
    assert await in_scope(pool, {"memory_id": child}, candidates) == {1}
    # Scoped to the parent: it is in there through the hierarchy, which is the
    # part that did not work.
    assert await in_scope(pool, {"memory_id": parent}, candidates) == {1}
    # And a sibling still sees nothing -- widening the scope must not widen it
    # to everything.
    sibling = await _memory(pool, actor, tenant, "other-workstream")
    assert await in_scope(pool, {"memory_id": sibling}, candidates) == set()


async def test_containment_includes_the_memory_itself(pool, tenant, principal_for):
    """Otherwise a scope on a leaf memory would resolve to nothing at all --
    the recursive version failing where the single-level one worked."""
    actor = await principal_for(tenant.api_key)
    await _seed_types(pool, actor, tenant)
    only = await _memory(pool, actor, tenant, "alone")
    assert await contained_memories(pool, only) == [only]


# ------------------------------------------------------- derived, and stale


async def _derived_rollup(pool, actor, tenant, child_key: str, parent_key: str):
    """A rollup memory, and the child it is built from."""
    child = await _memory(pool, actor, tenant, child_key)
    parent = await _memory(pool, actor, tenant, parent_key)
    await link(pool, actor, from_memory=parent, to_memory=child, relation="derived_from")
    return child, parent


async def test_a_child_changing_marks_the_rollup_stale(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Deterministic before probabilistic: the mark costs nothing and cannot be
    wrong, and recomputing is a decision somebody makes rather than something a
    write triggers -- the correction the alert system already had to make."""
    from memdog.retrieval import list_memories

    actor = await principal_for(tenant.api_key)
    await _seed_types(pool, actor, tenant)
    child, parent = await _derived_rollup(pool, actor, tenant, "notes", "digest")

    await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(
            producer_id=tenant.producer_id,
            items=[WriteItem(external_id="note-1", content=Inline(text="a change"),
                             memory=MemoryRef(type="factual", key="notes"))],
            options=WriteOptions(enrich=False),
        ),
    )

    rows = {m["memory_id"]: m for m in await list_memories(pool, actor, tenant.project_id)}
    assert rows[parent]["stale_since"] is not None
    assert rows[parent]["stale_reason"] == "a member was added"
    # The child itself is not stale: it *is* the change, not something derived
    # from it.
    assert rows[child]["stale_since"] is None


async def test_marking_is_idempotent_and_keeps_the_first_change(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A bulk import moving forty children must not enqueue forty recomputes.

    And `stale_since` answers *how long has this been wrong*, so it keeps the
    first change rather than the most recent one.
    """
    actor = await principal_for(tenant.api_key)
    await _seed_types(pool, actor, tenant)
    child, parent = await _derived_rollup(pool, actor, tenant, "notes", "digest")

    async def _add(external_id: str):
        await write_items(
            pool, queue, blobs, settings, actor,
            WriteRequest(
                producer_id=tenant.producer_id,
                items=[WriteItem(external_id=external_id, content=Inline(text=external_id),
                                 memory=MemoryRef(type="factual", key="notes"))],
                options=WriteOptions(enrich=False),
            ),
        )

    await _add("first")
    first = await pool.fetchval(
        "SELECT stale_since FROM memories WHERE memory_id = $1", parent)
    await _add("second")
    await _add("third")
    assert await pool.fetchval(
        "SELECT stale_since FROM memories WHERE memory_id = $1", parent) == first


async def test_a_diamond_marks_the_shared_ancestor_once(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Two children rolling into one parent is the requested shape, not an edge
    case, so a memory reachable by two paths must not be written twice."""
    actor = await principal_for(tenant.api_key)
    await _seed_types(pool, actor, tenant)
    left = await _memory(pool, actor, tenant, "left")
    right = await _memory(pool, actor, tenant, "right")
    parent = await _memory(pool, actor, tenant, "both")
    await link(pool, actor, from_memory=parent, to_memory=left, relation="derived_from")
    await link(pool, actor, from_memory=parent, to_memory=right, relation="derived_from")

    from memdog.memories import mark_ancestors_stale

    async with pool.acquire() as conn:
        assert await mark_ancestors_stale(conn, left, reason="a member was added") == 1
        # Already marked, so the second child costs nothing.
        assert await mark_ancestors_stale(conn, right, reason="a member was added") == 0


async def test_part_of_never_goes_stale(pool, tenant, principal_for):
    """A container has no separate state to keep in sync: its members *are* its
    children's members. Marking it would be a badge nothing can clear."""
    from memdog.memories import mark_ancestors_stale

    actor = await principal_for(tenant.api_key)
    await _seed_types(pool, actor, tenant)
    child = await _memory(pool, actor, tenant, "workstream")
    parent = await _memory(pool, actor, tenant, "programme")
    await link(pool, actor, from_memory=child, to_memory=parent, relation="part_of")

    async with pool.acquire() as conn:
        assert await mark_ancestors_stale(conn, child, reason="a member was added") == 0


async def test_compacting_a_parent_folds_what_its_children_hold(
    pool, queue, blobs, settings, tenant, principal_for, extractor
):
    """A `part_of` parent has no members of its own, so compacting one used to
    consider nothing and report a successful run over zero records."""
    from memdog.compaction import run

    actor = await principal_for(tenant.api_key)
    await _seed_types(pool, actor, tenant)
    child = await _memory(pool, actor, tenant, "workstream")
    parent = await _memory(pool, actor, tenant, "programme")
    await link(pool, actor, from_memory=child, to_memory=parent, relation="part_of")

    for i in range(3):
        await write_items(
            pool, queue, blobs, settings, actor,
            WriteRequest(
                producer_id=tenant.producer_id,
                items=[WriteItem(external_id=f"w-{i}", content=Inline(text="the same text"),
                                 memory=MemoryRef(type="factual", key="workstream"))],
                options=WriteOptions(enrich=False),
            ),
        )

    preview = await run(pool, actor, memory_id=parent, algorithm="dedupe", mode="dry")
    assert preview["considered"] == 3, "the parent sees what its children hold"


async def test_a_recompute_clears_the_flag_and_a_preview_does_not(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A preview reports what *would* happen. One that marked the rollup fresh
    would be a preview with a side effect."""
    from memdog.compaction import run

    actor = await principal_for(tenant.api_key)
    await _seed_types(pool, actor, tenant)
    child, parent = await _derived_rollup(pool, actor, tenant, "notes", "digest")
    await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(
            producer_id=tenant.producer_id,
            items=[WriteItem(external_id="note-1", content=Inline(text="a change"),
                             memory=MemoryRef(type="factual", key="notes"))],
            options=WriteOptions(enrich=False),
        ),
    )
    assert await pool.fetchval(
        "SELECT stale_since FROM memories WHERE memory_id = $1", parent) is not None

    await run(pool, actor, memory_id=parent, algorithm="dedupe", mode="dry")
    assert await pool.fetchval(
        "SELECT stale_since FROM memories WHERE memory_id = $1", parent) is not None

    await run(pool, actor, memory_id=parent, algorithm="dedupe", mode="live")
    assert await pool.fetchval(
        "SELECT stale_since FROM memories WHERE memory_id = $1", parent) is None


async def test_a_compaction_job_on_a_parent_counts_what_it_would_fold(
    pool, queue, blobs, settings, tenant, principal_for
):
    """The card said "0 members" beside a run that would consider three.

    A count next to a job that disagrees with what the job does is worse than
    no count: the card is what people read before deciding whether to run it.
    """
    from memdog.compaction import create_job, list_jobs

    actor = await principal_for(tenant.api_key)
    await _seed_types(pool, actor, tenant)
    child = await _memory(pool, actor, tenant, "workstream")
    parent = await _memory(pool, actor, tenant, "programme")
    await link(pool, actor, from_memory=child, to_memory=parent, relation="part_of")

    for i in range(3):
        await write_items(
            pool, queue, blobs, settings, actor,
            WriteRequest(
                producer_id=tenant.producer_id,
                items=[WriteItem(external_id=f"c-{i}", content=Inline(text="repeated"),
                                 memory=MemoryRef(type="factual", key="workstream"))],
                options=WriteOptions(enrich=False),
            ),
        )

    await create_job(pool, actor, project_id=tenant.project_id, memory_id=parent,
                     algorithm="dedupe", name="programme fold")
    jobs = await list_jobs(pool, actor, tenant.project_id)
    assert jobs[0]["members"] == 3
