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


@pytest.fixture
def envelope():
    import os

    from memdog.crypto import Envelope

    return Envelope(os.urandom(32))


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


# ------------------------------------------------------------------- push


async def test_a_match_is_queued_for_a_standing_subscription(
    pool, queue, blobs, settings, tenant, principal_for, envelope
):
    """The same sender, signature, backoff and dead-letter rule as alerts.

    A second pipeline would need its own version of each, and four controls are
    only worth something when they are the same four everywhere.
    """
    from memdog.event_delivery import create_subscription

    actor = await principal_for(tenant.api_key)
    query_id = await _query(pool, actor, tenant, {"query": "outage"})
    await create_subscription(
        pool, actor, envelope, project_id=tenant.project_id,
        url="https://example.com/hook", standing_query_id=query_id)

    await _write(pool, queue, blobs, settings, actor, tenant, "o-1", "A checkout outage")
    await standing.evaluate(pool, query_id, trigger="manual")

    queued = await pool.fetchval(
        "SELECT count(*) FROM event_deliveries WHERE match_id IS NOT NULL")
    assert queued == 1


async def test_an_alert_subscription_does_not_receive_standing_matches(
    pool, queue, blobs, settings, tenant, principal_for, envelope
):
    """`alert_id IS NULL` has always meant *every alert in this project*.

    Letting it also mean *and every standing query* would start posting a
    payload shape a subscriber registered last month has never seen.
    """
    from memdog.event_delivery import create_subscription

    actor = await principal_for(tenant.api_key)
    query_id = await _query(pool, actor, tenant, {"query": "outage"})
    await create_subscription(
        pool, actor, envelope, project_id=tenant.project_id,
        url="https://example.com/alerts")          # no standing_query_id: alert kind

    await _write(pool, queue, blobs, settings, actor, tenant, "o-2", "Another outage")
    await standing.evaluate(pool, query_id, trigger="manual")

    assert await pool.fetchval(
        "SELECT count(*) FROM event_deliveries WHERE match_id IS NOT NULL") == 0


async def test_a_withheld_match_is_never_queued(
    pool, queue, blobs, settings, tenant, principal_for, envelope
):
    """A subscriber cannot be told about something the query itself was not
    entitled to see."""
    from memdog.event_delivery import create_subscription

    actor = await principal_for(tenant.api_key)
    query_id = await _query(pool, actor, tenant, {"query": "confidential"})
    await create_subscription(
        pool, actor, envelope, project_id=tenant.project_id,
        url="https://example.com/hook", standing_query_id=query_id)

    await _write(pool, queue, blobs, settings, actor, tenant, "secret",
                 "A confidential matter", access=ItemAccess(
                     level="restricted", principals=["user:somebody-else"]))
    result = await standing.evaluate(pool, query_id, trigger="manual")

    assert result["withheld"] == 1
    assert await pool.fetchval(
        "SELECT count(*) FROM event_deliveries WHERE match_id IS NOT NULL") == 0


async def test_the_payload_is_rebuilt_against_the_subscriber_rights_now(
    pool, queue, blobs, settings, tenant, principal_for, envelope
):
    """Visibility at match time belongs to the query's owner; the subscription's
    owner is a different person whose rights may have changed since. A match
    they can no longer see is not owed, and sending it anyway is the leak."""
    from memdog.event_delivery import _match_payload

    actor = await principal_for(tenant.api_key)
    query_id = await _query(pool, actor, tenant, {"query": "outage"})
    data_id = await _write(pool, queue, blobs, settings, actor, tenant, "o-3",
                           "A payment outage")
    await standing.evaluate(pool, query_id, trigger="manual")
    match_id = await pool.fetchval(
        "SELECT match_id FROM standing_matches WHERE data_id = $1", data_id)

    body = await _match_payload(pool, match_id, tenant.user_id)
    assert body is not None and body["event"] == "standing_query.matched"
    assert body["data_id"] == data_id and body["preview"]

    # The record is re-scoped away from them; the delivery stops being owed.
    await pool.execute(
        "UPDATE data_items SET access_level = 'restricted', "
        "shared_with = '[\"user:somebody-else\"]'::jsonb WHERE data_id = $1", data_id)
    assert await _match_payload(pool, match_id, tenant.user_id) is None


# ------------------------------------------------------------- date rules


async def _dated(pool, queue, blobs, settings, actor, tenant, external_id, days_out):
    """A record whose own time is `days_out` days from now."""
    from datetime import datetime, timedelta, timezone

    response = await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(
            producer_id=tenant.producer_id,
            items=[WriteItem(
                external_id=external_id, content=Inline(text=f"contract {external_id}"),
                event_time=datetime.now(timezone.utc) + timedelta(days=days_out),
            )],
            options=WriteOptions(enrich=False),
        ),
    )
    return response.results[0].data_id


async def test_a_deadline_fires_when_the_calendar_reaches_it_not_when_it_arrives(
    pool, queue, blobs, settings, tenant, principal_for
):
    """The half an arrival query cannot do.

    Nothing arrives on the day a deadline approaches -- the record showed up
    months earlier and the only thing that changed is the calendar.
    """
    actor = await principal_for(tenant.api_key)
    due_soon = await _dated(pool, queue, blobs, settings, actor, tenant, "renews-soon", 30)
    await _dated(pool, queue, blobs, settings, actor, tenant, "renews-later", 200)

    created = await standing.create(
        pool, actor, project_id=tenant.project_id, name="renewals",
        selector={}, kind="date", date_field="event_time", offset_days=30, window_days=3)

    result = await standing.evaluate_date(pool, created["query_id"], trigger="manual")
    assert result["matches"] == 1
    assert [m["data_id"] for m in result["samples"]] == [due_soon]


async def test_retention_ageing_is_the_same_rule_pointed_backwards(
    pool, queue, blobs, settings, tenant, principal_for
):
    """"Older than seven years" is a negative offset. One column saying so beats
    two mechanisms that would drift apart."""
    actor = await principal_for(tenant.api_key)
    old = await _dated(pool, queue, blobs, settings, actor, tenant, "ancient", -400)
    await _dated(pool, queue, blobs, settings, actor, tenant, "recent", -2)

    created = await standing.create(
        pool, actor, project_id=tenant.project_id, name="ageing",
        selector={}, kind="date", date_field="event_time",
        offset_days=-400, window_days=5)

    result = await standing.evaluate_date(pool, created["query_id"], trigger="manual")
    assert [m["data_id"] for m in result["samples"]] == [old]


async def test_a_date_rule_matches_an_item_once_however_often_it_sweeps(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A daily sweep over an overlapping window would otherwise refill the feed
    with what it already said."""
    actor = await principal_for(tenant.api_key)
    await _dated(pool, queue, blobs, settings, actor, tenant, "renews", 30)
    created = await standing.create(
        pool, actor, project_id=tenant.project_id, name="renewals",
        selector={}, kind="date", date_field="event_time", offset_days=30, window_days=5)

    first = await standing.evaluate_date(pool, created["query_id"], trigger="tick")
    second = await standing.evaluate_date(pool, created["query_id"], trigger="tick")
    assert first["matches"] == 1 and second["matches"] == 1, "it still sees it"
    assert await pool.fetchval("SELECT count(*) FROM standing_matches") == 1, (
        "and records it once")


async def test_a_producer_supplied_date_is_reachable(
    pool, queue, blobs, settings, tenant, principal_for
):
    """The field a legal deadline actually lives in: whatever the producer put
    in `metadata`, not a column the platform invented."""
    from datetime import datetime, timedelta, timezone

    actor = await principal_for(tenant.api_key)
    due = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
    response = await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(
            producer_id=tenant.producer_id,
            items=[WriteItem(external_id="matter-1", content=Inline(text="a filing"),
                             metadata={"deadline": due})],
            options=WriteOptions(enrich=False),
        ),
    )
    created = await standing.create(
        pool, actor, project_id=tenant.project_id, name="filings", selector={},
        kind="date", date_field="metadata.deadline", offset_days=14, window_days=2)

    result = await standing.evaluate_date(pool, created["query_id"], trigger="manual")
    assert [m["data_id"] for m in result["samples"]] == [response.results[0].data_id]


async def test_a_date_rule_is_refused_without_a_field_or_an_offset(pool, tenant, principal_for):
    actor = await principal_for(tenant.api_key)
    for kwargs in ({"date_field": None, "offset_days": 30},
                   {"date_field": "event_time", "offset_days": None},
                   {"date_field": "nonsense", "offset_days": 30},
                   {"date_field": "event_time", "offset_days": 30, "window_days": 0}):
        with pytest.raises(StandingError):
            await standing.create(
                pool, actor, project_id=tenant.project_id,
                name=f"bad-{kwargs}", selector={}, kind="date", **kwargs)


async def test_the_sweep_runs_both_kinds(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Two mechanisms, one surface. A date rule has no watermark to be behind --
    what moved is the calendar -- so it is evaluated on every pass, which is
    affordable because each is a bounded range scan."""
    actor = await principal_for(tenant.api_key)
    await _dated(pool, queue, blobs, settings, actor, tenant, "renews", 30)
    dated = await standing.create(
        pool, actor, project_id=tenant.project_id, name="renewals", selector={},
        kind="date", date_field="event_time", offset_days=30, window_days=5)
    await standing.evaluate_date(pool, dated["query_id"], trigger="backtest", record=False)
    await standing.set_enabled(pool, actor, dated["query_id"], True)

    swept = await standing.tick(pool)
    assert len(swept["evaluated"]) == 1 and swept["evaluated"][0]["matches"] == 1


async def test_a_date_match_appears_in_the_feed(
    pool, queue, blobs, settings, tenant, principal_for
):
    """It did not, and everything else about it worked.

    A date rule has no domain-event sequence -- what moved is the calendar --
    so every match landed at 0, `sequence > 0` excluded all of them, and the
    feed was empty while the matches were recorded and the memory promotion
    ran. The failure that looks like nothing at all.
    """
    actor = await principal_for(tenant.api_key)
    await _dated(pool, queue, blobs, settings, actor, tenant, "renews", 30)
    created = await standing.create(
        pool, actor, project_id=tenant.project_id, name="renewals", selector={},
        kind="date", date_field="event_time", offset_days=30, window_days=5)
    await standing.evaluate_date(pool, created["query_id"], trigger="manual")

    feed = await standing.matches_for(pool, actor, created["query_id"])
    assert [m["external_id"] for m in feed["matches"]] == ["renews"]
    # And the cursor advances, so a poller resuming from it does not re-read.
    assert feed["cursor"] > 0
    resumed = await standing.matches_for(
        pool, actor, created["query_id"], since=feed["cursor"])
    assert resumed["matches"] == []
