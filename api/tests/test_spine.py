"""The milestone, as a test.

> Write an item into a project owned by an org with an access level -> a search
> scoped to that project finds it -> the response cites it -> the read is
> audited -> the row records which model embedded it.

Every assertion below is one clause of that sentence.
"""

from __future__ import annotations

import pytest

from memdog.contracts import (
    Inline,
    ItemAccess,
    Pending,
    RetrieveFilter,
    RetrieveRequest,
    WriteItem,
    WriteRequest,
)
from memdog.retrieval import NotFound, get_item, retrieve
from memdog.write import AdmissionError, write_items

pytestmark = pytest.mark.asyncio

TEXT = (
    "The quarterly filing was delayed after the reconciliation job stalled.\n\n"
    "Finance escalated to the platform team on Tuesday afternoon."
)


async def _write(pool, queue, blobs, settings, actor, producer_id, items, **kw):
    return await write_items(
        pool, queue, blobs, settings, actor, WriteRequest(producer_id=producer_id, items=items), **kw
    )


async def test_write_then_retrieve_cites_the_item(
    pool, queue, blobs, settings, embedder, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    response = await _write(
        pool, queue, blobs, settings, actor, tenant.producer_id,
        [WriteItem(external_id="filing-1", content=Inline(text=TEXT))],
    )

    assert response.accepted == 1 and response.failed == 0
    result = response.results[0]
    assert result.status == "created"
    # The write commits. Durable and readable before anything is enriched.
    assert result.state == "stored"
    item = await get_item(pool, actor, result.data_id)
    assert item["content_text"] == TEXT
    assert item["is_downloaded"] is True

    await queue.drain()

    found = await retrieve(
        pool, embedder, actor,
        RetrieveRequest(query="reconciliation stalled", filter=RetrieveFilter(project_id=tenant.project_id)),
    )
    assert [c.data_id for c in found.results] == [result.data_id]
    citation = found.results[0]
    # The citation points at a span of the source, not just at the document.
    assert TEXT[citation.span_start : citation.span_end] == citation.text
    assert citation.state == "searchable"

    # The row records which model embedded it.
    models = await pool.fetch("SELECT DISTINCT model_id FROM embeddings")
    assert [r["model_id"] for r in models] == [embedder.model_id]

    # The read is audited -- both the direct read and the retrieval.
    actions = await pool.fetch("SELECT action, data_id FROM access_log ORDER BY at")
    assert {r["action"] for r in actions} == {"data.read", "retrieve"}
    assert all(r["data_id"] == result.data_id for r in actions)

    # And the write is in the other store, which is not the same store.
    audited = await pool.fetchval(
        "SELECT action FROM audit_events WHERE target_id = $1", result.data_id
    )
    assert audited == "write.created"


async def test_state_is_a_staircase(
    pool, queue, blobs, settings, embedder, tenant, principal_for
):
    """'I just uploaded it and search cannot find it' is a support ticket, not a
    bug -- but only if the state is visible while it is true."""
    actor = await principal_for(tenant.api_key)
    response = await _write(
        pool, queue, blobs, settings, actor, tenant.producer_id,
        [WriteItem(external_id="s-1", content=Inline(text=TEXT))],
    )
    data_id = response.results[0].data_id

    before = await get_item(pool, actor, data_id)
    assert before["state"] == "stored"
    await queue.drain()
    after = await get_item(pool, actor, data_id)
    assert after["state"] == "searchable"


async def test_pending_content_is_not_downloaded(
    pool, queue, blobs, settings, tenant, principal_for
):
    """`is_downloaded` is derived. There is no way for a caller to assert it."""
    actor = await principal_for(tenant.api_key)
    response = await _write(
        pool, queue, blobs, settings, actor, tenant.producer_id,
        [WriteItem(
            external_id="att-1",
            content=Pending(provider="google-drive", resource_id="1AbC"),
        )],
    )
    item = await get_item(pool, actor, response.results[0].data_id)
    assert item["is_downloaded"] is False
    assert item["pending_ref"]["provider"] == "google-drive"
    assert item["state"] == "stored"
    assert "is_downloaded" not in WriteItem.model_fields


async def test_mime_is_sniffed_and_outranks_source_type(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A client-declared type chooses which agent runs, so it cannot be trusted."""
    actor = await principal_for(tenant.api_key)
    response = await _write(
        pool, queue, blobs, settings, actor, tenant.producer_id,
        [WriteItem(
            external_id="liar-1",
            content=Inline(text='{"latitude": 51.5, "longitude": -0.12}', mime_type="application/pdf"),
            source_type="pdf",
        )],
    )
    item = await get_item(pool, actor, response.results[0].data_id)
    assert item["mime_type"] == "application/json"   # sniffed, not declared
    assert item["source_type"] == "pdf"              # kept as the hint it is
    assert item["data_type"] == "sensor_gps"         # resolved by the payload heuristic
    assert item["classified_by_layer"] == 4


async def test_upsert_on_the_natural_key(
    pool, queue, blobs, settings, embedder, tenant, principal_for
):
    """external_id dedupes within a source -- re-crawling updates, never duplicates."""
    actor = await principal_for(tenant.api_key)
    first = await _write(
        pool, queue, blobs, settings, actor, tenant.producer_id,
        [WriteItem(external_id="rec-1", content=Inline(text="original body text"))],
    )
    await queue.drain()
    second = await _write(
        pool, queue, blobs, settings, actor, tenant.producer_id,
        [WriteItem(external_id="rec-1", content=Inline(text="revised body text"))],
    )
    await queue.drain()

    assert first.results[0].data_id == second.results[0].data_id
    assert second.results[0].status == "updated"
    assert await pool.fetchval("SELECT count(*) FROM data_items") == 1
    # The stale vector does not outlive the content it was made from.
    chunks = await pool.fetch("SELECT text FROM chunks")
    assert [c["text"] for c in chunks] == ["revised body text"]


async def test_disabled_producer_accepts_and_drops(
    pool, queue, blobs, settings, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    await pool.execute(
        "UPDATE producers SET status = 'disabled' WHERE producer_id = $1", tenant.producer_id
    )
    response = await _write(
        pool, queue, blobs, settings, actor, tenant.producer_id,
        [WriteItem(external_id="d-1", content=Inline(text="hello"))],
    )
    assert response.results[0].status == "dropped"
    assert await pool.fetchval("SELECT count(*) FROM data_items") == 0


async def test_one_bad_item_does_not_roll_back_the_batch(
    pool, queue, blobs, settings, tenant, principal_for
):
    """This is what 207 is for."""
    actor = await principal_for(tenant.api_key)
    response = await _write(
        pool, queue, blobs, settings, actor, tenant.producer_id,
        [
            WriteItem(external_id="ok-1", content=Inline(text="fine")),
            WriteItem(external_id="bad-1", content=Inline(text="also fine"),
                      access=ItemAccess(level="restricted")),
            WriteItem(external_id="ok-2", content=Inline(text="fine too")),
        ],
    )
    assert response.accepted == 2 and response.failed == 1
    assert response.results[1].status == "failed"
    assert await pool.fetchval("SELECT count(*) FROM data_items") == 2


async def test_idempotency_key_replays_and_rejects_mismatch(
    pool, queue, blobs, settings, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    items = [WriteItem(external_id="idem-1", content=Inline(text="body"))]
    first = await _write(pool, queue, blobs, settings, actor, tenant.producer_id, items,
                         idempotency_key="k-1")
    again = await _write(pool, queue, blobs, settings, actor, tenant.producer_id, items,
                         idempotency_key="k-1")
    assert first.results[0].data_id == again.results[0].data_id
    assert await pool.fetchval("SELECT count(*) FROM audit_events WHERE action LIKE 'write.%'") == 1

    with pytest.raises(AdmissionError) as exc:
        await _write(
            pool, queue, blobs, settings, actor, tenant.producer_id,
            [WriteItem(external_id="idem-1", content=Inline(text="different"))],
            idempotency_key="k-1",
        )
    assert exc.value.status == 409
