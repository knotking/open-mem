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
from .ids import new_id
from .telemetry import span

PREDICATES = (
    "works_for", "member_of", "reports_to", "collaborates_with",
    "located_in", "part_of", "owns", "produces", "uses",
    "attended", "about", "related_to",
)

MAX_DEPTH = 3

# Which predicates hold at most one open value at a time. A new fact on one of
# these closes the previous one; everything else accumulates.
#
# The default is multi-valued, and single-valued is opt-in, because getting this
# backwards is the expensive error. Marking `works_for` single-valued would
# silently close every second job as though the person had left it -- recoverable,
# since closing is not deletion, but wrong in a way that reads as correct. So the
# list stays short and conservative, and `works_for` is deliberately not on it:
# people hold two jobs, sit on boards, and consult.
SINGLE_VALUED = frozenset({"located_in", "reports_to"})

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
        valid_at: datetime | None = None, as_of: datetime | None = None,
    ) -> Neighbourhood: ...


class PostgresGraph:
    """Traversal as a recursive CTE, with the ACL inside it."""

    name = "postgres"

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def neighbourhood(
        self, principal: Principal, *, entity_id: str, depth: int = 1,
        predicates: list[str] | None = None, limit: int = 120,
        valid_at: datetime | None = None, as_of: datetime | None = None,
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
                valid_at or now, as_of or now,
            )

    async def _neighbourhood(
        self, principal: Principal, entity_id: str, depth: int,
        predicates: list[str] | None, limit: int,
        valid_at: datetime, as_of: datetime,
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
            predicates, depth, limit, valid_at, as_of,
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
                   f.valid_from, f.valid_to, f.basis, f.confidence,
                   count(DISTINCT ev.source_data_id) AS evidence,
                   coalesce(array_agg(DISTINCT ev.source_data_id)
                            FILTER (WHERE ev.source_data_id IS NOT NULL),
                            '{{}}') AS sources
              FROM entity_facts f
              LEFT JOIN entity_edges ev ON ev.fact_id = f.fact_id
             WHERE f.subject_id = ANY($4::text[]) AND f.object_id = ANY($4::text[])
               AND ($5::text[] IS NULL OR f.predicate = ANY($5))
               AND {_temporal(6, 7)}
               AND {_fact_visibility(1, 2, 3)}
             GROUP BY f.fact_id
             ORDER BY count(DISTINCT ev.source_data_id) DESC, f.valid_from DESC
            """,
            org_id, user_id, principals, ids, predicates, valid_at, as_of,
        ) if ids else []

        return Neighbourhood(
            root=Node(root["entity_id"], root["display_name"], root["type"], 0),
            nodes=nodes,
            edges=[
                Edge(e["subject_id"], e["predicate"], e["object_id"],
                     e["evidence"], list(e["sources"]), float(e["confidence"] or 0),
                     fact_id=e["fact_id"], valid_from=e["valid_from"],
                     valid_to=e["valid_to"], basis=e["basis"])
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
        if predicate not in PREDICATES:
            continue
        subject_id, object_id = by_name.get(subject), by_name.get(obj)
        if not subject_id or not object_id or subject_id == object_id:
            continue
        confidence = float(relation.get("confidence") or 0.5)
        fact_id = await _upsert_fact(
            conn, org_id=org_id, project_id=project_id, subject_id=subject_id,
            predicate=predicate, object_id=object_id, valid_from=valid_from,
            basis="derived", confidence=confidence,
            generator_version=generator_version,
        )
        await conn.execute(
            """
            INSERT INTO entity_edges (edge_id, org_id, project_id, subject_id,
                predicate, object_id, source_data_id, confidence, generator_version,
                fact_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (subject_id, predicate, object_id, source_data_id)
            DO UPDATE SET fact_id = EXCLUDED.fact_id
            """,
            new_id("edg"), org_id, project_id, subject_id, predicate,
            object_id, data_id, confidence, generator_version, fact_id,
        )
        written.append({"subject_id": subject_id, "predicate": predicate,
                        "object_id": object_id, "fact_id": fact_id})
    return written


async def _upsert_fact(
    conn, *, org_id: str, project_id: str, subject_id: str, predicate: str,
    object_id: str, valid_from, basis: str, confidence: float,
    generator_version: str | None = None, owner_id: str | None = None,
    access_level: str | None = None, shared_with: list[str] | None = None,
    key_id: str | None = None,
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
            asserted_by_key_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb, $14)
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
        json.dumps(shared_with or []), key_id,
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
