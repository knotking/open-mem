"""W7 -- rebuilding what a stale generator produced.

`GET /artifacts/stale` names the work; this does it. Without it, changing a
prompt or a model leaves a corpus in two states forever, and the staleness
query becomes a report nobody can act on.

Reprocess is deliberately the *same* run entity as deletion and bulk write: a
checkpointed, resumable job with per-item results. Four different job shapes
would mean four places to get resumability wrong.

**It cannot un-redact.** Reprocessing rebuilds derived artifacts from the stored
item; anything removed before storage is not recoverable here, and narrowing a
redaction rule later is irreversible for exactly that reason.
"""

from __future__ import annotations

import logging

import asyncpg

from .acl import visibility_params, visibility_sql
from .audit import record_audit
from .auth import DATA_WRITE, Principal
from .ids import new_id
from .queue import Message, Queue

log = logging.getLogger(__name__)

REPROCESS_TOPIC = "reprocess"


async def request_reprocess(
    pool: asyncpg.Pool,
    queue: Queue,
    principal: Principal,
    *,
    selector: dict,
    stage: str = "enrich",
    dry_run: bool = False,
) -> dict:
    """Queue a rebuild. `stage` decides how far down to start.

    `embed` re-chunks and re-embeds; `enrich` rebuilds the envelope only. They
    are separate because re-embedding a corpus is expensive and usually
    unnecessary when only a prompt changed.

    `interpret` is the third, and it is not a rebuild: it asks for work that was
    **never requested**. Enrichment is opt-in, so a crawl or a feed run with it
    off leaves a corpus that is stored, durable and unfindable, and the only way
    out of that state was one item at a time. It differs from the other two in
    that it emits `enrichment.requested` per item rather than republishing onto
    a pipeline topic -- which matters beyond bookkeeping: **the reconciler
    repairs requested work and never invents it**, so a message dropped between
    here and the worker is only recoverable if the request is in the log.

    Selectors compose, and all of them narrow: `data_ids`, `data_type`,
    `run_id`, `tags`, `stale_generator`, `stale_only`. The last two match on an
    existing artifact, so they cannot reach an item that was never enriched --
    which is exactly the item a crawl run with `enrich` off produces. `run_id`
    and `tags` are how that corpus is reached.
    """
    principal.require(DATA_WRITE)
    if stage not in ("embed", "enrich", "interpret"):
        raise ValueError("stage must be embed, enrich or interpret")

    org_id, user_id, principals = visibility_params(principal)
    predicate = visibility_sql("d", 2, 3, 4)
    clauses, params = [], [selector.get("project_id"), org_id, user_id, principals]

    if selector.get("data_ids"):
        params.append(selector["data_ids"])
        clauses.append(f"d.data_id = ANY(${len(params)}::text[])")
    if selector.get("data_type"):
        params.append(selector["data_type"])
        clauses.append(f"d.data_type = ${len(params)}")
    if selector.get("run_id"):
        # The selector a crawl actually needs. `enrich` is off by default, so
        # the intended sequence is crawl, read the count, then enrich what it
        # found -- and until this existed the last step meant enumerating ten
        # thousand data_ids by hand, because a never-enriched item has no
        # artifact for `stale_only` or `stale_generator` to match on.
        params.append(selector["run_id"])
        clauses.append(f"d.run_id = ${len(params)}")
    if selector.get("tags"):
        # Overlap, not containment: "any of these tags" is the question people
        # ask. `tags @> ARRAY[...]` would quietly return nothing whenever more
        # than one tag was passed.
        params.append(list(selector["tags"]))
        clauses.append(f"d.tags && ${len(params)}::text[]")
    if selector.get("stale_generator"):
        # The common case: everything an outdated generator produced.
        params.append(selector["stale_generator"])
        clauses.append(
            f"""EXISTS (SELECT 1 FROM artifacts a
                        JOIN artifact_sources s ON s.artifact_id = a.artifact_id
                        WHERE s.data_id = d.data_id
                          AND a.generator_version <> ${len(params)})"""
        )
    if selector.get("stale_only"):
        clauses.append(
            """EXISTS (SELECT 1 FROM artifacts a
                       JOIN artifact_sources s ON s.artifact_id = a.artifact_id
                       WHERE s.data_id = d.data_id AND a.stale_reason IS NOT NULL)"""
        )
    if not clauses:
        raise ValueError("a selector must narrow something")

    rows = await pool.fetch(
        f"""
        SELECT d.data_id, d.external_id, d.state, d.data_type,
               left(d.indexable_text, 120) AS preview
        FROM data_items d
        WHERE ($1::text IS NULL OR d.project_id = $1) AND {predicate}
          AND d.indexable_text IS NOT NULL
          AND {" AND ".join(clauses)}
        ORDER BY d.data_id LIMIT 10000
        """,
        *params,
    )
    data_ids = [r["data_id"] for r in rows]
    run_id = new_id("run")

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO runs (run_id, org_id, project_id, kind, mode, status, selector,
                              total, actor_user_id, actor_key_id, detail)
            VALUES ($1, $2, $3, 'reprocess', $4, $5, $6, $7, $8, $9, $10)
            """,
            run_id, principal.org_id, selector.get("project_id"),
            "dry_run" if dry_run else "execute",
            "completed" if dry_run else "queued",
            selector, len(data_ids), principal.user_id, principal.key_id,
            {"stage": stage},
        )
        await conn.executemany(
            "INSERT INTO run_items (run_id, data_id, status) VALUES ($1, $2, 'pending')",
            [(run_id, d) for d in data_ids],
        )
        await record_audit(
            conn, principal, action="reprocess.requested",
            project_id=selector.get("project_id"),
            target_type="run", target_id=run_id,
            detail={"selector": selector, "stage": stage, "items": len(data_ids)},
        )

    if not dry_run and data_ids:
        await queue.publish(REPROCESS_TOPIC, {"run_id": run_id, "stage": stage})

    # A count is not a preview. "4,212 items" reads the same whether the
    # selector caught the corpus you meant or every record in the project, and
    # the way to tell them apart is to look at a few. The breakdown by state is
    # the other half: for `interpret` it says how much of the selection is
    # already enriched and would be re-done for nothing.
    by_state: dict[str, int] = {}
    for r in rows:
        by_state[r["state"]] = by_state.get(r["state"], 0) + 1
    return {
        "run_id": run_id,
        "items": len(data_ids),
        "stage": stage,
        "mode": "dry_run" if dry_run else "execute",
        "by_state": by_state,
        "samples": [
            {"data_id": r["data_id"], "external_id": r["external_id"],
             "state": r["state"], "data_type": r["data_type"], "preview": r["preview"]}
            for r in rows[:8]
        ],
        "capped": len(data_ids) == 10000,
    }


class ReprocessWorker:
    """Fans a run out onto the ordinary pipeline topics.

    It republishes rather than re-implementing: a reprocess that used a
    different code path from a first-time write would drift from it, and the
    drift would only show up as inconsistent artifacts months later.
    """

    def __init__(self, pool: asyncpg.Pool, queue: Queue) -> None:
        self._pool = pool
        self._queue = queue

    def register(self, queue: Queue, topic: str = REPROCESS_TOPIC) -> None:
        queue.subscribe(topic, self.handle)

    async def _interpret(self, run_id: str, data_ids: list[str]) -> None:
        """Ask for enrichment on work nobody ever asked for.

        Through the event log rather than straight onto a topic, because that is
        what makes the request repairable: the reconciler sweeps for items whose
        state lags what their content warrants **and that have an
        `enrichment.requested` event**, deliberately, so that it never spends
        money nobody asked it to. A bulk interpret that skipped the log would be
        the one kind of enrichment a dropped message loses for good.

        One event per item, and each is idempotent at the far end: the enrich
        worker deletes and rewrites the artifact for its generator rather than
        appending, so a redelivery costs compute and changes nothing.
        """
        from .events import dispatch_pending, emit

        for data_id in data_ids:
            row = await self._pool.fetchrow(
                "SELECT org_id, project_id FROM data_items WHERE data_id = $1", data_id
            )
            if row is None:
                continue
            async with self._pool.acquire() as conn, conn.transaction():
                await emit(
                    conn,
                    event_type="enrichment.requested",
                    org_id=row["org_id"],
                    project_id=row["project_id"],
                    data_id=data_id,
                    payload={"embed": True, "summarize": True, "run_id": run_id,
                             "requested_after_the_fact": True},
                )
                await conn.execute(
                    "UPDATE run_items SET status = 'done', at = now() "
                    "WHERE run_id = $1 AND data_id = $2",
                    run_id, data_id,
                )
        await dispatch_pending(self._pool, self._queue)

    async def handle(self, message: Message) -> None:
        run_id, stage = message.body["run_id"], message.body.get("stage", "enrich")
        await self._pool.execute(
            "UPDATE runs SET status = 'running' WHERE run_id = $1 AND status = 'queued'", run_id
        )
        rows = await self._pool.fetch(
            "SELECT data_id FROM run_items WHERE run_id = $1 AND status = 'pending'", run_id
        )
        if stage == "interpret":
            await self._interpret(run_id, [r["data_id"] for r in rows])
            await self._pool.execute(
                """
                UPDATE runs SET status = 'completed', done = total, finished_at = now()
                WHERE run_id = $1
                """,
                run_id,
            )
            return
        topic = "embed" if stage == "embed" else "enrich"
        for row in rows:
            if stage == "embed":
                # Drop back to `stored` so the staircase is honest while the
                # rebuild is in flight, rather than claiming searchable with
                # vectors that are being replaced.
                await self._pool.execute(
                    "UPDATE data_items SET state = 'stored' WHERE data_id = $1", row["data_id"]
                )
            await self._queue.publish(topic, {"data_id": row["data_id"]})
            await self._pool.execute(
                "UPDATE run_items SET status = 'done', at = now() WHERE run_id = $1 AND data_id = $2",
                run_id, row["data_id"],
            )
        await self._pool.execute(
            """
            UPDATE runs SET status = 'completed', done = total, finished_at = now()
            WHERE run_id = $1
            """,
            run_id,
        )
