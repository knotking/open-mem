"""Deletion.

The distinction that governs everything here: `deleted_at` is when it became
invisible, `purged_at` is when the bytes actually went. An erasure certificate
is issued against the second. Reporting the first as the second is how a
compliance answer becomes false.
"""

from __future__ import annotations

import base64

import pytest

from memdog.contracts import Inline, RetrieveFilter, RetrieveRequest, WriteItem, WriteRequest, WriteOptions
from memdog.deletion import DELETE_TOPIC, DeleteWorker, request_deletion, unpurged_tombstones
from memdog.queue import InProcessQueue
from memdog.retrieval import NotFound, get_item, retrieve
from memdog.workers import EventWorker, ParseWorker
from memdog.write import write_items

pytestmark = pytest.mark.asyncio

TEXT = "The acquisition price was eighty four million dollars."


async def _write(pool, queue, blobs, settings, actor, producer_id, external_id, text=TEXT):
    return await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=producer_id, items=[
            WriteItem(external_id=external_id, content=Inline(text=text)),
        ],
                        options=WriteOptions(enrich=True)),
    )


async def test_the_item_is_invisible_before_the_request_returns(
    pool, queue, blobs, settings, embedder, tenant, principal_for
):
    """Visibility is transactional; reclamation is eventual. An erasure request
    cannot wait in a queue to take effect."""
    actor = await principal_for(tenant.api_key)
    written = await _write(pool, queue, blobs, settings, actor, tenant.producer_id, "gone-1")
    await queue.drain()
    data_id = written.results[0].data_id

    assert (await retrieve(
        pool, embedder, actor,
        RetrieveRequest(query="acquisition price", filter=RetrieveFilter(project_id=tenant.project_id)),
    )).results

    silent = InProcessQueue()   # no worker: the cascade has not run at all
    result = await request_deletion(
        pool, silent, actor, selector={"data_ids": [data_id]}, reason="test"
    )
    assert result.data_ids == [data_id]

    # Invisible immediately, through every read path, with nothing reclaimed yet.
    with pytest.raises(NotFound):
        await get_item(pool, actor, data_id)
    assert (await retrieve(
        pool, embedder, actor,
        RetrieveRequest(query="acquisition price", filter=RetrieveFilter(project_id=tenant.project_id)),
    )).results == []

    row = await pool.fetchrow("SELECT deleted_at, purged_at FROM data_items WHERE data_id = $1", data_id)
    assert row["deleted_at"] is not None and row["purged_at"] is None
    assert await unpurged_tombstones(pool, older_than_seconds=0) == 1
    await silent.close()


async def test_the_cascade_reclaims_and_the_root_row_goes_last(
    pool, blobs, settings, embedder, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    queue = InProcessQueue()
    ParseWorker(pool, blobs, queue=queue).register(queue)
    DeleteWorker(pool, blobs).register(queue)
    from memdog.workers import EmbedWorker, EventWorker
    from memdog.write import EMBED_TOPIC

    embed = EmbedWorker(pool, embedder, settings, queue=queue)
    await embed.ensure_generator()
    embed.register(queue, EMBED_TOPIC)

    written = await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=tenant.producer_id, items=[
            WriteItem(external_id="bytes-gone.csv",
                      content=Inline(bytes_b64=base64.b64encode(b"a,b\n1,2\n").decode())),
        ],
                        options=WriteOptions(enrich=True)),
    )
    await queue.drain()
    data_id = written.results[0].data_id
    storage_ref = (await get_item(pool, actor, data_id))["storage_ref"]
    assert await blobs.get(storage_ref)

    await request_deletion(pool, queue, actor, selector={"data_ids": [data_id]}, reason="erasure")
    await queue.drain()
    await queue.close()

    # Derived rows gone, bytes gone, and only then the certificate.
    assert await pool.fetchval("SELECT count(*) FROM chunks WHERE data_id = $1", data_id) == 0
    assert await pool.fetchval("SELECT count(*) FROM data_versions WHERE data_id = $1", data_id) == 0
    assert await pool.fetchval("SELECT count(*) FROM memory_members WHERE data_id = $1", data_id) == 0
    with pytest.raises(FileNotFoundError):
        await blobs.get(storage_ref)

    tombstone = await pool.fetchrow(
        "SELECT purged_at, content_text, storage_ref, deletion_reason FROM data_items WHERE data_id = $1",
        data_id,
    )
    # A metadata-only tombstone remains -- the row is what the audit record
    # points at, and it must outlive the content.
    assert tombstone["purged_at"] is not None
    assert tombstone["content_text"] is None and tombstone["storage_ref"] is None
    assert tombstone["deletion_reason"] == "erasure"


async def test_audit_records_survive_the_deletion_they_describe(
    pool, blobs, settings, tenant, principal_for
):
    """Deleting them defeats the purpose: they are the evidence that the
    deletion happened."""
    actor = await principal_for(tenant.api_key)
    queue = InProcessQueue()
    DeleteWorker(pool, blobs).register(queue)
    written = await _write(pool, queue, blobs, settings, actor, tenant.producer_id, "audited-gone")
    data_id = written.results[0].data_id
    await get_item(pool, actor, data_id)          # a read to be evidenced

    await request_deletion(pool, queue, actor, selector={"data_ids": [data_id]}, reason="gdpr")
    await queue.drain()
    await queue.close()

    writes = await pool.fetch("SELECT action, detail FROM audit_events WHERE org_id = $1", tenant.org_id)
    assert any(w["action"] == "delete.requested" for w in writes)
    # The access log survives the item it describes -- you cannot evidence
    # "we deleted it" if the evidence was inside the deletion.
    reads = await pool.fetch("SELECT action FROM access_log WHERE data_id = $1", data_id)
    assert reads


async def test_a_summary_of_deleted_content_is_marked_stale_not_left(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A summary spanning items, one of which is erased, still contains the
    erased content in prose. Deleting it loses value; leaving it is a
    compliance failure."""
    actor = await principal_for(tenant.api_key)
    written = await _write(pool, queue, blobs, settings, actor, tenant.producer_id, "summarised-1")
    await queue.drain()
    data_id = written.results[0].data_id
    assert await pool.fetchval("SELECT count(*) FROM artifacts") == 1

    worker = DeleteWorker(pool, blobs)
    await request_deletion(pool, queue, actor, selector={"data_ids": [data_id]})
    await worker.purge(data_id)

    artifact = await pool.fetchrow("SELECT stale_reason FROM artifacts LIMIT 1")
    assert artifact["stale_reason"] == "source_deleted"


async def test_legal_hold_returns_partial_completion_naming_what_was_withheld(
    pool, queue, blobs, settings, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    held = await _write(pool, queue, blobs, settings, actor, tenant.producer_id, "held-1")
    free = await _write(pool, queue, blobs, settings, actor, tenant.producer_id, "free-1")
    await pool.execute("UPDATE data_items SET legal_hold = true WHERE data_id = $1",
                       held.results[0].data_id)

    result = await request_deletion(
        pool, queue, actor,
        selector={"data_ids": [held.results[0].data_id, free.results[0].data_id]},
    )
    assert result.data_ids == [free.results[0].data_id]
    assert result.retained == [(held.results[0].data_id, "legal_hold")]
    # And the held item is still readable, not quietly tombstoned.
    assert await get_item(pool, actor, held.results[0].data_id)


async def test_a_dry_run_changes_nothing_and_reports_both_counts(
    pool, queue, blobs, settings, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    written = await _write(pool, queue, blobs, settings, actor, tenant.producer_id, "dry-1")
    result = await request_deletion(
        pool, queue, actor, selector={"data_ids": [written.results[0].data_id]}, dry_run=True
    )
    assert result.data_ids == [written.results[0].data_id]
    assert await get_item(pool, actor, written.results[0].data_id)   # untouched
    run = await pool.fetchrow("SELECT mode, status FROM runs WHERE run_id = $1", result.run_id)
    assert (run["mode"], run["status"]) == ("dry_run", "completed")


async def test_a_selector_cannot_reach_further_than_a_read_can(
    pool, queue, blobs, settings, tenant, other_tenant, principal_for
):
    """Deleting through a selector must never reach what reading through one
    would not."""
    owner = await principal_for(tenant.api_key)
    intruder = await principal_for(other_tenant.api_key)
    written = await _write(pool, queue, blobs, settings, owner, tenant.producer_id, "theirs-1")

    result = await request_deletion(
        pool, queue, intruder, selector={"data_ids": [written.results[0].data_id]}
    )
    assert result.data_ids == []
    assert await get_item(pool, owner, written.results[0].data_id)


async def test_a_time_range_must_name_its_clock(pool, queue, blobs, settings, tenant, principal_for):
    """ingested_at and event_time answer different questions, and a selector
    that does not say which is ambiguous exactly where it matters."""
    actor = await principal_for(tenant.api_key)
    with pytest.raises(ValueError):
        await request_deletion(
            pool, queue, actor,
            selector={"project_id": tenant.project_id, "time_clock": "whenever",
                      "since": "2020-01-01T00:00:00Z"},
        )


async def test_an_empty_selector_is_refused(pool, queue, blobs, settings, tenant, principal_for):
    """A selector that narrows nothing is a request to delete everything, and
    it is far more likely to be a bug than an intention."""
    actor = await principal_for(tenant.api_key)
    with pytest.raises(ValueError):
        await request_deletion(pool, queue, actor, selector={})


async def test_purging_twice_is_the_same_as_purging_once(
    pool, queue, blobs, settings, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    written = await _write(pool, queue, blobs, settings, actor, tenant.producer_id, "idem-gone")
    await queue.drain()
    data_id = written.results[0].data_id
    worker = DeleteWorker(pool, blobs)
    await request_deletion(pool, queue, actor, selector={"data_ids": [data_id]})
    await worker.purge(data_id)
    await worker.purge(data_id)      # must not raise
    assert await pool.fetchval(
        "SELECT purged_at IS NOT NULL FROM data_items WHERE data_id = $1", data_id
    )
