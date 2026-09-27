"""Bulk interpretation, and the difference between rebuilding and asking.

`embed` and `enrich` rebuild derived work that already exists. `interpret` is
the one that does not: enrichment is opt-in, so a crawl or a feed run with it
off produces a corpus that is stored, durable and unfindable -- and until this
existed the way out was one item at a time.
"""

from __future__ import annotations

import pytest

from open_mem.contracts import Inline, WriteItem, WriteOptions, WriteRequest
from open_mem.events import list_events
from open_mem.reprocess import ReprocessWorker, request_reprocess
from open_mem.retrieval import get_item
from open_mem.write import write_items

pytestmark = pytest.mark.asyncio

TEXT = (
    "The reconciliation job stalled overnight and finance escalated on Tuesday. "
    "The platform team rolled the deploy back at 14:02 UTC."
)


async def _stranded(pool, queue, blobs, settings, actor, tenant, n: int = 3) -> list[str]:
    """Items written the way a crawler with `enrich` off writes them."""
    response = await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(
            producer_id=tenant.producer_id,
            items=[WriteItem(external_id=f"cold-{i}", content=Inline(text=TEXT), tags=["cold"])
                   for i in range(n)],
            options=WriteOptions(enrich=False),
        ),
    )
    await queue.drain()
    return [r.data_id for r in response.results]


async def test_a_corpus_nobody_asked_to_interpret_can_be_asked_for_in_bulk(
    pool, queue, blobs, settings, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    data_ids = await _stranded(pool, queue, blobs, settings, actor, tenant)
    for data_id in data_ids:
        assert (await get_item(pool, actor, data_id))["state"] == "stored"

    ReprocessWorker(pool, queue).register(queue)
    result = await request_reprocess(
        pool, queue, actor,
        selector={"project_id": tenant.project_id, "tags": ["cold"]},
        stage="interpret",
    )
    assert result["items"] == len(data_ids)
    await queue.drain()

    for data_id in data_ids:
        assert (await get_item(pool, actor, data_id))["state"] == "enriched"


async def test_a_bulk_interpret_is_recorded_as_a_request_not_just_a_message(
    pool, queue, blobs, settings, tenant, principal_for
):
    """The reconciler repairs *requested* work and never invents it.

    So a bulk interpret that published straight onto the topic would be the one
    kind of enrichment a dropped message loses for good: nothing in the log
    would say it was ever wanted.
    """
    actor = await principal_for(tenant.api_key)
    data_ids = await _stranded(pool, queue, blobs, settings, actor, tenant, n=2)
    ReprocessWorker(pool, queue).register(queue)
    result = await request_reprocess(
        pool, queue, actor,
        selector={"project_id": tenant.project_id, "tags": ["cold"]},
        stage="interpret",
    )
    await queue.drain()

    for data_id in data_ids:
        events = [e for e in await list_events(pool, tenant.org_id, data_id=data_id)
                  if e["event_type"] == "enrichment.requested"]
        assert len(events) == 1, "one request per item, and it is in the log"
        assert events[0]["payload"]["run_id"] == result["run_id"]


async def test_a_dry_run_shows_what_it_would_touch_not_only_how_much(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A count reads the same whether the selector caught the corpus you meant
    or every record in the project. The way to tell them apart is to look."""
    actor = await principal_for(tenant.api_key)
    await _stranded(pool, queue, blobs, settings, actor, tenant, n=3)

    preview = await request_reprocess(
        pool, queue, actor,
        selector={"project_id": tenant.project_id, "tags": ["cold"]},
        stage="interpret", dry_run=True,
    )
    assert preview["items"] == 3
    assert preview["by_state"] == {"stored": 3}
    assert len(preview["samples"]) == 3
    assert preview["samples"][0]["external_id"].startswith("cold-")
    assert preview["samples"][0]["preview"], "a sample without its text is not a sample"
    assert preview["capped"] is False


async def test_a_dry_run_changes_nothing(
    pool, queue, blobs, settings, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    data_ids = await _stranded(pool, queue, blobs, settings, actor, tenant, n=2)
    ReprocessWorker(pool, queue).register(queue)
    await request_reprocess(
        pool, queue, actor,
        selector={"project_id": tenant.project_id, "tags": ["cold"]},
        stage="interpret", dry_run=True,
    )
    await queue.drain()
    for data_id in data_ids:
        assert (await get_item(pool, actor, data_id))["state"] == "stored"
        assert not [e for e in await list_events(pool, tenant.org_id, data_id=data_id)
                    if e["event_type"] == "enrichment.requested"]


async def test_an_unknown_stage_is_refused(pool, queue, tenant, principal_for):
    actor = await principal_for(tenant.api_key)
    with pytest.raises(ValueError):
        await request_reprocess(
            pool, queue, actor,
            selector={"project_id": tenant.project_id, "tags": ["cold"]},
            stage="summarise-everything", dry_run=True,
        )
