"""Alerts.

The tests that matter are not "does a match get recorded". They are: does a
write stay clear of evaluation, does a watermark refuse to advance past work
that was never done, does a truncated batch say so, and can a second tenant see
an event about a record they cannot read.

The last one is the reason the event row has no ACL column. Visibility belongs
to the subject and is resolved when someone reads, because a notification is a
side channel around every other access check in the system.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from memdog import alerts as alerts_mod
from memdog import entities as entities_mod
from memdog.alerts import (
    AlertError, backtest, create_alert, evaluate_gap, poll_events, set_enabled,
    tick, update_alert,
)
from memdog.graph import record_edges
from memdog.ids import new_id

pytestmark = pytest.mark.asyncio

NOW = lambda: datetime.now(timezone.utc)  # noqa: E731

PEOPLE = [
    {"name": "Priya Raman", "type": "person"},
    {"name": "Lisbon", "type": "location"},
    {"name": "Berlin", "type": "location"},
]


async def _item(pool, tenant, external_id, *, event_time=None):
    data_id = new_id("data")
    await pool.execute(
        """
        INSERT INTO data_items (data_id, org_id, project_id, owner_id, producer_id,
            external_id, state, access_level, content_text, event_time)
        VALUES ($1, $2, $3, $4, $5, $6, 'enriched', 'org', 'text', $7)
        """,
        data_id, tenant.org_id, tenant.project_id, tenant.user_id,
        tenant.producer_id, external_id, event_time or NOW(),
    )
    return data_id


async def _ingest(pool, tenant, external_id, where, *, event_time=None):
    data_id = await _item(pool, tenant, external_id, event_time=event_time)
    async with pool.acquire() as conn, conn.transaction():
        resolved = await entities_mod.resolve_mentions(
            conn, data_id=data_id, org_id=tenant.org_id,
            project_id=tenant.project_id, candidates=PEOPLE,
        )
        await record_edges(
            conn, data_id=data_id, org_id=tenant.org_id,
            project_id=tenant.project_id, resolved=resolved,
            relations=[{"subject": "Priya Raman", "predicate": "located_in",
                        "object": where}],
        )
    return {r["name"]: r["entity_id"] for r in resolved}


async def _alert(pool, principal, tenant, **kw):
    kw.setdefault("name", f"alert-{new_id('x')}")
    kw.setdefault("surface", "fact.superseded")
    return await create_alert(pool, principal, project_id=tenant.project_id, **kw)


async def _approve(pool, principal, alert_id):
    await backtest(pool, principal, alert_id)
    return await set_enabled(pool, principal, alert_id, True)


async def test_an_alert_fires_when_a_claim_is_superseded(pool, tenant, principal_for):
    """The whole point, end to end: a relocation is recorded as an event."""
    actor = await principal_for(tenant.api_key)
    alert = await _alert(pool, actor, tenant, where={"predicate": ["located_in"]})
    await _approve(pool, actor, alert["alert_id"])

    await _ingest(pool, tenant, "jan", "Lisbon", event_time=NOW() - timedelta(days=200))
    await _ingest(pool, tenant, "jun", "Berlin", event_time=NOW() - timedelta(days=10))

    result = await evaluate_gap(pool, alert["alert_id"], trigger="tick")
    assert result["matches"] == 1

    seen = await poll_events(pool, actor, since=0)
    assert len(seen["events"]) == 1
    assert seen["events"][0]["payload"]["predicate"] == "located_in"


async def test_a_write_does_not_evaluate_anything(pool, tenant, principal_for):
    """Evaluation is per window, never per write.

    Fifty alerts must cost a write exactly nothing, or one crawl importing ten
    thousand items becomes half a million evaluations.
    """
    actor = await principal_for(tenant.api_key)
    for i in range(5):
        alert = await _alert(pool, actor, tenant, name=f"watcher-{i}",
                             where={"predicate": ["located_in"]})
        await _approve(pool, actor, alert["alert_id"])

    await _ingest(pool, tenant, "one", "Lisbon")
    await _ingest(pool, tenant, "two", "Berlin", event_time=NOW())

    # The transitions exist; nothing has looked at them.
    assert await pool.fetchval(
        "SELECT count(*) FROM domain_events WHERE event_type = 'fact.superseded'") >= 1
    assert await pool.fetchval("SELECT count(*) FROM observed_events") == 0
    assert await pool.fetchval("SELECT count(*) FROM alert_runs WHERE trigger <> 'backtest'") == 0


async def test_a_new_alert_does_not_fire_on_history(pool, tenant, principal_for):
    """It starts at the head of the log, not at the beginning of it.

    An alert that fires a hundred notifications about last month the moment
    someone saves it is an alert they switch off.
    """
    actor = await principal_for(tenant.api_key)
    await _ingest(pool, tenant, "before-1", "Lisbon", event_time=NOW() - timedelta(days=9))
    await _ingest(pool, tenant, "before-2", "Berlin", event_time=NOW() - timedelta(days=8))

    alert = await _alert(pool, actor, tenant, where={"predicate": ["located_in"]})
    await _approve(pool, actor, alert["alert_id"])
    result = await evaluate_gap(pool, alert["alert_id"], trigger="tick")
    assert result["matches"] == 0


async def test_a_backtest_records_nothing_and_moves_nothing(pool, tenant, principal_for):
    """Same path, writes withheld — so what it reports is what a live run does."""
    actor = await principal_for(tenant.api_key)
    alert = await _alert(pool, actor, tenant, where={"predicate": ["located_in"]})
    await _ingest(pool, tenant, "a", "Lisbon", event_time=NOW() - timedelta(days=5))
    await _ingest(pool, tenant, "b", "Berlin", event_time=NOW())

    before = await pool.fetchval(
        "SELECT watermark FROM alerts WHERE alert_id = $1", alert["alert_id"])
    result = await backtest(pool, actor, alert["alert_id"], since_sequence=0)

    assert result["matches"] == 1, "it must find what a live run would find"
    assert await pool.fetchval("SELECT count(*) FROM observed_events") == 0
    assert await pool.fetchval(
        "SELECT watermark FROM alerts WHERE alert_id = $1", alert["alert_id"]) == before


async def test_enabling_requires_a_backtest_of_this_version(pool, tenant, principal_for):
    """The crawler's dry-run gate, renamed. A description is a guess until run."""
    actor = await principal_for(tenant.api_key)
    alert = await _alert(pool, actor, tenant, where={"predicate": ["located_in"]})

    with pytest.raises(AlertError) as exc:
        await set_enabled(pool, actor, alert["alert_id"], True)
    assert exc.value.status == 409

    await backtest(pool, actor, alert["alert_id"])
    assert (await set_enabled(pool, actor, alert["alert_id"], True))["enabled"]


async def test_editing_what_matches_disables_and_unapproves(pool, tenant, principal_for):
    """Carrying an approval onto a question that changed is the failure.

    Renaming is not that, and must not cost an approval — or nobody renames
    anything.
    """
    actor = await principal_for(tenant.api_key)
    alert = await _alert(pool, actor, tenant, where={"predicate": ["located_in"]})
    await _approve(pool, actor, alert["alert_id"])

    renamed = await update_alert(pool, actor, alert["alert_id"], {"name": "same question"})
    assert renamed["config_version"] == 1 and renamed["enabled"]

    edited = await update_alert(
        pool, actor, alert["alert_id"], {"where": {"predicate": ["reports_to"]}})
    assert edited["config_version"] == 2
    assert edited["enabled"] is False
    assert edited["backtested_version"] is None


async def test_the_selector_actually_selects(pool, tenant, principal_for):
    actor = await principal_for(tenant.api_key)
    alert = await _alert(pool, actor, tenant, where={"predicate": ["reports_to"]})
    await _approve(pool, actor, alert["alert_id"])

    await _ingest(pool, tenant, "a", "Lisbon", event_time=NOW() - timedelta(days=5))
    await _ingest(pool, tenant, "b", "Berlin", event_time=NOW())

    result = await evaluate_gap(pool, alert["alert_id"], trigger="tick")
    assert result["candidates"] >= 1, "the transition was considered"
    assert result["matches"] == 0, "and rejected on the predicate"


async def test_a_capped_batch_says_so_and_the_rest_is_picked_up(pool, tenant, principal_for):
    """Silent truncation reads as "nothing else matched", which for an alert
    system is the worst available lie."""
    actor = await principal_for(tenant.api_key)
    alert = await _alert(pool, actor, tenant, where={"predicate": ["located_in"]},
                         batch_cap=1)
    await _approve(pool, actor, alert["alert_id"])

    base = NOW() - timedelta(days=30)
    for i, where in enumerate(["Lisbon", "Berlin", "Lisbon", "Berlin"]):
        await _ingest(pool, tenant, f"move-{i}", where,
                      event_time=base + timedelta(days=i))

    first = await evaluate_gap(pool, alert["alert_id"], trigger="tick")
    assert first["deferred"] > 0, "the remainder is reported, never dropped"

    second = await evaluate_gap(pool, alert["alert_id"], trigger="tick")
    assert second["to_sequence"] > first["to_sequence"], "the watermark carried it forward"


async def test_re_evaluating_the_same_range_does_not_double_report(pool, tenant, principal_for):
    """A retry after a crash must not notify twice."""
    actor = await principal_for(tenant.api_key)
    alert = await _alert(pool, actor, tenant, where={"predicate": ["located_in"]})
    await _approve(pool, actor, alert["alert_id"])
    await _ingest(pool, tenant, "a", "Lisbon", event_time=NOW() - timedelta(days=5))
    await _ingest(pool, tenant, "b", "Berlin", event_time=NOW())

    await evaluate_gap(pool, alert["alert_id"], trigger="tick")
    await evaluate_gap(pool, alert["alert_id"], trigger="tick", from_sequence=0)
    assert await pool.fetchval("SELECT count(*) FROM observed_events") == 1


async def test_a_second_tenant_cannot_poll_another_tenants_events(
    pool, tenant, other_tenant, principal_for
):
    """The reason there is no ACL column on the event.

    Visibility is the subject's, resolved now — not a copy taken when the alert
    matched, which would be stale the moment the record was re-shared.
    """
    actor = await principal_for(tenant.api_key)
    stranger = await principal_for(other_tenant.api_key)
    alert = await _alert(pool, actor, tenant, where={"predicate": ["located_in"]})
    await _approve(pool, actor, alert["alert_id"])
    await _ingest(pool, tenant, "a", "Lisbon", event_time=NOW() - timedelta(days=5))
    await _ingest(pool, tenant, "b", "Berlin", event_time=NOW())
    await evaluate_gap(pool, alert["alert_id"], trigger="tick")

    assert len((await poll_events(pool, actor, since=0))["events"]) == 1
    assert (await poll_events(pool, stranger, since=0))["events"] == []


async def test_the_tick_evaluates_every_alert_that_is_behind(pool, tenant, principal_for):
    actor = await principal_for(tenant.api_key)
    alert = await _alert(pool, actor, tenant, where={"predicate": ["located_in"]})
    await _approve(pool, actor, alert["alert_id"])
    await _ingest(pool, tenant, "a", "Lisbon", event_time=NOW() - timedelta(days=5))
    await _ingest(pool, tenant, "b", "Berlin", event_time=NOW())

    result = await tick(pool)
    assert len(result["evaluated"]) == 1
    assert result["evaluated"][0]["matches"] == 1
    # Nothing left behind, so a second tick is a no-op rather than a repeat.
    assert (await tick(pool))["evaluated"] == []


async def test_a_disabled_alert_is_not_evaluated_by_the_tick(pool, tenant, principal_for):
    actor = await principal_for(tenant.api_key)
    alert = await _alert(pool, actor, tenant, where={"predicate": ["located_in"]})
    await _ingest(pool, tenant, "a", "Lisbon", event_time=NOW() - timedelta(days=5))
    await _ingest(pool, tenant, "b", "Berlin", event_time=NOW())
    assert (await tick(pool))["evaluated"] == []


async def test_llm_mode_is_refused_rather_than_quietly_treated_as_a_selector(
    pool, tenant, principal_for
):
    """Accepting it would look like a working alert that ignores its own words."""
    actor = await principal_for(tenant.api_key)
    with pytest.raises(AlertError) as exc:
        await _alert(pool, actor, tenant, mode="llm", describe="a person relocates",
                     where={"predicate": ["located_in"]})
    assert exc.value.status == 501


async def test_an_unknown_surface_or_field_is_refused_at_creation(pool, tenant, principal_for):
    actor = await principal_for(tenant.api_key)
    with pytest.raises(AlertError):
        await _alert(pool, actor, tenant, surface="fact.invented")
    with pytest.raises(AlertError):
        await _alert(pool, actor, tenant, where={"colour": ["blue"]})
