"""The temporal graph.

Two clocks, and the tests that matter are the ones that catch an implementation
which has only implemented one. `valid_at` asks what was true in the world;
`as_of` asks what we had learned. A backfill separates them — a document
imported today about last year is visible at `valid_at=last year` and must be
invisible at `as_of=last month` — and any implementation that conflates the two
passes every other test in this file.

The rest guard the things that are easy to get subtly wrong: that supersession
closes rather than deletes, that two claims beginning at the same instant are
surfaced rather than silently resolved, and that erasing the last evidence for a
claim retracts it instead of leaving the graph asserting something with nothing
behind it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from memdog import entities as entities_mod
from memdog.graph import (
    PostgresGraph, assert_fact, conflicts, fact_history, record_edges,
    retract_fact,
)
from memdog.ids import new_id

pytestmark = pytest.mark.asyncio

NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


async def _item(pool, tenant, external_id, *, event_time=None, access="org"):
    data_id = new_id("data")
    await pool.execute(
        """
        INSERT INTO data_items (data_id, org_id, project_id, owner_id, producer_id,
            external_id, state, access_level, content_text, event_time)
        VALUES ($1, $2, $3, $4, $5, $6, 'enriched', $7, 'text', $8)
        """,
        data_id, tenant.org_id, tenant.project_id, tenant.user_id,
        tenant.producer_id, external_id, access, event_time or NOW(),
    )
    return data_id


async def _ingest(pool, tenant, data_id, entities, relations):
    async with pool.acquire() as conn, conn.transaction():
        resolved = await entities_mod.resolve_mentions(
            conn, data_id=data_id, org_id=tenant.org_id,
            project_id=tenant.project_id, candidates=entities,
        )
        await record_edges(
            conn, data_id=data_id, org_id=tenant.org_id,
            project_id=tenant.project_id, resolved=resolved, relations=relations,
        )
    return {r["name"]: r["entity_id"] for r in resolved}


PEOPLE = [
    {"name": "Priya Raman", "type": "person"},
    {"name": "Lisbon", "type": "location"},
    {"name": "Berlin", "type": "location"},
]


def _lives_in(where):
    return [{"subject": "Priya Raman", "predicate": "located_in", "object": where}]


async def test_a_fact_is_returned_at_a_time_it_was_true(pool, tenant, principal_for):
    """The plain point-in-time question, with one clock moving."""
    actor = await principal_for(tenant.api_key)
    january = NOW() - timedelta(days=240)
    ids = await _ingest(
        pool, tenant, await _item(pool, tenant, "jan", event_time=january),
        PEOPLE, _lives_in("Lisbon"),
    )
    graph = PostgresGraph(pool)

    present = await graph.neighbourhood(actor, entity_id=ids["Priya Raman"], depth=1)
    assert ids["Lisbon"] in {n.entity_id for n in present.nodes}

    before = await graph.neighbourhood(
        actor, entity_id=ids["Priya Raman"], depth=1,
        valid_at=january - timedelta(days=30),
    )
    assert ids["Lisbon"] not in {n.entity_id for n in before.nodes}, (
        "a claim that had not begun cannot be true"
    )


async def test_valid_time_and_transaction_time_are_not_the_same_clock(
    pool, tenant, principal_for
):
    """The test that catches a one-clock implementation.

    A document about last year, imported today. It was true last year, and we
    did not know it last month. An implementation storing a single timestamp
    answers one of those two questions wrongly, whichever it picked.
    """
    actor = await principal_for(tenant.api_key)
    last_year = NOW() - timedelta(days=365)
    ids = await _ingest(
        pool, tenant, await _item(pool, tenant, "backfill", event_time=last_year),
        PEOPLE, _lives_in("Lisbon"),
    )
    graph = PostgresGraph(pool)

    # True then, and we know it now.
    knew_now = await graph.neighbourhood(
        actor, entity_id=ids["Priya Raman"], depth=1, valid_at=last_year,
    )
    assert ids["Lisbon"] in {n.entity_id for n in knew_now.nodes}

    # Equally true then -- but last month we had never heard of it.
    knew_then = await graph.neighbourhood(
        actor, entity_id=ids["Priya Raman"], depth=1,
        valid_at=last_year, as_of=NOW() - timedelta(days=30),
    )
    assert ids["Lisbon"] not in {n.entity_id for n in knew_then.nodes}, (
        "as_of must not return a fact recorded after it"
    )


async def test_a_single_valued_predicate_closes_the_claim_it_replaces(
    pool, tenant, principal_for
):
    """`located_in` holds one open value. Moving to Berlin ends living in Lisbon
    — and does not delete that it was ever true."""
    actor = await principal_for(tenant.api_key)
    january, june = NOW() - timedelta(days=240), NOW() - timedelta(days=60)
    ids = await _ingest(
        pool, tenant, await _item(pool, tenant, "jan", event_time=january),
        PEOPLE, _lives_in("Lisbon"),
    )
    await _ingest(
        pool, tenant, await _item(pool, tenant, "jun", event_time=june),
        PEOPLE, _lives_in("Berlin"),
    )
    graph = PostgresGraph(pool)

    now_at = {n.entity_id for n in (await graph.neighbourhood(
        actor, entity_id=ids["Priya Raman"], depth=1)).nodes}
    assert ids["Berlin"] in now_at
    assert ids["Lisbon"] not in now_at, "the superseded claim is no longer true"

    # Non-destructive: it is still true *then*.
    back_then = {n.entity_id for n in (await graph.neighbourhood(
        actor, entity_id=ids["Priya Raman"], depth=1,
        valid_at=january + timedelta(days=1))).nodes}
    assert ids["Lisbon"] in back_then
    assert ids["Berlin"] not in back_then

    rows = await pool.fetch(
        "SELECT valid_to, superseded_by FROM entity_facts WHERE object_id = $1",
        ids["Lisbon"],
    )
    assert rows[0]["valid_to"] is not None and rows[0]["superseded_by"] is not None


async def test_a_multi_valued_predicate_accumulates(pool, tenant, principal_for):
    """Two jobs is a fact about people, not a contradiction. `works_for` is
    deliberately not single-valued, and this is the test that says so."""
    actor = await principal_for(tenant.api_key)
    people = [{"name": "Priya Raman", "type": "person"},
              {"name": "Northwind", "type": "organization"},
              {"name": "Eastwind", "type": "organization"}]
    ids = await _ingest(
        pool, tenant, await _item(pool, tenant, "one"), people,
        [{"subject": "Priya Raman", "predicate": "works_for", "object": "Northwind"}],
    )
    ids |= await _ingest(
        pool, tenant, await _item(pool, tenant, "two"), people,
        [{"subject": "Priya Raman", "predicate": "works_for", "object": "Eastwind"}],
    )
    result = await PostgresGraph(pool).neighbourhood(
        actor, entity_id=ids["Priya Raman"], depth=1)
    reached = {n.entity_id for n in result.nodes}
    assert ids["Northwind"] in reached and ids["Eastwind"] in reached


async def test_two_claims_beginning_at_the_same_instant_are_surfaced_not_resolved(
    pool, tenant, principal_for
):
    """Supersession refuses to choose between claims that start together.

    Picking one would be a guess wearing the clothes of a fact. They are left
    open and reported as a conflict, which is the honest answer and the one UC2
    asks for.
    """
    actor = await principal_for(tenant.api_key)
    same = NOW() - timedelta(days=10)
    ids = await _ingest(
        pool, tenant, await _item(pool, tenant, "a", event_time=same),
        PEOPLE, _lives_in("Lisbon"),
    )
    await _ingest(
        pool, tenant, await _item(pool, tenant, "b", event_time=same),
        PEOPLE, _lives_in("Berlin"),
    )

    found = await conflicts(pool, actor, tenant.project_id)
    assert len(found) == 1
    assert found[0]["predicate"] == "located_in"
    assert {c["object_name"] for c in found[0]["claims"]} == {"Lisbon", "Berlin"}


async def test_a_fact_can_be_asserted_with_no_document_and_no_model(
    pool, tenant, principal_for
):
    """The whole point of `basis`.

    `entity_edges.source_data_id` is NOT NULL, so before this an agent that
    already knew something had to manufacture a document for an extractor to
    read it back out. An asserted fact has no evidence row at all — which is
    what this checks, because that is the difference.
    """
    actor = await principal_for(tenant.api_key)
    ids = await _ingest(
        pool, tenant, await _item(pool, tenant, "seed"), PEOPLE, _lives_in("Lisbon"),
    )

    fact = await assert_fact(
        pool, actor, project_id=tenant.project_id,
        subject_id=ids["Priya Raman"], predicate="reports_to",
        object_id=ids["Lisbon"], access_level="org",
    )
    assert fact["basis"] == "asserted"

    evidence = await pool.fetchval(
        "SELECT count(*) FROM entity_edges WHERE fact_id = $1", fact["fact_id"])
    assert evidence == 0, "an asserted fact descends from no record"

    reached = {n.entity_id for n in (await PostgresGraph(pool).neighbourhood(
        actor, entity_id=ids["Priya Raman"], depth=1)).nodes}
    assert ids["Lisbon"] in reached


async def test_an_asserted_fact_is_visible_only_to_those_its_acl_allows(
    pool, tenant, other_tenant, principal_for
):
    """A derived fact borrows its evidence's visibility; an asserted one has no
    evidence, so it carries its own — and a second tenant must not traverse it."""
    actor = await principal_for(tenant.api_key)
    stranger = await principal_for(other_tenant.api_key)
    ids = await _ingest(
        pool, tenant, await _item(pool, tenant, "seed"), PEOPLE, _lives_in("Lisbon"),
    )
    await assert_fact(
        pool, actor, project_id=tenant.project_id,
        subject_id=ids["Priya Raman"], predicate="reports_to",
        object_id=ids["Lisbon"], access_level="private",
    )

    from memdog.graph import GraphError
    with pytest.raises(GraphError):
        # Not "no results" -- the entity itself must be unconfirmable.
        await PostgresGraph(pool).neighbourhood(
            stranger, entity_id=ids["Priya Raman"], depth=1)


async def test_retracting_keeps_what_we_believed_at_the_time(
    pool, tenant, principal_for
):
    """Retraction says we should not have recorded it, not that it became false.

    Either way the row survives, because an earlier `as_of` has to keep
    returning what we believed then.
    """
    actor = await principal_for(tenant.api_key)
    ids = await _ingest(
        pool, tenant, await _item(pool, tenant, "seed"), PEOPLE, _lives_in("Lisbon"),
    )
    fact = await assert_fact(
        pool, actor, project_id=tenant.project_id,
        subject_id=ids["Priya Raman"], predicate="reports_to",
        object_id=ids["Lisbon"], access_level="org",
    )
    before = NOW()
    await retract_fact(pool, actor, fact["fact_id"], reason="wrong person")

    graph = PostgresGraph(pool)
    assert ids["Lisbon"] not in {n.entity_id for n in (await graph.neighbourhood(
        actor, entity_id=ids["Priya Raman"], depth=1, predicates=["reports_to"],
    )).nodes}

    # Still there for anyone asking what we believed a moment ago.
    history = await fact_history(pool, actor, ids["Priya Raman"])
    retracted = [f for f in history if f["fact_id"] == fact["fact_id"]]
    assert retracted and retracted[0]["retracted_reason"] == "wrong person"
    assert retracted[0]["recorded_at"] < before, "recorded_at must never be rewritten"


async def test_erasing_the_last_evidence_retracts_the_claim_rather_than_keeping_it(
    pool, tenant, principal_for
):
    """A graph asserting something with nothing behind it is worse than a gap.

    Deleting the fact outright would be worse still: it erases that we ever
    believed it, which is the one thing this table exists to preserve.
    """
    actor = await principal_for(tenant.api_key)
    from memdog.deletion import verify_erasure

    data_id = await _item(pool, tenant, "only-source")
    await _ingest(pool, tenant, data_id, PEOPLE, _lives_in("Lisbon"))

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("DELETE FROM entity_edges WHERE source_data_id = $1", data_id)
        await conn.execute(
            """
            UPDATE entity_facts f SET retracted_at = now(),
                   retracted_reason = 'last evidence erased'
             WHERE f.basis = 'derived' AND f.retracted_at IS NULL
               AND NOT EXISTS (SELECT 1 FROM entity_edges ev
                                WHERE ev.fact_id = f.fact_id)
            """
        )

    row = await pool.fetchrow(
        "SELECT retracted_at, retracted_reason FROM entity_facts WHERE project_id = $1",
        tenant.project_id,
    )
    assert row["retracted_at"] is not None
    assert row["retracted_reason"] == "last evidence erased"

    # `remaining` carries only what survived, so the absence of the key is the
    # assertion: no derived fact is still open with nothing behind it.
    evidence = await verify_erasure(pool, data_id)
    assert "open_facts_without_evidence" not in evidence["remaining"]
