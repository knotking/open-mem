"""The meter.

What is being checked here is not "does a row appear". It is the three
properties that separate a meter from a log, each of which is a way the obvious
implementation under-counts:

  * a call that failed still cost what it generated
  * the engine that answered is what is billed, not the one configured
  * a fallback from free to paid is visible as such
"""

from __future__ import annotations

import pytest

from memdog import quota, usage
from memdog.routing import Chain, Step, Unavailable

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def metered(pool):
    usage.configure(pool)
    try:
        yield pool
    finally:
        usage.reset()


async def _events(pool):
    return [dict(r) for r in await pool.fetch(
        "SELECT * FROM usage_events ORDER BY sequence"
    )]


async def test_a_successful_call_is_recorded_with_its_token_split(metered, tenant):
    async def call():
        usage.observe(tokens_in=1000, tokens_out=500, tokens_cached=200)
        return "answered"

    with usage.attributed(org_id=tenant.org_id, project_id=tenant.project_id,
                          user_id=tenant.user_id):
        async with usage.meter("answer", "gemini", model_id="gemini-2.5-flash"):
            await call()

    events = await _events(metered)
    assert len(events) == 1
    event = events[0]
    assert event["status"] == "ok"
    assert event["serving_engine"] == "gemini"
    assert event["serving_model"] == "gemini-2.5-flash"
    # Three columns, not a total: providers price them apart, so a sum cannot
    # be turned back into a cost.
    assert (event["tokens_in"], event["tokens_out"], event["tokens_cached"]) == (
        1000, 500, 200
    )
    assert event["org_id"] == tenant.org_id
    assert event["project_id"] == tenant.project_id
    assert event["user_id"] == tenant.user_id
    # 1000 in at 1.0 + 500 out at 4.0 + 200 cached at 0.25, per thousand.
    assert event["credits"] == 4


async def test_a_call_that_failed_after_generating_is_still_billed(metered, tenant):
    """The under-count this exists to prevent.

    A call that times out having produced three thousand tokens consumed three
    thousand tokens. Recording only successes reports less spend than occurred,
    in exactly the direction that produces a surprise bill.
    """
    with usage.attributed(org_id=tenant.org_id):
        with pytest.raises(TimeoutError):
            async with usage.meter("answer", "gemini", model_id="gemini-2.5-flash"):
                usage.observe(tokens_in=3000, tokens_out=1200)
                raise TimeoutError("provider took too long")

    events = await _events(metered)
    assert len(events) == 1
    assert events[0]["status"] == "timeout"
    assert events[0]["tokens_in"] == 3000
    assert events[0]["credits"] > 0


async def test_a_fallback_to_a_paid_engine_is_flagged_as_a_crossing(metered, tenant):
    """A $0 operation becoming a paid one is a category change, not a
    degradation -- so it is not inferable from `fallback_depth` alone."""
    async def broken(*_args, **_kwargs):
        raise Unavailable("local engine is down")

    async def paid(*_args, **_kwargs):
        usage.observe(tokens_in=100, tokens_out=100)
        return "answered"

    chain = Chain("extract", [
        Step(name="local", model_id="local-heuristic", call=broken),
        Step(name="gemini", model_id="gemini-2.5-flash", call=paid),
    ])

    with usage.attributed(org_id=tenant.org_id):
        served = await chain.run("some text")

    assert served.depth == 1
    events = await _events(metered)
    assert [e["serving_engine"] for e in events] == ["local", "gemini"]
    # The free primary that failed is not a crossing; the paid fallback that
    # answered is.
    assert [e["crossed_to_paid"] for e in events] == [False, True]
    assert events[0]["status"] == "failed"
    assert events[1]["status"] == "ok"
    # Attribution follows the engine that served, never the one configured.
    assert events[1]["serving_model"] == "gemini-2.5-flash"
    assert events[1]["configured_model"] == "local-heuristic"


async def test_a_paid_primary_answering_is_not_a_crossing(metered, tenant):
    async def paid(*_args, **_kwargs):
        usage.observe(tokens_in=10)
        return "answered"

    chain = Chain("answer", [Step(name="gemini", model_id="g", call=paid)])
    with usage.attributed(org_id=tenant.org_id):
        await chain.run("q")

    events = await _events(metered)
    assert events[0]["crossed_to_paid"] is False


async def test_the_rollup_moves_with_the_event(metered, tenant):
    """Enforcement reads the rollup, so an event without its increment would
    make every budget under-count -- the one direction a spending control must
    not fail in."""
    with usage.attributed(org_id=tenant.org_id, project_id=tenant.project_id,
                          user_id=tenant.user_id):
        for _ in range(3):
            async with usage.meter("answer", "gemini", model_id="g"):
                usage.observe(tokens_in=1000, tokens_out=1000)

    rows = {
        (r["scope"], r["scope_id"]): r
        for r in await metered.fetch("SELECT * FROM usage_spend")
    }
    assert set(rows) == {
        ("org", tenant.org_id),
        ("project", tenant.project_id),
        ("user", tenant.user_id),
    }
    for row in rows.values():
        assert row["events"] == 3
        assert row["credits"] == 15   # (1000*1 + 1000*4)/1000 = 5, three times


async def test_an_unattributed_call_does_not_break_the_operation(metered):
    """A model call with nobody to bill is a real condition -- a smoke test, a
    CLI sweep -- and failing the call because billing could not attribute it
    would be worse than the missing row."""
    async with usage.meter("answer", "gemini", model_id="g"):
        usage.observe(tokens_in=10)

    assert await _events(metered) == []


async def test_attribution_nests_without_restating_the_org(metered, tenant):
    with usage.attributed(org_id=tenant.org_id, project_id=tenant.project_id):
        with usage.attributed(data_id="dat_abc"):
            async with usage.meter("embed", "gemini", model_id="g"):
                usage.observe(tokens_in=4000)

    event = (await _events(metered))[0]
    assert event["data_id"] == "dat_abc"
    assert event["org_id"] == tenant.org_id
    assert event["project_id"] == tenant.project_id


async def test_embedding_is_metered_and_priced_below_generation(metered, tenant):
    """Embeddings are the highest-volume call in the system. Omitting them --
    the usual shortcut -- misses the largest line in a bulk ingest."""
    with usage.attributed(org_id=tenant.org_id):
        async with usage.meter("embed", "gemini", model_id="text-embedding@768"):
            usage.observe(tokens_in=10_000)

    event = (await _events(metered))[0]
    assert event["purpose"] == "embed"
    # 10k tokens of embedding costs a fraction of 10k tokens of generation.
    assert event["credits"] == 1
    assert quota.credits_for_tokens(
        "answer", tokens_in=10_000, tokens_out=0, tokens_cached=0
    ) == 10


async def test_a_skipped_engine_is_recorded_with_no_tokens(metered, tenant):
    """A chain permanently serving from its fallback looks exactly like a chain
    with no primary, unless the skips are recorded."""
    with usage.attributed(org_id=tenant.org_id):
        await usage.skipped("extract", "gemini", depth=0)

    event = (await _events(metered))[0]
    assert event["status"] == "skipped"
    assert event["credits"] == 0
    assert event["tokens_in"] == 0


async def test_purge_drops_raw_events_and_keeps_the_rollup(metered, tenant):
    """Two retentions, because the two answer different questions: raw events
    settle a dispute and are the volume; the rollup is what reporting reads."""
    with usage.attributed(org_id=tenant.org_id):
        async with usage.meter("answer", "gemini", model_id="g"):
            usage.observe(tokens_in=1000, tokens_out=1000)

    await metered.execute(
        "UPDATE usage_events SET occurred_at = now() - interval '90 days'"
    )
    dropped = await usage.purge_events(metered, older_than_days=30)

    assert dropped == 1
    assert await _events(metered) == []
    assert await metered.fetchval(
        "SELECT credits FROM usage_spend WHERE scope = 'org' AND scope_id = $1",
        tenant.org_id,
    ) == 5
