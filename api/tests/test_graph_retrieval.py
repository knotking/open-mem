"""The graph as a retrieval arm.

The point is not that it returns results. It is that it returns a record the
other two arms *cannot* — one that never contains the words searched for and is
reachable only through a relationship some other document asserted. That is the
"what else is connected to this?" question, and a test that could be satisfied
by lexical search would not be testing it.

The other half is the access rule. A traversal filtered afterwards leaks
structure: if a path runs through a record the caller cannot read, returning its
endpoints tells them that record exists. So the tests below check what the graph
declines to say as carefully as what it finds.
"""

from __future__ import annotations

import pytest

from memdog.auth import ApiKeyVerifier
from memdog.cases import upsert_case  # noqa: F401  (kept: exercises the same pool)
from memdog.contracts import RetrieveFilter, RetrieveRequest
from memdog.retrieval import graph_seeds, retrieve

pytestmark = pytest.mark.asyncio


async def _principal(pool, tenant):
    return await ApiKeyVerifier(pool).verify(tenant.api_key)


async def _somebody_else(pool):
    """A real second user. `owner_id` is a foreign key, so "private to someone
    who is not you" has to be an actual person."""
    from memdog.bootstrap import create_user

    return await create_user(pool, "someone.else@example.com")


async def _item(pool, tenant, external_id, text, *, access="org", owner=None):
    """A searchable record with one chunk.

    Written directly because these tests are about traversal, not the write
    path — but the chunk is not optional: every arm reads `chunks`, so a record
    without one is invisible to all three and the test would prove nothing.
    """
    from memdog.ids import new_id

    data_id = new_id("data")
    await pool.execute(
        """
        INSERT INTO data_items (data_id, org_id, project_id, producer_id, owner_id,
                                external_id, access_level, state, event_time,
                                content_text)
        VALUES ($1, $2, $3, $4, $5, $6, $7, 'searchable', now(), $8)
        """,
        data_id, tenant.org_id, tenant.project_id, tenant.producer_id,
        owner or tenant.user_id, external_id, access, text,
    )
    await pool.execute(
        """
        INSERT INTO chunks (chunk_id, data_id, ordinal, text, span_start, span_end)
        VALUES ($1, $2, 0, $3, 0, $4)
        """,
        new_id("chk"), data_id, text, len(text),
    )
    return data_id


async def _entity(pool, tenant, name, kind="person"):
    from memdog.entities import normalize
    from memdog.ids import new_id

    entity_id = new_id("ent")
    await pool.execute(
        """
        INSERT INTO entities (entity_id, org_id, project_id, type, display_name,
                              normalized_name)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        entity_id, tenant.org_id, tenant.project_id, kind, name, normalize(name),
    )
    return entity_id


async def _mention(pool, tenant, entity_id, data_id, surface):
    from memdog.ids import new_id

    await pool.execute(
        """
        INSERT INTO entity_mentions (mention_id, entity_id, data_id, org_id,
                                     project_id, surface, resolved_by)
        VALUES ($1, $2, $3, $4, $5, $6, 'exact_name')
        """,
        new_id("men"), entity_id, data_id, tenant.org_id, tenant.project_id, surface,
    )


async def _edge(pool, tenant, subject, predicate, obj, source_data_id):
    from memdog.ids import new_id

    await pool.execute(
        """
        INSERT INTO entity_edges (edge_id, org_id, project_id, subject_id,
                                  predicate, object_id, source_data_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        new_id("edg"), tenant.org_id, tenant.project_id, subject, predicate,
        obj, source_data_id,
    )


async def _world(pool, tenant, *, evidence_access="org"):
    """Priya works for Northwind. One record says so; another mentions only
    Northwind and never says "Priya" anywhere in its text."""
    priya = await _entity(pool, tenant, "Priya Raman")
    northwind = await _entity(pool, tenant, "Northwind", kind="organization")

    says_so = await _item(
        pool, tenant, "the-assertion",
        "Priya Raman works for Northwind.", access=evidence_access,
    )
    await _mention(pool, tenant, priya, says_so, "Priya Raman")
    await _mention(pool, tenant, northwind, says_so, "Northwind")
    await _edge(pool, tenant, priya, "works_for", northwind, says_so)

    # The payoff record. Nothing in this text matches a search for Priya.
    connected = await _item(
        pool, tenant, "the-connected-one",
        "Quarterly revenue at Northwind rose eleven per cent.",
    )
    await _mention(pool, tenant, northwind, connected, "Northwind")

    unrelated = await _item(
        pool, tenant, "the-unrelated-one",
        "A note about badger husbandry, mentioning nobody.",
    )
    return {"priya": priya, "northwind": northwind, "says_so": says_so,
            "connected": connected, "unrelated": unrelated}


async def _search(pool, embedder, tenant, query, match):
    return await retrieve(
        pool, embedder, await _principal(pool, tenant),
        RetrieveRequest(
            query=query,
            filter=RetrieveFilter(project_id=tenant.project_id),
            match=match, limit=20,
        ),
    )


async def _external(pool, response):
    ids = [c.data_id for c in response.results]
    if not ids:
        return set()
    rows = await pool.fetch(
        "SELECT external_id FROM data_items WHERE data_id = ANY($1::text[])", ids
    )
    return {r["external_id"] for r in rows}


async def test_the_graph_finds_what_the_other_arms_cannot(pool, embedder, tenant):
    """The whole reason for the arm.

    "Priya Raman" appears nowhere in the revenue note. Lexical cannot match it
    and the vector arm has no reason to; it is reachable only across an edge a
    different document asserted.
    """
    await _world(pool, tenant)

    without = await _search(pool, embedder, tenant, "Priya Raman", ["lexical"])
    assert "the-connected-one" not in await _external(pool, without)

    with_graph = await _search(
        pool, embedder, tenant, "Priya Raman", ["lexical", "graph"]
    )
    found = await _external(pool, with_graph)
    assert "the-connected-one" in found
    assert "the-unrelated-one" not in found


async def test_a_graph_result_says_it_came_from_the_graph(pool, embedder, tenant):
    """`matched_by` is what separates "search found this" from "something
    connected to it did"."""
    await _world(pool, tenant)
    response = await _search(
        pool, embedder, tenant, "Priya Raman", ["lexical", "graph"]
    )
    by_external = {}
    for citation in response.results:
        external = await pool.fetchval(
            "SELECT external_id FROM data_items WHERE data_id = $1", citation.data_id
        )
        by_external[external] = citation

    assert by_external["the-connected-one"].matched_by == ["gph"]
    # The record that asserts the relationship matches both ways.
    assert set(by_external["the-assertion"].matched_by) == {"gph", "lex"}


async def test_the_seeds_are_reported(pool, embedder, tenant):
    """A result reached through the graph contains none of the searched words,
    so without the seed the reader cannot tell whether the connection was the
    one they meant."""
    await _world(pool, tenant)
    response = await _search(
        pool, embedder, tenant, "Priya Raman", ["lexical", "graph"]
    )
    assert [s.display_name for s in response.graph_seeds] == ["Priya Raman"]
    assert response.graph_seeds[0].matched_on == "name"
    assert response.graph_seeds[0].type == "person"


async def test_no_seed_means_an_empty_list_not_a_silent_arm(pool, embedder, tenant):
    """"Found nothing to start from" and "found nothing connected" are
    different answers and the caller has to be able to tell them apart."""
    await _world(pool, tenant)
    response = await _search(
        pool, embedder, tenant, "badger husbandry", ["lexical", "graph"]
    )
    assert response.graph_seeds == []
    assert "the-unrelated-one" in await _external(pool, response)


async def test_the_graph_is_not_asked_unless_it_is_asked_for(pool, embedder, tenant):
    """An axis, not a default. It answers a different question from the one the
    other arms answer, and it is only as good as the entity layer beneath it."""
    await _world(pool, tenant)
    response = await _search(
        pool, embedder, tenant, "Priya Raman", ["vector", "lexical"]
    )
    assert response.graph_seeds == []
    assert "the-connected-one" not in await _external(pool, response)


async def test_an_unreadable_edge_is_not_traversable(pool, embedder, tenant):
    """The disclosure the design is careful about.

    The relationship is asserted by a record the caller cannot read. Traversing
    it anyway and filtering the results afterwards would still surface the
    connected record — and the existence of a connection is itself the thing the
    unreadable record's ACL is protecting.
    """
    world = await _world(pool, tenant, evidence_access="private")
    await pool.execute(
        "UPDATE data_items SET owner_id = $2 WHERE data_id = $1",
        world["says_so"], await _somebody_else(pool),
    )

    response = await _search(
        pool, embedder, tenant, "Priya Raman", ["lexical", "graph"]
    )
    found = await _external(pool, response)
    assert "the-assertion" not in found        # the evidence itself is hidden
    assert "the-connected-one" not in found    # and so is what it would reach


async def test_an_entity_visible_only_through_an_unreadable_record_is_no_seed(
    pool, embedder, tenant
):
    """Resolving against the entity table alone would confirm a name exists in
    this project to somebody who cannot see any record containing it."""
    secret = await _item(
        pool, tenant, "sealed", "Sofia Alvarez signed off.", access="private",
    )
    await pool.execute(
        "UPDATE data_items SET owner_id = $2 WHERE data_id = $1",
        secret, await _somebody_else(pool),
    )
    sofia = await _entity(pool, tenant, "Sofia Alvarez")
    await _mention(pool, tenant, sofia, secret, "Sofia Alvarez")

    seeds = await graph_seeds(
        pool, await _principal(pool, tenant),
        project_id=tenant.project_id, query="what did Sofia Alvarez decide",
    )
    assert seeds == []


async def test_a_short_name_does_not_seed_an_expansion(pool, embedder, tenant):
    """A two-letter entity would match nearly every query and expand over half
    the corpus."""
    await _entity(pool, tenant, "AI", kind="topic")
    visible = await _item(pool, tenant, "mentions-ai", "A note about AI.")
    entity = await pool.fetchval(
        "SELECT entity_id FROM entities WHERE display_name = 'AI'"
    )
    await _mention(pool, tenant, entity, visible, "AI")

    seeds = await graph_seeds(
        pool, await _principal(pool, tenant),
        project_id=tenant.project_id, query="what is our plan",
    )
    assert seeds == []


async def test_seeds_do_not_cross_a_project_boundary(pool, embedder, tenant):
    from memdog import control

    other = await control.create_project(
        pool, await _principal(pool, tenant), name="elsewhere"
    )
    await _world(pool, tenant)

    seeds = await graph_seeds(
        pool, await _principal(pool, tenant),
        project_id=other["project_id"], query="Priya Raman",
    )
    assert seeds == []
