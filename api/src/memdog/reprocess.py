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
    """
    principal.require(DATA_WRITE)
    if stage not in ("embed", "enrich"):
        raise ValueError("stage must be embed or enrich")

    org_id, user_id, principals = visibility_params(principal)
    predicate = visibility_sql("d", 2, 3, 4)
    clauses, params = [], [selector.get("project_id"), org_id, user_id, principals]

    if selector.get("data_ids"):
        params.append(selector["data_ids"])
        clauses.append(f"d.data_id = ANY(${len(params)}::text[])")
    if selector.get("data_type"):
        params.append(selector["data_type"])
        clauses.append(f"d.data_type = ${len(params)}")
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
        SELECT d.data_id FROM data_items d
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
    return {"run_id": run_id, "items": len(data_ids), "stage": stage,
            "mode": "dry_run" if dry_run else "execute"}


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

    async def handle(self, message: Message) -> None:
        run_id, stage = message.body["run_id"], message.body.get("stage", "enrich")
        await self._pool.execute(
            "UPDATE runs SET status = 'running' WHERE run_id = $1 AND status = 'queued'", run_id
        )
        rows = await self._pool.fetch(
            "SELECT data_id FROM run_items WHERE run_id = $1 AND status = 'pending'", run_id
        )
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
