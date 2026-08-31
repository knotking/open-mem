"""Crawler storage, run lifecycle and the scheduler.

The separation from `crawlers.py` is between *what a strategy does* and *what
surrounds a run*: checkpointing, dedupe, the dry-run gate, watermark advance,
the emit path. Nearly every way a crawler loses or duplicates data lives in
this file rather than in the discovery code.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import asyncpg

from .auth import CONFIG_WRITE, DATA_READ, DATA_WRITE, Principal
from .blobs import BlobStore
from .config import Settings
from .contracts import Inline, Pending, WriteItem, WriteOptions, WriteRequest
from .crawlers import (
    Auth,
    CrawlerConfig,
    CrawlerError,
    Discovered,
    RateLimited,
    discover,
    fingerprint,
    next_watermark,
    validate_config,
)
from .ids import new_id
from .queue import Queue
from .telemetry import record, span
from .write import write_items

# A run whose worker has not checked in for this long is not running; it is
# dead. The reaper marks it interrupted so the next tick resumes it from its
# checkpoint rather than waiting on a process that will never return.
STALE_HEARTBEAT_SECONDS = 300

# How often the emit phase reports progress. Small enough that a run cannot
# outlive its heartbeat, large enough that it is not a write per item.
PROGRESS_EVERY = 25


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _owned(pool: asyncpg.Pool, principal: Principal, crawler_id: str) -> asyncpg.Record:
    row = await pool.fetchrow(
        "SELECT * FROM crawlers WHERE crawler_id = $1 AND org_id = $2",
        crawler_id, principal.org_id,
    )
    if row is None:
        # 404 rather than 403: another org's crawler must be indistinguishable
        # from one that does not exist.
        raise CrawlerError("crawler not found", status=404)
    return row


# ------------------------------------------------------------------- CRUD

async def create_crawler(
    pool: asyncpg.Pool, principal: Principal, *, project_id: str,
    config: CrawlerConfig, schedule: dict | None = None, overlap: str = "skip",
) -> dict:
    """Created disabled, always. You cannot schedule a crawler that has never
    been dry-run, so there is no state in which it is created ready to go."""
    principal.require(CONFIG_WRITE)
    validate_config(config)

    owner_org = await pool.fetchval(
        "SELECT org_id FROM projects WHERE project_id = $1", project_id
    )
    if owner_org != principal.org_id:
        raise CrawlerError("project not found", status=404)

    crawler_id = new_id("crw")
    async with pool.acquire() as conn, conn.transaction():
        # Every crawler is a producer. That is what gives crawled data the same
        # admission control, ACL derivation and freshness detection as
        # everything else, rather than a parallel set of rules.
        producer_id = new_id("crw")
        await conn.execute(
            """
            INSERT INTO producers (producer_id, type, user_id, org_id, project_id,
                                   status, inbound_auth, defaults)
            VALUES ($1, 'crawler', $2, $3, $4, 'enabled', 'none', $5)
            """,
            producer_id, principal.user_id, principal.org_id, project_id,
            json.dumps({"tags": config.tags}),
        )
        await conn.execute(
            """
            INSERT INTO crawlers (crawler_id, producer_id, org_id, project_id, user_id,
                                  name, strategy, config, schedule, overlap, enabled)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, false)
            """,
            crawler_id, producer_id, principal.org_id, project_id, principal.user_id,
            config.name, config.strategy, config.model_dump_json(),
            json.dumps(schedule or {"type": "manual"}), overlap,
        )
    return {
        "crawler_id": crawler_id,
        "producer_id": producer_id,
        "status": "draft",
        "enabled": False,
        # Reported, never accepted. A crawler cannot widen access to the data
        # it produces.
        "acl": "inherited:personal",
        "dry_run_required": True,
    }


async def list_crawlers(pool: asyncpg.Pool, principal: Principal, project_id: str) -> list[dict]:
    principal.require(DATA_READ)
    rows = await pool.fetch(
        """
        SELECT c.*, r.status AS last_status, r.started_at AS last_run_at,
               r.discovered, r.emitted, r.skipped, r.failed, r.run_id AS last_run_id,
               -- Time since the last run that actually *succeeded*, not since
               -- the last run. A crawler failing every tick has a recent run
               -- and stale data, and only this column tells them apart.
               EXTRACT(EPOCH FROM (now() - ok.finished_at))::bigint
                   AS seconds_since_last_success
          FROM crawlers c
          LEFT JOIN LATERAL (
              SELECT * FROM crawl_runs WHERE crawler_id = c.crawler_id
               ORDER BY started_at DESC LIMIT 1
          ) r ON true
          LEFT JOIN LATERAL (
              SELECT finished_at FROM crawl_runs
               WHERE crawler_id = c.crawler_id AND status = 'completed'
                 AND mode = 'live'
               ORDER BY finished_at DESC LIMIT 1
          ) ok ON true
         WHERE c.project_id = $1 AND c.org_id = $2
         ORDER BY c.created_at DESC
        """,
        project_id, principal.org_id,
    )
    return [_present(row) for row in rows]


def _present(row: asyncpg.Record) -> dict:
    data = dict(row)
    data["config"] = json.loads(data["config"]) if isinstance(data.get("config"), str) \
        else data.get("config")
    if isinstance(data.get("schedule"), str):
        data["schedule"] = json.loads(data["schedule"])
    # The single most useful field on the list: whether this crawler is allowed
    # to run at all, and why not.
    data["dry_run_current"] = data.get("dry_run_version") == data.get("config_version")
    return data


async def update_crawler(
    pool: asyncpg.Pool, principal: Principal, crawler_id: str, *,
    config: CrawlerConfig | None = None, schedule: dict | None = None,
    overlap: str | None = None,
) -> dict:
    """Editing scope or strategy invalidates the dry run.

    Without this, "dry-run before enabling" is a formality you satisfy once and
    then edit around -- which is exactly how an approved narrow crawl becomes
    an unapproved broad one.
    """
    principal.require(CONFIG_WRITE)
    row = await _owned(pool, principal, crawler_id)
    if config is not None:
        validate_config(config)
        changed = fingerprint(config) != fingerprint(
            CrawlerConfig.model_validate_json(row["config"])
        )
        await pool.execute(
            """
            UPDATE crawlers
               SET config = $2, name = $3, strategy = $4, updated_at = now(),
                   config_version = config_version + CASE WHEN $5 THEN 1 ELSE 0 END,
                   dry_run_version = CASE WHEN $5 THEN NULL ELSE dry_run_version END,
                   enabled = CASE WHEN $5 THEN false ELSE enabled END
             WHERE crawler_id = $1
            """,
            crawler_id, config.model_dump_json(), config.name, config.strategy, changed,
        )
    if schedule is not None:
        await pool.execute(
            "UPDATE crawlers SET schedule = $2, next_due_at = NULL WHERE crawler_id = $1",
            crawler_id, json.dumps(schedule),
        )
    if overlap is not None:
        await pool.execute(
            "UPDATE crawlers SET overlap = $2 WHERE crawler_id = $1", crawler_id, overlap
        )
    return _present(await _owned(pool, principal, crawler_id))


async def set_enabled(
    pool: asyncpg.Pool, principal: Principal, crawler_id: str, enabled: bool
) -> dict:
    principal.require(CONFIG_WRITE)
    row = await _owned(pool, principal, crawler_id)
    if enabled and row["dry_run_version"] != row["config_version"]:
        # The gate. This is the point at which someone learns the job will
        # create forty-seven thousand items -- before it runs, not after.
        raise CrawlerError(
            "this configuration has not been dry-run; run one and review the "
            "estimate before enabling",
            status=409,
        )
    next_due = _now() if enabled else None
    await pool.execute(
        "UPDATE crawlers SET enabled = $2, next_due_at = $3 WHERE crawler_id = $1",
        crawler_id, enabled, next_due,
    )
    return _present(await _owned(pool, principal, crawler_id))


async def delete_crawler(pool: asyncpg.Pool, principal: Principal, crawler_id: str) -> dict:
    principal.require(CONFIG_WRITE)
    row = await _owned(pool, principal, crawler_id)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("DELETE FROM crawlers WHERE crawler_id = $1", crawler_id)
        # The producer is disabled, not deleted. The items it wrote belong to
        # the project rather than to the mechanism that discovered them, and
        # they still point at this producer for their provenance -- deleting it
        # would leave every crawled item unable to say where it came from.
        await conn.execute(
            "UPDATE producers SET status = 'disabled' WHERE producer_id = $1",
            row["producer_id"],
        )
    return {"crawler_id": crawler_id, "deleted": True, "data_retained": True,
            "producer_disabled": row["producer_id"]}


# -------------------------------------------------------------- run control

async def start_run(
    pool: asyncpg.Pool, principal: Principal, crawler_id: str, *, mode: str = "live"
) -> dict:
    principal.require(CONFIG_WRITE)
    row = await _owned(pool, principal, crawler_id)
    if mode == "live" and not row["enabled"]:
        raise CrawlerError("crawler is not enabled", status=409)

    live = await pool.fetchval(
        """
        SELECT run_id FROM crawl_runs
         WHERE crawler_id = $1 AND status IN ('pending', 'running')
         LIMIT 1
        """,
        crawler_id,
    )
    if live and row["overlap"] == "skip":
        raise CrawlerError(f"run {live} is already in flight", status=409)

    return await _create_run(pool, row, mode=mode)


async def _create_run(pool: asyncpg.Pool, row: asyncpg.Record, *, mode: str) -> dict:
    # `crun_`, not `run_`: the deletion and reprocess tables already mint
    # `run_`, and an id prefix that two tables share stops being able to say
    # what the id identifies.
    run_id = new_id("crun")
    await pool.execute(
        """
        INSERT INTO crawl_runs (run_id, crawler_id, org_id, mode, status,
                                pinned_config, pinned_version, watermark_before)
        VALUES ($1, $2, $3, $4, 'pending', $5, $6, $7)
        """,
        run_id, row["crawler_id"], row["org_id"], mode,
        row["config"] if isinstance(row["config"], str) else json.dumps(row["config"]),
        row["config_version"], row["watermark"],
    )
    return {"run_id": run_id, "mode": mode, "status": "pending"}


async def control_run(
    pool: asyncpg.Pool, principal: Principal, run_id: str, action: str
) -> dict:
    """`cancel` keeps the checkpoint.

    Discarding progress on cancel would make cancelling a six-hour job an
    irreversible decision, which means people would not cancel jobs they should.
    """
    principal.require(CONFIG_WRITE)
    row = await pool.fetchrow(
        "SELECT * FROM crawl_runs WHERE run_id = $1 AND org_id = $2",
        run_id, principal.org_id,
    )
    if row is None:
        raise CrawlerError("run not found", status=404)
    target = {"pause": "paused", "cancel": "cancelled", "resume": "pending"}.get(action)
    if target is None:
        raise CrawlerError("action must be pause, resume or cancel")
    if row["status"] in ("completed", "failed", "cancelled") and action != "resume":
        raise CrawlerError(f"run is already {row['status']}", status=409)
    await pool.execute(
        "UPDATE crawl_runs SET status = $2 WHERE run_id = $1", run_id, target
    )
    return {"run_id": run_id, "status": target, "checkpoint_retained": True}


async def get_run(pool: asyncpg.Pool, principal: Principal, run_id: str) -> dict:
    principal.require(DATA_READ)
    row = await pool.fetchrow(
        "SELECT * FROM crawl_runs WHERE run_id = $1 AND org_id = $2",
        run_id, principal.org_id,
    )
    if row is None:
        raise CrawlerError("run not found", status=404)
    data = dict(row)
    for key in ("pinned_config", "checkpoint"):
        if isinstance(data.get(key), str):
            data[key] = json.loads(data[key])
    data["errors"] = [
        dict(e) for e in await pool.fetch(
            "SELECT external_id, url, reason, at FROM crawl_errors "
            "WHERE run_id = $1 ORDER BY at LIMIT 100",
            run_id,
        )
    ]
    data["sample"] = [
        dict(f) for f in await pool.fetch(
            "SELECT external_id, url, payload FROM crawl_frontier "
            "WHERE run_id = $1 LIMIT 20",
            run_id,
        )
    ]
    for item in data["sample"]:
        if isinstance(item.get("payload"), str):
            item["payload"] = json.loads(item["payload"])
    return data


async def list_runs(
    pool: asyncpg.Pool, principal: Principal, crawler_id: str, limit: int = 20
) -> list[dict]:
    principal.require(DATA_READ)
    await _owned(pool, principal, crawler_id)
    return [dict(r) for r in await pool.fetch(
        """
        SELECT run_id, mode, status, discovered, emitted, skipped, failed,
               reason, started_at, finished_at
          FROM crawl_runs WHERE crawler_id = $1
         ORDER BY started_at DESC LIMIT $2
        """,
        crawler_id, limit,
    )]


# ------------------------------------------------------------- the executor

class CrawlWorker:
    """Claims a pending run and takes it to a terminal state.

    The ordering that matters: discover, checkpoint, dedupe, emit, and only
    then advance the watermark -- and only on a clean completion.
    """

    def __init__(self, pool: asyncpg.Pool, queue: Queue, blobs: BlobStore,
                 settings: Settings, envelope=None) -> None:
        self.pool = pool
        self.queue = queue
        self.blobs = blobs
        self.settings = settings
        # Needed only to decrypt a crawler's connection. Absent, an
        # authenticated crawler refuses rather than reaching its source
        # unauthenticated and reporting the 401 as the source's fault.
        self.envelope = envelope

    async def _auth(self, crawler) -> "Auth | None":
        """The credential this crawler was given, resolved at the moment of use.

        `None` for a public source, which is the ordinary case: a sitemap or an
        RSS feed needs nobody's permission, and treating that as unauthenticated
        rather than as a missing credential is the difference between a working
        crawler and a confusing error.
        """
        from .connections import ConnectionError_, authorize

        connection_id = crawler["connection_id"]
        if not connection_id:
            return None
        if self.envelope is None:
            raise CrawlerError(
                "this crawler authenticates through a connection and the "
                "deployment has no encryption configured", status=503,
            )
        try:
            headers, query = await authorize(
                self.pool, self.envelope, connection_id, crawler["org_id"]
            )
        except ConnectionError_ as exc:
            # Surfaced as the crawler's failure, which is what it is. Letting
            # the run continue unauthenticated would turn a configuration
            # problem into a 401 from the source and send whoever debugs it in
            # the wrong direction.
            raise CrawlerError(str(exc), status=exc.status) from exc
        return Auth(headers=headers, query=query)


    async def execute(self, run_id: str) -> dict:
        started = time.monotonic()
        with span("crawl.run", run_id=run_id) as current:
            result = await self._execute(run_id)
            for key in ("status", "discovered", "emitted", "skipped", "failed"):
                if result.get(key) is not None:
                    current.set_attribute(key, result[key])
            await self._measure(run_id, result, time.monotonic() - started)
            return result

    async def _measure(self, run_id: str, result: dict, seconds: float) -> None:
        """The signals the telemetry design calls the most valuable ones.

        `crawl.discovered` trending to zero against its own baseline is the
        crawler equivalent of a dead connection -- and it is invisible in an
        error rate, because a crawler that finds nothing fails at nothing.
        """
        row = await self.pool.fetchrow(
            """
            SELECT c.crawler_id, c.strategy, c.schedule, r.mode
              FROM crawl_runs r JOIN crawlers c ON c.crawler_id = r.crawler_id
             WHERE r.run_id = $1
            """,
            run_id,
        )
        if row is None:
            return
        labels = {"crawler_id": row["crawler_id"], "strategy": row["strategy"],
                  "mode": row["mode"]}
        record("crawl_runs", 1, status=result.get("status") or "unknown", **labels)
        record("crawl_discovered", result.get("discovered") or 0, **labels)
        record("crawl_emitted", result.get("emitted") or 0, **labels)
        record("crawl_dedupe_hits", result.get("skipped") or 0, **labels)
        record("crawl_duration", seconds, **labels)

        # Against the schedule, not in isolation: whether a run is too slow is
        # a question about its interval. Above 1.0 the next tick always lands
        # on a live run, so the crawler overlaps forever and never catches up.
        schedule = row["schedule"]
        if isinstance(schedule, str):
            schedule = json.loads(schedule)
        if (schedule or {}).get("type") == "interval":
            interval = float(schedule.get("every_seconds", 3600) or 3600)
            if interval > 0:
                record("crawl_duration_vs_interval", seconds / interval, **labels)

    async def _execute(self, run_id: str) -> dict:
        # Claimed with a conditional update, so two workers racing for the same
        # run produce one winner rather than two crawls.
        claimed = await self.pool.fetchrow(
            """
            UPDATE crawl_runs SET status = 'running', heartbeat_at = now()
             WHERE run_id = $1 AND status = 'pending'
            RETURNING *
            """,
            run_id,
        )
        if claimed is None:
            return {"run_id": run_id, "status": "not_claimable"}

        crawler = await self.pool.fetchrow(
            "SELECT * FROM crawlers WHERE crawler_id = $1", claimed["crawler_id"]
        )
        config = CrawlerConfig.model_validate_json(claimed["pinned_config"])
        checkpoint = json.loads(claimed["checkpoint"]) if isinstance(
            claimed["checkpoint"], str) else dict(claimed["checkpoint"] or {})

        status, reason = "completed", None
        found: list[Discovered] = []
        try:
            found, budget, stopped = await discover(
                config, watermark=await cursor_for(
                self.pool, claimed["crawler_id"], config.scope_param or ""),
            checkpoint=checkpoint,
                auth=await self._auth(crawler),
            )
            if stopped:
                # Hitting a limit is a partial run, not a complete one. Calling
                # it complete would advance the watermark past records the
                # crawl never reached.
                status, reason = "partial", stopped
        except RateLimited as exc:
            # Recorded against the credential, not the crawler: the quota
            # belongs to the token, so every other crawler sharing it should
            # wait too rather than each discovering the limit for itself.
            status, reason = "rate_limited", str(exc)
            await mark_limited(
                self.pool, crawler["connection_id"],
                seconds=exc.retry_after, reason=str(exc))
        except CrawlerError as exc:
            # 401 means the credential is the problem. Failing loudly matters:
            # a run that ended 'completed' would advance the watermark and the
            # skipped window would never be revisited.
            status, reason = "failed", str(exc)
            if exc.status == 401 and crawler["connection_id"]:
                # Drop the cached token as well. An exchanged credential is
                # held until shortly before it expires, so a source that
                # started refusing -- consent revoked, scope changed, secret
                # rotated at the provider -- would go on being refused with the
                # same dead token for up to an hour after the fix.
                from .grants import forget

                forget(crawler["connection_id"])
        except Exception as exc:  # noqa: BLE001 - a run must always terminate
            status, reason = "failed", f"{exc.__class__.__name__}: {exc}"

        await self.pool.execute(
            "UPDATE crawl_runs SET discovered = $2, checkpoint = $3, heartbeat_at = now() "
            "WHERE run_id = $1",
            run_id, len(found), json.dumps(checkpoint),
        )

        emitted = skipped = failed = 0
        if status == "rate_limited":
            # Not a failed crawl. The run stops, the cursor stays exactly where
            # it was, and the credential carries the cooling-off so every other
            # crawler sharing it waits too.
            await self.pool.execute(
                "UPDATE crawl_runs SET status = 'rate_limited', reason = $2, "
                "finished_at = now(), heartbeat_at = now() WHERE run_id = $1",
                run_id, reason,
            )
            return {"run_id": run_id, "status": "rate_limited", "reason": reason}
        if status != "failed":
            emitted, skipped, failed = await self._emit(
                claimed, crawler, config, found, dry=claimed["mode"] == "dry"
            )
            if failed and status == "completed":
                # Partial success: one bad item does not fail a run, but it
                # does mean the run did not do everything it claimed.
                status = "partial"
                reason = f"{failed} item(s) failed"

        advanced = claimed["watermark_before"]
        scope = config.scope_param or ""
        if status == "completed" and claimed["mode"] == "live":
            advanced = next_watermark(config, found, claimed["watermark_before"])
            await self.pool.execute(
                "UPDATE crawlers SET watermark = $2 WHERE crawler_id = $1",
                claimed["crawler_id"], advanced,
            )
            # Only on completion, and per scope. Same rule the crawler-level
            # watermark has always had, one level down: a scope that
            # half-finished must not record a position past records nobody
            # looked at.
            await advance_cursor(
                self.pool, claimed["crawler_id"], scope, advanced,
                kind="etag" if config.incremental == "etag" else "watermark",
                items=emitted,
            )
        elif claimed["mode"] == "live" and status in ("failed", "partial", "interrupted"):
            await record_scope_failure(
                self.pool, claimed["crawler_id"], scope, reason or status)

        await self.pool.execute(
            """
            UPDATE crawl_runs
               SET status = $2, reason = $3, emitted = $4, skipped = $5, failed = $6,
                   watermark_after = $7, finished_at = now(), heartbeat_at = now()
             WHERE run_id = $1
            """,
            run_id, status, reason, emitted, skipped, failed, advanced,
        )

        if claimed["mode"] == "dry" and status in ("completed", "partial"):
            # Approval is bound to the exact config version that was examined.
            await self.pool.execute(
                "UPDATE crawlers SET dry_run_version = $2 WHERE crawler_id = $1",
                claimed["crawler_id"], claimed["pinned_version"],
            )

        return {"run_id": run_id, "status": status, "discovered": len(found),
                "emitted": emitted, "skipped": skipped, "failed": failed,
                "reason": reason}

    async def _emit(self, run, crawler, config: CrawlerConfig,
                    found: list[Discovered], *, dry: bool) -> tuple[int, int, int]:
        """Dedupe, then write through the ordinary write path.

        A dry run walks the identical code and stops short of the write, so
        what it reports is what a live run would do rather than a parallel
        estimate that can drift from it.
        """
        principal = Principal(
            user_id=crawler["user_id"], org_id=crawler["org_id"],
            capabilities=frozenset({DATA_WRITE, DATA_READ}),
            project_id=crawler["project_id"], mode="crawler",
        )
        emitted = skipped = failed = 0
        batch: list[WriteItem] = []

        # Progress is written *during* the run, not only at the end.
        #
        # `reap()` marks any run whose heartbeat is older than
        # STALE_HEARTBEAT_SECONDS as interrupted, and this phase used to write
        # none at all -- so an emit taking longer than five minutes reaped
        # itself while still running. With `max_items` defaulting to 1000 that
        # is the ordinary case for a real source, not an edge, and it presented
        # as noise: runs randomly interrupted, a watermark that never advanced,
        # and a next run that re-fetched everything.
        #
        # The same write also makes the counters move, so a run in flight can be
        # watched rather than being a blank row until it finishes.
        async def beat() -> None:
            await self.pool.execute(
                "UPDATE crawl_runs SET emitted = $2, skipped = $3, failed = $4, "
                "heartbeat_at = now() WHERE run_id = $1",
                run["run_id"], emitted, skipped, failed,
            )

        for index, item in enumerate(found):
            if index % PROGRESS_EVERY == 0:
                await beat()
            unchanged = await self.pool.fetchval(
                "SELECT 1 FROM crawl_seen WHERE crawler_id = $1 AND external_id = $2 "
                "AND version_hash = $3",
                crawler["crawler_id"], item.external_id, item.version_hash(),
            )
            status = "skipped" if unchanged else "queued"
            await self.pool.execute(
                """
                INSERT INTO crawl_frontier (run_id, external_id, url, depth, payload, status)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (run_id, external_id) DO NOTHING
                """,
                run["run_id"], item.external_id, item.url, item.depth,
                json.dumps({"title": item.title, "fields": item.fields,
                            "preview": (item.text or "")[:400]}),
                status,
            )
            if unchanged:
                skipped += 1
                continue
            if dry:
                continue
            if item.pending:
                # A listing found a reference, not a document. The bytes are a
                # second request needing the same credential, so it names the
                # connection and the fetch worker makes it -- which is where
                # the byte cap and the blob store already are.
                content = Pending(
                    provider=item.pending["provider"],
                    resource_id=item.pending["resource_id"],
                    connection_id=crawler["connection_id"],
                    hints=item.pending.get("hints") or {},
                )
            else:
                content = Inline(text=item.text or item.title or item.external_id)
            batch.append(WriteItem(
                external_id=item.external_id,
                content=content,
                # `crawler:<id>` is what makes "which of these did that crawler
                # pull?" answerable, and with it the reprocess selector that
                # backfills a crawl run enriched with `enrich` off.
                tags=[*config.tags, f"crawler:{crawler['crawler_id']}"],
                metadata={"title": item.title, "source_url": item.url,
                          **item.fields},
                memory={"key": config.memory_key or crawler["crawler_id"],
                        "type": config.memory_type},
            ))

        if dry or not batch:
            return emitted, skipped, failed

        # Chunked so a large discovery does not become one enormous
        # transaction, and so a failure loses one chunk rather than the run.
        for start in range(0, len(batch), 50):
            # Every chunk, because a write with enrichment is the slow part and
            # a chunk can easily outlast the stale threshold on its own.
            await beat()
            chunk = batch[start:start + 50]
            try:
                response = await write_items(
                    self.pool, self.queue, self.blobs, self.settings, principal,
                    WriteRequest(producer_id=crawler["producer_id"], items=chunk,
                                 options=WriteOptions(enrich=config.enrich)),
                    run_id=run["run_id"],
                )
                for result, item in zip(response.results, chunk, strict=False):
                    if getattr(result, "status", "") in ("created", "updated"):
                        emitted += 1
                        await self.pool.execute(
                            """
                            INSERT INTO crawl_seen (crawler_id, external_id, version_hash,
                                                    last_run_id, last_seen_at)
                            VALUES ($1, $2, $3, $4, now())
                            ON CONFLICT (crawler_id, external_id)
                            DO UPDATE SET version_hash = EXCLUDED.version_hash,
                                          last_run_id = EXCLUDED.last_run_id,
                                          last_seen_at = now()
                            """,
                            crawler["crawler_id"], item.external_id,
                            _hash_for(found, item.external_id), run["run_id"],
                        )
                        await self.pool.execute(
                            "UPDATE crawl_frontier SET status = 'done' "
                            "WHERE run_id = $1 AND external_id = $2",
                            run["run_id"], item.external_id,
                        )
                    else:
                        failed += 1
            except Exception as exc:  # noqa: BLE001
                failed += len(chunk)
                await self.pool.execute(
                    "INSERT INTO crawl_errors (run_id, external_id, reason) "
                    "VALUES ($1, $2, $3)",
                    run["run_id"], chunk[0].external_id,
                    f"{exc.__class__.__name__}: {exc}"[:500],
                )
        return emitted, skipped, failed


def _hash_for(found: list[Discovered], external_id: str) -> str:
    for item in found:
        if item.external_id == external_id:
            return item.version_hash()
    return ""


# --------------------------------------------------------------- scheduler

async def tick(pool: asyncpg.Pool, worker: CrawlWorker, *, limit: int = 5,
               org_id: str | None = None) -> dict:
    """One scheduler pass, behind a single-holder advisory lock.

    Two schedulers electing themselves is how a nightly crawl becomes two
    nightly crawls, so the lock is taken before anything is selected.

    `org_id` scopes the pass to one organization. The platform scheduler runs
    unscoped; a person triggering a tick from the console must not be able to
    start crawls belonging to somebody else, and the lock alone does not
    prevent that -- it only prevents two of them at once.
    """
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT pg_try_advisory_lock(hashtext('memdog.crawl'))"):
            return {"skipped_lock": True, "started": [], "skipped": [], "runs": []}
        try:
            return await _tick(pool, conn, worker, limit, org_id)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(hashtext('memdog.crawl'))")


async def tick_for(pool: asyncpg.Pool, principal: Principal, worker: CrawlWorker,
                   *, limit: int = 5) -> dict:
    """A manual tick from the API, scoped to the caller's own organization."""
    principal.require(CONFIG_WRITE)
    return await tick(pool, worker, limit=limit, org_id=principal.org_id)


async def _tick(pool: asyncpg.Pool, conn, worker: CrawlWorker, limit: int,
                org_id: str | None = None) -> dict:
    reaped = await reap(pool)

    due = await conn.fetch(
        """
        SELECT * FROM crawlers
         WHERE enabled AND next_due_at IS NOT NULL AND next_due_at <= now()
           AND ($2::text IS NULL OR org_id = $2)
         ORDER BY next_due_at LIMIT $1
        -- No row lock here, deliberately. `tick()` holds a session-level
        -- advisory lock across the whole pass, so a second scheduler selects
        -- nothing at all rather than racing for rows -- and this statement runs
        -- outside an explicit transaction, where FOR UPDATE would release at
        -- statement end and protect nothing while appearing to.
        """,
        limit, org_id,
    )
    started, skipped, limited = [], [], []
    for row in due:
        # A run that cannot succeed should not consume the slot that says it
        # tried. The credential is cooling, so this is neither a failure of the
        # crawl nor a quiet source, and calling it either would misreport a
        # healthy one.
        cooling = await is_limited(pool, row["connection_id"])
        if cooling:
            limited.append({"crawler_id": row["crawler_id"], "reason": cooling})
            await conn.execute(
                "UPDATE crawlers SET next_due_at = $2 WHERE crawler_id = $1",
                row["crawler_id"],
                await conn.fetchval(
                    "SELECT limited_until FROM connections WHERE connection_id = $1",
                    row["connection_id"]),
            )
            continue
        live = await conn.fetchval(
            "SELECT run_id FROM crawl_runs WHERE crawler_id = $1 "
            "AND status IN ('pending', 'running') LIMIT 1",
            row["crawler_id"],
        )
        schedule = json.loads(row["schedule"]) if isinstance(row["schedule"], str) \
            else dict(row["schedule"])
        if live and row["overlap"] == "skip":
            # Recorded as skipped rather than queued. Queueing a crawl that
            # runs longer than its interval guarantees a backlog that never
            # drains.
            skipped.append(row["crawler_id"])
        else:
            run = await _create_run(pool, row, mode="live")
            started.append(run["run_id"])
        await conn.execute(
            "UPDATE crawlers SET next_due_at = $2 WHERE crawler_id = $1",
            row["crawler_id"], _next_due(schedule),
        )

    executed = []
    for run_id in started:
        executed.append(await worker.execute(run_id))
    return {"started": started, "skipped": skipped, "reaped": reaped,
            # Reported separately from `skipped`, which means "already running".
            # A reader has to be able to tell a busy crawler from a throttled
            # credential; both look like "did not run" and only one is a problem
            # that will clear on its own.
            "rate_limited": limited, "runs": executed}


def _next_due(schedule: dict) -> datetime | None:
    if schedule.get("type") == "interval":
        return _now() + timedelta(seconds=int(schedule.get("every_seconds", 3600)))
    return None


async def reap(pool: asyncpg.Pool) -> int:
    """A run whose worker died is not running.

    The heartbeat, not the queue, is what says so -- the queue lost the message
    when the worker went away, which is precisely the case being repaired.
    """
    return await pool.fetchval(
        """
        WITH stale AS (
            UPDATE crawl_runs SET status = 'interrupted', finished_at = now(),
                   reason = 'worker heartbeat went stale'
             WHERE status = 'running'
               AND heartbeat_at < now() - make_interval(secs => $1)
            RETURNING 1
        ) SELECT count(*) FROM stale
        """,
        STALE_HEARTBEAT_SECONDS,
    ) or 0


# -- sync state -------------------------------------------------------------


async def cursor_for(pool, crawler_id: str, scope: str = "") -> str | None:
    """Where this crawler got to, for this scope.

    Falls back to `crawlers.watermark` so a crawler that predates per-scope
    cursors keeps exactly the position it had -- the single-scope case is
    `scope = ''`, and nothing has to be migrated for it to keep working.
    """
    found = await pool.fetchval(
        "SELECT cursor FROM crawl_cursors WHERE crawler_id = $1 AND scope = $2",
        crawler_id, scope)
    if found is not None:
        return found
    return await pool.fetchval(
        "SELECT watermark FROM crawlers WHERE crawler_id = $1", crawler_id)


async def advance_cursor(
    pool, crawler_id: str, scope: str, cursor: str | None, *,
    kind: str = "watermark", items: int = 0,
) -> None:
    """Only when that scope completed.

    Same rule the crawler-level watermark has always had, applied one level
    down: a scope that half-finished must not record a position past records
    nobody looked at, because nothing ever comes back for them.
    """
    await pool.execute(
        """
        INSERT INTO crawl_cursors (crawler_id, scope, cursor, kind, items_seen,
            last_ok_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, now(), now())
        ON CONFLICT (crawler_id, scope) DO UPDATE
           SET cursor = EXCLUDED.cursor, kind = EXCLUDED.kind,
               items_seen = crawl_cursors.items_seen + EXCLUDED.items_seen,
               last_ok_at = now(), last_error = NULL, updated_at = now()
        """,
        crawler_id, scope, cursor, kind, items,
    )


async def record_scope_failure(pool, crawler_id: str, scope: str, reason: str) -> None:
    """A scope that failed keeps its cursor and records why.

    Keeping the cursor is the point: the next run retries the same range rather
    than skipping it, and the reason is what tells a reader that the gap is a
    failure rather than a quiet source.
    """
    await pool.execute(
        """
        INSERT INTO crawl_cursors (crawler_id, scope, last_error, updated_at)
        VALUES ($1, $2, $3, now())
        ON CONFLICT (crawler_id, scope) DO UPDATE
           SET last_error = EXCLUDED.last_error, updated_at = now()
        """,
        crawler_id, scope, reason[:500],
    )


async def is_limited(pool, connection_id: str | None) -> str | None:
    """Whether this credential is cooling, and until when.

    On the connection rather than the crawler because the quota belongs to the
    token: two crawlers sharing one Slack connection draw on the same allowance
    and neither can see the other.
    """
    if not connection_id:
        return None
    row = await pool.fetchrow(
        "SELECT limited_until, last_limit_reason FROM connections "
        "WHERE connection_id = $1 AND limited_until > now()", connection_id)
    if row is None:
        return None
    return (f"{row['last_limit_reason'] or 'rate limited'} until "
            f"{row['limited_until'].isoformat(timespec='seconds')}")


async def mark_limited(
    pool, connection_id: str | None, *, seconds: int, reason: str,
) -> None:
    """Honour what the API said rather than guessing a backoff.

    `Retry-After` is the source telling us exactly when it will answer again;
    inventing a shorter interval is how a cooling token becomes a banned one.
    """
    if not connection_id:
        return
    await pool.execute(
        "UPDATE connections SET limited_until = now() + make_interval(secs => $2), "
        "last_limit_reason = $3 WHERE connection_id = $1",
        connection_id, max(1, seconds), reason[:200])


async def source_lag(pool, project_id: str) -> list[dict]:
    """How far behind each source is, per scope.

    The number every project signal depends on. One computed over a source that
    stopped syncing is confidently wrong, and "no activity for seven days" is
    indistinguishable from "the connector broke seven days ago" without it.
    """
    rows = await pool.fetch(
        """
        SELECT c.crawler_id, c.name, cu.scope, cu.last_ok_at, cu.last_error,
               cu.items_seen,
               EXTRACT(EPOCH FROM (now() - cu.last_ok_at))::bigint AS behind_seconds
          FROM crawlers c
          LEFT JOIN crawl_cursors cu ON cu.crawler_id = c.crawler_id
         WHERE c.project_id = $1
         ORDER BY cu.last_ok_at NULLS FIRST
        """,
        project_id,
    )
    return [dict(r) for r in rows]
