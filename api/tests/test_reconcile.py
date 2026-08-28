"""The reconciler -- the queue is not the record of outstanding work.

Every test here starts from the same premise: the row committed, the job did
not survive. That is not an exotic failure. It is a Cloud Run scale-in, a pod
eviction, or a broker that exhausted its redelivery budget.
"""

from __future__ import annotations

import pytest

from memdog.contracts import Inline, WriteItem, WriteRequest
from memdog.queue import InProcessQueue
from memdog.reconcile import reconcile
from memdog.retrieval import get_item
from memdog.workers import EmbedWorker, EnrichWorker
from memdog.write import EMBED_TOPIC, write_items

pytestmark = pytest.mark.asyncio

TEXT = "The reconciliation job stalled overnight and the quarterly filing slipped."


async def _orphaned_write(pool, blobs, settings, actor, producer_id, external_id):
    """A write whose enrichment never runs -- the queue has no subscribers."""
    lost = InProcessQueue()
    response = await write_items(
        pool, lost, blobs, settings, actor,
        WriteRequest(producer_id=producer_id,
                     items=[WriteItem(external_id=external_id, content=Inline(text=TEXT))]),
    )
    return response.results[0].data_id


async def test_it_recovers_an_item_whose_job_was_lost(
    pool, blobs, settings, embedder, extractor, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    data_id = await _orphaned_write(pool, blobs, settings, actor, tenant.producer_id, "lost-1")
    assert (await get_item(pool, actor, data_id))["state"] == "stored"

    queue = InProcessQueue()
    embed = EmbedWorker(pool, embedder, settings, queue=queue)
    await embed.ensure_generator()
    embed.register(queue, EMBED_TOPIC)
    enrich = EnrichWorker(pool, extractor, settings)
    await enrich.ensure_generator()
    enrich.register(queue)

    # grace_seconds=0: the item is stale the moment it is behind.
    swept = await reconcile(pool, queue, embed_generator=embed.generator_version, grace_seconds=0)
    assert swept.embed == 1
    await queue.drain()
    await queue.close()

    assert (await get_item(pool, actor, data_id))["state"] == "enriched"


async def test_the_grace_period_does_not_race_a_worker_already_running(
    pool, blobs, settings, embedder, tenant, principal_for
):
    """An item written a second ago is not stuck; it is in flight."""
    actor = await principal_for(tenant.api_key)
    await _orphaned_write(pool, blobs, settings, actor, tenant.producer_id, "fresh-1")

    queue = InProcessQueue()
    embed = EmbedWorker(pool, embedder, settings, queue=queue)
    swept = await reconcile(
        pool, queue, embed_generator=embed.generator_version, grace_seconds=300
    )
    assert swept.total == 0
    await queue.close()


async def test_it_is_safe_to_run_against_a_healthy_corpus(
    pool, queue, blobs, settings, embedder, tenant, principal_for
):
    """Sweeping must be boring. Nothing is behind, so nothing is re-enqueued."""
    actor = await principal_for(tenant.api_key)
    await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=tenant.producer_id,
                     items=[WriteItem(external_id="ok-1", content=Inline(text=TEXT))]),
    )
    await queue.drain()

    embed = EmbedWorker(pool, embedder, settings, queue=queue)
    swept = await reconcile(
        pool, queue, embed_generator=embed.generator_version, grace_seconds=0
    )
    assert swept.total == 0


async def test_it_re_embeds_vectors_from_a_generator_no_longer_assigned(
    pool, queue, blobs, settings, embedder, tenant, principal_for
):
    """Being `searchable` is not enough -- it must be searchable in the vector
    space retrieval actually queries."""
    actor = await principal_for(tenant.api_key)
    await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=tenant.producer_id,
                     items=[WriteItem(external_id="drift-1", content=Inline(text=TEXT))]),
    )
    await queue.drain()

    swept = await reconcile(
        pool, queue, embed_generator="gen_something_else_entirely", grace_seconds=0
    )
    assert swept.embed == 1


async def test_a_pending_item_is_never_swept(
    pool, blobs, settings, embedder, tenant, principal_for
):
    """An item awaiting a fetch is not behind -- it is waiting for a worker this
    slice does not have. Re-enqueueing it forever would be a busy loop."""
    from memdog.contracts import Pending

    actor = await principal_for(tenant.api_key)
    queue = InProcessQueue()
    await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=tenant.producer_id,
                     items=[WriteItem(external_id="pend-1",
                                      content=Pending(provider="google-drive",
                                                      resource_id="1AbC"))]),
    )
    embed = EmbedWorker(pool, embedder, settings, queue=queue)
    swept = await reconcile(
        pool, queue, embed_generator=embed.generator_version, grace_seconds=0
    )
    assert swept.total == 0
    await queue.close()
