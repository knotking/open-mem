"""The milestone, as a test.

> Write an item into a project owned by an org with an access level -> a search
> scoped to that project finds it -> the response cites it -> the read is
> audited -> the row records which model embedded it.

Every assertion below is one clause of that sentence.
"""

from __future__ import annotations

import pytest
from open_mem.ids import new_id

from open_mem.contracts import (
    WriteOptions,
    Inline,
    ItemAccess,
    Pending,
    RetrieveFilter,
    RetrieveRequest,
    WriteItem,
    WriteRequest,
)
from open_mem.retrieval import NotFound, get_item, retrieve
from open_mem.events import dispatch_pending, emit_audited
from open_mem.write import EMBED_TOPIC, AdmissionError, write_items


async def _request_enrichment(pool, actor, tenant, data_id):
    """Ask for enrichment after the fact -- the normal path now that it is
    off by default."""
    async with pool.acquire() as conn, conn.transaction():
        return await emit_audited(
            conn, actor, event_type="enrichment.requested",
            org_id=tenant.org_id, project_id=tenant.project_id, data_id=data_id,
            payload={"embed": True, "summarize": True},
        )

pytestmark = pytest.mark.asyncio

TEXT = (
    "The quarterly filing was delayed after the reconciliation job stalled.\n\n"
    "Finance escalated to the platform team on Tuesday afternoon."
)


async def _write(pool, queue, blobs, settings, actor, producer_id, items, **kw):
    return await write_items(
        pool, queue, blobs, settings, actor, WriteRequest(producer_id=producer_id, items=items,
                        options=WriteOptions(enrich=True)), **kw
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
    assert citation.state == "enriched"   # drained all the way up the staircase

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
    pool, blobs, settings, embedder, extractor, tenant, principal_for
):
    """Three rungs, each reached by consuming an event.

    'I just uploaded it and search cannot find it' is a support ticket, not a
    bug -- but only if the state is visible while it is true. The middle rung is
    observable because embedding and summarising are separate steps of one
    enrichment event: an item is searchable whether or not the summary lands.
    """
    from open_mem.queue import InProcessQueue
    from open_mem.workers import EmbedWorker, EnrichWorker, EventWorker

    actor = await principal_for(tenant.api_key)
    queue = InProcessQueue()
    embed = EmbedWorker(pool, embedder, settings, queue=queue)
    await embed.ensure_generator()
    embed.register(queue, EMBED_TOPIC)
    # Only embedding is wired, so the enrichment event does the part it can.
    EventWorker(pool, queue, embed_worker=embed).register(queue)

    response = await _write(
        pool, queue, blobs, settings, actor, tenant.producer_id,
        [WriteItem(external_id="s-1", content=Inline(text=TEXT))],
    )
    data_id = response.results[0].data_id
    # Durable and readable before any enrichment has run.
    assert (await get_item(pool, actor, data_id))["state"] == "stored"

    await queue.drain()
    assert (await get_item(pool, actor, data_id))["state"] == "searchable"
    await queue.close()

    # Summarisation becomes available; asking again completes the climb.
    queue2 = InProcessQueue()
    enrich = EnrichWorker(pool, extractor, settings)
    await enrich.ensure_generator()
    EventWorker(pool, queue2, embed_worker=embed, enrich_worker=enrich).register(queue2)
    await _request_enrichment(pool, actor, tenant, data_id)
    await dispatch_pending(pool, queue2)
    await queue2.drain()
    await queue2.close()
    assert (await get_item(pool, actor, data_id))["state"] == "enriched"


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
    # The write response says so too, so a client need not fetch to find out.
    assert response.results[0].state == "stored"
    assert response.results[0].is_downloaded is False

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
    assert item["classified_by_layer"] == 4          # layer 3's hint was discarded


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


async def test_explicit_data_type_short_circuits_at_layer_one(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A layer that short-circuits everything cannot sit below what it
    short-circuits -- which is why it is layer 1 and not layer 3."""
    actor = await principal_for(tenant.api_key)
    response = await _write(
        pool, queue, blobs, settings, actor, tenant.producer_id,
        [WriteItem(
            external_id="explicit-1",
            content=Inline(text='{"latitude": 51.5, "longitude": -0.12}'),
            source_type="pdf",
            data_type="clinical_note",
        )],
    )
    item = await get_item(pool, actor, response.results[0].data_id)
    assert item["data_type"] == "clinical_note"
    assert item["classified_by_layer"] == 1


async def test_a_revision_reads_in_full_only_when_it_is_asked_for(
    pool, queue, blobs, settings, tenant, principal_for
):
    """The listing previews; one revision reads whole.

    Forty revisions of a long document is a response nobody wants, and most
    callers are choosing which to read rather than reading all of them. So the
    full text is a second request, and it carries its own access record because
    unlike the listing it discloses content.
    """
    from open_mem.retrieval import get_versions, one_version
    from open_mem.workers import record_version

    owner = await principal_for(tenant.api_key)
    long_text = "x" * 900
    data_id = new_id("data")
    await pool.execute(
        """
        INSERT INTO data_items (data_id, org_id, project_id, owner_id, producer_id,
            external_id, state, access_level, content_text, event_time)
        VALUES ($1, $2, $3, $4, $5, 'versioned', 'stored', 'org', 'seed', now())
        """,
        data_id, tenant.org_id, tenant.project_id, tenant.user_id, tenant.producer_id,
    )
    async with pool.acquire() as conn:
        await record_version(conn, data_id, source="write", content_text=long_text)

    listed = await get_versions(pool, owner, data_id)
    assert len(listed[0]["preview"]) == 400, "the listing is a preview, not the text"

    full = await one_version(pool, owner, data_id, listed[0]["version_id"])
    assert full["content_text"] == long_text


async def test_a_second_tenant_cannot_read_a_revision(
    pool, tenant, other_tenant, principal_for
):
    """404, not 403 — a revision of a record you cannot read must not be
    confirmable."""
    from open_mem.retrieval import get_versions, one_version
    from open_mem.workers import record_version

    owner = await principal_for(tenant.api_key)
    stranger = await principal_for(other_tenant.api_key)
    data_id = new_id("data")
    await pool.execute(
        """
        INSERT INTO data_items (data_id, org_id, project_id, owner_id, producer_id,
            external_id, state, access_level, content_text, event_time)
        VALUES ($1, $2, $3, $4, $5, 'private-versioned', 'stored', 'private', 'secret', now())
        """,
        data_id, tenant.org_id, tenant.project_id, tenant.user_id, tenant.producer_id,
    )
    async with pool.acquire() as conn:
        await record_version(conn, data_id, source="write", content_text="secret text")

    listed = await get_versions(pool, owner, data_id)
    assert await one_version(pool, stranger, data_id, listed[0]["version_id"]) == {}


async def test_metadata_is_queryable_from_sql(
    pool, queue, blobs, settings, tenant, principal_for
):
    """It was stored as a JSON string containing JSON.

    The write path serialised it and the pool's jsonb codec serialised it
    again, so `jsonb_typeof` reported `string` and `metadata ->> 'key'`
    returned NULL for every row -- a column whose entire purpose is being
    queried, and which nothing could query. It read back correctly in Python,
    which is why it survived: the round trip through `json.loads` undid the
    damage, and only SQL ever saw it.
    """
    actor = await principal_for(tenant.api_key)
    response = await _write(
        pool, queue, blobs, settings, actor, tenant.producer_id,
        [WriteItem(external_id="meta-1", content=Inline(text=TEXT),
                   metadata={"deadline": "2027-01-01T00:00:00+00:00", "matter": "A-12"})],
    )
    data_id = response.results[0].data_id

    kind = await pool.fetchval(
        "SELECT jsonb_typeof(metadata) FROM data_items WHERE data_id = $1", data_id)
    assert kind == "object", "an object, not a string of one"
    assert await pool.fetchval(
        "SELECT metadata ->> 'matter' FROM data_items WHERE data_id = $1", data_id) == "A-12"
