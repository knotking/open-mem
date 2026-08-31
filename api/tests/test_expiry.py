"""TTL, finally enforced.

`memory_types.ttl_seconds` and `on_expiry` were storable, editable, displayed
and computed for months while **nothing swept**: a `conversation` memory with a
one-hour TTL was still there a year later, `orphan_delete` and `archive` had
never run once, and a retention policy that does not run is a compliance claim
rather than a control.

The rule under test throughout is the one `effective_expiry` already stated:
**the latest expiry wins, and unbounded wins outright.** An item in an
hour-long conversation and in a permanent factual memory is not an hour-old
item, and expiring it per membership is the `orphan_delete` bug in another form.
"""

from __future__ import annotations

import pytest

from memdog import memories
from memdog.contracts import Inline, MemoryRef, WriteItem, WriteOptions, WriteRequest
from memdog.memories import due_for_expiry, sweep_all, sweep_expired
from memdog.retrieval import get_item
from memdog.write import write_items

pytestmark = pytest.mark.asyncio


async def _write_into(pool, queue, blobs, settings, actor, tenant, *, external_id,
                      type_name, key):
    response = await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(
            producer_id=tenant.producer_id,
            items=[WriteItem(external_id=external_id, content=Inline(text="a passing remark"),
                             memory=MemoryRef(type=type_name, key=key))],
            options=WriteOptions(enrich=False),
        ),
    )
    return response.results[0].data_id


async def _age(pool, data_id: str, seconds: int) -> None:
    """Backdate a membership rather than sleeping through a TTL."""
    await pool.execute(
        "UPDATE memory_members SET added_at = now() - make_interval(secs => $2) "
        "WHERE data_id = $1",
        data_id, seconds,
    )


async def test_an_expired_conversation_is_deleted_through_the_ordinary_cascade(
    pool, queue, blobs, settings, tenant, principal_for
):
    """`orphan_delete` is the shipped default for `conversation`, whose TTL is
    an hour. The item goes through `request_deletion`, so it gets the same
    tombstone, blob reclamation and audit as any other erasure -- expiry must
    not become a second erasure path that forgets one of them."""
    actor = await principal_for(tenant.api_key)
    data_id = await _write_into(pool, queue, blobs, settings, actor, tenant,
                                external_id="chat-1", type_name="conversation", key="thread-1")
    await _age(pool, data_id, 7200)

    result = await sweep_expired(pool, queue, actor, project_id=tenant.project_id)
    assert result["considered"] == 1
    assert result["deleted"] == 1
    assert result["run_id"] is not None

    row = await pool.fetchrow("SELECT deleted_at FROM data_items WHERE data_id = $1", data_id)
    assert row["deleted_at"] is not None


async def test_a_permanent_membership_keeps_an_item_alive(
    pool, queue, blobs, settings, tenant, principal_for
):
    """The whole reason expiry is computed as a maximum. Sweeping per
    membership would delete a record a factual memory still depends on."""
    actor = await principal_for(tenant.api_key)
    data_id = await _write_into(pool, queue, blobs, settings, actor, tenant,
                                external_id="chat-2", type_name="conversation", key="thread-2")
    # A type with no TTL: unbounded, and unbounded wins outright.
    await memories.create_type(pool, actor, project_id=tenant.project_id,
                               name="factual", ttl_seconds=None)
    factual = await memories.create_memory(
        pool, actor, project_id=tenant.project_id, type_name="factual", memory_key="facts")
    await memories.add_members(pool, actor, factual["memory_id"], [data_id])
    await _age(pool, data_id, 7200)

    assert await due_for_expiry(pool, project_id=tenant.project_id) == []
    result = await sweep_expired(pool, queue, actor, project_id=tenant.project_id)
    assert result["considered"] == 0
    assert (await get_item(pool, actor, data_id))["state"] is not None   # still there


async def test_archive_takes_it_out_of_the_working_set_without_erasing_it(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Out of the way and gone are different states, and only one is
    reversible. An archived item stays readable, searchable with
    `include_archived` and citable -- the same column compaction uses."""
    actor = await principal_for(tenant.api_key)
    await memories.create_type(pool, actor, project_id=tenant.project_id,
                               name="briefing", ttl_seconds=60, on_expiry="archive")
    data_id = await _write_into(pool, queue, blobs, settings, actor, tenant,
                                external_id="brief-1", type_name="briefing", key="b-1")
    await _age(pool, data_id, 600)

    result = await sweep_expired(pool, queue, actor, project_id=tenant.project_id)
    assert (result["archived"], result["deleted"]) == (1, 0)
    row = await pool.fetchrow(
        "SELECT archived_at, archived_by, deleted_at FROM data_items WHERE data_id = $1", data_id)
    assert row["archived_at"] is not None
    assert row["archived_by"] == "expiry"
    assert row["deleted_at"] is None


async def test_keep_members_refiles_rather_than_stranding(
    pool, queue, blobs, settings, tenant, principal_for
):
    """An item whose last membership expires under `keep_members` must not
    become unreachable. It lands in the applicable default, which is where
    unattached items live."""
    actor = await principal_for(tenant.api_key)
    await memories.create_type(pool, actor, project_id=tenant.project_id,
                               name="session", ttl_seconds=60, on_expiry="keep_members")
    data_id = await _write_into(pool, queue, blobs, settings, actor, tenant,
                                external_id="sess-1", type_name="session", key="s-1")
    await _age(pool, data_id, 600)

    result = await sweep_expired(pool, queue, actor, project_id=tenant.project_id)
    assert (result["refiled"], result["deleted"]) == (1, 0)

    rows = await memories.memories_for_item(pool, data_id)
    assert [r["type"] for r in rows] == ["default"]
    # And it is not due again on the next pass, which would be an endless sweep.
    assert await due_for_expiry(pool, project_id=tenant.project_id) == []


async def test_a_dry_run_reports_what_it_would_do_and_does_none_of_it(
    pool, queue, blobs, settings, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    data_id = await _write_into(pool, queue, blobs, settings, actor, tenant,
                                external_id="chat-3", type_name="conversation", key="thread-3")
    await _age(pool, data_id, 7200)

    preview = await sweep_expired(pool, queue, actor, project_id=tenant.project_id, dry_run=True)
    assert preview["applied"] is False
    assert preview["deleted"] == 1
    assert preview["samples"][0]["data_id"] == data_id
    assert preview["samples"][0]["on_expiry"] == "orphan_delete"

    row = await pool.fetchrow("SELECT deleted_at FROM data_items WHERE data_id = $1", data_id)
    assert row["deleted_at"] is None, "a preview that deletes is not a preview"


async def test_the_scheduled_pass_covers_records_it_cannot_see_as_one_person(
    pool, queue, blobs, settings, tenant, principal_for
):
    """The cascade selects under the caller's *visibility*, so one privileged
    pass would silently skip every private record -- exactly the ones with the
    tightest retention need. `sweep_all` acts per owner instead."""
    actor = await principal_for(tenant.api_key)
    data_id = await _write_into(pool, queue, blobs, settings, actor, tenant,
                                external_id="chat-4", type_name="conversation", key="thread-4")
    await pool.execute(
        "UPDATE data_items SET access_level = 'private' WHERE data_id = $1", data_id)
    await _age(pool, data_id, 7200)

    totals = await sweep_all(pool, queue)
    assert totals["deleted"] == 1
    row = await pool.fetchrow("SELECT deleted_at FROM data_items WHERE data_id = $1", data_id)
    assert row["deleted_at"] is not None
