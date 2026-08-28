"""Version history.

Nothing is mutated in place, which is only useful if the history answers the
question it exists for: *why does this say something different than it did last
week?* That needs the revision, what produced it, and the ability to see the
previous text -- not just a count.
"""

from __future__ import annotations

import pytest

from memdog.contracts import Inline, WriteItem, WriteRequest
from memdog.queue import InProcessQueue
from memdog.retrieval import NotFound, get_versions
from memdog.workers import ParseWorker
from memdog.write import write_items

pytestmark = pytest.mark.asyncio


async def _write(pool, queue, blobs, settings, actor, producer_id, external_id, text):
    return await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=producer_id, items=[
            WriteItem(external_id=external_id, content=Inline(text=text)),
        ]),
    )


async def test_rewriting_a_key_appends_a_revision_and_keeps_the_old_text(
    pool, queue, blobs, settings, tenant, principal_for
):
    """The update path. Same id, new revision, and the previous text survives --
    otherwise 'what changed' is unanswerable."""
    actor = await principal_for(tenant.api_key)
    first = await _write(pool, queue, blobs, settings, actor, tenant.producer_id,
                          "doc-1", "The filing was delayed.")
    await queue.drain()
    second = await _write(pool, queue, blobs, settings, actor, tenant.producer_id,
                           "doc-1", "The filing was delayed, then resubmitted on Friday.")
    await queue.drain()

    assert first.results[0].data_id == second.results[0].data_id
    assert second.results[0].status == "updated"

    versions = await get_versions(pool, actor, first.results[0].data_id)
    assert [v["revision"] for v in versions] == [2, 1]
    assert "resubmitted on Friday" in versions[0]["preview"]
    assert "resubmitted" not in (versions[1]["preview"] or "")
    # Different content, therefore different checksums -- the cheap way to see
    # that a revision was a real change rather than a re-run.
    assert versions[0]["checksum"] != versions[1]["checksum"]


async def test_a_rewrite_with_identical_content_is_still_a_revision(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Re-crawling the same record is an event even when nothing changed, and
    the matching checksums are what make that legible rather than confusing."""
    actor = await principal_for(tenant.api_key)
    text = "Unchanged between crawls."
    first = await _write(pool, queue, blobs, settings, actor, tenant.producer_id, "doc-2", text)
    await queue.drain()
    await _write(pool, queue, blobs, settings, actor, tenant.producer_id, "doc-2", text)
    await queue.drain()

    versions = await get_versions(pool, actor, first.results[0].data_id)
    assert len(versions) == 2
    assert versions[0]["checksum"] == versions[1]["checksum"]


async def test_parsing_bytes_adds_a_revision_above_the_write(
    pool, blobs, settings, tenant, principal_for
):
    """Revision 1 is the upload -- zero characters, because the caller sent
    bytes. Revision 2 is what the parser made of them."""
    import base64

    actor = await principal_for(tenant.api_key)
    queue = InProcessQueue()
    ParseWorker(pool, blobs, queue=queue).register(queue)
    csv_bytes = b"account,amount\nreconciliation,4200\n"
    written = await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=tenant.producer_id, items=[
            WriteItem(external_id="ledger.csv",
                      content=Inline(bytes_b64=base64.b64encode(csv_bytes).decode())),
        ]),
    )
    await queue.drain()
    await queue.close()

    versions = await get_versions(pool, actor, written.results[0].data_id)
    assert [(v["revision"], v["source"]) for v in versions] == [(2, "parse"), (1, "write")]
    assert versions[1]["content_chars"] == 0
    assert "reconciliation" in versions[0]["preview"]
    # No model was involved, so none is claimed.
    assert versions[0]["model_id"] is None


async def test_versions_are_not_a_second_access_path(
    pool, queue, blobs, settings, tenant, other_tenant, principal_for
):
    """A version is not independently shareable. Treating it as its own object
    would be a way to read content whose permissions live on the parent."""
    owner = await principal_for(tenant.api_key)
    intruder = await principal_for(other_tenant.api_key)
    written = await _write(pool, queue, blobs, settings, owner, tenant.producer_id,
                            "private-doc", "The acquisition price was eighty four million.")
    await queue.drain()

    assert await get_versions(pool, owner, written.results[0].data_id)
    with pytest.raises(NotFound):
        await get_versions(pool, intruder, written.results[0].data_id)


async def test_reading_versions_is_audited_like_any_other_read(
    pool, queue, blobs, settings, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    written = await _write(pool, queue, blobs, settings, actor, tenant.producer_id,
                            "audited-doc", "Some content.")
    await queue.drain()
    await get_versions(pool, actor, written.results[0].data_id)

    actions = await pool.fetch(
        "SELECT action FROM access_log WHERE data_id = $1", written.results[0].data_id
    )
    assert "versions.read" in {r["action"] for r in actions}


async def test_revisions_are_dense_and_ordered_under_concurrency(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Revision numbers come from the table, not a counter on the row, so two
    workers racing produce two revisions rather than one lost update."""
    import asyncio

    from memdog.workers import record_version

    actor = await principal_for(tenant.api_key)
    written = await _write(pool, queue, blobs, settings, actor, tenant.producer_id,
                            "racy-doc", "Original.")
    await queue.drain()
    data_id = written.results[0].data_id

    async def append(n: int) -> None:
        async with pool.acquire() as conn, conn.transaction():
            await record_version(conn, data_id, source="reprocess",
                                 content_text=f"rebuild {n}")

    await asyncio.gather(*(append(i) for i in range(4)))
    versions = await get_versions(pool, actor, data_id)
    numbers = sorted(v["revision"] for v in versions)
    # Exactly five: the write plus four appends. A dropped revision would look
    # identical to the change never having happened, so nothing may be lost.
    assert numbers == [1, 2, 3, 4, 5]
    bodies = {v["preview"] for v in versions if v["source"] == "reprocess"}
    assert len(bodies) == 4
