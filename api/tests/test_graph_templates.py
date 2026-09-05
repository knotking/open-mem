"""Templates: deciding the shape of the graph before reading the document.

The thing under test is not that a template produces edges. It is that
declaring one *changes what can be said* -- the model is offered a narrower
vocabulary, an edge whose endpoints the predicate does not permit is refused,
and a traversal can afterwards be confined to the lens that drew it.

The last of those is the one worth being careful about. A template filter
applied to the *result* of a traversal returns endpoints joined by edges the
filter excluded, which reads as the template having asserted something it never
saw. So the tests below check the shape of the path, not just its endpoints.
"""

from __future__ import annotations

import pytest

from memdog import graph_templates, predicates as predicates_mod
from memdog.auth import ApiKeyVerifier
from memdog.entities import resolve_mentions
from memdog.graph import PostgresGraph, record_edges
from memdog.ids import new_id

pytestmark = pytest.mark.asyncio

FIGURES = [
    {"name": "Krishna", "type": "person"},
    {"name": "Arjuna", "type": "person"},
    {"name": "karma yoga", "type": "topic"},
    {"name": "delusion", "type": "topic"},
    {"name": "Kurukshetra", "type": "location"},
]


async def _principal(pool, tenant):
    return await ApiKeyVerifier(pool).verify(tenant.api_key)


async def _item(pool, tenant, external_id, *, template=None):
    data_id = new_id("data")
    await pool.execute(
        """
        INSERT INTO data_items (data_id, org_id, project_id, owner_id, producer_id,
            external_id, state, access_level, content_text, event_time, template)
        VALUES ($1, $2, $3, $4, $5, $6, 'enriched', 'org', 'text', now(), $7)
        """,
        data_id, tenant.org_id, tenant.project_id, tenant.user_id,
        tenant.producer_id, external_id, template,
    )
    return data_id


async def _ingest(pool, tenant, external_id, relations, *, template=None):
    data_id = await _item(pool, tenant, external_id, template=template)
    async with pool.acquire() as conn, conn.transaction():
        resolved = await resolve_mentions(
            conn, data_id=data_id, org_id=tenant.org_id,
            project_id=tenant.project_id, candidates=FIGURES,
        )
        written = await record_edges(
            conn, data_id=data_id, org_id=tenant.org_id,
            project_id=tenant.project_id, resolved=resolved,
            relations=relations, template=template,
        )
    return {r["name"]: r["entity_id"] for r in resolved}, written


# --------------------------------------------------------------- the registry


def test_a_template_selects_from_the_global_registry_and_invents_nothing():
    """The rule that keeps this one graph instead of many.

    A template with a private vocabulary produces a subgraph nothing else can
    traverse into, which costs the graph the only thing it is for.
    """
    for template in graph_templates.TEMPLATES.values():
        unknown = set(template.predicates) - set(predicates_mod.REGISTRY)
        assert not unknown, f"{template.name} invented {unknown}"


def test_every_predicate_a_template_offers_serves_a_question_it_declares():
    """Not automatable in full -- but a template with no questions at all is a
    prompt nobody can review, and that much is checkable."""
    for template in graph_templates.TEMPLATES.values():
        assert template.questions, f"{template.name} declares no questions"
        assert template.look_for.strip(), f"{template.name} says nothing to look for"


def test_an_unknown_template_is_refused_rather_than_ignored():
    """Ignoring a typo produces a generic graph the caller believes is
    specialised -- and about edges, where a relationship nobody looked for
    leaves no trace of not having been sought."""
    with pytest.raises(graph_templates.UnknownTemplate) as caught:
        graph_templates.get("scriptre")
    # The valid names are in the message, because they are discoverable anyway.
    assert "scripture" in str(caught.value)


def test_no_template_means_the_whole_vocabulary():
    assert set(graph_templates.predicates_for(None)) == set(predicates_mod.REGISTRY)


def test_a_template_narrows_the_offered_predicates():
    """Most of what a template *does*: the model cannot answer with a predicate
    that was never in the enum."""
    from memdog.extraction import envelope_schema

    offered = graph_templates.predicates_for("scripture")
    assert "works_for" not in offered and "teaches" in offered
    enum = envelope_schema(offered)["properties"]["relations"]["items"] \
        ["properties"]["predicate"]["enum"]
    assert set(enum) == set(offered)


def test_the_digest_changes_when_the_offered_predicates_change():
    """Narrowing the enum changes what the model can say, so it changes the
    artifact as surely as editing a sentence does -- and `/reprocess` can only
    find what a changed digest marks stale."""
    scripture = graph_templates.TEMPLATES["scripture"]
    altered = graph_templates.Template(
        name=scripture.name, questions=scripture.questions,
        predicates=scripture.predicates[:-1], look_for=scripture.look_for,
        traps=scripture.traps,
    )
    assert altered.digest() != scripture.digest()


# ----------------------------------------------------------------- the writes


async def test_an_edge_records_the_template_that_drew_it(pool, tenant):
    """What makes premeditation reversible: editing a template makes exactly
    its edges stale, and a template applied in error is retractable."""
    _, written = await _ingest(
        pool, tenant, "gita-1",
        [{"subject": "Krishna", "predicate": "teaches", "object": "karma yoga"}],
        template="scripture",
    )
    assert [e["template"] for e in written] == ["scripture"]
    stored = await pool.fetchval(
        "SELECT template FROM entity_facts WHERE fact_id = $1", written[0]["fact_id"]
    )
    assert stored == "scripture"


async def test_an_edge_outside_the_template_is_dropped(pool, tenant):
    """A predicate the model was never offered means it ignored the enum, or a
    template changed after this text was read. Either way it must not be stored
    under a template that does not claim it."""
    _, written = await _ingest(
        pool, tenant, "gita-2",
        [{"subject": "Krishna", "predicate": "works_for", "object": "Arjuna"},
         {"subject": "Krishna", "predicate": "teaches", "object": "karma yoga"}],
        template="scripture",
    )
    assert [e["predicate"] for e in written] == ["teaches"]


async def test_a_predicate_refuses_endpoints_its_types_do_not_permit(pool, tenant):
    """`teaches` runs agent -> idea. A place teaching a doctrine is refused at
    write time rather than found later by somebody reading a bad answer."""
    _, written = await _ingest(
        pool, tenant, "gita-3",
        [{"subject": "Kurukshetra", "predicate": "teaches", "object": "karma yoga"}],
        template="scripture",
    )
    assert written == []


async def test_open_domain_extraction_still_writes_the_whole_vocabulary(pool, tenant):
    """A template is opt-in. Without one nothing narrows, which is what every
    existing caller already gets."""
    _, written = await _ingest(
        pool, tenant, "plain-1",
        [{"subject": "Krishna", "predicate": "related_to", "object": "Arjuna"}],
    )
    assert [e["predicate"] for e in written] == ["related_to"]
    assert written[0]["template"] is None


# -------------------------------------------------------------- the traversal


async def test_traversal_confined_to_a_template_walks_only_its_edges(pool, tenant):
    """The search half, and the reason the filter lives inside the recursion.

    Two documents connect the same pair of entities: one read as scripture, one
    read plainly. Asking for the scripture graph must return the first edge and
    not the second — and must not reach a node that only the excluded edge
    connects.
    """
    ids, _ = await _ingest(
        pool, tenant, "gita-4",
        [{"subject": "Krishna", "predicate": "teaches", "object": "karma yoga"}],
        template="scripture",
    )
    await _ingest(
        pool, tenant, "memo-1",
        [{"subject": "Krishna", "predicate": "related_to", "object": "delusion"}],
    )

    graph = PostgresGraph(pool)
    actor = await _principal(pool, tenant)

    everything = await graph.neighbourhood(
        actor, entity_id=ids["Krishna"], depth=1, predicates=None, limit=50)
    assert {e.predicate for e in everything.edges} == {"teaches", "related_to"}

    scripture = await graph.neighbourhood(
        actor, entity_id=ids["Krishna"], depth=1, predicates=None, limit=50,
        template="scripture")
    assert {e.predicate for e in scripture.edges} == {"teaches"}
    # And the node reachable only through the excluded edge is gone with it.
    assert ids["delusion"] not in {n.entity_id for n in scripture.nodes}
    assert all(e.template == "scripture" for e in scripture.edges)


async def test_a_claim_two_records_share_is_reachable_through_either_lens(pool, tenant):
    """Facts merge across records; lenses do not.

    A second document asserting a claim we already hold corroborates it rather
    than creating a new one, so the claim's own `template` column records only
    whichever pass wrote it first. Filtering on that column reported one edge
    where seven had been drawn — a filter that looks precise and is quietly
    wrong, which is the worst way for one to fail. The question is whether any
    visible evidence was read under the lens.
    """
    relation = [{"subject": "Krishna", "predicate": "teaches",
                 "object": "karma yoga"}]
    # The plain pass first, so it is the one that creates the fact.
    ids, _ = await _ingest(pool, tenant, "merge-plain", relation)
    await _ingest(pool, tenant, "merge-scripture", relation, template="scripture")

    graph = PostgresGraph(pool)
    actor = await _principal(pool, tenant)
    found = await graph.neighbourhood(
        actor, entity_id=ids["Krishna"], depth=1, predicates=None, limit=50,
        template="scripture")
    assert [e.predicate for e in found.edges] == ["teaches"], (
        "the scripture pass asserted this claim, so it is reachable through "
        "that lens even though the plain pass created the row"
    )
    # And two records now stand behind one claim, not two claims.
    assert found.edges[0].evidence == 2


async def test_an_edge_says_whether_it_was_read_off_the_page_or_into_it(pool, tenant):
    """A path crossing an interpretive edge is a reading, and an answer resting
    on one should be able to say so."""
    ids, _ = await _ingest(
        pool, tenant, "gita-5",
        [{"subject": "Krishna", "predicate": "teaches", "object": "karma yoga"},
         {"subject": "karma yoga", "predicate": "about", "object": "delusion"}],
        template="scripture",
    )
    graph = PostgresGraph(pool)
    found = await graph.neighbourhood(
        await _principal(pool, tenant), entity_id=ids["Krishna"], depth=2,
        predicates=None, limit=50, template="scripture")
    classes = {e.predicate: e.confidence_class for e in found.edges}
    assert classes["teaches"] == "interpretive"
    assert classes["about"] == "structural"


# ------------------------------------------------- anchoring on a named entity


async def test_a_named_anchor_replaces_the_one_parsed_from_the_question(pool, tenant):
    """`graph_seeds` scrapes entity names out of the question text, which works
    for "what did Krishna teach" and not for a question that never names its
    subject. Naming the anchor is the difference between hoping the graph arm
    keys off the right thing and saying where to start."""
    from memdog.retrieval import graph_seeds, seeds_for_ids

    ids, _ = await _ingest(
        pool, tenant, "anchor-1",
        [{"subject": "Krishna", "predicate": "teaches", "object": "karma yoga"}],
        template="scripture",
    )
    actor = await _principal(pool, tenant)

    # A question that names nobody resolves to nothing by parsing...
    parsed = await graph_seeds(
        pool, actor, project_id=tenant.project_id,
        query="what does it say about detachment")
    assert parsed == []

    # ...and to exactly the chosen entity when the caller says so.
    chosen = await seeds_for_ids(
        pool, actor, project_id=tenant.project_id,
        entity_ids=[ids["Krishna"]])
    assert [s.display_name for s in chosen] == ["Krishna"]
    # Reported as chosen, not matched -- a reader can tell an entity the system
    # found from one a person insisted on.
    assert chosen[0].matched_on == "chosen"


async def test_an_entity_you_cannot_see_is_not_a_usable_anchor(pool, tenant):
    """An id is far easier to enumerate than a name, so passing one must not
    confirm the entity exists to somebody who can see no record naming it."""
    from memdog.bootstrap import create_user
    from memdog.retrieval import seeds_for_ids

    other = await create_user(pool, f"outsider-{new_id('x')}@example.com")
    data_id = await _item(pool, tenant, f"private-{new_id('x')}", template="scripture")
    await pool.execute(
        "UPDATE data_items SET access_level = 'private', owner_id = $2 WHERE data_id = $1",
        data_id, other,
    )
    async with pool.acquire() as conn, conn.transaction():
        resolved = await resolve_mentions(
            conn, data_id=data_id, org_id=tenant.org_id,
            project_id=tenant.project_id,
            candidates=[{"name": "Secret Doctrine", "type": "topic"}],
        )
    hidden = resolved[0]["entity_id"]

    seeds = await seeds_for_ids(
        pool, await _principal(pool, tenant), project_id=tenant.project_id,
        entity_ids=[hidden])
    assert seeds == [], "an entity whose only evidence is unreadable is not an anchor"


async def test_an_unresolvable_anchor_narrows_rather_than_fails(pool, tenant):
    """The caller is a scope picker sending what it last loaded. One stale id
    should narrow the answer, not turn the question into an error."""
    from memdog.retrieval import seeds_for_ids

    seeds = await seeds_for_ids(
        pool, await _principal(pool, tenant), project_id=tenant.project_id,
        entity_ids=["ent_does_not_exist"])
    assert seeds == []
