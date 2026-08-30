"""Compaction.

The test that matters is the first one: **compaction never deletes.** mem0
reconciles by overwriting and what it replaces is gone; the temporal graph here
shipped on the opposite premise, so a compaction that destroyed its inputs would
make `as_of` lie about everything it touched.

The rest guard the things that would be quietly wrong: an artifact that widens
visibility by summarising, a preview that writes, and a scheduled job nobody
previewed.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from memdog.compaction import (
    CompactionError, create_job, list_jobs, run, runs_for, set_enabled, tick,
    update_job,
)
from memdog.ids import new_id

pytestmark = pytest.mark.asyncio


async def _memory(pool, tenant, name="working set", kind="organizational"):
    memory_id = new_id("mem")
    await pool.execute(
        """
        INSERT INTO memories (memory_id, org_id, project_id, type, memory_key, owner_id)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        memory_id, tenant.org_id, tenant.project_id, kind, name, tenant.user_id,
    )
    return memory_id


async def _member(pool, tenant, memory_id, external_id, text, *, access="org"):
    data_id = new_id("data")
    checksum = "sha256:" + str(abs(hash(text)))
    await pool.execute(
        """
        INSERT INTO data_items (data_id, org_id, project_id, owner_id, producer_id,
            external_id, state, access_level, content_text, checksum, event_time)
        VALUES ($1, $2, $3, $4, $5, $6, 'enriched', $7, $8, $9, now())
        """,
        data_id, tenant.org_id, tenant.project_id, tenant.user_id,
        tenant.producer_id, external_id, access, text, checksum,
    )
    await pool.execute(
        "INSERT INTO memory_members (memory_id, data_id, added_by) VALUES ($1, $2, 'explicit')",
        memory_id, data_id)
    return data_id


class FakeExtractor:
    model_id = "test-extractor"

    def __init__(self):
        self.calls = 0

    async def extract(self, text, *, data_type, prompt=None):
        self.calls += 1

        class Envelope:
            title = "Folded"
            summary = "A summary of the members."
        return Envelope()


async def test_compaction_archives_and_never_deletes(pool, tenant, principal_for):
    """The test the feature exists to pass.

    An archived member is out of the working set and still fetchable. If this
    ever fails, compaction has become a deletion with a friendlier name.
    """
    actor = await principal_for(tenant.api_key)
    memory_id = await _memory(pool, tenant)
    a = await _member(pool, tenant, memory_id, "one", "the same text")
    b = await _member(pool, tenant, memory_id, "two", "the same text")

    result = await run(pool, actor, memory_id=memory_id, algorithm="dedupe")
    assert result["archived"] == 1

    rows = await pool.fetch(
        "SELECT data_id, archived_at, content_text FROM data_items "
        "WHERE data_id = ANY($1::text[])", [a, b])
    assert len(rows) == 2, "both rows still exist"
    assert all(r["content_text"] for r in rows), "and still have their text"
    assert sum(1 for r in rows if r["archived_at"]) == 1


async def test_archived_members_leave_the_working_set_but_not_the_corpus(
    pool, tenant, principal_for
):
    """The whole user-visible effect: a default, not a deletion."""
    from memdog.retrieval import list_items

    actor = await principal_for(tenant.api_key)
    memory_id = await _memory(pool, tenant)
    await _member(pool, tenant, memory_id, "one", "duplicated")
    await _member(pool, tenant, memory_id, "two", "duplicated")
    await run(pool, actor, memory_id=memory_id, algorithm="dedupe")

    default_view = await list_items(pool, actor, tenant.project_id)
    with_archived = await list_items(pool, actor, tenant.project_id, include_archived=True)
    assert len(with_archived["items"]) == len(default_view["items"]) + 1


async def test_a_preview_writes_nothing(pool, tenant, principal_for):
    """Same code path, writes withheld — so what it reports is what would happen."""
    actor = await principal_for(tenant.api_key)
    memory_id = await _memory(pool, tenant)
    await _member(pool, tenant, memory_id, "one", "same")
    await _member(pool, tenant, memory_id, "two", "same")

    result = await run(pool, actor, memory_id=memory_id, algorithm="dedupe", mode="dry")
    assert result["archived"] == 1, "it reports what a live run would do"
    assert await pool.fetchval(
        "SELECT count(*) FROM data_items WHERE archived_at IS NOT NULL") == 0


async def test_a_summary_takes_the_acl_of_its_most_restrictive_source(
    pool, tenant, principal_for
):
    """Otherwise compaction is a way to widen visibility by summarising.

    A summary spanning a private record and two org ones is private — the rule
    `acl.strictest` applies to every other derived artifact, and a compaction
    artifact is not special.
    """
    actor = await principal_for(tenant.api_key)
    memory_id = await _memory(pool, tenant)
    await _member(pool, tenant, memory_id, "public-ish", "alpha", access="org")
    await _member(pool, tenant, memory_id, "secret", "beta", access="private")

    await run(pool, actor, memory_id=memory_id, algorithm="summarize",
              extractor=FakeExtractor())
    level = await pool.fetchval(
        "SELECT access_level FROM artifacts WHERE kind = 'compaction'")
    assert level == "private"


async def test_a_summary_records_where_each_part_came_from(pool, tenant, principal_for):
    """With offsets. Without them a summary can name its sources and not point
    into them, and every citation degrades to a document-level reference."""
    actor = await principal_for(tenant.api_key)
    memory_id = await _memory(pool, tenant)
    await _member(pool, tenant, memory_id, "one", "the first passage")
    await _member(pool, tenant, memory_id, "two", "the second passage")

    await run(pool, actor, memory_id=memory_id, algorithm="summarize",
              extractor=FakeExtractor())
    rows = await pool.fetch(
        "SELECT data_id, span_start, span_end FROM artifact_sources ORDER BY span_start")
    assert len(rows) == 2
    assert rows[0]["span_end"] > rows[0]["span_start"]
    assert rows[1]["span_start"] > rows[0]["span_end"], "spans do not overlap"


async def test_summarize_refuses_rather_than_pretending_without_a_model(
    pool, tenant, principal_for
):
    """`dedupe` needs no model and is offered as the alternative."""
    actor = await principal_for(tenant.api_key)
    memory_id = await _memory(pool, tenant)
    await _member(pool, tenant, memory_id, "one", "alpha")
    await _member(pool, tenant, memory_id, "two", "beta")
    with pytest.raises(CompactionError) as exc:
        await run(pool, actor, memory_id=memory_id, algorithm="summarize", extractor=None)
    assert exc.value.status == 503
    assert "dedupe" in str(exc.value)


async def test_scheduling_requires_a_preview_of_this_version(pool, tenant, principal_for):
    """A compaction nobody has looked at is one that empties a memory quietly."""
    actor = await principal_for(tenant.api_key)
    memory_id = await _memory(pool, tenant)
    job = await create_job(pool, actor, project_id=tenant.project_id,
                           name="nightly", memory_id=memory_id, algorithm="dedupe",
                           schedule={"type": "interval", "every_seconds": 86400})

    with pytest.raises(CompactionError) as exc:
        await set_enabled(pool, actor, job["job_id"], True)
    assert exc.value.status == 409

    await run(pool, actor, job_id=job["job_id"], mode="dry")
    assert (await set_enabled(pool, actor, job["job_id"], True))["enabled"]


async def test_editing_what_it_would_do_stops_it(pool, tenant, principal_for):
    actor = await principal_for(tenant.api_key)
    memory_id = await _memory(pool, tenant)
    other = await _memory(pool, tenant, name="somewhere else")
    job = await create_job(pool, actor, project_id=tenant.project_id, name="j",
                           memory_id=memory_id, algorithm="dedupe")
    await run(pool, actor, job_id=job["job_id"], mode="dry")
    await set_enabled(pool, actor, job["job_id"], True)

    edited = await update_job(pool, actor, job["job_id"], {"memory_id": other})
    assert edited["enabled"] is False and edited["dry_run_version"] is None


async def test_a_run_records_what_it_cost_and_freed(pool, tenant, principal_for):
    """Metrics somebody deciding whether to keep the job would actually ask for."""
    actor = await principal_for(tenant.api_key)
    memory_id = await _memory(pool, tenant)
    job = await create_job(pool, actor, project_id=tenant.project_id, name="j",
                           memory_id=memory_id, algorithm="dedupe")
    await _member(pool, tenant, memory_id, "one", "identical text here")
    await _member(pool, tenant, memory_id, "two", "identical text here")
    await run(pool, actor, job_id=job["job_id"])

    runs = await runs_for(pool, actor, job["job_id"])
    live = [r for r in runs if r["mode"] == "live"][0]
    assert live["considered"] == 2 and live["archived"] == 1
    assert live["bytes_before"] > live["bytes_after"], "it freed something"
    assert live["model_calls"] == 0, "dedupe costs nothing"


async def test_the_sweep_runs_only_jobs_that_are_due(pool, tenant, principal_for):
    actor = await principal_for(tenant.api_key)
    memory_id = await _memory(pool, tenant)
    job = await create_job(pool, actor, project_id=tenant.project_id, name="j",
                           memory_id=memory_id, algorithm="dedupe",
                           schedule={"type": "interval", "every_seconds": 3600})
    assert (await tick(pool))["ran"] == [], "not scheduled yet"

    await run(pool, actor, job_id=job["job_id"], mode="dry")
    await set_enabled(pool, actor, job["job_id"], True)
    await pool.execute("UPDATE compaction_jobs SET next_due_at = now() WHERE job_id = $1",
                       job["job_id"])
    assert len((await tick(pool))["ran"]) == 1
    # And it re-schedules itself rather than running every pass.
    assert (await tick(pool))["ran"] == []


async def test_compacting_twice_does_not_re_archive(pool, tenant, principal_for):
    """Already-archived members are out of the working set; counting them again
    would report work that did not happen."""
    actor = await principal_for(tenant.api_key)
    memory_id = await _memory(pool, tenant)
    await _member(pool, tenant, memory_id, "one", "same")
    await _member(pool, tenant, memory_id, "two", "same")

    first = await run(pool, actor, memory_id=memory_id, algorithm="dedupe")
    second = await run(pool, actor, memory_id=memory_id, algorithm="dedupe")
    assert first["archived"] == 1 and second["archived"] == 0
