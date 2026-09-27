"""Graph traversal -- layer 2, behind a seam.

Postgres is the implementation, not the interface. Traversal sits behind
`GraphStore` for the same reason the blob store, the queue and the embedding
engine do: the decision to keep the graph in the record store is a good one for
this system at this size, and it should stay a decision rather than becoming an
assumption welded into every caller.

What keeps it here for now is not performance. It is that the access rule and
the traversal have to be the same query. Filtering a traversal afterwards leaks
structure -- the endpoints of a path through a record you cannot see still tell
you that record exists -- so the visibility predicate is joined into the
recursive term, and a path touching anything invisible is not returned at all.

Two kinds of connection, deliberately not merged:

**Asserted edges** are claims a document made. They carry a predicate, the
record that said so, and a confidence, because somebody asserted them and
somebody can be wrong.

**Co-mentions** are two entities named in the same record. They are not stored,
because `entity_mentions` already records them and a materialised copy would go
stale; they are computed at query time. They are also the reason the graph is
not empty before a model has ever extracted a relationship -- which matters
much more than it sounds, since edge extraction is the part that needs a
working model and co-mention is the part that needs nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

import asyncpg

from .acl import visibility_params, visibility_sql
from .auth import DATA_READ, DATA_WRITE, Principal
from . import graph_templates, predicates as predicates_mod
from .ids import new_id
from .telemetry import record, span

# The vocabulary lives in `predicates.py` now, with a domain and a range on
# each. It was defined here and again in `extraction.RELATION_PREDICATES`, and
# nothing failed if the two drifted -- a predicate the extractor offered and the
# store did not accept is written by the model, passed by the schema, and
# rejected by a CHECK at the very end of enrichment.
PREDICATES = predicates_mod.NAMES

MAX_DEPTH = 3

# The ceiling on a whole-project overview, which is a readability limit before
# it is a cost one. Past a few hundred edges a drawn graph is a grey disc that
# tells a reader nothing, so a caller asking for more is asking for a worse
# picture; and because `overview` is reachable from the unauthenticated demo
# surface, the bound has to live here rather than at the call site.
MAX_OVERVIEW = 500

# Which predicates hold at most one open value at a time. A new fact on one of
# these closes the previous one; everything else accumulates.
#
# The default is multi-valued, and single-valued is opt-in, because getting this
# backwards is the expensive error. Marking `works_for` single-valued would
# silently close every second job as though the person had left it -- recoverable,
# since closing is not deletion, but wrong in a way that reads as correct. So the
# list stays short and conservative, and `works_for` is deliberately not on it:
# people hold two jobs, sit on boards, and consult.
#
# None of the template predicates is single-valued either, and `leads_to` is the
# one worth naming: many things lead to the same outcome, and closing the
# previous claim each time would leave a causal graph holding only whichever
# cause was ingested last.
SINGLE_VALUED = predicates_mod.SINGLE_VALUED

# No model decides what stopped being true. Everywhere else in this codebase the
# deterministic layer runs first -- six classification layers before any LLM,
# an identifier join before an inference -- and supersession is the same shape:
# a declared cardinality, not a judgement.


def _fact_visibility(org_p: int, user_p: int, principals_p: int, alias: str = "f") -> str:
    """Visibility for a fact, which has two sources depending on its basis.

    A **derived** fact stores no ACL of its own: it is visible exactly when some
    evidence for it is. That is deliberate -- evidence is mutable, and a stored
    answer would be wrong the moment a source is added or erased, which is the
    argument memories.md already makes about expiry.

    An **asserted** fact has no evidence to ask, so it carries the columns
    `visibility_sql` expects and is asked directly.
    """
    return f"""(
        ({alias}.basis = 'derived' AND EXISTS (
            SELECT 1 FROM entity_edges ev
              JOIN data_items d ON d.data_id = ev.source_data_id
             WHERE ev.fact_id = {alias}.fact_id
               AND {visibility_sql("d", org_p, user_p, principals_p)}))
        OR ({alias}.basis = 'asserted'
            AND {visibility_sql(alias, org_p, user_p, principals_p)})
    )"""


def _drawn_under(template_p: int, org_p: int, user_p: int, principals_p: int,
                 alias: str = "f") -> str:
    """Whether any *visible evidence* for this fact was read under a template.

    Asked of the evidence rather than of `entity_facts.template`, and the
    difference is not academic. Facts merge across records -- a second document
    asserting a claim we already hold corroborates it rather than creating a
    new one -- so the claim's own `template` column records whichever lens
    happened to write it first and is silent about every lens that agreed.

    That is exactly what happened the first time this ran: the same passage was
    ingested once plainly and once as scripture, the plain pass created the
    facts, and the scripture pass corroborated them. Filtering on the claim's
    column then reported one edge where seven had been drawn -- the filter
    looked precise and was quietly wrong, which is the worst way for a filter
    to fail.

    A claim is not owned by one lens. It is *reachable through* a lens when
    something that lens read asserts it, and a record may be read through
    several.

    The visibility join is not optional: without it, a template filter would
    confirm that some record the caller cannot read was read under a given
    template, which is the same structural leak the traversal ACL exists to
    prevent.
    """
    return f"""(${template_p}::text IS NULL OR EXISTS (
        SELECT 1 FROM entity_edges ev
          JOIN data_items d ON d.data_id = ev.source_data_id
         WHERE ev.fact_id = {alias}.fact_id
           AND ev.template = ${template_p}
           AND {visibility_sql("d", org_p, user_p, principals_p)}))"""


def _from_source(memory_p: int, data_p: int, org_p: int, user_p: int,
                 principals_p: int, alias: str = "f") -> str:
    """Whether any *visible evidence* for this fact came from a given memory, or
    from one particular record.

    Asked of the evidence rather than of the claim, for the reason
    `_drawn_under` gives at length: facts merge across records, so a claim's own
    row remembers whichever write created it and is silent about every other
    record that later corroborated it. "Which claims does this memory support"
    is a question about evidence, and answering it from the claim would report
    one edge where several were asserted.

    The visibility join is not optional. Without it a filter would confirm that
    some record the caller cannot read sits in a named memory -- the same
    structural leak the traversal ACL exists to prevent, arriving through a
    filter rather than through a path.
    """
    return f"""(
        (${memory_p}::text IS NULL AND ${data_p}::text IS NULL)
        OR EXISTS (
            SELECT 1 FROM entity_edges ev
              JOIN data_items d ON d.data_id = ev.source_data_id
             WHERE ev.fact_id = {alias}.fact_id
               AND (${data_p}::text IS NULL OR ev.source_data_id = ${data_p})
               AND (${memory_p}::text IS NULL OR EXISTS (
                     SELECT 1 FROM memory_members mm
                      WHERE mm.data_id = ev.source_data_id
                        AND mm.memory_id = ${memory_p}))
               AND {visibility_sql("d", org_p, user_p, principals_p)})
    )"""


def _temporal(valid_p: int, asof_p: int, alias: str = "f") -> str:
    """The two clocks, as a predicate.

    Both go inside the query rather than over its results, for the reason
    `acl.py` gives about wrong rows and the stronger one 0021 gives about a
    filtered traversal disclosing the shape of what it hid.
    """
    return f"""(
        {alias}.valid_from <= ${valid_p}
        AND ({alias}.valid_to IS NULL OR {alias}.valid_to > ${valid_p})
        AND {alias}.recorded_at <= ${asof_p}
        AND ({alias}.retracted_at IS NULL OR {alias}.retracted_at > ${asof_p})
    )"""


class GraphError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Node:
    entity_id: str
    display_name: str
    type: str
    depth: int


@dataclass
class Edge:
    subject_id: str
    predicate: str
    object_id: str
    # How many distinct records assert this. One document saying something is a
    # claim; four saying it independently is closer to a fact, and collapsing
    # them into a boolean throws that away.
    evidence: int
    source_data_ids: list[str] = field(default_factory=list)
    confidence: float = 0.5
    fact_id: str | None = None
    valid_from: datetime | None = None
    # None means still true, which is not the same as "we stopped looking".
    valid_to: datetime | None = None
    basis: str = "derived"
    # Which template drew this edge. None is open-domain extraction.
    template: str | None = None

    @property
    def confidence_class(self) -> str:
        """"structural" or "interpretive" -- a property of the predicate.

        Carried on the edge because it is the thing a reader needs at the point
        of traversal: a path crossing an interpretive edge is a reading, and an
        answer resting on one should say so. Derived rather than stored, so
        reclassifying a predicate does not require rewriting its edges.
        """
        return predicates_mod.REGISTRY[self.predicate].confidence \
            if self.predicate in predicates_mod.REGISTRY else "structural"


@dataclass
class Neighbourhood:
    root: Node
    nodes: list[Node]
    edges: list[Edge]
    truncated: bool = False


@dataclass
class Overview:
    """A whole project's graph, rather than a walk outward from one node.

    Deliberately not a `Neighbourhood` with the root left out. A neighbourhood
    answers *what is near this thing*, and every node in it carries a depth that
    means something. Here there is no centre and no depth, and reusing the type
    would have every consumer reading a `depth` of zero as a fact about the
    graph rather than as an absent field.

    The two also truncate differently, which is the part that would have been
    silently wrong. A neighbourhood caps *nodes* and keeps whatever edges join
    them; this caps *claims* and keeps whatever nodes they touch, because the
    question it answers is "what does this corpus most assert" and an answer
    that dropped the best-attested edge to fit a node budget would be a
    different answer that looked the same.
    """

    nodes: list[Node]
    edges: list[Edge]
    truncated: bool = False


class GraphStore(Protocol):
    async def neighbourhood(
        self, principal: Principal, *, entity_id: str, depth: int,
        predicates: list[str] | None, limit: int,
        valid_at: datetime | None = None, as_of: datetime | None = None,
        template: str | None = None,
    ) -> Neighbourhood: ...

    async def overview(
        self, principal: Principal, *, project_id: str, limit: int = 200,
        predicates: list[str] | None = None, template: str | None = None,
        memory_id: str | None = None, data_id: str | None = None,
        valid_at: datetime | None = None, as_of: datetime | None = None,
    ) -> Overview: ...


class PostgresGraph:
    """Traversal as a recursive CTE, with the ACL inside it."""

    name = "postgres"

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def neighbourhood(
        self, principal: Principal, *, entity_id: str, depth: int = 1,
        predicates: list[str] | None = None, limit: int = 120,
        valid_at: datetime | None = None, as_of: datetime | None = None,
        template: str | None = None,
    ) -> Neighbourhood:
        """`valid_at` asks what was true then; `as_of` asks what we believed then.

        Both default to now, so a caller that does not care about time gets
        exactly the graph it got before this existed.
        """
        principal.require(DATA_READ)
        if not 1 <= depth <= MAX_DEPTH:
            raise GraphError(f"depth must be between 1 and {MAX_DEPTH}")
        if predicates:
            unknown = sorted(set(predicates) - set(PREDICATES))
            if unknown:
                raise GraphError(f"unknown predicate(s): {', '.join(unknown)}")
        now = datetime.now(timezone.utc)

        with span("graph.neighbourhood", depth=depth, store=self.name):
            return await self._neighbourhood(
                principal, entity_id, depth, predicates, limit,
                valid_at or now, as_of or now, template,
            )

    async def _neighbourhood(
        self, principal: Principal, entity_id: str, depth: int,
        predicates: list[str] | None, limit: int,
        valid_at: datetime, as_of: datetime, template: str | None = None,
    ) -> Neighbourhood:
        org_id, user_id, principals = visibility_params(principal)
        # An entity is visible only through a record the caller can read, so
        # visibility is asked of `data_items` and inherited from there.
        visible = visibility_sql("d", 2, 3, 4)

        root = await self.pool.fetchrow(
            f"""
            SELECT e.entity_id, e.display_name, e.type
              FROM entities e
             WHERE e.entity_id = $1
               AND EXISTS (
                   SELECT 1 FROM entity_mentions m
                     JOIN data_items d ON d.data_id = m.data_id
                    WHERE m.entity_id = e.entity_id
                      AND d.deleted_at IS NULL AND {visible}
               )
            """,
            entity_id, org_id, user_id, principals,
        )
        if root is None:
            # Indistinguishable from "does not exist", which is the point: an
            # entity the caller cannot see evidence for must not be confirmable.
            raise GraphError("entity not found", status=404)

        # `reachable` walks both directions -- an edge is a relationship, and
        # which end was written as the subject is a grammatical accident.
        # Visibility is enforced on every hop, not once at the end, so a path
        # cannot pass *through* a record the caller cannot read.
        rows = await self.pool.fetch(
            f"""
            WITH RECURSIVE visible_edges AS (
                SELECT f.subject_id, f.predicate, f.object_id
                  FROM entity_facts f
                 WHERE ($5::text[] IS NULL OR f.predicate = ANY($5))
                   -- Inside the recursive term, not applied to the result. A
                   -- path is only within a template if every hop of it is;
                   -- filtering afterwards would return endpoints joined by
                   -- edges the filter excluded, which reads as the template
                   -- having asserted something it never saw.
                   AND {_drawn_under(10, 2, 3, 4)}
                   AND {_temporal(8, 9)}
                   AND {_fact_visibility(2, 3, 4)}
            ),
            reachable (entity_id, depth) AS (
                SELECT $1::text, 0
                UNION
                SELECT next_id, r.depth + 1
                  FROM reachable r
                  JOIN LATERAL (
                      SELECT ve.object_id AS next_id FROM visible_edges ve
                       WHERE ve.subject_id = r.entity_id
                      UNION
                      SELECT ve.subject_id FROM visible_edges ve
                       WHERE ve.object_id = r.entity_id
                  ) step ON true
                 WHERE r.depth < $6
            )
            SELECT r.entity_id, min(r.depth) AS depth, e.display_name, e.type
              FROM reachable r
              JOIN entities e ON e.entity_id = r.entity_id
             WHERE EXISTS (
                   SELECT 1 FROM entity_mentions m
                     JOIN data_items d ON d.data_id = m.data_id
                    WHERE m.entity_id = r.entity_id
                      AND d.deleted_at IS NULL AND {visible}
               )
             GROUP BY r.entity_id, e.display_name, e.type
             ORDER BY min(r.depth), e.display_name
             LIMIT $7
            """,
            entity_id, org_id, user_id, principals,
            predicates, depth, limit, valid_at, as_of, template,
        )
        nodes = [
            Node(r["entity_id"], r["display_name"], r["type"], r["depth"])
            for r in rows
        ]
        ids = [n.entity_id for n in nodes]

        # Its own parameter numbering: the traversal's $1 (the root) has no
        # place here, and an unreferenced parameter has no inferable type.
        # Evidence is counted per fact rather than per (subject, predicate,
        # object): the same claim held over two periods is two facts, and
        # collapsing them would report one window covering a gap when the claim
        # was not true.
        edges = await self.pool.fetch(
            f"""
            SELECT f.fact_id, f.subject_id, f.predicate, f.object_id,
                   f.valid_from, f.valid_to, f.basis, f.confidence, f.template,
                   count(DISTINCT ev.source_data_id) AS evidence,
                   coalesce(array_agg(DISTINCT ev.source_data_id)
                            FILTER (WHERE ev.source_data_id IS NOT NULL),
                            '{{}}') AS sources
              FROM entity_facts f
              LEFT JOIN entity_edges ev ON ev.fact_id = f.fact_id
             WHERE f.subject_id = ANY($4::text[]) AND f.object_id = ANY($4::text[])
               AND ($5::text[] IS NULL OR f.predicate = ANY($5))
               AND {_drawn_under(8, 1, 2, 3)}
               AND {_temporal(6, 7)}
               AND {_fact_visibility(1, 2, 3)}
             GROUP BY f.fact_id
             ORDER BY count(DISTINCT ev.source_data_id) DESC, f.valid_from DESC
            """,
            org_id, user_id, principals, ids, predicates, valid_at, as_of,
            template,
        ) if ids else []

        return Neighbourhood(
            root=Node(root["entity_id"], root["display_name"], root["type"], 0),
            nodes=nodes,
            edges=[
                Edge(e["subject_id"], e["predicate"], e["object_id"],
                     e["evidence"], list(e["sources"]), float(e["confidence"] or 0),
                     fact_id=e["fact_id"], valid_from=e["valid_from"],
                     valid_to=e["valid_to"], basis=e["basis"],
                     template=e["template"])
                for e in edges
            ],
            truncated=len(nodes) >= limit,
        )

    async def overview(
        self, principal: Principal, *, project_id: str, limit: int = 200,
        predicates: list[str] | None = None, template: str | None = None,
        memory_id: str | None = None, data_id: str | None = None,
        valid_at: datetime | None = None, as_of: datetime | None = None,
    ) -> Overview:
        """Everything a project currently claims, best-attested first.

        **Claims are selected, and nodes follow from them.** The obvious
        implementation picks the most-mentioned entities and then asks what
        joins them, and it produces a picture that is wrong in a way nobody can
        see: the busiest nodes in a corpus are its cast, and the edges between
        the cast are the ones the text bothered to state least often. Starting
        from the facts means the thing on screen is what the corpus most
        insists on, and the nodes are whatever that turns out to involve.

        **Both endpoints are joined rather than filtered afterwards**, so an
        edge cannot be returned pointing at a node that is not in the node list.
        A merged entity keeps its facts -- `entities.merge` moves mentions and
        leaves `entity_facts` alone -- so without the `merged_into` join those
        facts would come back as edges to a node the caller never receives.

        Visibility is the same predicate the traversal uses, asked of the
        evidence and then again of each endpoint's mentions. The second check is
        not redundant in the way it looks: a fact is visible through *some*
        evidence, and an endpoint whose every mention sits in records the caller
        cannot read must not be named just because a relation mentioning it was
        extracted somewhere the caller can.
        """
        principal.require(DATA_READ)
        if not 1 <= limit <= MAX_OVERVIEW:
            raise GraphError(f"limit must be between 1 and {MAX_OVERVIEW}")
        if predicates:
            unknown = sorted(set(predicates) - set(PREDICATES))
            if unknown:
                raise GraphError(f"unknown predicate(s): {', '.join(unknown)}")
        now = datetime.now(timezone.utc)
        valid_at, as_of = valid_at or now, as_of or now

        org_id, user_id, principals = visibility_params(principal)
        seen = visibility_sql("d", 1, 2, 3)

        def mentioned(alias: str) -> str:
            return f"""EXISTS (
                SELECT 1 FROM entity_mentions m
                  JOIN data_items d ON d.data_id = m.data_id
                 WHERE m.entity_id = {alias}.entity_id
                   AND d.deleted_at IS NULL AND {seen})"""

        with span("graph.overview", store=self.name):
            # One query rather than facts-then-entities. The endpoints are
            # already joined to decide the row belongs, so selecting their names
            # here costs nothing and removes the window in which a second query
            # could see a different graph than the first.
            rows = await self.pool.fetch(
                f"""
                SELECT f.fact_id, f.predicate, f.confidence, f.basis, f.template,
                       f.valid_from, f.valid_to,
                       s.entity_id AS subject_id, s.display_name AS subject_name,
                       s.type AS subject_type,
                       o.entity_id AS object_id, o.display_name AS object_name,
                       o.type AS object_type,
                       count(DISTINCT ev.source_data_id) AS evidence
                  FROM entity_facts f
                  JOIN entities s ON s.entity_id = f.subject_id
                                 AND s.merged_into IS NULL
                  JOIN entities o ON o.entity_id = f.object_id
                                 AND o.merged_into IS NULL
                  LEFT JOIN entity_edges ev ON ev.fact_id = f.fact_id
                 WHERE f.project_id = $4
                   AND ($5::text[] IS NULL OR f.predicate = ANY($5))
                   AND {_drawn_under(8, 1, 2, 3)}
                   AND {_from_source(10, 11, 1, 2, 3)}
                   AND {_temporal(6, 7)}
                   AND {_fact_visibility(1, 2, 3)}
                   AND {mentioned("s")}
                   AND {mentioned("o")}
                 GROUP BY f.fact_id, s.entity_id, s.display_name, s.type,
                          o.entity_id, o.display_name, o.type
                 ORDER BY count(DISTINCT ev.source_data_id) DESC,
                          f.confidence DESC, f.fact_id
                 LIMIT $9
                """,
                org_id, user_id, principals, project_id, predicates,
                valid_at, as_of, template, limit, memory_id, data_id,
            )

        nodes: dict[str, Node] = {}
        edges: list[Edge] = []
        for row in rows:
            for side in ("subject", "object"):
                entity_id = row[f"{side}_id"]
                # `depth` is zero for every node and means "no centre here",
                # which is why this returns an Overview and not a Neighbourhood.
                nodes.setdefault(entity_id, Node(
                    entity_id, row[f"{side}_name"], row[f"{side}_type"], 0))
            edges.append(Edge(
                row["subject_id"], row["predicate"], row["object_id"],
                row["evidence"],
                # Deliberately empty. The record ids behind a claim are the one
                # thing on an edge that names something outside the graph, and
                # an overview is read to see shape -- `neighbourhood` is where a
                # caller who wants the evidence goes and gets it one node at a
                # time, under the same ACL.
                [],
                float(row["confidence"] or 0), fact_id=row["fact_id"],
                valid_from=row["valid_from"], valid_to=row["valid_to"],
                basis=row["basis"], template=row["template"],
            ))

        return Overview(
            nodes=sorted(nodes.values(), key=lambda n: n.display_name),
            edges=edges,
            truncated=len(edges) >= limit,
        )

    async def co_mentioned(
        self, principal: Principal, *, entity_id: str, limit: int = 25,
    ) -> list[dict]:
        """Entities named in the same records as this one.

        Not an assertion about anything -- appearing together is weak evidence
        and is reported as a count so a reader can judge it. Its value is that
        it needs no extraction at all, so the graph is useful the moment
        entities exist rather than only once a model has read for relationships.
        """
        principal.require(DATA_READ)
        org_id, user_id, principals = visibility_params(principal)
        visible = visibility_sql("d", 2, 3, 4)
        rows = await self.pool.fetch(
            f"""
            SELECT other.entity_id, e.display_name, e.type,
                   count(DISTINCT other.data_id) AS shared_records
              FROM entity_mentions mine
              JOIN entity_mentions other ON other.data_id = mine.data_id
                                        AND other.entity_id <> mine.entity_id
              JOIN entities e ON e.entity_id = other.entity_id
              JOIN data_items d ON d.data_id = other.data_id
             WHERE mine.entity_id = $1
               AND e.merged_into IS NULL
               AND d.deleted_at IS NULL AND {visible}
             GROUP BY other.entity_id, e.display_name, e.type
             ORDER BY count(DISTINCT other.data_id) DESC, e.display_name
             LIMIT $5
            """,
            entity_id, org_id, user_id, principals, limit,
        )
        return [dict(r) for r in rows]


async def record_edges(
    conn: asyncpg.Connection, *, data_id: str, org_id: str, project_id: str,
    resolved: list[dict], relations: list[dict],
    generator_version: str | None = None, template: str | None = None,
) -> list[dict]:
    """Store the relationships a document asserted, in the enrichment transaction.

    Extraction names entities by name, not by id, so relations are matched back
    against what the resolver produced for this same record. A relation naming
    something the resolver did not produce is dropped rather than guessed at --
    inventing an endpoint would attach a real claim to the wrong node, and the
    graph has no way to show that later.
    """
    by_name, type_of = {}, {}
    for item in resolved:
        key = item["name"].strip().casefold()
        by_name[key] = item["entity_id"]
        type_of[key] = item.get("type")

    # What the template said may be looked for. An edge outside that set is a
    # predicate the model was not offered, which means it either ignored the
    # enum or a template changed after this text was read -- both worth
    # dropping rather than storing under a template that does not claim it.
    offered = frozenset(graph_templates.predicates_for(template))

    # Valid time is the world's clock, so it comes from the record's event_time
    # rather than from now(). Backfilling a two-year-old document must place its
    # claims two years ago, or every historical import lands as breaking news.
    valid_from = await conn.fetchval(
        "SELECT event_time FROM data_items WHERE data_id = $1", data_id
    )

    written = []
    for relation in relations or []:
        predicate = (relation.get("predicate") or "").strip()
        subject = (relation.get("subject") or "").strip().casefold()
        obj = (relation.get("object") or "").strip().casefold()
        if predicate not in PREDICATES or predicate not in offered:
            continue
        subject_id, object_id = by_name.get(subject), by_name.get(obj)
        if not subject_id or not object_id or subject_id == object_id:
            continue
        # Domain and range. `teaches` runs agent -> idea, so a city teaching a
        # doctrine is refused here rather than discovered a quarter later by
        # somebody reading a bad answer. An unknown or absent type passes:
        # the type comes from the same model that produced the relation and is
        # the least reliable thing on the row, so refusing a plausible claim
        # over a shaky guess about one of its endpoints is the wrong trade.
        if not predicates_mod.permits(predicate, type_of.get(subject),
                                      type_of.get(obj)):
            record("graph_edge_refused", 1, predicate=predicate)
            continue
        confidence = float(relation.get("confidence") or 0.5)
        fact_id = await _upsert_fact(
            conn, org_id=org_id, project_id=project_id, subject_id=subject_id,
            predicate=predicate, object_id=object_id, valid_from=valid_from,
            basis="derived", confidence=confidence,
            generator_version=generator_version, template=template,
        )
        await conn.execute(
            """
            INSERT INTO entity_edges (edge_id, org_id, project_id, subject_id,
                predicate, object_id, source_data_id, confidence, generator_version,
                fact_id, template)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (subject_id, predicate, object_id, source_data_id)
            DO UPDATE SET fact_id = EXCLUDED.fact_id, template = EXCLUDED.template
            """,
            new_id("edg"), org_id, project_id, subject_id, predicate,
            object_id, data_id, confidence, generator_version, fact_id, template,
        )
        written.append({"subject_id": subject_id, "predicate": predicate,
                        "object_id": object_id, "fact_id": fact_id,
                        "template": template})
    return written


async def _upsert_fact(
    conn, *, org_id: str, project_id: str, subject_id: str, predicate: str,
    object_id: str, valid_from, basis: str, confidence: float,
    generator_version: str | None = None, owner_id: str | None = None,
    access_level: str | None = None, shared_with: list[str] | None = None,
    key_id: str | None = None, template: str | None = None,
) -> str:
    """Record the claim, then close whatever it replaced.

    Supersession is deterministic: a predicate declared single-valued holds one
    open value, so a later fact closes the earlier one. Nothing is deleted and
    `recorded_at` is not touched -- closing says the claim stopped being true,
    not that we stopped believing we once knew it, and an earlier `as_of` still
    returns it exactly as it stood.
    """
    import json

    row = await conn.fetchrow(
        """
        INSERT INTO entity_facts (fact_id, org_id, project_id, subject_id,
            predicate, object_id, valid_from, basis, confidence,
            generator_version, owner_id, access_level, shared_with,
            asserted_by_key_id, template)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb, $14, $15)
        ON CONFLICT (project_id, subject_id, predicate, object_id)
            WHERE valid_to IS NULL AND retracted_at IS NULL
        -- A second source for a claim we already hold is more evidence, not a
        -- new claim: confidence rises to the best any source offered, and
        -- valid_from moves back to the earliest evidence, because learning of an
        -- older document means the claim was true earlier than we thought -- not
        -- that a second claim began.
        DO UPDATE SET confidence = greatest(entity_facts.confidence, EXCLUDED.confidence),
                      valid_from = least(entity_facts.valid_from, EXCLUDED.valid_from)
        -- `xmax = 0` is true only for a row this statement inserted. Without it
        -- a second source for a claim we already hold would announce itself as a
        -- new one, and an alert watching assertions would fire on every
        -- corroboration.
        RETURNING fact_id, (xmax = 0) AS inserted
        """,
        new_id("fct"), org_id, project_id, subject_id, predicate, object_id,
        valid_from, basis, confidence, generator_version, owner_id, access_level,
        json.dumps(shared_with or []), key_id, template,
    )
    fact_id = row["fact_id"]

    types = await _endpoint_types(conn, subject_id, object_id)
    if row["inserted"]:
        await _emit_transition(
            conn, "fact.asserted", org_id=org_id, project_id=project_id,
            payload={"fact_id": fact_id, "subject_id": subject_id,
                     "predicate": predicate, "object_id": object_id,
                     "basis": basis, **types},
        )

    if predicate in SINGLE_VALUED:
        closed = await conn.fetch(
            """
            UPDATE entity_facts
               SET valid_to = $4, superseded_by = $5
             WHERE project_id = $1 AND subject_id = $2 AND predicate = $3
               AND fact_id <> $5
               AND valid_to IS NULL AND retracted_at IS NULL
               -- Strictly earlier. Two claims asserted as true at the same
               -- instant do not supersede each other -- they contradict, and
               -- that is for the conflicts view to surface rather than for this
               -- to silently resolve.
               AND valid_from < $4
            RETURNING fact_id, subject_id, predicate, object_id, basis
            """,
            project_id, subject_id, predicate, valid_from, fact_id,
        )
        for old_fact in closed:
            # The closed fact's OWN endpoints, not the new one's. `types` above
            # describes the claim that just opened, and spreading it here made
            # the superseded event say object_name=Berlin where Lisbon had
            # ended -- with the object_id still pointing at Lisbon. Invisible
            # while only types were carried, because both are locations.
            closed_types = await _endpoint_types(
                conn, old_fact["subject_id"], old_fact["object_id"])
            await _emit_transition(
                conn, "fact.superseded", org_id=org_id, project_id=project_id,
                payload={"fact_id": old_fact["fact_id"],
                         "subject_id": old_fact["subject_id"],
                         "predicate": old_fact["predicate"],
                         "object_id": old_fact["object_id"],
                         "basis": old_fact["basis"],
                         # What replaced it, named, so a reader does not have to
                         # resolve an id to learn where the person went.
                         "superseded_by": fact_id,
                         "replaced_by_name": types.get("object_name"),
                         **closed_types},
            )
    return fact_id


async def _endpoint_types(conn, subject_id: str, object_id: str) -> dict:
    """Entity types **and names**, denormalised onto the transition.

    A selector asking for "any person's location changing" has to be answerable
    without a join, because the alert evaluator reads the event log and nothing
    else -- and by the time it looks, the entity may have been merged away.

    The names are here for a reason a live test found and no unit test could.
    An alert described in words is shown this payload and nothing else, so
    "a person has moved to a city in Spain" was being judged against
    `object_id: ent_01M18Z…` -- and the model correctly declined every time,
    because an identifier is not evidence of anything. A reader of the console
    had the same problem: types are not names.
    """
    rows = await conn.fetch(
        "SELECT entity_id, type, display_name FROM entities "
        "WHERE entity_id = ANY($1::text[])",
        [subject_id, object_id],
    )
    by_id = {r["entity_id"]: r for r in rows}
    subject, obj = by_id.get(subject_id), by_id.get(object_id)
    return {
        "subject_type": subject["type"] if subject else None,
        "object_type": obj["type"] if obj else None,
        # Denormalised on purpose. A merge later can make these disagree with
        # the entity, and that is correct: the transition records what was
        # asserted at the time, not what the graph says about it now.
        "subject_name": subject["display_name"] if subject else None,
        "object_name": obj["display_name"] if obj else None,
    }


async def _emit_transition(conn, event_type: str, *, org_id: str,
                           project_id: str, payload: dict) -> None:
    from .alerts import emit_transition

    await emit_transition(conn, event_type, org_id=org_id,
                          project_id=project_id, payload=payload)


async def assert_fact(
    pool: asyncpg.Pool, principal: Principal, *, project_id: str,
    subject_id: str, predicate: str, object_id: str,
    valid_from=None, confidence: float = 1.0,
    access_level: str = "private", shared_with: list[str] | None = None,
) -> dict:
    """State a fact outright. No document, no model, no cost.

    `entity_edges.source_data_id` is NOT NULL, so until now every claim had to
    descend from a record -- an agent that already knew something had to write a
    document for an extractor to read it back out. An asserted fact skips all of
    it, and `basis` keeps it distinguishable from one a model inferred, which is
    the distinction anyone auditing the graph will ask for first.
    """
    principal.require(DATA_WRITE)
    if predicate not in PREDICATES:
        raise GraphError(f"unknown predicate {predicate!r}")
    if subject_id == object_id:
        raise GraphError("a fact cannot relate an entity to itself")

    rows = await pool.fetch(
        "SELECT entity_id FROM entities WHERE entity_id = ANY($1::text[]) "
        "AND org_id = $2 AND project_id = $3",
        [subject_id, object_id], principal.org_id, project_id,
    )
    if len(rows) != 2:
        raise GraphError("subject or object not found in this project", status=404)

    async with pool.acquire() as conn, conn.transaction():
        fact_id = await _upsert_fact(
            conn, org_id=principal.org_id, project_id=project_id,
            subject_id=subject_id, predicate=predicate, object_id=object_id,
            valid_from=valid_from or datetime.now(timezone.utc),
            basis="asserted", confidence=confidence,
            owner_id=principal.user_id, access_level=access_level,
            shared_with=shared_with, key_id=principal.key_id,
        )
        row = await conn.fetchrow(
            "SELECT fact_id, valid_from, valid_to, basis, recorded_at "
            "FROM entity_facts WHERE fact_id = $1", fact_id,
        )
    return dict(row)


async def retract_fact(
    pool: asyncpg.Pool, principal: Principal, fact_id: str, reason: str | None = None,
) -> dict:
    """Withdraw a claim without erasing that it was made.

    Distinct from supersession: `valid_to` says the fact stopped being true,
    `retracted_at` says we should not have recorded it. Deleting the row would
    destroy the answer to "what did we believe in March", which is the question
    this table exists to answer.
    """
    principal.require(DATA_WRITE)
    row = await pool.fetchrow(
        """
        UPDATE entity_facts SET retracted_at = now(), retracted_reason = $3
         WHERE fact_id = $1 AND org_id = $2 AND retracted_at IS NULL
        RETURNING fact_id, retracted_at, retracted_reason
        """,
        fact_id, principal.org_id, reason,
    )
    if row is None:
        raise GraphError("fact not found or already retracted", status=404)
    async with pool.acquire() as conn:
        detail = await conn.fetchrow(
            "SELECT org_id, project_id, subject_id, predicate, object_id, basis "
            "FROM entity_facts WHERE fact_id = $1", fact_id)
        await _emit_transition(
            conn, "fact.retracted", org_id=detail["org_id"],
            project_id=detail["project_id"],
            payload={"fact_id": fact_id, "subject_id": detail["subject_id"],
                     "predicate": detail["predicate"],
                     "object_id": detail["object_id"], "basis": detail["basis"],
                     "reason": reason},
        )
    return dict(row)


async def conflicts(
    pool: asyncpg.Pool, principal: Principal, project_id: str, limit: int = 50,
) -> list[dict]:
    """Single-valued predicates holding more than one open value.

    This is UC2's "conflict surfacing when a doc and a later thread disagree".
    With cardinality declared it is a query rather than a model call: two open
    `located_in` facts for one subject cannot both be true, and supersession
    deliberately refuses to pick between claims that share a `valid_from`.
    """
    principal.require(DATA_READ)
    org_id, user_id, principals = visibility_params(principal)
    rows = await pool.fetch(
        f"""
        SELECT f.subject_id, s.display_name AS subject_name, f.predicate,
               array_agg(json_build_object(
                   'fact_id', f.fact_id, 'object_id', f.object_id,
                   'object_name', o.display_name, 'valid_from', f.valid_from,
                   'basis', f.basis, 'confidence', f.confidence
               ) ORDER BY f.valid_from) AS claims
          FROM entity_facts f
          JOIN entities s ON s.entity_id = f.subject_id
          JOIN entities o ON o.entity_id = f.object_id
         WHERE f.project_id = $5
           AND f.predicate = ANY($6::text[])
           AND f.valid_to IS NULL AND f.retracted_at IS NULL
           AND {_fact_visibility(1, 2, 3)}
         GROUP BY f.subject_id, s.display_name, f.predicate
        HAVING count(*) > 1
         ORDER BY count(*) DESC
         LIMIT $4
        """,
        org_id, user_id, principals, limit, project_id, sorted(SINGLE_VALUED),
    )
    import json as _json
    return [
        {"subject_id": r["subject_id"], "subject_name": r["subject_name"],
         "predicate": r["predicate"],
         "claims": [_json.loads(c) if isinstance(c, str) else c for c in r["claims"]]}
        for r in rows
    ]


async def fact_history(
    pool: asyncpg.Pool, principal: Principal, entity_id: str, limit: int = 200,
) -> list[dict]:
    """Every claim that has touched this entity, closed and open alike.

    Not filtered by time on purpose -- this is the view that answers "how did we
    come to believe this", so a superseded fact is the point rather than noise.
    """
    principal.require(DATA_READ)
    org_id, user_id, principals = visibility_params(principal)
    rows = await pool.fetch(
        f"""
        SELECT f.fact_id, f.subject_id, s.display_name AS subject_name,
               f.predicate, f.object_id, o.display_name AS object_name,
               f.valid_from, f.valid_to, f.recorded_at, f.retracted_at,
               f.retracted_reason, f.superseded_by, f.basis, f.confidence,
               (SELECT count(DISTINCT ev.source_data_id)
                  FROM entity_edges ev WHERE ev.fact_id = f.fact_id) AS evidence
          FROM entity_facts f
          JOIN entities s ON s.entity_id = f.subject_id
          JOIN entities o ON o.entity_id = f.object_id
         WHERE (f.subject_id = $5 OR f.object_id = $5)
           AND {_fact_visibility(1, 2, 3)}
         ORDER BY f.valid_from DESC, f.recorded_at DESC
         LIMIT $4
        """,
        org_id, user_id, principals, limit, entity_id,
    )
    return [dict(r) for r in rows]


def build_graph(pool: asyncpg.Pool, settings=None) -> GraphStore:
    """One implementation today. The seam exists so that stays a choice."""
    return PostgresGraph(pool)
