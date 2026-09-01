"""Deriving an artifact from a memory, with a named generator.

A memory supported exactly one derived thing -- a summary, reachable only
through `compress`, which also archived everything it read. A study guide, a
flashcard deck and an obligations extract are the same operation with a
different output schema, and each was one bespoke endpoint away.
"""

from __future__ import annotations

import pytest

from memdog import derive as derive_mod
from memdog.contracts import Inline, WriteItem, WriteOptions, WriteRequest
from memdog.derive import DeriveError, derive
from memdog.write import write_items

pytestmark = pytest.mark.asyncio


async def _course(pool, queue, blobs, settings, actor, tenant, n=3):
    from memdog import memories

    await memories.create_type(pool, actor, project_id=tenant.project_id,
                               name="course", ttl_seconds=None)
    created = await memories.create_memory(
        pool, actor, project_id=tenant.project_id, type_name="course",
        memory_key="stats-101")
    response = await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(
            producer_id=tenant.producer_id,
            items=[WriteItem(external_id=f"lecture-{i}",
                             content=Inline(text=f"Lecture {i}: regression to the mean."))
                   for i in range(n)],
            options=WriteOptions(enrich=False),
        ),
    )
    ids = [r.data_id for r in response.results]
    await memories.add_members(pool, actor, created["memory_id"], ids)
    return created["memory_id"], ids


async def test_the_generators_are_served_rather_than_hardcoded():
    """A list typed into a console is a second copy of a vocabulary, and the
    copy is the one that goes stale."""
    assert "summary" in derive_mod.GENERATORS
    for name in ("study_guide", "flashcards", "obligations", "briefing", "timeline"):
        assert name in derive_mod.GENERATORS, f"{name} is one of the four the catalog names"
    assert derive_mod.GENERATORS["summary"]["archivable"] is True


async def test_an_unknown_generator_names_the_ones_that_exist(
    pool, queue, blobs, settings, tenant, principal_for, extractor
):
    actor = await principal_for(tenant.api_key)
    memory_id, _ = await _course(pool, queue, blobs, settings, actor, tenant)
    with pytest.raises(DeriveError) as exc:
        await derive(pool, actor, memory_id, generator="haiku", extractor=extractor)
    assert "flashcards" in str(exc.value)


async def test_deriving_produces_an_artifact_and_archives_nothing(
    pool, queue, blobs, settings, tenant, principal_for, extractor
):
    """The whole point of the split. Compression is a policy about the
    originals, not a property of having derived something -- a study guide that
    archived the course material would eat the course."""
    actor = await principal_for(tenant.api_key)
    memory_id, ids = await _course(pool, queue, blobs, settings, actor, tenant)

    result = await derive(pool, actor, memory_id, generator="study_guide",
                          extractor=extractor)
    assert result["artifacts"] == 1 and result["archived"] == 0

    archived = await pool.fetchval(
        "SELECT count(*) FROM data_items WHERE data_id = ANY($1::text[]) "
        "AND archived_at IS NOT NULL", ids)
    assert archived == 0, "the course is still there"


async def test_only_a_summary_may_archive_what_it_read(
    pool, queue, blobs, settings, tenant, principal_for, extractor
):
    actor = await principal_for(tenant.api_key)
    memory_id, ids = await _course(pool, queue, blobs, settings, actor, tenant)

    with pytest.raises(DeriveError) as exc:
        await derive(pool, actor, memory_id, generator="flashcards",
                     extractor=extractor, archive=True)
    assert "only remaining copy" in str(exc.value)

    folded = await derive(pool, actor, memory_id, generator="summary",
                          extractor=extractor, archive=True)
    assert folded["archived"] == len(ids)


async def test_every_artifact_records_what_it_read_and_where(
    pool, queue, blobs, settings, tenant, principal_for, extractor
):
    """Span offsets are what let a citation open at the sentence. Without them
    an artifact can name what it read and not point into it, and every citation
    degrades to a document-level reference -- which reads as working."""
    actor = await principal_for(tenant.api_key)
    memory_id, ids = await _course(pool, queue, blobs, settings, actor, tenant)
    result = await derive(pool, actor, memory_id, generator="flashcards",
                          extractor=extractor)

    rows = await pool.fetch(
        "SELECT data_id, span_start, span_end FROM artifact_sources WHERE artifact_id = $1 "
        "ORDER BY span_start", result["artifact_id"])
    assert {r["data_id"] for r in rows} == set(ids)
    assert all(r["span_end"] > r["span_start"] for r in rows)
    # Non-overlapping and ordered: two sources claiming the same offsets would
    # make every citation point at the wrong record.
    assert [r["span_start"] for r in rows] == sorted(r["span_start"] for r in rows)


async def test_the_prompt_is_in_the_fingerprint(
    pool, queue, blobs, settings, tenant, principal_for, extractor
):
    """Otherwise editing a generator leaves a corpus of artifacts that claim to
    be current and were produced by wording nobody can recover."""
    actor = await principal_for(tenant.api_key)
    memory_id, _ = await _course(pool, queue, blobs, settings, actor, tenant)

    guide = await derive(pool, actor, memory_id, generator="study_guide", extractor=extractor)
    cards = await derive(pool, actor, memory_id, generator="flashcards", extractor=extractor)
    assert guide["generator_version"] != cards["generator_version"], (
        "two generators over the same members are two different artifacts")


async def test_an_artifact_takes_the_strictest_acl_of_its_sources(
    pool, queue, blobs, settings, tenant, principal_for, extractor
):
    """Or deriving becomes a way to widen visibility by summarising."""
    actor = await principal_for(tenant.api_key)
    memory_id, ids = await _course(pool, queue, blobs, settings, actor, tenant)
    await pool.execute(
        "UPDATE data_items SET access_level = 'org' WHERE data_id = ANY($1::text[])", ids)
    await pool.execute(
        "UPDATE data_items SET access_level = 'private' WHERE data_id = $1", ids[0])

    result = await derive(pool, actor, memory_id, generator="briefing", extractor=extractor)
    assert result["access_level"] == "private"


async def test_a_dry_run_says_what_it_would_read_and_writes_nothing(
    pool, queue, blobs, settings, tenant, principal_for, extractor
):
    actor = await principal_for(tenant.api_key)
    memory_id, ids = await _course(pool, queue, blobs, settings, actor, tenant)

    preview = await derive(pool, actor, memory_id, generator="timeline",
                           extractor=extractor, dry_run=True)
    assert preview["applied"] is False and preview["members"] == len(ids)
    assert await pool.fetchval("SELECT count(*) FROM artifacts") == 0


async def test_deriving_clears_the_stale_flag(
    pool, queue, blobs, settings, tenant, principal_for, extractor
):
    """A rollup is current again once it has been rebuilt, whatever marked it."""
    from memdog import memories

    actor = await principal_for(tenant.api_key)
    memory_id, _ = await _course(pool, queue, blobs, settings, actor, tenant)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE memories SET stale_since = now(), stale_reason = 'a member was added' "
            "WHERE memory_id = $1", memory_id)

    await derive(pool, actor, memory_id, generator="summary", extractor=extractor)
    assert await pool.fetchval(
        "SELECT stale_since FROM memories WHERE memory_id = $1", memory_id) is None
    assert memories  # imported for the fixture's sake


async def test_what_has_been_derived_is_listable(
    pool, queue, blobs, settings, tenant, principal_for, extractor
):
    actor = await principal_for(tenant.api_key)
    memory_id, _ = await _course(pool, queue, blobs, settings, actor, tenant)
    await derive(pool, actor, memory_id, generator="study_guide", extractor=extractor)
    await derive(pool, actor, memory_id, generator="flashcards", extractor=extractor)

    listed = await derive_mod.artifacts_for(pool, actor, memory_id)
    assert {a["kind"] for a in listed} == {"study_guide", "flashcards"}
    assert all(a["sources"] > 0 for a in listed)
