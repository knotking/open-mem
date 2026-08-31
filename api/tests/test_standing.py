"""Standing queries — the one primitive that speaks first.

Three rules carry the design and each is a mistake made somewhere else first:
never re-scan, delivery is a read, and time is not a selector.
"""

from __future__ import annotations

import pytest

from memdog import standing
from memdog.contracts import Inline, ItemAccess, WriteItem, WriteOptions, WriteRequest
from memdog.standing import StandingError
from memdog.write import write_items

pytestmark = pytest.mark.asyncio


async def _write(pool, queue, blobs, settings, actor, tenant, external_id, text, **kw):
    response = await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(
            producer_id=tenant.producer_id,
            items=[WriteItem(external_id=external_id, content=Inline(text=text), **kw)],
            options=WriteOptions(enrich=False),
        ),
    )
    return response.results[0].data_id


async def _query(pool, actor, tenant, selector, delivery=None, name="watch"):
    created = await standing.create(
        pool, actor, project_id=tenant.project_id, name=name,
        selector=selector, delivery=delivery)
    return created["query_id"]


async def test_it_matches_what_arrives_after_it_was_registered(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A query registered today is a statement about what arrives next.

    Starting at sequence zero would replay the whole corpus into the feed on the
    first tick -- the re-scan this design exists to avoid, wearing a hat.
    """
    actor = await principal_for(tenant.api_key)
    await _write(pool, queue, blobs, settings, actor, tenant, "before",
                 "Acme announced a recall this morning")

    query_id = await _query(pool, actor, tenant, {"query": "acme"})
    after = await _write(pool, queue, blobs, settings, actor, tenant, "after",
                         "Acme issued a second recall notice")

    result = await standing.evaluate(pool, query_id, trigger="manual")
    assert result["matches"] == 1
    assert [m["data_id"] for m in result["samples"]] == [after]


async def test_the_matcher_understands_words_rather_than_substrings(
    pool, queue, blobs, settings, tenant, principal_for
):
    """The reason this is not an alert with a `contains` condition.

    `contains` misses the possessive and matches the unrelated word; a lexical
    match does neither, and a brand monitor that cannot find a plural is not one.
    """
    actor = await principal_for(tenant.api_key)
    query_id = await _query(pool, actor, tenant, {"query": "recall"})
    hit = await _write(pool, queue, blobs, settings, actor, tenant, "plural",
                       "Two recalls were issued this week")
    await _write(pool, queue, blobs, settings, actor, tenant, "unrelated",
                 "The recalcitrant vendor refused")

    result = await standing.evaluate(pool, query_id, trigger="manual")
    assert [m["data_id"] for m in result["samples"]] == [hit]


async def test_a_phrase_and_an_exclusion_are_what_people_type(
    pool, queue, blobs, settings, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    query_id = await _query(pool, actor, tenant, {"query": '"data breach" -rumour'})
    real = await _write(pool, queue, blobs, settings, actor, tenant, "real",
                        "A data breach was confirmed by the vendor")
    await _write(pool, queue, blobs, settings, actor, tenant, "rumoured",
                 "A data breach rumour circulated on a forum")

    result = await standing.evaluate(pool, query_id, trigger="manual")
    assert [m["data_id"] for m in result["samples"]] == [real]


async def test_a_selector_that_narrows_nothing_is_refused(pool, tenant, principal_for):
    """Every write would match, and the feed would be a copy of the project."""
    actor = await principal_for(tenant.api_key)
    with pytest.raises(StandingError):
        await standing.create(pool, actor, project_id=tenant.project_id,
                              name="everything", selector={})


async def test_enabling_is_gated_on_a_backtest_of_this_version(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A selector that matches everything looks exactly like one that works
    until somebody reads what it caught."""
    actor = await principal_for(tenant.api_key)
    query_id = await _query(pool, actor, tenant, {"query": "acme"})

    with pytest.raises(StandingError) as exc:
        await standing.set_enabled(pool, actor, query_id, True)
    assert exc.value.status == 409

    await standing.evaluate(pool, query_id, trigger="backtest", record=False, from_sequence=0)
    assert (await standing.set_enabled(pool, actor, query_id, True))["enabled"] is True

    # And editing what matches drops the approval.
    await standing.update(pool, actor, query_id, selector={"query": "different"})
    with pytest.raises(StandingError):
        await standing.set_enabled(pool, actor, query_id, True)


async def test_a_backtest_writes_nothing_and_moves_no_watermark(
    pool, queue, blobs, settings, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    query_id = await _query(pool, actor, tenant, {"query": "acme"})
    await _write(pool, queue, blobs, settings, actor, tenant, "one", "Acme again")

    before = await pool.fetchval(
        "SELECT watermark FROM standing_queries WHERE query_id = $1", query_id)
    result = await standing.evaluate(
        pool, query_id, trigger="backtest", record=False, from_sequence=0)
    assert result["samples"], "a backtest returns the matches, not just a count"
    assert await pool.fetchval(
        "SELECT watermark FROM standing_queries WHERE query_id = $1", query_id) == before
    assert await pool.fetchval("SELECT count(*) FROM standing_matches") == 0


async def test_each_item_is_seen_once(
    pool, queue, blobs, settings, tenant, principal_for
):
    """The whole reason a standing query is cheap. A second pass over the same
    corpus must find nothing, and must not re-deliver what it already said."""
    actor = await principal_for(tenant.api_key)
    query_id = await _query(pool, actor, tenant, {"query": "acme"})
    await _write(pool, queue, blobs, settings, actor, tenant, "one", "Acme news")

    first = await standing.evaluate(pool, query_id, trigger="tick")
    second = await standing.evaluate(pool, query_id, trigger="tick")
    assert (first["matches"], second["matches"]) == (1, 0)
    assert await pool.fetchval("SELECT count(*) FROM standing_matches") == 1


async def test_a_match_the_owner_cannot_see_is_withheld_and_counted(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Delivery is a read. A match handed to somebody who cannot see the item is
    a leak through the notification channel -- and dropping it silently makes
    the feed incomplete in a way nobody can explain, so it is recorded and
    counted instead."""
    actor = await principal_for(tenant.api_key)
    query_id = await _query(pool, actor, tenant, {"query": "confidential"})
    data_id = await _write(pool, queue, blobs, settings, actor, tenant, "secret",
                           "A confidential matter", access=ItemAccess(
                               level="restricted", principals=["user:somebody-else"]))

    result = await standing.evaluate(pool, query_id, trigger="manual")
    assert (result["candidates"], result["matches"], result["withheld"]) == (1, 0, 1)

    feed = await standing.matches_for(pool, actor, query_id)
    assert feed["matches"] == [], "the item itself never reaches the feed"
    assert feed["withheld"] == 1, "but the fact that something was withheld does"
    assert await pool.fetchval(
        "SELECT visible FROM standing_matches WHERE data_id = $1", data_id) is False


async def test_delivery_into_a_memory_needs_no_network(
    pool, queue, blobs, settings, tenant, principal_for
):
    """The delivery target with no URL, no secret and no retry -- and it
    composes with everything already built."""
    from memdog.retrieval import memory_members

    actor = await principal_for(tenant.api_key)
    query_id = await _query(
        pool, actor, tenant, {"query": "outage"},
        delivery={"kind": "memory", "memory_type": "default", "memory_key": "outages"})
    data_id = await _write(pool, queue, blobs, settings, actor, tenant, "o-1",
                           "A checkout outage began at 14:02")

    await standing.evaluate(pool, query_id, trigger="manual")
    memory_id = await pool.fetchval(
        "SELECT memory_id FROM memories WHERE memory_key = $1", "outages")
    members = await memory_members(pool, actor, memory_id)
    assert [m["data_id"] for m in members] == [data_id]


async def test_the_sweep_evaluates_only_enabled_queries_that_are_behind(
    pool, queue, blobs, settings, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    query_id = await _query(pool, actor, tenant, {"query": "acme"})
    await _write(pool, queue, blobs, settings, actor, tenant, "one", "Acme news")

    assert await standing.tick(pool) == {"evaluated": []}, "a disabled query is not swept"

    await standing.evaluate(pool, query_id, trigger="backtest", record=False, from_sequence=0)
    await standing.set_enabled(pool, actor, query_id, True)
    swept = await standing.tick(pool)
    assert len(swept["evaluated"]) == 1 and swept["evaluated"][0]["matches"] == 1
