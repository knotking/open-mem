"""The domain event log.

Recording data and enriching it are two different commitments: one is cheap,
synchronous and always wanted; the other costs money and usually is not. These
tests are about the seam between them.
"""

from __future__ import annotations

import pytest

from open_mem.contracts import (
    EnrichmentOptions,
    Inline,
    WriteItem,
    WriteOptions,
    WriteRequest,
)
from open_mem.events import dispatch_pending, list_events
from open_mem.queue import InProcessQueue
from open_mem.retrieval import get_item
from open_mem.write import write_items

pytestmark = pytest.mark.asyncio

TEXT = "The reconciliation job stalled overnight and the filing slipped to Monday."


async def _write(pool, queue, blobs, settings, actor, producer_id, external_id, **options):
    return await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(
            producer_id=producer_id,
            items=[WriteItem(external_id=external_id, content=Inline(text=TEXT))],
            options=WriteOptions(**options),
        ),
    )


async def test_enrichment_is_off_unless_asked_for(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A default that quietly bills people is the wrong default, however
    convenient it looks in a demo."""
    actor = await principal_for(tenant.api_key)
    written = await _write(pool, queue, blobs, settings, actor, tenant.producer_id, "quiet-1")
    await queue.drain()

    item = await get_item(pool, actor, written.results[0].data_id)
    # Recorded, durable, readable -- and deliberately not searchable.
    assert item["state"] == "stored"
    assert item["content_text"] == TEXT

    types = [e["event_type"] for e in await list_events(pool, tenant.org_id)]
    assert "data.recorded" in types
    assert "enrichment.requested" not in types


async def test_asking_for_it_records_two_ordered_events(
    pool, queue, blobs, settings, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    written = await _write(
        pool, queue, blobs, settings, actor, tenant.producer_id, "loud-1", enrich=True
    )
    await queue.drain()
    data_id = written.results[0].data_id

    events = await list_events(pool, tenant.org_id, data_id=data_id)
    by_type = {e["event_type"]: e for e in events}
    recorded, requested = by_type["data.recorded"], by_type["enrichment.requested"]

    # The ordering is a fact about the data, not a hope about timing.
    assert requested["caused_by"] == recorded["event_id"]
    assert requested["sequence"] > recorded["sequence"]
    assert (await get_item(pool, actor, data_id))["state"] == "enriched"


async def test_an_enrichment_never_dispatches_before_its_cause_is_consumed(
    pool, blobs, settings, tenant, principal_for
):
    """Two processes handling the two events at the same moment must still
    produce the right order."""
    actor = await principal_for(tenant.api_key)
    idle = InProcessQueue()          # nothing consumes, so nothing is marked consumed
    await _write(pool, idle, blobs, settings, actor, tenant.producer_id, "ordered-1",
                 enrich=True)

    rows = await list_events(pool, tenant.org_id)
    recorded = next(e for e in rows if e["event_type"] == "data.recorded")
    requested = next(e for e in rows if e["event_type"] == "enrichment.requested")
    assert recorded["status"] == "dispatched"
    # Held back: its cause has been published but not observed.
    assert requested["status"] == "pending"

    dispatched = await dispatch_pending(pool, idle)
    assert dispatched == 0
    await idle.close()


async def test_graph_events_are_logged_with_no_consumer(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A graph that begins the day someone writes the consumer has lost
    everything before it. The intent is recorded now and drained later."""
    actor = await principal_for(tenant.api_key)
    await _write(pool, queue, blobs, settings, actor, tenant.producer_id, "graph-1")
    await queue.drain()

    graph = [e for e in await list_events(pool, tenant.org_id)
             if e["event_type"] == "graph.build.requested"]
    assert len(graph) == 1
    # A state, not a failure.
    assert graph[0]["status"] == "no_consumer"
    assert graph[0]["last_error"] is None


async def test_every_event_is_audited(pool, queue, blobs, settings, tenant, principal_for):
    """The log is operational and drains; the audit record is evidence and
    never does."""
    actor = await principal_for(tenant.api_key)
    await _write(pool, queue, blobs, settings, actor, tenant.producer_id, "audited-1",
                 enrich=True)
    await queue.drain()

    actions = {r["action"] for r in await pool.fetch(
        "SELECT action FROM audit_events WHERE org_id = $1", tenant.org_id
    )}
    assert "event.data.recorded" in actions
    assert "event.enrichment.requested" in actions


async def test_enrichment_can_be_requested_after_the_fact(
    pool, queue, blobs, settings, tenant, principal_for
):
    """The normal path: record now, decide later whether it is worth spending
    on."""
    from open_mem.events import emit_audited

    actor = await principal_for(tenant.api_key)
    written = await _write(pool, queue, blobs, settings, actor, tenant.producer_id, "later-1")
    await queue.drain()
    data_id = written.results[0].data_id
    assert (await get_item(pool, actor, data_id))["state"] == "stored"

    async with pool.acquire() as conn, conn.transaction():
        await emit_audited(
            conn, actor, event_type="enrichment.requested",
            org_id=tenant.org_id, project_id=tenant.project_id, data_id=data_id,
            payload={"embed": True, "summarize": True},
        )
    await dispatch_pending(pool, queue)
    await queue.drain()

    assert (await get_item(pool, actor, data_id))["state"] == "enriched"


async def test_the_steps_are_independently_selectable(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Embedding makes it findable; summarising describes it. Someone who wants
    search but not summaries should not pay for summaries."""
    actor = await principal_for(tenant.api_key)
    written = await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(
            producer_id=tenant.producer_id,
            items=[WriteItem(external_id="embed-only", content=Inline(text=TEXT))],
            options=WriteOptions(
                enrich=True, enrichment=EnrichmentOptions(embed=True, summarize=False)
            ),
        ),
    )
    await queue.drain()
    data_id = written.results[0].data_id

    assert (await get_item(pool, actor, data_id))["state"] == "searchable"
    assert await pool.fetchval("SELECT count(*) FROM artifacts") == 0
    assert await pool.fetchval(
        "SELECT count(*) FROM chunks WHERE data_id = $1", data_id
    ) >= 1


async def test_a_prompt_override_applies_to_one_request_only(
    pool, queue, blobs, settings, tenant, principal_for
):
    """An override that quietly became the default would change a project's
    behaviour with no audit trail on the setting that appears to control it."""
    import open_mem.prompts as prompts

    before = prompts.BY_DATA_TYPE.get("document_text")
    actor = await principal_for(tenant.api_key)
    await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(
            producer_id=tenant.producer_id,
            items=[WriteItem(external_id="override-1", content=Inline(text=TEXT))],
            options=WriteOptions(
                enrich=True,
                enrichment=EnrichmentOptions(prompt="Extract only the deadline."),
            ),
        ),
    )
    await queue.drain()

    # The registry is exactly as it was: nothing was persisted.
    assert prompts.BY_DATA_TYPE.get("document_text") == before
    # And the override is on the event, so it is auditable after the fact.
    events = await list_events(pool, tenant.org_id)
    requested = next(e for e in events if e["event_type"] == "enrichment.requested")
    assert requested["payload"]["prompt_override"] == "Extract only the deadline."


async def test_a_failed_enrichment_keeps_the_event_for_another_attempt(
    pool, blobs, settings, embedder, tenant, principal_for
):
    """A failed dispatch is not a lost intention -- which is the property the
    queue alone never had."""
    from open_mem.workers import EventWorker

    class Broken:
        model_id = "broken-v1"

        async def _enrich(self, data_id, **kw):
            raise RuntimeError("extractor is down")

    actor = await principal_for(tenant.api_key)
    queue = InProcessQueue(max_attempts=1, base_delay=0.001)
    EventWorker(pool, queue, enrich_worker=Broken()).register(queue)
    await _write(pool, queue, blobs, settings, actor, tenant.producer_id, "broken-1",
                 enrich=True)
    await queue.drain()
    await queue.close()

    requested = next(e for e in await list_events(pool, tenant.org_id)
                     if e["event_type"] == "enrichment.requested")
    # Back to pending, with the reason recorded -- not silently dropped.
    assert requested["status"] == "pending"
    assert "extractor is down" in requested["last_error"]


async def test_a_provider_quota_defers_without_spending_an_attempt(
    pool, blobs, settings, tenant, principal_for
):
    """A daily quota does not reset inside a retry budget.

    Counting these would walk a perfectly good request to `failed` in seconds
    and need a human to notice, when the right behaviour is to try later.
    """
    from open_mem.multimodal import QuotaExhausted
    from open_mem.workers import EventWorker

    class OutOfQuota:
        model_id = "quota-v1"

        async def _enrich(self, data_id, **kw):
            raise QuotaExhausted("429 Too Many Requests")

    actor = await principal_for(tenant.api_key)
    queue = InProcessQueue(max_attempts=1, base_delay=0.001)
    EventWorker(pool, queue, enrich_worker=OutOfQuota()).register(queue)
    await _write(pool, queue, blobs, settings, actor, tenant.producer_id, "quota-1",
                 enrich=True)
    await queue.drain()
    await queue.close()

    requested = next(e for e in await list_events(pool, tenant.org_id)
                     if e["event_type"] == "enrichment.requested")
    assert requested["status"] == "pending"
    # Still at zero: waiting is not a failed attempt.
    assert requested["attempts"] == 0
    assert "429" in requested["last_error"]
