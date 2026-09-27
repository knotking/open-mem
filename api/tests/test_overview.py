"""The overview.

Every number is ACL-scoped, so two people looking at the same project can
legitimately see different totals. That is correct, and it is the reason these
are computed rather than kept as counters somewhere.
"""

from __future__ import annotations

import base64

import pytest

from open_mem.contracts import Inline, MemoryRef, WriteItem, WriteRequest, WriteOptions
from open_mem.retrieval import project_overview
from open_mem.write import write_items

pytestmark = pytest.mark.asyncio


async def test_it_reports_the_corpus_and_what_is_not_being_read(
    pool, queue, blobs, settings, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=tenant.producer_id, items=[
            WriteItem(external_id="doc-1", content=Inline(text="A readable document."),
                      memory=MemoryRef(key="t-1", type="conversation")),
            # A binary blob nothing can parse: it must show up as "not read"
            # rather than vanishing from the totals.
            WriteItem(external_id="mystery.dcm",
                      content=Inline(bytes_b64=base64.b64encode(b"\x00\x01\x02\x03").decode())),
        ],
                        options=WriteOptions(enrich=True)),
    )
    await queue.drain()

    view = await project_overview(pool, actor, tenant.project_id)
    assert view["counts"]["total"] == 2
    assert view["counts"]["enriched"] == 1
    assert view["counts"]["with_bytes"] == 1
    assert view["derived"]["chunks"] >= 1
    # The property the whole design turns on, surfaced as a number.
    assert view["derived"]["vector_spaces"] == 1
    assert view["containers"]["memories"] >= 1
    assert view["activity"]["writes_24h"] >= 2

    not_read = {r["parse_status"]: r["n"] for r in view["not_read"]}
    assert not_read.get("unsupported") == 1


async def test_the_totals_are_what_the_caller_can_see(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Not the project's true size -- the caller's view of it."""
    from open_mem.auth import DATA_READ, issue_key
    from open_mem.bootstrap import create_user

    owner = await principal_for(tenant.api_key)
    await write_items(
        pool, queue, blobs, settings, owner,
        WriteRequest(producer_id=tenant.producer_id, items=[
            WriteItem(external_id="private-doc", content=Inline(text="Private.")),
        ],
                        options=WriteOptions(enrich=True)),
    )
    await queue.drain()

    other_id = await create_user(pool, "viewer-overview@example.com")
    await pool.execute(
        "INSERT INTO memberships (user_id, org_id, role) VALUES ($1, $2, 'member')",
        other_id, tenant.org_id,
    )
    token = await issue_key(pool, user_id=other_id, org_id=tenant.org_id,
                            capabilities=[DATA_READ])
    outsider = await principal_for(token)

    assert (await project_overview(pool, owner, tenant.project_id))["counts"]["total"] == 1
    assert (await project_overview(pool, outsider, tenant.project_id))["counts"]["total"] == 0
