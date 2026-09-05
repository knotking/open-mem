"""Read: `GET /data/{id}` and `POST /retrieve`.

The ACL predicate and the similarity search are **one query with one plan**.
Filtering after retrieval would return the wrong twenty rows and then hide some
of them -- ask for ten, get three -- which is a different and worse failure than
filtering before.

`e.model_id = $n` is the fallback-chain protection made physical: even a mixed
index returns comparable results, because a query only ever considers one vector
space.
"""

from __future__ import annotations

import os

import asyncpg

from .acl import visibility_params, visibility_sql
from .audit import record_access, record_access_many
from .auth import DATA_READ, Principal
from .contracts import (
    Citation,
    Corpus,
    Excluded,
    GraphSeed,
    RetrieveRequest,
    RetrieveResponse,
)
from .db import vector_literal
from .graph import build_graph
from .ids import new_id
from .telemetry import span
from .inference import EmbeddingEngine

RRF_K = 60  # the usual constant; large enough that rank 1 does not dominate

# How many candidates the HNSW scan visits before the ACL and the filters
# cut it down. The default is 40, which is the same number the arm asks for,
# so any filtering at all comes straight out of the result.
HNSW_EF_SEARCH = int(os.environ.get("HNSW_EF_SEARCH", "200"))


class NotFound(Exception):
    pass


async def get_item(
    pool: asyncpg.Pool, principal: Principal, data_id: str
) -> dict:
    principal.require(DATA_READ)
    org_id, user_id, principals = visibility_params(principal)
    predicate = visibility_sql("d", 2, 3, 4)
    row = await pool.fetchrow(
        f"""
        SELECT d.data_id, d.project_id, d.external_id, d.access_level, d.state,
               d.mime_type, d.source_type, d.data_type, d.classified_by_layer,
               d.content_text, d.extracted_text, d.storage_ref, d.pending_ref,
               d.is_downloaded, d.parse_status, d.parse_detail,
               d.size_bytes, d.checksum, d.event_time, d.ingested_at, d.tags,
               d.identifiers, d.producer_id, d.connection_id, d.run_id,
               d.metadata, d.template,
               -- What this record contributed to the graph.
               --
               -- The progress panel claimed "entities recorded" on every
               -- successful enrichment and reported no number, so a record that
               -- produced eighteen entities and one that produced none rendered
               -- identically -- and the second is the case somebody needs to
               -- know about, because it is the difference between "the graph is
               -- built" and "the graph is empty and nothing said so".
               --
               -- Counted here rather than in a second request: it is one row
               -- of the item's own state, and a panel that has to make two
               -- calls to say whether a step finished will eventually show one
               -- of them stale.
               (SELECT count(*) FROM entity_mentions m
                 WHERE m.data_id = d.data_id) AS entity_count,
               (SELECT count(*) FROM entity_edges e
                 WHERE e.source_data_id = d.data_id) AS edge_count
        FROM data_items d
        WHERE d.data_id = $1 AND {predicate}
        """,
        data_id,
        org_id,
        user_id,
        principals,
    )
    if row is None:
        # Indistinguishable from "does not exist": a 403 on an item you cannot
        # see still tells you it exists.
        raise NotFound(data_id)
    await record_access(
        pool,
        principal,
        action="data.read",
        project_id=row["project_id"],
        data_id=data_id,
    )
    return dict(row)


# An entity name shorter than this matches too much to be evidence of anything:
# "AI", "Q3" and a two-letter surname would each seed an expansion over half the
# corpus.
MIN_SEED_NAME = 3


async def graph_seeds(
    pool: asyncpg.Pool, principal: Principal, *, project_id: str, query: str
) -> list[GraphSeed]:
    """Entities the query names, resolved the way a mention is resolved.

    Matching reuses `entities.normalize` on both sides rather than doing its own
    casefolding, so the query and the stored name cannot drift apart -- a
    resolver that normalised differently from the writer would silently stop
    matching its own data.

    **An entity is visible only through a record that mentions it and that the
    caller can read.** Resolving against the entity table alone would confirm
    that a name exists in this project to someone who cannot see any record
    containing it, which is the same disclosure the traversal is careful about.
    """
    from .entities import normalize

    normalized = normalize(query)
    if not normalized:
        return []
    org_id, user_id, principals = visibility_params(principal)
    rows = await pool.fetch(
        f"""
        SELECT e.entity_id, e.display_name, e.type
        FROM entities e
        WHERE e.project_id = $1
          AND length(e.normalized_name) >= {MIN_SEED_NAME}
          AND position(e.normalized_name IN $2) > 0
          AND EXISTS (
              SELECT 1 FROM entity_mentions m
              JOIN data_items d ON d.data_id = m.data_id
              WHERE m.entity_id = e.entity_id AND {visibility_sql("d", 3, 4, 5)}
          )
        ORDER BY length(e.normalized_name) DESC
        LIMIT 8
        """,
        project_id, normalized, org_id, user_id, principals,
    )
    return [
        GraphSeed(entity_id=r["entity_id"], display_name=r["display_name"],
                  type=r["type"], matched_on="name")
        for r in rows
    ]


async def seeds_for_ids(
    pool: asyncpg.Pool, principal: Principal, *, project_id: str,
    entity_ids: list[str],
) -> list[GraphSeed]:
    """Seeds the caller named, rather than seeds scraped from the question.

    Visibility is asked the same way `graph_seeds` asks it: an entity is
    reachable only through a record that mentions it and that the caller can
    read. Without that, passing an id would confirm the entity exists to
    somebody who cannot see a single record containing it -- and an id is far
    easier to enumerate than a name.

    An id that does not resolve is dropped rather than raised. The caller is a
    scope picker sending what it last loaded, and one stale entity should
    narrow the answer, not fail the question.
    """
    if not entity_ids:
        return []
    org_id, user_id, principals = visibility_params(principal)
    rows = await pool.fetch(
        f"""
        SELECT e.entity_id, e.display_name, e.type
        FROM entities e
        WHERE e.project_id = $1 AND e.entity_id = ANY($2::text[])
          AND EXISTS (
              SELECT 1 FROM entity_mentions m
              JOIN data_items d ON d.data_id = m.data_id
              WHERE m.entity_id = e.entity_id AND {visibility_sql("d", 3, 4, 5)}
          )
        ORDER BY e.display_name
        """,
        project_id, entity_ids, org_id, user_id, principals,
    )
    return [
        GraphSeed(entity_id=r["entity_id"], display_name=r["display_name"],
                  type=r["type"], matched_on="chosen")
        for r in rows
    ]


async def _expand(
    graph, principal: Principal, seeds: list[GraphSeed], *, limit: int,
    valid_at=None, as_of=None, template: str | None = None,
) -> dict[str, int]:
    """Seeds, plus what one hop reaches, with the fewest hops to each.

    Goes through `GraphStore` rather than reading `entity_edges` here. The
    traversal and the access rule have to be the same query -- an edge whose
    evidence the caller cannot read must not be walked, because returning its
    far endpoint discloses that the evidence exists -- and that rule is already
    implemented once, inside the store. Writing a second traversal in a
    retrieval CTE would be a second place for it to be right, and it costs the
    seam its meaning: swap the store for another and browsing would follow
    while search quietly did not.

    The price is a query per seed rather than one fused query. Seeds are capped
    at eight and each call is an indexed traversal, so the ceiling is small and
    known -- which is a better trade than a faster query that has to be audited
    separately.
    """
    from .graph import GraphError

    reachable: dict[str, int] = {}
    for seed in seeds:
        try:
            found = await graph.neighbourhood(
                principal, entity_id=seed.entity_id, depth=1,
                predicates=None, limit=limit,
                valid_at=valid_at, as_of=as_of, template=template,
            )
        except GraphError:
            # The entity resolved a moment ago and is gone, or is not visible
            # after all. Not an error for the search -- the other seeds still
            # stand, and one that does not is simply absent from the results.
            continue
        for node in [found.root, *found.nodes]:
            hops = min(node.depth, reachable.get(node.entity_id, node.depth))
            reachable[node.entity_id] = hops
    return reachable


async def retrieve(
    pool: asyncpg.Pool,
    embedder: EmbeddingEngine,
    principal: Principal,
    request: RetrieveRequest,
    embed_generator: str | None = None,
    graph=None,
) -> RetrieveResponse:
    """Traced as one span so the arms, the fusion and the audit write are all
    attributable to the query that caused them."""
    with span("retrieve", project_id=request.filter.project_id,
              match=",".join(request.match), model_id=embedder.model_id):
        return await _retrieve(
            pool, embedder, principal, request, embed_generator, graph
        )


async def _retrieve(
    pool: asyncpg.Pool,
    embedder: EmbeddingEngine,
    principal: Principal,
    request: RetrieveRequest,
    embed_generator: str | None = None,
    graph=None,
) -> RetrieveResponse:
    principal.require(DATA_READ)
    org_id, user_id, principals = visibility_params(principal)
    predicate = visibility_sql("d", 3, 4, 5)

    # Parameters are bound in the order the query happens to need them: an
    # unreferenced placeholder has no inferable type, so a vector-only search
    # must not carry the lexical arm's parameters.
    params: list = []

    def bind(value: object) -> str:
        params.append(value)
        return f"${len(params)}"

    project_p = bind(request.filter.project_id)
    org_p, user_p, principals_p = bind(org_id), bind(user_id), bind(principals)
    predicate = visibility_sql("d", int(org_p[1:]), int(user_p[1:]), int(principals_p[1:]))
    # Over-fetch each arm, then fuse. Each arm applies the ACL itself, so the
    # fusion never sees a row the caller could not have retrieved directly.
    arm_limit_p = bind(max(request.limit * 4, 40))
    tags_p = bind(request.filter.tags)
    since_p, until_p = bind(request.filter.since), bind(request.filter.until)
    memories_p = bind(request.filter.memory_ids)
    keywords_p = bind(request.filter.keywords)
    template_p = bind(request.filter.template)
    entities_p = bind(request.filter.entity_ids)

    # `EXISTS` rather than a join: a record can be in several of the selected
    # memories and a join would return it once per membership, which the fusion
    # step would then read as several separate hits and rank accordingly.
    filters = f"""
        d.project_id = {project_p}
        AND {predicate}
        AND ({tags_p}::text[] = '{{}}' OR d.tags && {tags_p}::text[])
        AND ({memories_p}::text[] = '{{}}' OR EXISTS (
              SELECT 1 FROM memory_members mm
              WHERE mm.data_id = d.data_id AND mm.memory_id = ANY({memories_p}::text[])))
        AND ({keywords_p}::text[] = '{{}}' OR EXISTS (
              SELECT 1 FROM artifacts a2
              JOIN artifact_sources s2 ON s2.artifact_id = a2.artifact_id
              WHERE s2.data_id = d.data_id AND a2.keywords && {keywords_p}::text[]))
        AND ({template_p}::text IS NULL OR d.template = {template_p})
        AND ({entities_p}::text[] = '{{}}' OR EXISTS (
              SELECT 1 FROM entity_mentions em
              WHERE em.data_id = d.data_id
                AND em.entity_id = ANY({entities_p}::text[])))
        AND ({since_p}::timestamptz IS NULL OR d.event_time >= {since_p})
        AND ({until_p}::timestamptz IS NULL OR d.event_time <= {until_p})
    """

    arms = []
    if "vector" in request.match:
        vector = (await embedder.embed([request.query], task="query"))[0]
        vec_p = bind(vector_literal(vector))
        model_p = bind(embedder.model_id)
        arms.append(
            f"""
            vec AS (
                SELECT c.chunk_id, c.data_id, c.text, c.span_start, c.span_end, d.state,
                       row_number() OVER (ORDER BY e.embedding <=> {vec_p}::vector) AS rank,
                       1 - (e.embedding <=> {vec_p}::vector) AS score
                FROM embeddings e
                JOIN chunks c ON c.chunk_id = e.chunk_id
                JOIN data_items d ON d.data_id = c.data_id
                WHERE {filters} AND e.model_id = {model_p}
                ORDER BY e.embedding <=> {vec_p}::vector
                LIMIT {arm_limit_p}
            )"""
        )
    if "lexical" in request.match:
        query_p = bind(request.query)
        arms.append(
            f"""
            lex AS (
                SELECT c.chunk_id, c.data_id, c.text, c.span_start, c.span_end, d.state,
                       row_number() OVER (ORDER BY ts_rank_cd(c.ts, q.query) DESC) AS rank,
                       ts_rank_cd(c.ts, q.query) AS score
                FROM chunks c
                JOIN data_items d ON d.data_id = c.data_id
                CROSS JOIN websearch_to_tsquery('english', {query_p}) AS q(query)
                WHERE {filters} AND c.ts @@ q.query
                ORDER BY score DESC
                LIMIT {arm_limit_p}
            )"""
        )
    seeds: list[GraphSeed] = []
    reachable: dict[str, int] = {}
    if "graph" in request.match:
        # A named anchor replaces the parsed one rather than adding to it. If
        # the caller said where to start, starting somewhere else as well is
        # not extra recall -- it is the scope they set being quietly widened.
        seeds = await seeds_for_ids(
            pool, principal, project_id=request.filter.project_id,
            entity_ids=request.filter.entity_ids,
        ) if request.filter.entity_ids else await graph_seeds(
            pool, principal, project_id=request.filter.project_id, query=request.query
        )
        if seeds:
            reachable = await _expand(
                graph or build_graph(pool), principal, seeds,
                limit=request.limit * 8,
                valid_at=request.filter.valid_at, as_of=request.filter.as_of,
                # The same lens on both halves. A search narrowed to scripture
                # records whose graph arm walked every edge in the project
                # would rank records by connections the filter excluded, and
                # nothing in the result would show it.
                template=request.filter.template,
            )
    if reachable:
        ids_p = bind(list(reachable))
        hops_p = bind([reachable[entity_id] for entity_id in reachable])
        # One hop, and the ACL is *inside* the traversal rather than applied to
        # its result. Filtering afterwards leaks structure: if a path runs
        # through a record the caller cannot read, returning its endpoints tells
        # them that record exists, which is precisely what its ACL forbids. So
        # an edge is only traversable when its own evidence is readable.
        #
        # The arm returns each record's opening chunk. It is claiming the
        # *record* is connected -- it has no view about which passage answers
        # the question, and picking one by relevance would be the other arms'
        # job done worse.
        arms.append(
            f"""
            gph AS (
                WITH reachable AS (
                    -- Handed in, not walked here. The traversal ran through
                    -- `GraphStore`, which is the seam the whole graph layer
                    -- sits behind -- a second implementation of it inside a
                    -- retrieval query would be a second place the access rule
                    -- has to be right, and the second place is always the one
                    -- that is wrong.
                    SELECT * FROM unnest({ids_p}::text[], {hops_p}::int[])
                        AS r(entity_id, hops)
                ),
                touched AS (
                    -- MIN(hops) because a record can mention both a seed and a
                    -- neighbour, and it is the closest connection that should
                    -- rank it.
                    SELECT m.data_id, MIN(r.hops) AS hops, count(*) AS mentions
                    FROM entity_mentions m
                    JOIN reachable r ON r.entity_id = m.entity_id
                    GROUP BY m.data_id
                ),
                opening AS (
                    -- One chunk per record, chosen by position and not by
                    -- relevance. `DISTINCT ON` has to order by the key it
                    -- distinguishes, so the ranking cannot happen here: a
                    -- window function is computed before the distinct, and the
                    -- ranks would come out full of gaps that RRF reads as
                    -- weaker matches.
                    SELECT DISTINCT ON (c.data_id)
                           c.chunk_id, c.data_id, c.text, c.span_start, c.span_end,
                           d.state, t.hops, t.mentions
                    FROM touched t
                    JOIN chunks c ON c.data_id = t.data_id
                    JOIN data_items d ON d.data_id = t.data_id
                    WHERE {filters}
                    ORDER BY c.data_id, c.ordinal
                )
                SELECT chunk_id, data_id, text, span_start, span_end, state,
                       row_number() OVER (
                           ORDER BY hops, mentions DESC, data_id
                       ) AS rank,
                       -- A record naming the entity outranks one merely
                       -- connected to it. Only consulted when `rank` is
                       -- `none`; RRF reads the rank above.
                       CASE WHEN hops = 0 THEN 1.0 ELSE 0.5 END AS score
                FROM opening
                ORDER BY hops, mentions DESC
                LIMIT {arm_limit_p}
            )"""
        )

    if not request.match:
        raise ValueError("at least one match mode is required")

    names = [a.strip().split()[0] for a in arms]
    union = " UNION ALL ".join(
        f"SELECT chunk_id, data_id, text, span_start, span_end, state, rank, score, "
        f"'{name}' AS arm FROM {name}"
        for name in names
    )
    if request.rank == "rrf":
        # Reciprocal rank fusion: comparable across arms whose scores are not.
        fused = f"SUM(1.0 / ({RRF_K} + rank))"
    else:
        fused = "MAX(score)"

    limit_p = bind(request.limit * 2 + 5)
    sql = f"""
        WITH {",".join(arms)},
        fused AS ({union})
        SELECT chunk_id,
               MIN(data_id) AS data_id,
               MIN(text) AS text,
               MIN(span_start) AS span_start,
               MIN(span_end) AS span_end,
               MIN(state) AS state,
               {fused} AS score,
               array_agg(DISTINCT arm) AS matched_by
        FROM fused
        GROUP BY chunk_id
        ORDER BY score DESC
        LIMIT {limit_p}
    """
    # A graph-only search whose query named no entity builds no arms at all.
    # That is an empty result, not a bad request: the caller asked for a mode
    # that exists and it had nothing to start from, which `graph_seeds` says.
    # Raising here returned a 500 for a perfectly ordinary question.
    #
    # The search still runs through everything below -- the query row, the
    # corpus counts, the audit -- because a search that found nothing is still
    # a search that happened, and the trace is the part that explains why.
    if not arms:
        rows = []
    elif "vector" in request.match:
        # HNSW visits `ef_search` candidates and *then* applies the WHERE
        # clause, so leaving it at the default 40 while the arm also asks for 40
        # means a single excluded row costs a result. Raised for the vector arm
        # only, and `SET LOCAL` so it dies with the transaction rather than
        # riding a pooled connection into somebody else's query.
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(f"SET LOCAL hnsw.ef_search = {HNSW_EF_SEARCH}")
            rows = await conn.fetch(sql, *params)
    else:
        rows = await pool.fetch(sql, *params)

    query_id = new_id("qry")
    all_hits = [
        Citation(
            data_id=r["data_id"],
            chunk_id=r["chunk_id"],
            text=r["text"],
            span_start=r["span_start"],
            span_end=r["span_end"],
            score=float(r["score"]),
            matched_by=list(r["matched_by"]),
            state=r["state"],
        )
        for r in rows
    ]
    # Everything above the limit was retrieved and then cut. That is a
    # threshold exclusion, and it is the difference between "retrieval did not
    # find it" and "retrieval found it and ranked it too low".
    citations = all_hits[: request.limit]
    kept = {c.data_id for c in citations}
    excluded = [
        Excluded(data_id=h.data_id, reason="threshold", score=h.score, state=h.state)
        for h in all_hits[request.limit :]
        if h.data_id not in kept
    ]

    # Records the caller can see that could not have matched, because they are
    # not searchable yet. This is the cause people most often mistake for bad
    # retrieval.
    predicate_c = visibility_sql("d", 2, 3, 4)
    not_ready = await pool.fetch(
        f"""
        SELECT d.data_id, d.state FROM data_items d
        WHERE d.project_id = $1 AND {predicate_c} AND d.state = 'stored'
        ORDER BY d.data_id LIMIT 25
        """,
        request.filter.project_id, org_id, user_id, principals,
    )
    excluded.extend(
        Excluded(data_id=r["data_id"], reason="not_yet_enriched", state=r["state"])
        for r in not_ready
    )

    counts = await pool.fetchrow(
        f"""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE d.state = 'stored') AS stored,
               count(*) FILTER (WHERE d.state = 'searchable') AS searchable,
               count(*) FILTER (WHERE d.state = 'enriched') AS enriched
        FROM data_items d
        WHERE d.project_id = $1 AND {predicate_c}
        """,
        request.filter.project_id, org_id, user_id, principals,
    )
    corpus = Corpus(**dict(counts))

    async with pool.acquire() as conn, conn.transaction():
        # The question is stored even under the metadata-only default: a query
        # log without questions cannot answer "what was this key being used for?"
        await conn.execute(
            """
            INSERT INTO queries (query_id, user_id, org_id, project_id, question, model_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            query_id,
            principal.user_id,
            principal.org_id,
            request.filter.project_id,
            request.query,
            embedder.model_id,
        )
        seen: set[str] = set()
        for rank, citation in enumerate(citations, start=1):
            if citation.data_id in seen:
                continue
            seen.add(citation.data_id)
            await conn.execute(
                """
                INSERT INTO query_sources (query_id, data_id, rank, score, used)
                VALUES ($1, $2, $3, $4, true)
                """,
                query_id,
                citation.data_id,
                rank,
                citation.score,
            )
        # FR-SCH-13: the excluded are recorded too, with the reason. This is
        # what makes the trace reconstructable after the fact rather than only
        # visible in the moment.
        for offset, item in enumerate(excluded, start=len(seen) + 1):
            if item.data_id in seen:
                continue
            seen.add(item.data_id)
            await conn.execute(
                """
                INSERT INTO query_sources (query_id, data_id, rank, score, used, excluded_reason)
                VALUES ($1, $2, $3, $4, false, $5)
                ON CONFLICT DO NOTHING
                """,
                query_id, item.data_id, offset, item.score, item.reason,
            )
        await record_access_many(
            conn,
            principal,
            action="retrieve",
            project_id=request.filter.project_id,
            data_ids=sorted(seen),
            query_id=query_id,
        )

    return RetrieveResponse(
        query_id=query_id,
        results=citations,
        model_id=embedder.model_id,
        generator_version=embed_generator,
        corpus=corpus,
        excluded=excluded,
        graph_seeds=seeds,
    )


async def get_artifacts(
    pool: asyncpg.Pool, principal: Principal, data_id: str
) -> list[dict]:
    """The derived layer for one item, with the ACL applied to the *artifact*.

    Sharing an item does not publish what was derived from it, so the artifact's
    own access level is what governs here -- not the source's.
    """
    principal.require(DATA_READ)
    org_id, user_id, principals = visibility_params(principal)
    predicate = visibility_sql("a", 2, 3, 4)
    rows = await pool.fetch(
        f"""
        SELECT a.artifact_id, a.kind, a.title, a.description, a.summary,
               a.keywords, a.language, a.fields, a.model_id, a.generator_version,
               a.served_by_model, a.fallback_depth, a.access_level, a.created_at,
               s.span_start, s.span_end
        FROM artifacts a
        JOIN artifact_sources s ON s.artifact_id = a.artifact_id
        WHERE s.data_id = $1 AND {predicate}
        ORDER BY a.created_at DESC
        """,
        data_id,
        org_id,
        user_id,
        principals,
    )
    await record_access(
        pool, principal, action="artifacts.read", data_id=data_id
    )
    return [dict(r) for r in rows]


async def stale_artifacts(
    pool: asyncpg.Pool, principal: Principal, current: dict[str, str], limit: int = 100
) -> list[dict]:
    """What needs rebuilding, and why.

    Staleness is a join, not a flag somebody remembers to set: an artifact whose
    `generator_version` is not the one currently assigned for its purpose is
    stale by construction. That is the payoff of the fingerprint -- it catches
    prompt, model, schema, parser and chunker changes nobody thought to version.
    """
    principal.require(DATA_READ)
    org_id, user_id, principals = visibility_params(principal)
    predicate = visibility_sql("a", 2, 3, 4)
    rows = await pool.fetch(
        f"""
        SELECT a.artifact_id, a.kind, a.generator_version, g.purpose, g.model_id,
               s.data_id
        FROM artifacts a
        JOIN generators g ON g.generator_version = a.generator_version
        JOIN artifact_sources s ON s.artifact_id = a.artifact_id
        WHERE a.org_id = $1 AND {predicate}
          AND a.generator_version <> COALESCE($5::jsonb ->> g.purpose, a.generator_version)
        ORDER BY a.created_at
        LIMIT $6
        """,
        org_id,
        org_id,
        user_id,
        principals,
        current,
        limit,
    )
    return [dict(r) for r in rows]


async def staircase(
    pool: asyncpg.Pool, principal: Principal, project_id: str
) -> dict:
    """Counts per readiness state (FR-SBX-6).

    Someone uploads five hundred records, immediately asks a question, gets a
    thin answer and concludes the product does not work. They are wrong for a
    reason this endpoint can show them.
    """
    principal.require(DATA_READ)
    org_id, user_id, principals = visibility_params(principal)
    predicate = visibility_sql("d", 2, 3, 4)
    row = await pool.fetchrow(
        f"""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE d.state = 'stored') AS stored,
               count(*) FILTER (WHERE d.state = 'searchable') AS searchable,
               count(*) FILTER (WHERE d.state = 'enriched') AS enriched,
               count(*) FILTER (WHERE d.pending_ref IS NOT NULL) AS awaiting_fetch,
               max(d.updated_at) AS last_change
        FROM data_items d
        WHERE d.project_id = $1 AND {predicate}
        """,
        project_id, org_id, user_id, principals,
    )
    return dict(row)


async def get_versions(
    pool: asyncpg.Pool, principal: Principal, data_id: str
) -> list[dict]:
    """Every revision of an item, newest first.

    The ACL is checked against the *item*, once, rather than per row: a version
    is not independently shareable, and treating it as such would be a second
    access path to content whose permissions live on the parent.
    """
    principal.require(DATA_READ)
    org_id, user_id, principals = visibility_params(principal)
    predicate = visibility_sql("d", 2, 3, 4)
    visible = await pool.fetchval(
        f"SELECT 1 FROM data_items d WHERE d.data_id = $1 AND {predicate}",
        data_id, org_id, user_id, principals,
    )
    if not visible:
        raise NotFound(data_id)

    rows = await pool.fetch(
        """
        SELECT version_id, revision, source, content_chars, checksum, mime_type,
               model_id, model_version, response_id, generator_version, tokens,
               detail, created_at, left(content_text, 400) AS preview
        FROM data_versions WHERE data_id = $1 ORDER BY revision DESC
        """,
        data_id,
    )
    await record_access(pool, principal, action="versions.read", data_id=data_id)
    return [dict(r) for r in rows]


async def one_version(
    pool: asyncpg.Pool, principal: Principal, data_id: str, version_id: str
) -> dict:
    """One revision, with its text in full.

    The listing above returns a 400-character preview on purpose -- forty
    revisions of a long document is a response nobody wants and most callers are
    choosing which one to read, not reading them all. So the full text is a
    second request, made only for the one that was chosen.

    Its own access record for the same reason: this discloses content, and the
    listing does not.
    """
    visible = visibility_sql("d", 2, 3, 4)
    org_id, user_id, principals = visibility_params(principal)
    row = await pool.fetchrow(
        f"""
        SELECT v.version_id, v.revision, v.source, v.content_text, v.content_chars,
               v.checksum, v.mime_type, v.model_id, v.model_version,
               v.generator_version, v.tokens, v.detail, v.created_at
          FROM data_versions v
          JOIN data_items d ON d.data_id = v.data_id
         WHERE v.data_id = $1 AND v.version_id = $5 AND {visible}
        """,
        data_id, org_id, user_id, principals, version_id,
    )
    if row is None:
        # Indistinguishable from "does not exist", which is the point: a
        # revision of a record you cannot read must not be confirmable. The
        # endpoint turns this into a 404; this module does not own HTTP.
        return {}
    await record_access(pool, principal, action="version.read", data_id=data_id)
    return dict(row)


async def list_memories(pool: asyncpg.Pool, principal: Principal, project_id: str) -> list[dict]:
    """Memories in a project, with live member counts.

    Counts are computed rather than maintained: a denormalised counter is a
    number that drifts the first time a member is removed by a path that forgot
    to decrement it.

    **A memory with no members you can see is not listed at all.** Showing it
    with a count of zero would disclose that the container exists, and a
    memory_key is frequently meaningful on its own -- a thread id, a user id, a
    case reference. That is the same rule the retrieval trace follows for ACL
    exclusions. A memory you own is always yours to see, empty or not.
    """
    principal.require(DATA_READ)
    org_id, user_id, principals = visibility_params(principal)
    predicate = visibility_sql("d", 4, 5, 6)
    rows = await pool.fetch(
        f"""
        SELECT m.memory_id, m.type, m.memory_key, m.title, m.owner_id, m.created_at,
               t.ttl_seconds, t.on_expiry,
               -- A rollup nobody can tell is out of date is one people keep
               -- quoting. Returned with the listing rather than behind a second
               -- request, because the moment it matters is while reading it.
               m.stale_since, m.stale_reason,
               (SELECT count(*) FROM memory_members mm
                  JOIN data_items d ON d.data_id = mm.data_id
                 WHERE mm.memory_id = m.memory_id AND {predicate}) AS members
        FROM memories m
        LEFT JOIN memory_types t ON t.project_id = m.project_id AND t.name = m.type
        WHERE m.project_id = $1 AND m.org_id = $2 AND m.deleted_at IS NULL
          AND (
            m.owner_id = $5
            OR EXISTS (SELECT 1 FROM memory_members mm
                         JOIN data_items d ON d.data_id = mm.data_id
                        WHERE mm.memory_id = m.memory_id AND {predicate})
          )
        ORDER BY m.created_at DESC
        LIMIT $3
        """,
        project_id, org_id, 200, org_id, user_id, principals,
    )
    return [dict(r) for r in rows]


async def project_keywords(
    pool, principal, project_id: str, *, limit: int = 200
) -> list[dict]:
    """What this project is about, as the model has described it.

    Counted over artifacts the caller can actually see, not over the project:
    a keyword whose every record is hidden must not appear, or the count itself
    discloses that something exists. This is the same rule entity listing
    follows and for the same reason.

    Counts are of *records*, not of mentions. A keyword repeated across five
    chunks of one document is one record's worth of evidence, and ranking by
    mentions would put a long document above a broad theme.
    """
    org_id, user_id, principals = visibility_params(principal)
    predicate = visibility_sql("d", 2, 3, 4)
    rows = await pool.fetch(
        f"""
        SELECT keyword, count(DISTINCT s.data_id) AS records
        FROM artifacts a
        JOIN artifact_sources s ON s.artifact_id = a.artifact_id
        JOIN data_items d ON d.data_id = s.data_id
        CROSS JOIN LATERAL unnest(a.keywords) AS keyword
        WHERE a.project_id = $1
          AND d.deleted_at IS NULL
          AND {predicate}
        GROUP BY keyword
        ORDER BY records DESC, keyword ASC
        LIMIT $5
        """,
        project_id, org_id, user_id, principals, min(limit, 500),
    )
    return [{"keyword": r["keyword"], "records": r["records"]} for r in rows]


async def project_tags(pool, principal, project_id: str, *, limit: int = 200) -> list[dict]:
    """The tags actually in use, counted over what the caller can see.

    The sibling of `project_keywords`, and deliberately a separate function
    rather than a parameter: a tag is an assertion by a person and a keyword is
    a model's guess, and the moment one endpoint returns both nobody can tell
    which said what.

    Scoped by visibility for the same reason as keywords -- a tag applied only
    to records the caller cannot see must not appear, because the tag itself
    would disclose that they exist.
    """
    org_id, user_id, principals = visibility_params(principal)
    predicate = visibility_sql("d", 2, 3, 4)
    rows = await pool.fetch(
        f"""
        SELECT tag, count(*) AS records
        FROM data_items d
        CROSS JOIN LATERAL unnest(d.tags) AS tag
        WHERE d.project_id = $1 AND d.deleted_at IS NULL AND {predicate}
        GROUP BY tag
        ORDER BY records DESC, tag ASC
        LIMIT $5
        """,
        project_id, org_id, user_id, principals, min(limit, 500),
    )
    return [{"tag": r["tag"], "records": r["records"]} for r in rows]


async def memory_members(pool: asyncpg.Pool, principal: Principal, memory_id: str) -> list[dict]:
    principal.require(DATA_READ)
    org_id, user_id, principals = visibility_params(principal)
    predicate = visibility_sql("d", 2, 3, 4)
    rows = await pool.fetch(
        f"""
        SELECT d.data_id, d.state, d.mime_type, d.data_type, d.event_time,
               mm.added_by, mm.added_at,
               left(coalesce(d.content_text, d.extracted_text), 200) AS preview
        FROM memory_members mm
        JOIN data_items d ON d.data_id = mm.data_id
        WHERE mm.memory_id = $1 AND {predicate}
        ORDER BY mm.added_at DESC LIMIT 200
        """,
        memory_id, org_id, user_id, principals,
    )
    return [dict(r) for r in rows]


async def item_memories(pool: asyncpg.Pool, principal: Principal, data_id: str) -> dict:
    """The item's side of the mapping, with its computed effective expiry."""
    from .memories import effective_expiry, memories_for_item

    principal.require(DATA_READ)
    org_id, user_id, principals = visibility_params(principal)
    predicate = visibility_sql("d", 2, 3, 4)
    visible = await pool.fetchval(
        f"SELECT 1 FROM data_items d WHERE d.data_id = $1 AND {predicate}",
        data_id, org_id, user_id, principals,
    )
    if not visible:
        raise NotFound(data_id)
    memberships = await memories_for_item(pool, data_id)
    return {
        "memberships": memberships,
        # Derived on read, never stored. Any stored answer is wrong the moment
        # someone adds or removes a member.
        "effective_expiry": effective_expiry(memberships),
    }


async def audit_trail(
    pool: asyncpg.Pool, principal: Principal, *, project_id: str | None = None, limit: int = 100
) -> dict:
    """Reads and writes, from the two stores that hold them.

    They are separate for good reasons -- different volume, retention and access
    pattern, and the read log must survive the deletion of what it describes --
    so this presents them together rather than merging them.
    """
    principal.require(DATA_READ)
    writes = await pool.fetch(
        """
        SELECT event_id AS id, action, target_type, target_id, actor_user_id,
               actor_key_id, detail, at
        FROM audit_events
        WHERE org_id = $1 AND ($2::text IS NULL OR project_id = $2)
        ORDER BY at DESC LIMIT $3
        """,
        principal.org_id, project_id, limit,
    )
    reads = await pool.fetch(
        """
        SELECT access_id AS id, action, data_id, query_id, user_id, key_id, at
        FROM access_log
        WHERE org_id = $1 AND ($2::text IS NULL OR project_id = $2)
        ORDER BY at DESC LIMIT $3
        """,
        principal.org_id, project_id, limit,
    )
    return {"writes": [dict(r) for r in writes], "reads": [dict(r) for r in reads]}


async def list_items(
    pool: asyncpg.Pool,
    principal: Principal,
    project_id: str,
    *,
    limit: int = 50,
    before: str | None = None,
    state: str | None = None,
    include_archived: bool = False,
) -> dict:
    """Browse a project's items, newest first.

    Paginated on `data_id` rather than an offset. Ids are ULIDs, so ordering by
    id *is* ordering by time -- and a keyset cursor does not skip or repeat rows
    when something is written while someone is paging, which OFFSET does.
    """
    principal.require(DATA_READ)
    org_id, user_id, principals = visibility_params(principal)
    predicate = visibility_sql("d", 2, 3, 4)
    rows = await pool.fetch(
        f"""
        SELECT d.data_id, d.external_id, d.state, d.mime_type, d.data_type,
               d.size_bytes, d.event_time, d.ingested_at, d.access_level,
               d.parse_status, d.is_downloaded, d.storage_ref IS NOT NULL AS has_bytes,
               left(coalesce(d.content_text, d.extracted_text), 180) AS preview,
               d.archived_at,
               (SELECT count(*) FROM data_versions v WHERE v.data_id = d.data_id) AS revisions
        FROM data_items d
        WHERE d.project_id = $1 AND {predicate}
          AND ($5::text IS NULL OR d.data_id < $5)
          AND ($6::text IS NULL OR d.state = $6)
          -- Compaction moves records out of the working set rather than
          -- deleting them, so they are excluded here and returned when asked
          -- for. That is the whole user-visible effect of compaction.
          AND ($8::boolean OR d.archived_at IS NULL)
        ORDER BY d.data_id DESC
        LIMIT $7
        """,
        project_id, org_id, user_id, principals, before, state, min(limit, 200),
        include_archived,
    )
    items = [dict(r) for r in rows]
    return {
        "items": items,
        # The cursor is the last id, so the caller never constructs one.
        "next_before": items[-1]["data_id"] if len(items) == min(limit, 200) else None,
    }


async def project_overview(
    pool: asyncpg.Pool, principal: Principal, project_id: str,
    embedder: EmbeddingEngine | None = None,
) -> dict:
    """One call that answers "is this working, and what is in it?".

    Every number is ACL-scoped, so two people looking at the same project can
    legitimately see different totals -- which is correct, and the reason this
    is not a cached counter somewhere.
    """
    principal.require(DATA_READ)
    org_id, user_id, principals = visibility_params(principal)
    predicate = visibility_sql("d", 2, 3, 4)

    counts = await pool.fetchrow(
        f"""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE d.state = 'stored') AS stored,
               count(*) FILTER (WHERE d.state = 'searchable') AS searchable,
               count(*) FILTER (WHERE d.state = 'enriched') AS enriched,
               count(*) FILTER (WHERE d.storage_ref IS NOT NULL) AS with_bytes,
               count(*) FILTER (WHERE d.pending_ref IS NOT NULL) AS awaiting_fetch,
               coalesce(sum(d.size_bytes), 0) AS bytes_stored,
               min(d.ingested_at) AS first_write,
               max(d.ingested_at) AS last_write
        FROM data_items d WHERE d.project_id = $1 AND {predicate}
        """,
        project_id, org_id, user_id, principals,
    )

    by_type = await pool.fetch(
        f"""
        SELECT coalesce(d.data_type, 'unknown') AS data_type, count(*) AS n
        FROM data_items d WHERE d.project_id = $1 AND {predicate}
        GROUP BY 1 ORDER BY n DESC LIMIT 8
        """,
        project_id, org_id, user_id, principals,
    )

    # What is in the corpus that we are *not* reading, and why. The most
    # useful number on the page when something looks wrong.
    problems = await pool.fetch(
        f"""
        SELECT d.parse_status, count(*) AS n
        FROM data_items d WHERE d.project_id = $1 AND {predicate}
          AND d.parse_status IS NOT NULL AND d.parse_status <> 'parsed'
        GROUP BY 1 ORDER BY n DESC
        """,
        project_id, org_id, user_id, principals,
    )

    models = await pool.fetch(
        f"""
        SELECT v.model_id, count(*) AS calls, coalesce(sum(v.tokens), 0) AS tokens
        FROM data_versions v JOIN data_items d ON d.data_id = v.data_id
        WHERE d.project_id = $1 AND {predicate} AND v.model_id IS NOT NULL
        GROUP BY 1 ORDER BY tokens DESC LIMIT 6
        """,
        project_id, org_id, user_id, principals,
    )

    derived = await pool.fetchrow(
        f"""
        SELECT (SELECT count(*) FROM chunks c JOIN data_items d ON d.data_id = c.data_id
                 WHERE d.project_id = $1 AND {predicate}) AS chunks,
               (SELECT count(*) FROM embeddings e JOIN data_items d ON d.data_id = e.data_id
                 WHERE d.project_id = $1 AND {predicate}) AS embeddings,
               (SELECT count(DISTINCT e.model_id) FROM embeddings e
                  JOIN data_items d ON d.data_id = e.data_id
                 WHERE d.project_id = $1 AND {predicate}) AS vector_spaces,
               (SELECT count(*) FROM data_versions v JOIN data_items d ON d.data_id = v.data_id
                 WHERE d.project_id = $1 AND {predicate}) AS revisions
        """,
        project_id, org_id, user_id, principals,
    )

    # Which vector space the stored embeddings are actually in, and whether the
    # configured embedder queries it. `vector_spaces: 1` says the corpus is
    # consistent with itself; it does not say it is consistent with retrieval.
    # A corpus embedded under one model and searched under another reports
    # every record enriched and answers every vector search with nothing, and
    # until this row existed there was no number anywhere that disagreed.
    spaces = await pool.fetch(
        f"""
        SELECT e.model_id, count(*) AS embeddings,
               count(DISTINCT e.data_id) AS records
        FROM embeddings e JOIN data_items d ON d.data_id = e.data_id
        WHERE d.project_id = $1 AND {predicate}
        GROUP BY 1 ORDER BY 2 DESC LIMIT 6
        """,
        project_id, org_id, user_id, principals,
    )
    # The vector arm joins embeddings to chunks by `chunk_id`, so an embedding
    # whose chunk was rewritten under it is counted above and reachable by
    # nothing.
    joinable = await pool.fetchrow(
        f"""
        SELECT count(*) AS rows,
               count(*) FILTER (WHERE e.embedding IS NULL) AS empty,
               min(vector_dims(e.embedding)) AS dims
        FROM embeddings e
        JOIN chunks c ON c.chunk_id = e.chunk_id
        JOIN data_items d ON d.data_id = c.data_id
        WHERE d.project_id = $1 AND {predicate}
        """,
        project_id, org_id, user_id, principals,
    )
    queried = embedder.model_id if embedder else None
    # The same shape the vector arm runs -- joins, ACL, model filter, index
    # scan -- but with a vector already in the table as the query. If this
    # returns rows and the arm does not, the storage side is sound and the
    # question is what the *query* embedded to.
    probe = await pool.fetchval(
        f"""
        WITH probe AS (
            SELECT e.embedding AS v FROM embeddings e
            JOIN data_items d ON d.data_id = e.data_id
            WHERE d.project_id = $1 AND {predicate} LIMIT 1
        )
        SELECT count(*) FROM (
            SELECT c.chunk_id FROM embeddings e
            JOIN chunks c ON c.chunk_id = e.chunk_id
            JOIN data_items d ON d.data_id = c.data_id
            CROSS JOIN probe
            WHERE d.project_id = $1 AND {predicate}
              AND ($5::text IS NULL OR e.model_id = $5)
            ORDER BY e.embedding <=> probe.v LIMIT 40
        ) t
        """,
        project_id, org_id, user_id, principals, queried,
    )
    # The same probe with the query vector as a *bound constant*, which is what
    # lets the planner reach for the HNSW index -- the CROSS JOIN above cannot
    # use it. Two different numbers here mean the vectors are fine and the
    # index is not, which is otherwise indistinguishable from bad retrieval.
    sample = await pool.fetchval(
        f"""
        SELECT e.embedding::text FROM embeddings e
        JOIN data_items d ON d.data_id = e.data_id
        WHERE d.project_id = $1 AND {predicate} LIMIT 1
        """,
        project_id, org_id, user_id, principals,
    )
    probe_indexed = None
    if sample:
        # Under the same `ef_search` the vector arm runs with, so this number is
        # what retrieval will actually see. Reporting the default instead would
        # show a healthy index as broken.
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(f"SET LOCAL hnsw.ef_search = {HNSW_EF_SEARCH}")
            probe_indexed = await conn.fetchval(
                f"""
                SELECT count(*) FROM (
                    SELECT c.chunk_id FROM embeddings e
                    JOIN chunks c ON c.chunk_id = e.chunk_id
                    JOIN data_items d ON d.data_id = c.data_id
                    WHERE d.project_id = $1 AND {predicate}
                    ORDER BY e.embedding <=> $5::vector LIMIT 40
                ) t
                """,
                project_id, org_id, user_id, principals, sample,
            )
    vector_index = {
        "queried_as": queried,
        "spaces": [dict(r) | {"queried": r["model_id"] == queried} for r in spaces],
        # The one number that answers "will vector search find anything?".
        "reachable": dict(joinable),
        "probe_neighbours": probe,
        "probe_indexed": probe_indexed,
        "searchable_here": sum(
            r["records"] for r in spaces if r["model_id"] == queried
        ),
    }

    containers = await pool.fetchrow(
        """
        SELECT (SELECT count(*) FROM memories m
                 WHERE m.project_id = $1 AND m.deleted_at IS NULL) AS memories,
               (SELECT count(*) FROM cases c
                 WHERE c.project_id = $1 AND c.deleted_at IS NULL) AS cases,
               (SELECT count(*) FROM share_links s
                 WHERE s.project_id = $1 AND s.revoked_at IS NULL
                   AND s.expires_at > now()) AS live_shares
        """,
        project_id,
    )

    activity = await pool.fetchrow(
        """
        SELECT (SELECT count(*) FROM audit_events a
                 WHERE a.project_id = $1 AND a.at > now() - interval '24 hours') AS writes_24h,
               (SELECT count(*) FROM access_log l
                 WHERE l.project_id = $1 AND l.at > now() - interval '24 hours') AS reads_24h,
               (SELECT count(*) FROM queries q
                 WHERE q.project_id = $1 AND q.asked_at > now() - interval '24 hours') AS queries_24h
        """,
        project_id,
    )

    return {
        "counts": dict(counts),
        "by_data_type": [dict(r) for r in by_type],
        "not_read": [dict(r) for r in problems],
        "models": [dict(r) for r in models],
        "derived": dict(derived),
        "vector_index": vector_index,
        "containers": dict(containers),
        "activity": dict(activity),
    }
