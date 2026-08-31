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
