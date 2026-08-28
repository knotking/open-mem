"""The reconciler -- the queue is not the record of outstanding work.

Every test here starts from the same premise: the row committed, the job did
not survive. That is not an exotic failure. It is a Cloud Run scale-in, a pod
eviction, or a broker that exhausted its redelivery budget.
"""

from __future__ import annotations

import pytest

from memdog.contracts import Inline, WriteItem, WriteRequest, WriteOptions
from memdog.queue import InProcessQueue
from memdog.reconcile import reconcile
from memdog.retrieval import get_item
from memdog.workers import EmbedWorker, EventWorker, EnrichWorker, EventWorker
from memdog.write import EMBED_TOPIC, write_items

pytestmark = pytest.mark.asyncio

TEXT = "The reconciliation job stalled overnight and the quarterly filing slipped."


async def _orphaned_write(pool, blobs, settings, actor, producer_id, external_id):
    """A write whose enrichment never runs.

    The events are emitted and durable; only their delivery is lost, which is
    exactly the failure the reconciler exists to repair now that the log is the
    record of work.
    """
    lost = InProcessQueue()
    response = await write_items(
        pool, lost, blobs, settings, actor,
        WriteRequest(producer_id=producer_id,
                     items=[WriteItem(external_id=external_id, content=Inline(text=TEXT))],
                        options=WriteOptions(enrich=True)),
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
    EventWorker(
        pool, queue, embed_worker=embed, enrich_worker=enrich
    ).register(queue)

    # grace_seconds=0: the item is behind the moment it is behind.
    swept = await reconcile(pool, queue, embed_generator=embed.generator_version, grace_seconds=0)
    # Undelivered events are re-dispatched; the row-level sweep is the backstop.
    assert swept.total >= 1
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
                     items=[WriteItem(external_id="ok-1", content=Inline(text=TEXT))],
                        options=WriteOptions(enrich=True)),
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
                     items=[WriteItem(external_id="drift-1", content=Inline(text=TEXT))],
                        options=WriteOptions(enrich=True)),
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
    from memdog.contracts import Pending, WriteOptions

    actor = await principal_for(tenant.api_key)
    queue = InProcessQueue()
    await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=tenant.producer_id,
                     items=[WriteItem(external_id="pend-1",
                                      content=Pending(provider="google-drive",
                                                      resource_id="1AbC"))],
                        options=WriteOptions(enrich=True)),
    )
    embed = EmbedWorker(pool, embedder, settings, queue=queue)
    swept = await reconcile(
        pool, queue, embed_generator=embed.generator_version, grace_seconds=0
    )
    # The row tiers have nothing to do. Undelivered events may still be
    # re-dispatched -- that is the log being repaired, not the item being swept.
    assert (swept.parse, swept.embed, swept.enrich) == (0, 0, 0)
    await queue.close()


async def test_it_recovers_bytes_that_were_never_parsed(
    pool, blobs, settings, embedder, extractor, tenant, principal_for
):
    """The tier that was missing, and whose absence was invisible.

    The "stuck at stored" sweep requires text, which by definition excludes the
    items that still need parsing. A media file whose parse job was lost sat at
    `stored` forever, looking identical to one that had been examined and
    declined.
    """
    import base64

    from memdog.contracts import Inline, WriteOptions
    from memdog.workers import EnrichWorker, ParseWorker

    actor = await principal_for(tenant.api_key)
    lost = InProcessQueue()          # nothing consumes: the parse job is dropped
    written = await write_items(
        pool, lost, blobs, settings, actor,
        WriteRequest(producer_id=tenant.producer_id, items=[
            WriteItem(external_id="orphan.csv",
                      content=Inline(bytes_b64=base64.b64encode(b"a,b\n1,2\n").decode())),
        ],
                        options=WriteOptions(enrich=True)),
    )
    data_id = written.results[0].data_id
    row = await pool.fetchrow(
        "SELECT state, parse_status, indexable_text FROM data_items WHERE data_id = $1", data_id
    )
    assert (row["state"], row["parse_status"], row["indexable_text"]) == ("stored", None, None)
    await lost.close()

    queue = InProcessQueue()
    parse = ParseWorker(pool, blobs, queue=queue)
    parse.register(queue)
    embed = EmbedWorker(pool, embedder, settings, queue=queue)
    await embed.ensure_generator()
    embed.register(queue, EMBED_TOPIC)
    enrich = EnrichWorker(pool, extractor, settings)
    await enrich.ensure_generator()
    enrich.register(queue)
    EventWorker(
        pool, queue, parse_worker=parse, embed_worker=embed, enrich_worker=enrich
    ).register(queue)

    swept = await reconcile(
        pool, queue, embed_generator=embed.generator_version, grace_seconds=0
    )
    assert swept.parse + swept.events >= 1
    await queue.drain()
    await queue.close()

    healed = await get_item(pool, actor, data_id)
    assert healed["state"] == "enriched"
    assert "a: 1" in healed["extracted_text"]


async def test_an_item_already_examined_is_not_swept_again(
    pool, blobs, settings, embedder, tenant, principal_for
):
    """`parse_status` is what distinguishes 'never read' from 'read and
    declined'. Without it the sweep would retry an unsupported file forever."""
    import base64

    from memdog.contracts import Inline, WriteOptions
    from memdog.workers import EventWorker, ParseWorker

    actor = await principal_for(tenant.api_key)
    queue = InProcessQueue()
    parse = ParseWorker(pool, blobs, queue=queue)
    parse.register(queue)
    # The enrichment event is what causes a parse attempt, so the worker that
    # consumes it has to be present for the item to be examined at all.
    EventWorker(pool, queue, parse_worker=parse).register(queue)
    await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=tenant.producer_id, items=[
            WriteItem(external_id="mystery.dcm",
                      content=Inline(bytes_b64=base64.b64encode(b"\x00\x01\x02\x03").decode())),
        ],
                        options=WriteOptions(enrich=True)),
    )
    await queue.drain()

    embed = EmbedWorker(pool, embedder, settings, queue=queue)
    swept = await reconcile(
        pool, queue, embed_generator=embed.generator_version, grace_seconds=0
    )
    await queue.close()
    # `parse_status` is what distinguishes "never read" from "read and
    # declined". The item must not be re-parsed; unconsumed events are a
    # separate concern and may legitimately be re-delivered.
    assert swept.parse == 0
