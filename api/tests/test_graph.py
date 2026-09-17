"""Graph traversal.

The tests that matter are not "can it find a neighbour". They are: does a path
stop at a record the caller cannot read, does an edge keep the evidence that
justifies it, and does deleting a record take the claims it made with it.

The visibility ones are the reason this lives in Postgres. Filtering a
traversal after the fact leaks structure — the endpoints of a path through a
hidden record still disclose that the record exists — so the predicate is
inside the recursive query and a path touching anything invisible is never
returned at all.
"""

from __future__ import annotations

import pytest

from memdog import entities as entities_mod
from memdog.graph import GraphError, PostgresGraph, record_edges
from memdog.ids import new_id

pytestmark = pytest.mark.asyncio


async def _item(pool, tenant, external_id, access="private"):
    data_id = new_id("data")
    await pool.execute(
        """
        INSERT INTO data_items (data_id, org_id, project_id, owner_id, producer_id,
            external_id, state, access_level, content_text, event_time)
        VALUES ($1, $2, $3, $4, $5, $6, 'enriched', $7, 'text', now())
        """,
        data_id, tenant.org_id, tenant.project_id, tenant.user_id,
        tenant.producer_id, external_id, access,
    )
    return data_id


async def _ingest(pool, tenant, data_id, entities, relations):
    """Resolve entities and edges the way enrichment does — one transaction."""
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
    {"name": "Northwind Trading", "type": "organization"},
    {"name": "Lisbon", "type": "location"},
]
RELATIONS = [
    {"subject": "Priya Raman", "predicate": "works_for", "object": "Northwind Trading"},
    {"subject": "Northwind Trading", "predicate": "located_in", "object": "Lisbon"},
]


async def test_a_relationship_the_document_asserted_becomes_an_edge(pool, tenant, principal_for):
    actor = await principal_for(tenant.api_key)
    data_id = await _item(pool, tenant, "a")
    ids = await _ingest(pool, tenant, data_id, PEOPLE, RELATIONS)

    result = await PostgresGraph(pool).neighbourhood(
        actor, entity_id=ids["Priya Raman"], depth=1)
    assert {e.predicate for e in result.edges} == {"works_for"}
    assert ids["Northwind Trading"] in {n.entity_id for n in result.nodes}


async def test_traversal_reaches_further_at_greater_depth(pool, tenant, principal_for):
    """Priya works for Northwind; Northwind is in Lisbon. Lisbon is two hops
    away and must not appear at one."""
    actor = await principal_for(tenant.api_key)
    data_id = await _item(pool, tenant, "a")
    ids = await _ingest(pool, tenant, data_id, PEOPLE, RELATIONS)
    graph = PostgresGraph(pool)

    near = await graph.neighbourhood(actor, entity_id=ids["Priya Raman"], depth=1)
    far = await graph.neighbourhood(actor, entity_id=ids["Priya Raman"], depth=2)

    assert ids["Lisbon"] not in {n.entity_id for n in near.nodes}
    assert ids["Lisbon"] in {n.entity_id for n in far.nodes}


async def test_an_edge_is_traversed_in_both_directions(pool, tenant, principal_for):
    """Which end was written as the subject is a grammatical accident of the
    sentence, not a fact about the relationship."""
    actor = await principal_for(tenant.api_key)
    data_id = await _item(pool, tenant, "a")
    ids = await _ingest(pool, tenant, data_id, PEOPLE, RELATIONS)

    result = await PostgresGraph(pool).neighbourhood(
        actor, entity_id=ids["Northwind Trading"], depth=1)
    assert ids["Priya Raman"] in {n.entity_id for n in result.nodes}


async def test_an_edge_carries_the_records_that_assert_it(pool, tenant, principal_for):
    """One document saying something is a claim; several saying it
    independently is closer to a fact, and a boolean throws that away."""
    actor = await principal_for(tenant.api_key)
    for name in ("a", "b", "c"):
        data_id = await _item(pool, tenant, name)
        await _ingest(pool, tenant, data_id, PEOPLE, RELATIONS[:1])

    result = await PostgresGraph(pool).neighbourhood(
        actor, entity_id=(await pool.fetchval(
            "SELECT entity_id FROM entities WHERE normalized_name = 'priya raman'")),
        depth=1)
    edge = next(e for e in result.edges if e.predicate == "works_for")
    assert edge.evidence == 3
    assert len(edge.source_data_ids) == 3


async def test_a_relation_naming_an_unextracted_entity_is_dropped(pool, tenant):
    """Inventing an endpoint would attach a real claim to the wrong node, and
    nothing downstream could show that later."""
    data_id = await _item(pool, tenant, "a")
    await _ingest(pool, tenant, data_id, PEOPLE, [
        {"subject": "Priya Raman", "predicate": "works_for", "object": "Someone Unmentioned"},
    ])
    assert await pool.fetchval("SELECT count(*) FROM entity_edges") == 0


async def test_a_predicate_outside_the_vocabulary_is_dropped(pool, tenant):
    """The closed set is the whole reason a typed edge is queryable."""
    data_id = await _item(pool, tenant, "a")
    await _ingest(pool, tenant, data_id, PEOPLE, [
        {"subject": "Priya Raman", "predicate": "vibes_with", "object": "Northwind Trading"},
    ])
    assert await pool.fetchval("SELECT count(*) FROM entity_edges") == 0


async def test_another_org_cannot_traverse_your_graph(
    pool, tenant, other_tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    data_id = await _item(pool, tenant, "a")
    ids = await _ingest(pool, tenant, data_id, PEOPLE, RELATIONS)

    intruder = await principal_for(other_tenant.api_key)
    with pytest.raises(GraphError) as exc:
        await PostgresGraph(pool).neighbourhood(
            intruder, entity_id=ids["Priya Raman"], depth=1)
    # 404, not 403: confirming the entity exists is itself the disclosure.
    assert exc.value.status == 404


async def test_a_path_does_not_run_through_a_record_the_caller_cannot_read(
    pool, tenant, other_tenant, principal_for
):
    """The load-bearing test. Priya→Northwind is readable; Northwind→Lisbon is
    asserted only by a record belonging to another org. Lisbon must not appear
    at depth 2, because arriving there would disclose the hidden record."""
    owner = await principal_for(tenant.api_key)
    readable = await _item(pool, tenant, "readable")
    # Only the first hop is ingested normally; Lisbon is created below so the
    # edge reaching it can be attributed to a record the caller cannot see.
    ids = await _ingest(pool, tenant, readable, PEOPLE[:2], RELATIONS[:1])

    # The second hop is asserted by a record the caller cannot see.
    hidden = new_id("data")
    await pool.execute(
        """
        INSERT INTO data_items (data_id, org_id, project_id, owner_id, producer_id,
            external_id, state, access_level, content_text, event_time)
        VALUES ($1, $2, $3, $4, $5, 'hidden', 'enriched', 'private', 'text', now())
        """,
        hidden, other_tenant.org_id, other_tenant.project_id,
        other_tenant.user_id, other_tenant.producer_id,
    )
    lisbon = new_id("ent")
    await pool.execute(
        """
        INSERT INTO entities (entity_id, org_id, project_id, type, display_name,
                              normalized_name)
        VALUES ($1, $2, $3, 'location', 'Lisbon', 'lisbon')
        """,
        lisbon, tenant.org_id, tenant.project_id,
    )
    async with pool.acquire() as conn, conn.transaction():
        from memdog.graph import _upsert_fact
        fact_id = await _upsert_fact(
            conn, org_id=tenant.org_id, project_id=tenant.project_id,
            subject_id=ids["Northwind Trading"], predicate="located_in",
            object_id=lisbon,
            valid_from=await conn.fetchval(
                "SELECT event_time FROM data_items WHERE data_id = $1", hidden),
            basis="derived", confidence=0.5,
        )
        await conn.execute(
            """
            INSERT INTO entity_edges (edge_id, org_id, project_id, subject_id,
                predicate, object_id, source_data_id, fact_id)
            VALUES ($1, $2, $3, $4, 'located_in', $5, $6, $7)
            """,
            new_id("edg"), tenant.org_id, tenant.project_id,
            ids["Northwind Trading"], lisbon, hidden, fact_id,
        )

    result = await PostgresGraph(pool).neighbourhood(
        owner, entity_id=ids["Priya Raman"], depth=3)
    assert lisbon not in {n.entity_id for n in result.nodes}, (
        "reaching Lisbon would disclose the record that asserted the hop"
    )


async def test_deleting_a_record_removes_the_claims_it_made(pool, tenant, principal_for):
    """An edge names the record that asserted it. Keeping the claim after the
    record is gone leaves a graph asserting something with no evidence."""
    actor = await principal_for(tenant.api_key)
    data_id = await _item(pool, tenant, "a")
    ids = await _ingest(pool, tenant, data_id, PEOPLE, RELATIONS)
    assert await pool.fetchval("SELECT count(*) FROM entity_edges") == 2

    await pool.execute("DELETE FROM data_items WHERE data_id = $1", data_id)
    assert await pool.fetchval("SELECT count(*) FROM entity_edges") == 0


async def test_co_mentions_need_no_extraction_at_all(pool, tenant, principal_for):
    """Two entities in one record are connected by that fact alone. It is weak
    evidence — and it is the reason the graph is useful before a model has read
    anything for relationships, which is the common case early on."""
    actor = await principal_for(tenant.api_key)
    data_id = await _item(pool, tenant, "a")
    # Entities, no relations at all.
    ids = await _ingest(pool, tenant, data_id, PEOPLE, [])
    assert await pool.fetchval("SELECT count(*) FROM entity_edges") == 0

    together = await PostgresGraph(pool).co_mentioned(
        actor, entity_id=ids["Priya Raman"])
    names = {c["display_name"] for c in together}
    assert {"Northwind Trading", "Lisbon"} <= names
    assert all(c["shared_records"] == 1 for c in together)


async def test_co_mentions_respect_visibility(pool, tenant, other_tenant, principal_for):
    owner = await principal_for(tenant.api_key)
    data_id = await _item(pool, tenant, "a")
    ids = await _ingest(pool, tenant, data_id, PEOPLE, [])

    intruder = await principal_for(other_tenant.api_key)
    assert await PostgresGraph(pool).co_mentioned(
        intruder, entity_id=ids["Priya Raman"]) == []


async def test_depth_and_predicates_are_validated(pool, tenant, principal_for):
    actor = await principal_for(tenant.api_key)
    data_id = await _item(pool, tenant, "a")
    ids = await _ingest(pool, tenant, data_id, PEOPLE, RELATIONS)
    graph = PostgresGraph(pool)

    for bad_depth in (0, 9):
        with pytest.raises(GraphError):
            await graph.neighbourhood(actor, entity_id=ids["Lisbon"], depth=bad_depth)

    with pytest.raises(GraphError) as exc:
        await graph.neighbourhood(actor, entity_id=ids["Lisbon"], depth=1,
                                  predicates=["not_a_predicate"])
    assert "unknown predicate" in str(exc.value)


async def test_filtering_by_predicate_narrows_the_traversal(pool, tenant, principal_for):
    actor = await principal_for(tenant.api_key)
    data_id = await _item(pool, tenant, "a")
    ids = await _ingest(pool, tenant, data_id, PEOPLE, RELATIONS)

    result = await PostgresGraph(pool).neighbourhood(
        actor, entity_id=ids["Priya Raman"], depth=2, predicates=["works_for"])
    assert ids["Lisbon"] not in {n.entity_id for n in result.nodes}


# --------------------------------------------------------------- overview
#
# A whole project at once, rather than a walk from a root. These matter more
# than the traversal's equivalents rather than less: `overview` is what the
# unauthenticated demo surface calls, so a visibility hole here is one a
# stranger can reach without a credential.


async def test_the_overview_returns_the_projects_claims_without_a_root(
    pool, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    data_id = await _item(pool, tenant, "a")
    ids = await _ingest(pool, tenant, data_id, PEOPLE, RELATIONS)

    result = await PostgresGraph(pool).overview(
        actor, project_id=tenant.project_id)

    assert {e.predicate for e in result.edges} == {"works_for", "located_in"}
    assert {n.entity_id for n in result.nodes} == set(ids.values())


async def test_every_overview_edge_has_both_endpoints_in_its_nodes(
    pool, tenant, principal_for
):
    """The invariant a drawing depends on. An edge pointing at a node the
    caller was not given is a line to nowhere, and the way it happens is a
    filter applied to nodes after the edges were chosen."""
    actor = await principal_for(tenant.api_key)
    for name in ("a", "b"):
        data_id = await _item(pool, tenant, name)
        await _ingest(pool, tenant, data_id, PEOPLE, RELATIONS)

    result = await PostgresGraph(pool).overview(
        actor, project_id=tenant.project_id)
    known = {n.entity_id for n in result.nodes}
    for edge in result.edges:
        assert edge.subject_id in known and edge.object_id in known


async def test_the_overview_counts_how_often_a_claim_was_asserted(
    pool, tenant, principal_for
):
    """The number that makes the picture worth reading: a claim three records
    make independently is not the same claim as one made once."""
    actor = await principal_for(tenant.api_key)
    for name in ("a", "b", "c"):
        data_id = await _item(pool, tenant, name)
        await _ingest(pool, tenant, data_id, PEOPLE, RELATIONS[:1])

    result = await PostgresGraph(pool).overview(
        actor, project_id=tenant.project_id)
    assert next(e for e in result.edges if e.predicate == "works_for").evidence == 3


async def test_the_overview_carries_no_record_ids(pool, tenant, principal_for):
    """Not an oversight. This is the shape the public surface returns, and a
    record id is the one thing on an edge that names something outside the
    graph."""
    actor = await principal_for(tenant.api_key)
    data_id = await _item(pool, tenant, "a")
    await _ingest(pool, tenant, data_id, PEOPLE, RELATIONS)

    result = await PostgresGraph(pool).overview(
        actor, project_id=tenant.project_id)
    assert all(e.source_data_ids == [] for e in result.edges)


async def test_another_org_sees_none_of_your_overview(
    pool, tenant, other_tenant, principal_for
):
    """Empty rather than an error: there is no single entity to be coy about,
    so the disclosure to avoid is the claims themselves."""
    actor = await principal_for(tenant.api_key)
    data_id = await _item(pool, tenant, "a")
    await _ingest(pool, tenant, data_id, PEOPLE, RELATIONS)

    intruder = await principal_for(other_tenant.api_key)
    result = await PostgresGraph(pool).overview(
        intruder, project_id=tenant.project_id)
    assert result.nodes == [] and result.edges == []


async def test_a_claim_only_a_hidden_record_makes_is_not_in_the_overview(
    pool, tenant, other_tenant, principal_for
):
    """The traversal's load-bearing test, restated for the surface a stranger
    can reach. Priya→Northwind is readable; Northwind→Lisbon is asserted only
    by a record belonging to another org, so Lisbon is not a node here and the
    hop is not an edge."""
    owner = await principal_for(tenant.api_key)
    readable = await _item(pool, tenant, "readable")
    ids = await _ingest(pool, tenant, readable, PEOPLE[:2], RELATIONS[:1])

    hidden = new_id("data")
    await pool.execute(
        """
        INSERT INTO data_items (data_id, org_id, project_id, owner_id, producer_id,
            external_id, state, access_level, content_text, event_time)
        VALUES ($1, $2, $3, $4, $5, 'hidden', 'enriched', 'private', 'text', now())
        """,
        hidden, other_tenant.org_id, other_tenant.project_id,
        other_tenant.user_id, other_tenant.producer_id,
    )
    lisbon = new_id("ent")
    await pool.execute(
        """
        INSERT INTO entities (entity_id, org_id, project_id, type, display_name,
                              normalized_name)
        VALUES ($1, $2, $3, 'location', 'Lisbon', 'lisbon')
        """,
        lisbon, tenant.org_id, tenant.project_id,
    )
    async with pool.acquire() as conn, conn.transaction():
        from memdog.graph import _upsert_fact
        fact_id = await _upsert_fact(
            conn, org_id=tenant.org_id, project_id=tenant.project_id,
            subject_id=ids["Northwind Trading"], predicate="located_in",
            object_id=lisbon,
            valid_from=await conn.fetchval(
                "SELECT event_time FROM data_items WHERE data_id = $1", hidden),
            basis="derived", confidence=0.5,
        )
        await conn.execute(
            """
            INSERT INTO entity_edges (edge_id, org_id, project_id, subject_id,
                predicate, object_id, source_data_id, fact_id)
            VALUES ($1, $2, $3, $4, 'located_in', $5, $6, $7)
            """,
            new_id("edg"), tenant.org_id, tenant.project_id,
            ids["Northwind Trading"], lisbon, hidden, fact_id,
        )

    result = await PostgresGraph(pool).overview(
        owner, project_id=tenant.project_id)
    assert lisbon not in {n.entity_id for n in result.nodes}
    assert "located_in" not in {e.predicate for e in result.edges}


async def test_an_entity_with_no_readable_mention_is_not_named(
    pool, tenant, other_tenant, principal_for
):
    """The second visibility check, and the reason it is not redundant.

    The fact below is asserted by a record the caller *can* read, so it passes
    `_fact_visibility` on its own. Its object is an entity every mention of
    which sits in a record the caller cannot read — naming it would disclose
    that the entity exists on the strength of somebody else's document.
    """
    owner = await principal_for(tenant.api_key)
    readable = await _item(pool, tenant, "readable")
    ids = await _ingest(pool, tenant, readable, PEOPLE[:2], RELATIONS[:1])

    # An entity in this project whose only mention lives in another org's record.
    hidden = new_id("data")
    await pool.execute(
        """
        INSERT INTO data_items (data_id, org_id, project_id, owner_id, producer_id,
            external_id, state, access_level, content_text, event_time)
        VALUES ($1, $2, $3, $4, $5, 'hidden', 'enriched', 'private', 'text', now())
        """,
        hidden, other_tenant.org_id, other_tenant.project_id,
        other_tenant.user_id, other_tenant.producer_id,
    )
    secret = new_id("ent")
    await pool.execute(
        """
        INSERT INTO entities (entity_id, org_id, project_id, type, display_name,
                              normalized_name)
        VALUES ($1, $2, $3, 'organization', 'Acme Holdings', 'acme holdings')
        """,
        secret, tenant.org_id, tenant.project_id,
    )
    await pool.execute(
        """
        INSERT INTO entity_mentions (mention_id, entity_id, data_id, org_id,
            project_id, surface, resolved_by)
        VALUES ($1, $2, $3, $4, $5, 'Acme Holdings', 'new')
        """,
        new_id("men"), secret, hidden, other_tenant.org_id,
        other_tenant.project_id,
    )
    # ...asserted by a record the caller CAN read.
    async with pool.acquire() as conn, conn.transaction():
        from memdog.graph import _upsert_fact
        fact_id = await _upsert_fact(
            conn, org_id=tenant.org_id, project_id=tenant.project_id,
            subject_id=ids["Priya Raman"], predicate="works_for",
            object_id=secret,
            valid_from=await conn.fetchval(
                "SELECT event_time FROM data_items WHERE data_id = $1", readable),
            basis="derived", confidence=0.5,
        )
        await conn.execute(
            """
            INSERT INTO entity_edges (edge_id, org_id, project_id, subject_id,
                predicate, object_id, source_data_id, fact_id)
            VALUES ($1, $2, $3, $4, 'works_for', $5, $6, $7)
            """,
            new_id("edg"), tenant.org_id, tenant.project_id,
            ids["Priya Raman"], secret, readable, fact_id,
        )

    result = await PostgresGraph(pool).overview(
        owner, project_id=tenant.project_id)
    assert secret not in {n.entity_id for n in result.nodes}


async def test_the_overview_limit_is_bounded(pool, tenant, principal_for):
    """`overview` is reachable from an unauthenticated endpoint, so the ceiling
    belongs here rather than at the call site."""
    from memdog.graph import MAX_OVERVIEW

    actor = await principal_for(tenant.api_key)
    graph = PostgresGraph(pool)
    for bad in (0, MAX_OVERVIEW + 1):
        with pytest.raises(GraphError):
            await graph.overview(actor, project_id=tenant.project_id, limit=bad)

    with pytest.raises(GraphError) as exc:
        await graph.overview(actor, project_id=tenant.project_id,
                             predicates=["not_a_predicate"])
    assert "unknown predicate" in str(exc.value)


# ---------------------------------------------------- the project graph route
#
# The HTTP layer over `overview`. The store is tested directly above; what only
# the route can get wrong is the shape it returns and whether the filters it
# advertises are wired to anything.


@pytest.fixture
async def http(pool, tenant):
    import httpx

    from memdog.app import app

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_the_project_graph_route_returns_nodes_edges_and_glosses(
    pool, tenant, http
):
    """The glosses are the point of returning them: without the vocabulary the
    console has to carry its own copy of the predicate registry, which drifts
    from the server's in silence."""
    data_id = await _item(pool, tenant, "a")
    await _ingest(pool, tenant, data_id, PEOPLE, RELATIONS)

    response = await http.get(
        f"/api/v1/projects/{tenant.project_id}/graph",
        headers={"Authorization": f"Bearer {tenant.api_key}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert {e["predicate"] for e in body["edges"]} == {"works_for", "located_in"}
    assert {n["entity_id"] for n in body["nodes"]}
    # `confidence_class` is a property, so `vars()` does not reach it -- and it
    # is the one thing on an edge that says whether a claim was read off the
    # page or read into it.
    assert all("confidence_class" in e for e in body["edges"])
    assert {p["predicate"] for p in body["predicates"]} == {"works_for", "located_in"}
    assert all(p.get("gloss") for p in body["predicates"])


async def test_the_project_graph_route_filters_by_predicate(pool, tenant, http):
    data_id = await _item(pool, tenant, "a")
    await _ingest(pool, tenant, data_id, PEOPLE, RELATIONS)

    response = await http.get(
        f"/api/v1/projects/{tenant.project_id}/graph?predicates=works_for",
        headers={"Authorization": f"Bearer {tenant.api_key}"},
    )
    assert response.status_code == 200
    assert {e["predicate"] for e in response.json()["edges"]} == {"works_for"}


async def test_the_project_graph_route_refuses_a_bad_limit(pool, tenant, http):
    """400 rather than a clamp. `overview` is reachable unauthenticated through
    the demo surface, so its bound is enforced in the store; a route that
    quietly rounded a caller's number down would be reporting a different
    query than the one it ran."""
    from memdog.graph import MAX_OVERVIEW

    response = await http.get(
        f"/api/v1/projects/{tenant.project_id}/graph?limit={MAX_OVERVIEW + 1}",
        headers={"Authorization": f"Bearer {tenant.api_key}"},
    )
    assert response.status_code == 400


async def test_another_orgs_project_graph_is_empty_over_http(
    pool, tenant, other_tenant, http
):
    data_id = await _item(pool, tenant, "a")
    await _ingest(pool, tenant, data_id, PEOPLE, RELATIONS)

    response = await http.get(
        f"/api/v1/projects/{tenant.project_id}/graph",
        headers={"Authorization": f"Bearer {other_tenant.api_key}"},
    )
    assert response.status_code == 200
    assert response.json()["edges"] == []
