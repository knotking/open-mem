"""Re-adding a file after it was erased."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_writing_again_after_an_erasure_brings_the_item_back(
    pool, queue, blobs, tenant, principal_for, settings
):
    """The bug that made a document impossible to re-add.

    The upsert's conflict target is `(project, producer, external_id)` and it
    matches a tombstoned row like any other. Without clearing `deleted_at` the
    write updated the dead row and returned its id: the response said success,
    every read of that id answered 404 because the ACL predicate excludes
    deleted rows, and the console reported a failure it could not explain. The
    file was, precisely as reported, never saved -- written into a row nothing
    could see, with no error anywhere.
    """
    from memdog.contracts import Inline, WriteItem, WriteRequest
    from memdog.deletion import request_deletion
    from memdog.write import write_items

    principal = await principal_for(tenant.api_key)

    def request():
        return WriteRequest(
            producer_id=tenant.producer_id,
            items=[WriteItem(external_id="gita.pdf",
                             content=Inline(text="Chapter 1: Arjuna Vishada Yoga"))],
        )

    first = await write_items(pool, queue, blobs, settings, principal, request())
    data_id = first.results[0].data_id
    assert first.results[0].status == "created"

    await request_deletion(pool, queue, principal,
                           selector={"project_id": tenant.project_id,
                                     "data_ids": [data_id]},
                           reason="test")
    assert await pool.fetchval(
        "SELECT deleted_at IS NOT NULL FROM data_items WHERE data_id = $1", data_id)

    # The same file, added again -- which is an ordinary thing to do.
    again = await write_items(pool, queue, blobs, settings, principal, request())
    assert again.results[0].status in ("created", "updated")
    back = again.results[0].data_id

    # Whatever id it lands on, it must be readable. That is the whole point.
    row = await pool.fetchrow(
        "SELECT deleted_at, purged_at, content_text FROM data_items WHERE data_id = $1", back)
    assert row["deleted_at"] is None, "re-added item is still tombstoned and cannot be read"
    assert row["purged_at"] is None
    assert "Arjuna" in (row["content_text"] or "")
