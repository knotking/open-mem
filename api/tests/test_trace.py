"""The retrieval trace -- the sandbox's actual output.

A sandbox that shows only an answer is a demo: the answer is a lagging
indicator of ingestion quality, filtered through a model that sounds right
regardless. These tests cover what the trace has to distinguish, because
"the answer is missing something I know is in the data" has several different
causes with different fixes.
"""

from __future__ import annotations

import pytest

from open_mem.contracts import (
    WriteOptions,
    Inline,
    RetrieveFilter,
    RetrieveRequest,
    WriteItem,
    WriteRequest,
)
from open_mem.queue import InProcessQueue
from open_mem.retrieval import retrieve, staircase
from open_mem.events import dispatch_pending, emit_audited
from open_mem.workers import EmbedWorker, EnrichWorker, EventWorker, EventWorker
from open_mem.write import EMBED_TOPIC, write_items

pytestmark = pytest.mark.asyncio


async def _write(pool, queue, blobs, settings, actor, producer_id, items):
    return await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=producer_id, items=items,
                        options=WriteOptions(enrich=True)),
    )


async def test_the_staircase_counts_each_rung(
    pool, blobs, settings, embedder, extractor, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    queue = InProcessQueue()
    await _write(
        pool, queue, blobs, settings, actor, tenant.producer_id,
        [WriteItem(external_id=f"s-{i}", content=Inline(text=f"Record number {i} about storage."))
         for i in range(3)],
    )
    counts = await staircase(pool, actor, tenant.project_id)
    assert (counts["total"], counts["stored"], counts["searchable"]) == (3, 3, 0)

    embed = EmbedWorker(pool, embedder, settings, queue=queue)
    await embed.ensure_generator()
    embed.register(queue, EMBED_TOPIC)
    EventWorker(pool, queue, embed_worker=embed).register(queue)
    await queue.drain()
    counts = await staircase(pool, actor, tenant.project_id)
    assert (counts["searchable"], counts["enriched"]) == (3, 0)

    # Summarisation arrives; re-requesting enrichment finishes the climb.
    enrich = EnrichWorker(pool, extractor, settings)
    await enrich.ensure_generator()
    second = InProcessQueue()
    EventWorker(pool, second, embed_worker=embed, enrich_worker=enrich).register(second)
    for row in await pool.fetch(
        "SELECT data_id, project_id FROM data_items WHERE project_id = $1", tenant.project_id
    ):
        async with pool.acquire() as conn, conn.transaction():
            await emit_audited(
                conn, actor, event_type="enrichment.requested",
                org_id=tenant.org_id, project_id=row["project_id"], data_id=row["data_id"],
                payload={"embed": False, "summarize": True},
            )
    await dispatch_pending(pool, second)
    await second.drain()
    await second.close()
    await queue.close()
    counts = await staircase(pool, actor, tenant.project_id)
    assert counts["enriched"] == 3


async def test_a_retrieval_says_what_it_answered_over(
    pool, queue, blobs, settings, embedder, tenant, principal_for
):
    """FR-SBX-7. An early answer becomes informative rather than damning."""
    actor = await principal_for(tenant.api_key)
    await _write(
        pool, queue, blobs, settings, actor, tenant.producer_id,
        [WriteItem(external_id="ready-1",
                   content=Inline(text="The scheduler change was rolled back at noon."))],
    )
    await queue.drain()
    # A second item that never gets enriched: written with enrichment off.
    await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(
            producer_id=tenant.producer_id,
            items=[WriteItem(external_id="behind-1",
                             content=Inline(text="A record about the scheduler that is still behind."))],
            options={"enrich": False},
        ),
    )

    found = await retrieve(
        pool, embedder, actor,
        RetrieveRequest(query="scheduler rolled back",
                        filter=RetrieveFilter(project_id=tenant.project_id)),
        embed_generator="gen_current",
    )
    assert found.corpus.total == 2
    assert found.corpus.stored == 1        # the one still behind
    assert found.corpus.enriched == 1

    # And it is named as excluded for the right reason, not silently missing.
    behind = [e for e in found.excluded if e.reason == "not_yet_enriched"]
    assert len(behind) == 1
    assert behind[0].state == "stored"


async def test_the_trace_is_reconstructable_after_the_fact(
    pool, queue, blobs, settings, embedder, tenant, principal_for
):
    """FR-SCH-13: excluded sources are persisted with their reason, so the
    trace survives the moment it was shown."""
    actor = await principal_for(tenant.api_key)
    await _write(
        pool, queue, blobs, settings, actor, tenant.producer_id,
        [WriteItem(external_id="t-1", content=Inline(text="Storage costs rose in March."))],
    )
    await queue.drain()
    await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(
            producer_id=tenant.producer_id,
            items=[WriteItem(external_id="t-2", content=Inline(text="Unrelated but unenriched."))],
            options={"enrich": False},
        ),
    )

    found = await retrieve(
        pool, embedder, actor,
        RetrieveRequest(query="storage costs", filter=RetrieveFilter(project_id=tenant.project_id)),
        embed_generator="gen_current",
    )
    rows = await pool.fetch(
        "SELECT data_id, used, excluded_reason FROM query_sources WHERE query_id = $1",
        found.query_id,
    )
    assert {r["excluded_reason"] for r in rows} == {None, "not_yet_enriched"}
    assert sum(1 for r in rows if r["used"]) == 1


async def test_acl_exclusions_are_not_reported_and_cannot_be(
    pool, queue, blobs, settings, embedder, tenant, principal_for
):
    """Reporting 'three records were hidden from you' discloses their
    existence, which is the thing the ACL is for. The predicate runs inside the
    query, so the count does not exist to be reported."""
    from open_mem.auth import DATA_READ, issue_key
    from open_mem.bootstrap import create_user

    owner = await principal_for(tenant.api_key)
    await _write(
        pool, queue, blobs, settings, owner, tenant.producer_id,
        [WriteItem(external_id="secret-1", content=Inline(text="A private record about budgets."))],
    )
    await queue.drain()

    other_id = await create_user(pool, "outsider@example.com")
    await pool.execute(
        "INSERT INTO memberships (user_id, org_id, role) VALUES ($1, $2, 'member')",
        other_id, tenant.org_id,
    )
    token = await issue_key(pool, user_id=other_id, org_id=tenant.org_id,
                            capabilities=[DATA_READ])
    outsider = await principal_for(token)

    found = await retrieve(
        pool, embedder, outsider,
        RetrieveRequest(query="budgets", filter=RetrieveFilter(project_id=tenant.project_id)),
        embed_generator="gen_current",
    )
    assert found.results == []
    assert found.excluded == []
    assert found.corpus.total == 0          # not "1 hidden"
    assert found.acl_exclusions_reported is False
