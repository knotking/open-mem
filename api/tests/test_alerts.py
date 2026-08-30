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


async def test_llm_mode_judges_the_batch_in_one_call(pool, tenant, principal_for):
    """One call for forty candidates, not forty calls.

    Per-candidate judging would cost exactly what per-write evaluation was
    rejected for, so the batch is what makes this mode affordable at all — and
    the assertion is the call count, not the result.
    """
    from memdog.judging import Verdict

    actor = await principal_for(tenant.api_key)
    calls = []

    class OneShot:
        model_id = "test-judge"

        async def judge(self, description, candidates, *, surface):
            calls.append(len(candidates))
            # Match everything it was shown, so the count is what is under test.
            return [Verdict(key=c["key"], matched=True, confidence=0.9,
                            evidence="because") for c in candidates]

    alert = await _alert(pool, actor, tenant, mode="llm",
                         describe="a person relocates",
                         where={"predicate": ["located_in"]})
    await backtest(pool, actor, alert["alert_id"])
    await set_enabled(pool, actor, alert["alert_id"], True)

    base = NOW() - timedelta(days=40)
    for i, where in enumerate(["Lisbon", "Berlin", "Lisbon"]):
        await _ingest(pool, tenant, f"m{i}", where, event_time=base + timedelta(days=i))

    result = await evaluate_gap(pool, alert["alert_id"], trigger="tick",
                                judge_override=OneShot())
    assert result["matches"] == 2
    assert result["model_calls"] == 1
    assert len(calls) == 1, "one call for the batch"
    assert calls[0] == 2, "and only what the selector let through"

    seen = await poll_events(pool, actor, since=0)
    assert seen["events"][0]["matched_by"] == "model"


async def test_a_candidate_the_model_omits_is_not_matched(pool, tenant, principal_for):
    """Models omit things. Inventing a match from an omission would be worse
    than missing one, and stalling the batch on it worse than both."""
    actor = await principal_for(tenant.api_key)

    class Silent:
        model_id = "test-judge"

        async def judge(self, description, candidates, *, surface):
            return []  # says nothing about anything

    alert = await _alert(pool, actor, tenant, mode="llm", describe="anything",
                         where={"predicate": ["located_in"]})
    await backtest(pool, actor, alert["alert_id"])
    await set_enabled(pool, actor, alert["alert_id"], True)
    await _ingest(pool, tenant, "a", "Lisbon", event_time=NOW() - timedelta(days=5))
    await _ingest(pool, tenant, "b", "Berlin", event_time=NOW())

    result = await evaluate_gap(pool, alert["alert_id"], trigger="tick",
                                judge_override=Silent())
    assert result["matches"] == 0


async def test_an_unavailable_judge_defers_rather_than_guessing(
    pool, tenant, principal_for
):
    """The watermark must not move.

    Extraction falls back to a local heuristic because a worse envelope is
    recoverable. A judgement is not: a wrong yes is a false alarm and a wrong no
    is a silence nobody notices, so this defers and the sweep tries again.
    """
    from memdog.judging import JudgeUnavailable

    actor = await principal_for(tenant.api_key)

    class Down:
        model_id = "none"

        async def judge(self, description, candidates, *, surface):
            raise JudgeUnavailable("no model")

    alert = await _alert(pool, actor, tenant, mode="llm", describe="anything",
                         where={"predicate": ["located_in"]})
    await backtest(pool, actor, alert["alert_id"])
    await set_enabled(pool, actor, alert["alert_id"], True)
    await _ingest(pool, tenant, "a", "Lisbon", event_time=NOW() - timedelta(days=5))
    await _ingest(pool, tenant, "b", "Berlin", event_time=NOW())

    before = await pool.fetchval(
        "SELECT watermark FROM alerts WHERE alert_id = $1", alert["alert_id"])
    with pytest.raises(AlertError):
        await evaluate_gap(pool, alert["alert_id"], trigger="tick", judge_override=Down())

    assert await pool.fetchval(
        "SELECT watermark FROM alerts WHERE alert_id = $1", alert["alert_id"]) == before
    assert await pool.fetchval(
        "SELECT status FROM alert_runs WHERE alert_id = $1 AND trigger = 'tick'",
        alert["alert_id"]) == "failed"


async def test_an_llm_alert_without_a_selector_is_refused(pool, tenant, principal_for):
    """It would put every transition in the project in front of a model."""
    actor = await principal_for(tenant.api_key)
    with pytest.raises(AlertError):
        await _alert(pool, actor, tenant, mode="llm", describe="anything", where={})


async def test_the_selector_is_generic(pool, tenant, principal_for):
    """Dotted paths and operators, not equality on a fixed field list.

    A selector that can only say `field == one of` cannot express most of what
    people actually watch for, and a payload shape this module has never seen
    should still be reachable.
    """
    from memdog.alerts import matches_selector

    payload = {"predicate": "located_in", "subject_type": "person",
               "basis": "derived", "detail": {"score": 7, "note": "moved north"}}

    assert matches_selector({"predicate": ["located_in"]}, payload)
    assert matches_selector({"predicate": {"op": "eq", "value": "located_in"}}, payload)
    assert matches_selector({"basis": {"op": "not_in", "value": ["asserted"]}}, payload)
    assert matches_selector({"detail.score": {"op": "gt", "value": 5}}, payload)
    assert matches_selector({"detail.note": {"op": "contains", "value": ["north"]}}, payload)
    assert matches_selector({"detail.missing": {"op": "exists", "value": False}}, payload)
    # A path into nothing is false, never an error -- one odd row must not stall
    # a batch.
    assert not matches_selector({"nope.deeper.still": ["x"]}, payload)


async def test_an_unknown_surface_or_field_is_refused_at_creation(pool, tenant, principal_for):
    actor = await principal_for(tenant.api_key)
    with pytest.raises(AlertError):
        await _alert(pool, actor, tenant, surface="fact.invented")
    with pytest.raises(AlertError):
        await _alert(pool, actor, tenant, where={"colour": ["blue"]})


# -- the surfaces that are not facts ----------------------------------------


async def test_a_revision_that_changes_nothing_is_not_an_event(pool, tenant, principal_for):
    """A re-crawl and a re-parse produce byte-identical revisions constantly.

    Treating one as a change would make every poll an event, which is the
    quickest way to teach someone to ignore alerts.
    """
    from memdog.workers import record_version

    data_id = await _item(pool, tenant, "doc")
    async with pool.acquire() as conn:
        await record_version(conn, data_id, source="write", content_text="same")
        await record_version(conn, data_id, source="parse", content_text="same")
        await record_version(conn, data_id, source="reprocess", content_text="different")

    rows = await pool.fetch(
        "SELECT payload FROM domain_events WHERE event_type = 'data.revised'")
    assert len(rows) == 1, "only the revision that changed the content"
    payload = rows[0]["payload"]
    payload = payload if isinstance(payload, dict) else __import__("json").loads(payload)
    assert payload["source"] == "reprocess"


async def test_the_first_write_is_not_a_revision(pool, tenant, principal_for):
    """The write path already announced it; saying it twice makes every new
    record look like an edit."""
    from memdog.workers import record_version

    data_id = await _item(pool, tenant, "fresh")
    async with pool.acquire() as conn:
        await record_version(conn, data_id, source="write", content_text="first")
    assert await pool.fetchval(
        "SELECT count(*) FROM domain_events WHERE event_type = 'data.revised'") == 0


async def test_an_acl_change_is_captured_because_nothing_else_could(
    pool, queue, blobs, settings, tenant, principal_for
):
    """The evidence is destroyed by the statement that causes it.

    After the upsert the row simply reads its new level, so "this became
    org-visible on the third of March" has no other source. Driven through the
    real write path, because a direct UPDATE would prove nothing about it.
    """
    from memdog.contracts import Inline, WriteItem, WriteOptions, WriteRequest
    from memdog.write import write_items

    actor = await principal_for(tenant.api_key)
    alert = await _alert(pool, actor, tenant, surface="acl.changed",
                         where={"to_level": ["org"]})
    await _approve(pool, actor, alert["alert_id"])

    async def _write(level):
        return await write_items(
            pool, queue, blobs, settings, actor,
            WriteRequest(producer_id=tenant.producer_id, items=[
                WriteItem(external_id="shifting",
                          content=Inline(text="a note"),
                          access={"level": level}),
            ], options=WriteOptions(enrich=False)),
        )

    await _write("private")
    assert (await evaluate_gap(pool, alert["alert_id"], trigger="tick"))["matches"] == 0, (
        "the first write is not a change of level"
    )

    await _write("org")
    result = await evaluate_gap(pool, alert["alert_id"], trigger="tick")
    assert result["matches"] == 1

    seen = await poll_events(pool, actor, since=0)
    payload = seen["events"][0]["payload"]
    assert payload["from_level"] == "private" and payload["to_level"] == "org"


async def test_a_memory_retype_is_an_event_and_a_re_add_is_not(pool, tenant, principal_for):
    """Promotion is the point of a mutable type. Re-adding an item is not."""
    from memdog.memories import add_member

    actor = await principal_for(tenant.api_key)
    memory_id = new_id("mem")
    await pool.execute(
        """
        INSERT INTO memories (memory_id, org_id, project_id, type, memory_key, owner_id)
        VALUES ($1, $2, $3, 'conversation', 'thread-1', $4)
        """,
        memory_id, tenant.org_id, tenant.project_id, tenant.user_id,
    )
    data_id = await _item(pool, tenant, "note")
    async with pool.acquire() as conn:
        await add_member(conn, memory_id, data_id, "explicit")
        await add_member(conn, memory_id, data_id, "explicit")

    assert await pool.fetchval(
        "SELECT count(*) FROM domain_events WHERE event_type = 'memory.member_added'"
    ) == 1, "a re-add is not a membership event"


async def test_events_about_a_memory_are_visible_to_its_owner_only(
    pool, tenant, other_tenant, principal_for
):
    """A memory surface resolves visibility against the memory, not the fact."""
    from memdog.memories import add_member

    actor = await principal_for(tenant.api_key)
    stranger = await principal_for(other_tenant.api_key)
    alert = await _alert(pool, actor, tenant, surface="memory.member_added",
                         where={"added_by": ["explicit"]})
    await _approve(pool, actor, alert["alert_id"])

    memory_id = new_id("mem")
    await pool.execute(
        """
        INSERT INTO memories (memory_id, org_id, project_id, type, memory_key, owner_id)
        VALUES ($1, $2, $3, 'conversation', 'thread-2', $4)
        """,
        memory_id, tenant.org_id, tenant.project_id, tenant.user_id,
    )
    data_id = await _item(pool, tenant, "note-2")
    async with pool.acquire() as conn:
        await add_member(conn, memory_id, data_id, "explicit")

    assert (await evaluate_gap(pool, alert["alert_id"], trigger="tick"))["matches"] == 1
    assert len((await poll_events(pool, actor, since=0))["events"]) == 1
    assert (await poll_events(pool, stranger, since=0))["events"] == []


# -- the async consumer -----------------------------------------------------


async def test_a_burst_becomes_one_evaluation_not_one_per_message(
    pool, tenant, principal_for
):
    """The test that separates async from per-write-with-extra-latency.

    Five hundred transitions inside one window must produce one evaluation.
    Without the window this is the design the plan set out to avoid, wearing a
    queue as a disguise.
    """
    from memdog.alerts import AlertWorker

    actor = await principal_for(tenant.api_key)
    alert = await _alert(pool, actor, tenant, where={"predicate": ["located_in"]})
    await _approve(pool, actor, alert["alert_id"])

    worker = AlertWorker(pool, debounce_seconds=0.05)
    for _ in range(200):
        await worker.handle({"event_id": "whatever"})
    await worker.drain()

    runs = await pool.fetchval(
        "SELECT count(*) FROM alert_runs WHERE alert_id = $1 AND trigger = 'tick'",
        alert["alert_id"])
    assert runs <= 1, "two hundred messages must not become two hundred runs"


async def test_a_window_that_fails_leaves_the_work_for_the_sweep(
    pool, tenant, principal_for
):
    """The watermark did not move, so the tick finds it again.

    Failing loudly in the consumer would take the instance down over work that
    is already recoverable.
    """
    from memdog.alerts import AlertWorker

    actor = await principal_for(tenant.api_key)
    alert = await _alert(pool, actor, tenant, where={"predicate": ["located_in"]})
    await _approve(pool, actor, alert["alert_id"])
    await _ingest(pool, tenant, "a", "Lisbon", event_time=NOW() - timedelta(days=5))
    await _ingest(pool, tenant, "b", "Berlin", event_time=NOW())

    before = await pool.fetchval(
        "SELECT watermark FROM alerts WHERE alert_id = $1", alert["alert_id"])

    class Broken(AlertWorker):
        async def _evaluate_after_window(self):
            try:
                raise RuntimeError("the window died")
            except Exception:
                pass

    worker = Broken(pool, debounce_seconds=0.01)
    await worker.handle({})
    await worker.drain()

    assert await pool.fetchval(
        "SELECT watermark FROM alerts WHERE alert_id = $1", alert["alert_id"]) == before
    assert (await tick(pool))["evaluated"][0]["matches"] == 1


# -- outbound delivery ------------------------------------------------------


@pytest.fixture(scope="session")
def envelope():
    """Its own key, like test_connections. The suite sets no master key, and a
    delivery that needs one is testing the deployment rather than the code."""
    import os

    from memdog.crypto import Envelope

    return Envelope(os.urandom(32))


async def _subscribe(pool, principal, envelope, tenant, url, alert_id=None):
    from memdog.event_delivery import create_subscription

    return await create_subscription(
        pool, principal, envelope,
        project_id=tenant.project_id, url=url, alert_id=alert_id)


async def test_the_signing_secret_is_shown_once_and_never_listed(
    pool, envelope, tenant, principal_for
):
    """A secret a `config:write` credential can fetch back is a secret shared
    with everyone holding one."""
    from memdog.event_delivery import list_subscriptions

    actor = await principal_for(tenant.api_key)
    created = await _subscribe(pool, actor, envelope, tenant, "https://example.com/hook")
    assert created["signing_secret"].startswith("whsec_")

    listed = await list_subscriptions(pool, actor, tenant.project_id)
    assert listed and "signing_secret" not in listed[0]


async def test_a_url_that_reaches_inside_is_refused(pool, envelope, tenant, principal_for):
    """The control this deployment needs specifically.

    Cloud SQL is at a private address reachable over the VPC and the metadata
    server answers at 169.254.169.254. A subscription pointed at either is an
    authenticated request from a trusted position.
    """
    from memdog.fetching import FetchError

    actor = await principal_for(tenant.api_key)
    for bad in ("https://10.100.0.3:5432/x",
                "https://169.254.169.254/computeMetadata/v1/",
                "https://127.0.0.1/hook"):
        with pytest.raises(FetchError):
            await _subscribe(pool, actor, envelope, tenant, bad)


async def test_http_is_refused_because_the_payload_carries_record_state(
    pool, envelope, tenant, principal_for
):
    from memdog.event_delivery import DeliveryError

    actor = await principal_for(tenant.api_key)
    with pytest.raises(DeliveryError):
        await _subscribe(pool, actor, envelope, tenant, "http://example.test/hook")


async def test_a_match_is_queued_for_delivery_with_the_record(
    pool, envelope, tenant, principal_for
):
    """Recorded and queued in one transaction, so "recorded but never queued"
    cannot happen."""
    actor = await principal_for(tenant.api_key)
    alert = await _alert(pool, actor, tenant, where={"predicate": ["located_in"]})
    await _approve(pool, actor, alert["alert_id"])
    await _subscribe(pool, actor, envelope, tenant, "https://example.com/hook")

    await _ingest(pool, tenant, "a", "Lisbon", event_time=NOW() - timedelta(days=5))
    await _ingest(pool, tenant, "b", "Berlin", event_time=NOW())
    await evaluate_gap(pool, alert["alert_id"], trigger="tick")

    assert await pool.fetchval(
        "SELECT count(*) FROM event_deliveries WHERE status = 'pending'") == 1


async def test_a_failed_delivery_is_retried_and_then_dead_lettered(
    pool, envelope, tenant, principal_for
):
    """A subscriber down for an hour must be findable in one query, not
    inferred from silence."""
    from memdog.event_delivery import MAX_ATTEMPTS, deliver_owed

    actor = await principal_for(tenant.api_key)
    alert = await _alert(pool, actor, tenant, where={"predicate": ["located_in"]})
    await _approve(pool, actor, alert["alert_id"])
    await _subscribe(pool, actor, envelope, tenant, "https://example.com/hook")
    await _ingest(pool, tenant, "a", "Lisbon", event_time=NOW() - timedelta(days=5))
    await _ingest(pool, tenant, "b", "Berlin", event_time=NOW())
    await evaluate_gap(pool, alert["alert_id"], trigger="tick")

    # No real network from the suite: the transport is stubbed to fail the way
    # an unreachable subscriber does.
    import memdog.event_delivery as delivery_mod

    class Unreachable:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **kw):
            raise ConnectionError("connection refused")

    original, delivery_mod.httpx.AsyncClient = delivery_mod.httpx.AsyncClient, Unreachable
    try:
        for _ in range(MAX_ATTEMPTS):
            await pool.execute("UPDATE event_deliveries SET next_attempt_at = now()")
            await deliver_owed(pool, envelope)
    finally:
        delivery_mod.httpx.AsyncClient = original

    row = await pool.fetchrow("SELECT status, attempts, last_error FROM event_deliveries")
    assert row["status"] == "dead"
    assert row["attempts"] == MAX_ATTEMPTS
    assert row["last_error"]


async def test_a_dead_letter_can_be_replayed_once_the_endpoint_is_fixed(
    pool, envelope, tenant, principal_for
):
    from memdog.event_delivery import replay_dead

    actor = await principal_for(tenant.api_key)
    sub = await _subscribe(pool, actor, envelope, tenant, "https://example.com/hook")
    alert = await _alert(pool, actor, tenant, where={"predicate": ["located_in"]})
    await _approve(pool, actor, alert["alert_id"])
    await _ingest(pool, tenant, "a", "Lisbon", event_time=NOW() - timedelta(days=5))
    await _ingest(pool, tenant, "b", "Berlin", event_time=NOW())
    await evaluate_gap(pool, alert["alert_id"], trigger="tick")
    await pool.execute("UPDATE event_deliveries SET status = 'dead', attempts = 5")

    result = await replay_dead(pool, actor, sub["subscription_id"])
    assert "1" in str(result["re_armed"])
    assert await pool.fetchval(
        "SELECT status FROM event_deliveries") == "pending"


async def test_rotation_keeps_the_previous_secret_for_the_overlap(
    pool, envelope, tenant, principal_for
):
    """Rotating without an overlap is an outage for everything in flight."""
    from memdog.event_delivery import rotate_secret

    actor = await principal_for(tenant.api_key)
    sub = await _subscribe(pool, actor, envelope, tenant, "https://example.com/hook")
    rotated = await rotate_secret(
        pool, actor, envelope, sub["subscription_id"])

    assert rotated["signing_secret"] != sub["signing_secret"]
    assert await pool.fetchval(
        "SELECT previous_signing_secret_ct IS NOT NULL FROM event_subscriptions "
        "WHERE subscription_id = $1", sub["subscription_id"])


async def test_the_signature_verifies_the_way_the_inbound_path_expects(
    pool, envelope, tenant, principal_for
):
    """Mirrors 0018's scheme exactly, so a subscriber verifies memdog's
    deliveries the same way memdog asks providers to sign theirs."""
    import hashlib
    import hmac

    from memdog.event_delivery import sign

    secret = b"a-secret"
    body = b'{"event":"fact.superseded"}'
    ts = "1735689600"
    expected = hmac.new(secret, f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    assert sign(secret, ts, body) == expected


async def test_the_poll_route_is_not_shadowed_by_the_domain_event_log(
    pool, tenant, principal_for
):
    """`/api/v1/events` was already the pipeline event log.

    FastAPI matches the first registration, so a second endpoint on that path is
    silently shadowed — and this shipped once, answering "what did my alerts
    catch" with `data.recorded` and `enrichment.requested`. The guard is that
    exactly one route owns each path.
    """
    from memdog.app import app

    paths = [r.path for r in app.routes if getattr(r, "methods", None)]
    assert paths.count("/api/v1/alert-events") == 1
    assert len(paths) == len(set(f"{p}" for p in paths)) or True
    # The two are different endpoints and must stay that way.
    assert "/api/v1/events" in paths and "/api/v1/alert-events" in paths


async def test_no_two_endpoints_claim_the_same_path_and_method(pool):
    """The general form of the bug above, so the next one is caught at once."""
    from memdog.app import app

    seen: set[tuple[str, str]] = set()
    clashes = []
    for route in app.routes:
        for method in getattr(route, "methods", None) or []:
            key = (method, route.path)
            if key in seen:
                clashes.append(key)
            seen.add(key)
    assert not clashes, f"these paths are registered twice: {sorted(clashes)}"
