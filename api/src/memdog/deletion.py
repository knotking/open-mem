"""Deletion -- tombstone now, reclaim eventually.

An erasure request cannot wait in a queue to take effect, but a cascade across
Postgres, pgvector and an object store cannot complete inside a request. The
tombstone resolves it: **visibility is transactional, reclamation is eventual.**

Two things are easy to get wrong and both are load-bearing:

**The root row is deleted last.** `data_items` is what tells the cascade which
chunks, which blobs and which contributions belong to this item. Remove it first
and a cascade that fails halfway has lost its own map.

**A tombstone is not an erasure.** `deleted_at` is when it became invisible;
`purged_at` is when the bytes actually went. An erasure certificate is issued
against the second, never the first -- reporting the first as the second is how
a compliance answer becomes false.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import asyncpg

from .acl import visibility_params, visibility_sql
from .audit import record_audit
from .auth import DATA_WRITE, Principal
from .ids import new_id
from .queue import Message, Queue

log = logging.getLogger(__name__)

DELETE_TOPIC = "delete"


class NotFound(Exception):
    pass


@dataclass(frozen=True)
class Deletion:
    run_id: str
    data_ids: list[str]
    retained: list[tuple[str, str]]


def _instant(value: object, field: str) -> datetime:
    """Coerce a selector's timestamp, which arrives from JSON as a string.

    asyncpg wants a `datetime` and refuses a `str` outright, so `since` and
    `until` -- the time_range selector FR-DEL-3 requires and this module's own
    comments describe -- raised a 500 for every caller that reached the endpoint
    over HTTP. There is no way to send a datetime in a JSON body, so the
    selector was unusable rather than merely awkward.

    Parsed here rather than at the endpoint because the seed's reset builds a
    selector too: a coercion that lives in one caller is one the next caller
    does not have.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(
                f"{field} is not an ISO-8601 instant: {value!r}"
            ) from exc
    else:
        raise ValueError(f"{field} must be an ISO-8601 instant")
    # Naive means UTC, not server-local. A selector whose span depends on where
    # the process happens to run deletes a different set in staging than in
    # production, and nothing about the request would show it.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def _selected(
    conn, principal: Principal, selector: dict
) -> list[asyncpg.Record]:
    """Resolve a selector to items the caller can actually delete.

    The ACL predicate is the same one retrieval uses. Deleting through a
    selector must never reach further than reading through one would.
    """
    org_id, user_id, principals = visibility_params(principal)
    predicate = visibility_sql("d", 2, 3, 4)
    clauses, params = [], [selector.get("project_id"), org_id, user_id, principals]

    if selector.get("data_ids"):
        params.append(selector["data_ids"])
        clauses.append(f"d.data_id = ANY(${len(params)}::text[])")
    if selector.get("producer_id"):
        params.append(selector["producer_id"])
        clauses.append(f"d.producer_id = ${len(params)}")
    if selector.get("tags"):
        params.append(selector["tags"])
        clauses.append(f"d.tags && ${len(params)}::text[]")
    # A time_range must name its clock: ingested_at and event_time answer
    # different questions and a selector that does not say which is ambiguous
    # in exactly the cases that matter (FR-DEL-3).
    clock = selector.get("time_clock", "ingested_at")
    if clock not in ("ingested_at", "event_time"):
        raise ValueError("time_clock must be ingested_at or event_time")
    if selector.get("since"):
        params.append(_instant(selector["since"], "since"))
        clauses.append(f"d.{clock} >= ${len(params)}")
    if selector.get("until"):
        params.append(_instant(selector["until"], "until"))
        clauses.append(f"d.{clock} <= ${len(params)}")

    if not clauses:
        raise ValueError("a selector must narrow something")

    where = " AND ".join(clauses)
    return await conn.fetch(
        f"""
        SELECT d.data_id, d.legal_hold
        FROM data_items d
        WHERE ($1::text IS NULL OR d.project_id = $1)
          AND {predicate} AND {where}
        ORDER BY d.data_id
        LIMIT 10000
        """,
        *params,
    )


async def request_deletion(
    pool: asyncpg.Pool,
    queue: Queue,
    principal: Principal,
    *,
    selector: dict,
    reason: str | None = None,
    dry_run: bool = False,
    grace_seconds: int = 0,
) -> Deletion:
    """The synchronous half: tombstone, audit, enqueue. Nothing else.

    Cleanup and erasure use this same path and differ only in the delay before
    reclamation starts -- one pipeline, so the cascade is exercised constantly
    rather than only in the rare compliance case.
    """
    principal.require(DATA_WRITE)
    run_id = new_id("run")

    async with pool.acquire() as conn, conn.transaction():
        rows = await _selected(conn, principal, selector)
        # Legal hold outranks erasure. The request returns partial completion
        # naming what was withheld rather than silently doing less (FR-DEL-9).
        deletable = [r["data_id"] for r in rows if not r["legal_hold"]]
        retained = [(r["data_id"], "legal_hold") for r in rows if r["legal_hold"]]

        await conn.execute(
            """
            INSERT INTO runs (run_id, org_id, project_id, kind, mode, status, selector,
                              reason, total, retained, actor_user_id, actor_key_id)
            VALUES ($1, $2, $3, 'delete', $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            run_id, principal.org_id, selector.get("project_id"),
            "dry_run" if dry_run else "execute",
            "completed" if dry_run else "queued",
            selector, reason, len(deletable), len(retained),
            principal.user_id, principal.key_id,
        )
        await conn.executemany(
            "INSERT INTO run_items (run_id, data_id, status) VALUES ($1, $2, $3)",
            [(run_id, d, "pending") for d in deletable]
            + [(run_id, d, "retained") for d, _ in retained],
        )

        if not dry_run and deletable:
            # Invisible from this instant, in the same transaction as the
            # audit record. Enforced by the shared ACL predicate, which every
            # read path already goes through.
            await conn.execute(
                """
                UPDATE data_items
                SET deleted_at = now(), deletion_reason = $2, deletion_run_id = $3
                WHERE data_id = ANY($1::text[]) AND deleted_at IS NULL
                """,
                deletable, reason, run_id,
            )

        await record_audit(
            conn, principal,
            action="delete.requested" if not dry_run else "delete.dry_run",
            project_id=selector.get("project_id"),
            target_type="run", target_id=run_id,
            detail={"selector": selector, "reason": reason,
                    "deleting": len(deletable), "retained": len(retained)},
        )

    if not dry_run and deletable:
        await queue.publish(DELETE_TOPIC, {"run_id": run_id, "grace_seconds": grace_seconds})
    return Deletion(run_id=run_id, data_ids=deletable, retained=retained)


class DeleteWorker:
    """The asynchronous half -- W9. Resumable and idempotent.

    Every step is written so that running it twice is the same as running it
    once, because a cascade that cannot be safely retried is a cascade that
    strands data on its first network error.
    """

    def __init__(self, pool: asyncpg.Pool, blobs) -> None:
        self._pool = pool
        self._blobs = blobs

    def register(self, queue: Queue, topic: str = DELETE_TOPIC) -> None:
        queue.subscribe(topic, self.handle)

    async def handle(self, message: Message) -> None:
        run_id = message.body["run_id"]
        await self._pool.execute(
            "UPDATE runs SET status = 'running' WHERE run_id = $1 AND status = 'queued'",
            run_id,
        )
        rows = await self._pool.fetch(
            "SELECT data_id FROM run_items WHERE run_id = $1 AND status = 'pending'",
            run_id,
        )
        for row in rows:
            try:
                await self.purge(row["data_id"], run_id)
                await self._pool.execute(
                    "UPDATE run_items SET status = 'done', at = now() WHERE run_id = $1 AND data_id = $2",
                    run_id, row["data_id"],
                )
                await self._pool.execute(
                    "UPDATE runs SET done = done + 1 WHERE run_id = $1", run_id
                )
            except Exception as exc:  # noqa: BLE001 -- one item must not stop the run
                log.error("purge failed for %s: %r", row["data_id"], exc)
                await self._pool.execute(
                    """
                    UPDATE run_items SET status = 'failed', reason = $3, at = now()
                    WHERE run_id = $1 AND data_id = $2
                    """,
                    run_id, row["data_id"], repr(exc)[:400],
                )
                await self._pool.execute(
                    "UPDATE runs SET failed = failed + 1 WHERE run_id = $1", run_id
                )
        await self._pool.execute(
            """
            UPDATE runs SET status = CASE WHEN failed > 0 THEN 'failed' ELSE 'completed' END,
                            finished_at = now()
            WHERE run_id = $1
            """,
            run_id,
        )

    async def purge(self, data_id: str, run_id: str | None = None) -> dict | None:
        """Steps 4-9. The root row goes last, and only after the blob."""
        item = await self._pool.fetchrow(
            "SELECT storage_ref, org_id, project_id FROM data_items WHERE data_id = $1",
            data_id,
        )
        if item is None:
            return None  # already purged; idempotent by construction

        async with self._pool.acquire() as conn, conn.transaction():
            # 5. Contributions, not the entities. An entity mentioned by fifty
            # documents is not deleted because one of them went away.
            await conn.execute("DELETE FROM artifact_sources WHERE data_id = $1", data_id)
            # 7 (the summaries half). A summary that absorbed this item still
            # contains its content in prose, so it is marked for rebuild rather
            # than deleted or quietly left.
            await conn.execute(
                """
                UPDATE artifacts a SET stale_reason = 'source_deleted'
                WHERE NOT EXISTS (SELECT 1 FROM artifact_sources s WHERE s.artifact_id = a.artifact_id)
                  AND a.stale_reason IS NULL
                """
            )
            # Stored answers that quoted it are invalidated for the same reason.
            await conn.execute(
                "UPDATE queries SET answer = NULL WHERE query_id IN "
                "(SELECT query_id FROM query_sources WHERE data_id = $1)",
                data_id,
            )
            await conn.execute("DELETE FROM query_sources WHERE data_id = $1", data_id)
            # Membership goes; an item held by another memory is unaffected
            # because membership is per (memory, item).
            await conn.execute("DELETE FROM memory_members WHERE data_id = $1", data_id)
            # Case membership too. Missing this left a purged item still listed
            # as a member of a patient's timeline -- hidden by the ACL predicate,
            # but present, which is not the same thing as erased.
            await conn.execute("DELETE FROM case_members WHERE data_id = $1", data_id)
            # The normalized projection is the part that actually bites: its
            # payload is a *copy* of the record -- names, identifiers, amounts --
            # so leaving it behind means the tombstone reports an erasure that
            # did not happen.
            # Mentions are personal data derived from the record -- a name
            # someone was observed to be associated with. The FK cascades when
            # the row goes, but the purge deletes explicitly so erasure does
            # not depend on a cascade nobody re-checks.
            await conn.execute("DELETE FROM entity_mentions WHERE data_id = $1", data_id)
            # An edge names the record that asserted it. Deleting the record
            # and keeping the claim would leave a graph asserting something
            # with no evidence behind it -- and the endpoints of that edge are
            # themselves derived personal data.
            await conn.execute("DELETE FROM entity_edges WHERE source_data_id = $1", data_id)
            await conn.execute("DELETE FROM normalized_records WHERE data_id = $1", data_id)
            # A share link to a purged item can only 404; removing it stops the
            # public inventory listing something that no longer exists.
            await conn.execute("DELETE FROM share_links WHERE data_id = $1", data_id)
            # 4 and 6. Chunks cascade to embeddings by foreign key.
            await conn.execute("DELETE FROM chunks WHERE data_id = $1", data_id)
            await conn.execute("DELETE FROM data_versions WHERE data_id = $1", data_id)

        # 7. The network call that can fail -- outside the transaction, because
        # holding one open across an object-store round trip is how a deletion
        # run takes a database down with it.
        if item["storage_ref"]:
            try:
                await self._blobs.delete(item["storage_ref"])
            except FileNotFoundError:
                pass  # already gone; the goal is absence, not a successful call

        # 9. Only now. A metadata-only tombstone remains: the audit record of
        # the deletion must outlive the thing deleted.
        await self._pool.execute(
            """
            UPDATE data_items
            SET purged_at = now(), content_text = NULL, extracted_text = NULL,
                storage_ref = NULL, pending_ref = NULL, parse_detail = NULL,
                identifiers = '{}', tags = '{}'
            WHERE data_id = $1
            """,
            data_id,
        )
        # FR-DEL-10: erasure runs a verification pass and the result is
        # recorded, because "we deleted it" is a claim until something checked.
        verdict = await verify_erasure(self._pool, data_id)
        if not verdict["complete"]:
            log.error("erasure incomplete for %s: %s", data_id, verdict["remaining"])
        return verdict


async def verify_erasure(pool: asyncpg.Pool, data_id: str) -> dict:
    """Check that nothing item-scoped survived, and say what did.

    An erasure certificate that is issued without looking is a claim, not
    evidence. This re-queries every table that holds item-scoped data rather
    than trusting that the cascade ran -- the cascade is exactly the thing under
    test.
    """
    checks = {
        "chunks": "SELECT count(*) FROM chunks WHERE data_id = $1",
        "embeddings": "SELECT count(*) FROM embeddings WHERE data_id = $1",
        "versions": "SELECT count(*) FROM data_versions WHERE data_id = $1",
        "artifact_sources": "SELECT count(*) FROM artifact_sources WHERE data_id = $1",
        "query_sources": "SELECT count(*) FROM query_sources WHERE data_id = $1",
        "memory_members": "SELECT count(*) FROM memory_members WHERE data_id = $1",
        "case_members": "SELECT count(*) FROM case_members WHERE data_id = $1",
        "entity_mentions": "SELECT count(*) FROM entity_mentions WHERE data_id = $1",
        "entity_edges": "SELECT count(*) FROM entity_edges WHERE source_data_id = $1",
        "normalized_records": "SELECT count(*) FROM normalized_records WHERE data_id = $1",
        "share_links": "SELECT count(*) FROM share_links WHERE data_id = $1",
    }
    remaining = {}
    for name, sql in checks.items():
        count = await pool.fetchval(sql, data_id)
        if count:
            remaining[name] = count

    row = await pool.fetchrow(
        """
        SELECT purged_at, content_text IS NULL AND extracted_text IS NULL
               AND storage_ref IS NULL AS content_cleared
        FROM data_items WHERE data_id = $1
        """,
        data_id,
    )
    return {
        "data_id": data_id,
        "purged_at": row["purged_at"] if row else None,
        "content_cleared": bool(row and row["content_cleared"]),
        "remaining": remaining,
        # The certificate is issued against this, not against deleted_at.
        "complete": bool(row and row["purged_at"] and row["content_cleared"] and not remaining),
    }


async def unpurged_tombstones(pool: asyncpg.Pool, older_than_seconds: int = 3600) -> int:
    """The metric that catches a cascade which quietly stopped (FR-DEL-19)."""
    return await pool.fetchval(
        """
        SELECT count(*) FROM data_items
        WHERE deleted_at IS NOT NULL AND purged_at IS NULL
          AND deleted_at < now() - make_interval(secs => $1)
        """,
        older_than_seconds,
    )
