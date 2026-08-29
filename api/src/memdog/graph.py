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
from typing import Protocol

import asyncpg

from .acl import visibility_params, visibility_sql
from .auth import DATA_READ, Principal
from .ids import new_id
from .telemetry import span

PREDICATES = (
    "works_for", "member_of", "reports_to", "collaborates_with",
    "located_in", "part_of", "owns", "produces", "uses",
    "attended", "about", "related_to",
)

MAX_DEPTH = 3


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


@dataclass
class Neighbourhood:
    root: Node
    nodes: list[Node]
    edges: list[Edge]
    truncated: bool = False


class GraphStore(Protocol):
    async def neighbourhood(
        self, principal: Principal, *, entity_id: str, depth: int,
        predicates: list[str] | None, limit: int,
    ) -> Neighbourhood: ...


class PostgresGraph:
    """Traversal as a recursive CTE, with the ACL inside it."""

    name = "postgres"

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def neighbourhood(
        self, principal: Principal, *, entity_id: str, depth: int = 1,
        predicates: list[str] | None = None, limit: int = 120,
    ) -> Neighbourhood:
        principal.require(DATA_READ)
        if not 1 <= depth <= MAX_DEPTH:
            raise GraphError(f"depth must be between 1 and {MAX_DEPTH}")
        if predicates:
            unknown = sorted(set(predicates) - set(PREDICATES))
            if unknown:
                raise GraphError(f"unknown predicate(s): {', '.join(unknown)}")

        with span("graph.neighbourhood", depth=depth, store=self.name):
            return await self._neighbourhood(
                principal, entity_id, depth, predicates, limit
            )

    async def _neighbourhood(
        self, principal: Principal, entity_id: str, depth: int,
        predicates: list[str] | None, limit: int,
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
                SELECT g.subject_id, g.predicate, g.object_id, g.source_data_id,
                       g.confidence
                  FROM entity_edges g
                  JOIN data_items d ON d.data_id = g.source_data_id
                 WHERE d.deleted_at IS NULL
                   AND ($5::text[] IS NULL OR g.predicate = ANY($5))
                   AND {visible}
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
            predicates, depth, limit,
        )
        nodes = [
            Node(r["entity_id"], r["display_name"], r["type"], r["depth"])
            for r in rows
        ]
        ids = [n.entity_id for n in nodes]

        # Its own parameter numbering: the traversal's $1 (the root) has no
        # place here, and an unreferenced parameter has no inferable type.
        edge_visible = visibility_sql("d", 1, 2, 3)
        edges = await self.pool.fetch(
            f"""
            SELECT g.subject_id, g.predicate, g.object_id,
                   count(DISTINCT g.source_data_id) AS evidence,
                   max(g.confidence) AS confidence,
                   array_agg(DISTINCT g.source_data_id) AS sources
              FROM entity_edges g
              JOIN data_items d ON d.data_id = g.source_data_id
             WHERE g.subject_id = ANY($4::text[]) AND g.object_id = ANY($4::text[])
               AND d.deleted_at IS NULL
               AND ($5::text[] IS NULL OR g.predicate = ANY($5))
               AND {edge_visible}
             GROUP BY g.subject_id, g.predicate, g.object_id
             ORDER BY count(DISTINCT g.source_data_id) DESC
            """,
            org_id, user_id, principals, ids, predicates,
        ) if ids else []

        return Neighbourhood(
            root=Node(root["entity_id"], root["display_name"], root["type"], 0),
            nodes=nodes,
            edges=[
                Edge(e["subject_id"], e["predicate"], e["object_id"],
                     e["evidence"], list(e["sources"]), float(e["confidence"] or 0))
                for e in edges
            ],
            truncated=len(nodes) >= limit,
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
    generator_version: str | None = None,
) -> list[dict]:
    """Store the relationships a document asserted, in the enrichment transaction.

    Extraction names entities by name, not by id, so relations are matched back
    against what the resolver produced for this same record. A relation naming
    something the resolver did not produce is dropped rather than guessed at --
    inventing an endpoint would attach a real claim to the wrong node, and the
    graph has no way to show that later.
    """
    by_name = {}
    for item in resolved:
        by_name[item["name"].strip().casefold()] = item["entity_id"]

    written = []
    for relation in relations or []:
        predicate = (relation.get("predicate") or "").strip()
        subject = (relation.get("subject") or "").strip().casefold()
        obj = (relation.get("object") or "").strip().casefold()
        if predicate not in PREDICATES:
            continue
        subject_id, object_id = by_name.get(subject), by_name.get(obj)
        if not subject_id or not object_id or subject_id == object_id:
            continue
        await conn.execute(
            """
            INSERT INTO entity_edges (edge_id, org_id, project_id, subject_id,
                predicate, object_id, source_data_id, confidence, generator_version)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (subject_id, predicate, object_id, source_data_id) DO NOTHING
            """,
            new_id("edg"), org_id, project_id, subject_id, predicate,
            object_id, data_id, float(relation.get("confidence") or 0.5),
            generator_version,
        )
        written.append({"subject_id": subject_id, "predicate": predicate,
                        "object_id": object_id})
    return written


def build_graph(pool: asyncpg.Pool, settings=None) -> GraphStore:
    """One implementation today. The seam exists so that stays a choice."""
    return PostgresGraph(pool)
