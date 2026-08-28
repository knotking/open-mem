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

import asyncpg

from .acl import visibility_params, visibility_sql
from .audit import record_access, record_access_many
from .auth import DATA_READ, Principal
from .contracts import Citation, Corpus, Excluded, RetrieveRequest, RetrieveResponse
from .db import vector_literal
from .ids import new_id
from .inference import EmbeddingEngine

RRF_K = 60  # the usual constant; large enough that rank 1 does not dominate


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
               d.content_text, d.storage_ref, d.pending_ref, d.is_downloaded,
               d.size_bytes, d.checksum, d.event_time, d.ingested_at, d.tags,
               d.identifiers, d.producer_id, d.connection_id
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


async def retrieve(
    pool: asyncpg.Pool,
    embedder: EmbeddingEngine,
    principal: Principal,
    request: RetrieveRequest,
    embed_generator: str | None = None,
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

    filters = f"""
        d.project_id = {project_p}
        AND {predicate}
        AND ({tags_p}::text[] = '{{}}' OR d.tags && {tags_p}::text[])
        AND ({since_p}::timestamptz IS NULL OR d.event_time >= {since_p})
        AND ({until_p}::timestamptz IS NULL OR d.event_time <= {until_p})
    """

    arms = []
    if "vector" in request.match:
        vector = (await embedder.embed([request.query]))[0]
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
    if not arms:
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
