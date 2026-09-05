"""The domain event log.

Recording data and enriching it are two different commitments. One is cheap,
synchronous and always wanted; the other costs money, takes time and is usually
not. Collapsing them into one transaction forces every writer to pay for
enrichment they may not want, and makes the write latency a function of a model
call.

So a write emits **`data.recorded`**, always. If the caller asked for it, it also
emits **`enrichment.requested`**, which carries `caused_by` pointing at the first.
Dispatch will not run an event ahead of its cause -- the ordering is a fact
about the data rather than a hope about timing.

Events with no consumer are still events. `graph.build.requested` is emitted
today and consumed by nobody; the alternative is a graph that begins at whatever
date someone finally builds the consumer, having lost everything before it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import asyncpg

from .audit import record_audit
from .ids import new_id
from .queue import Queue

log = logging.getLogger(__name__)

# What each event type is dispatched onto. An event whose type is absent here
# is logged and left alone -- that is the "no consumer yet" case, and it is a
# state rather than an error.
TOPIC_FOR_EVENT: dict[str, str] = {
    "data.recorded": "record",
    "fetch.requested": "fetch",
    "enrichment.requested": "enrich_request",
    "reprocess.requested": "reprocess",
}

# Transitions alerts watch. All onto one topic, because the message says only
# *something happened* -- the consumer coalesces and then reads the log itself,
# so which transition woke it is not information it needs.
for _surface in ("fact.asserted", "fact.superseded", "fact.retracted",
                 "data.revised", "memory.member_added", "memory.retyped",
                 "case.member_promoted", "acl.changed"):
    TOPIC_FOR_EVENT[_surface] = "alerts"

# `enrichment.refused` is emitted when a sensitivity policy withholds the
# expensive tier from a record. Nothing consumes it and nothing should -- it is
# evidence that a control fired, and the question it answers ("why does this
# clinical note have no summary?") is asked by a person, not by a worker.
#
# `entity.extraction.requested` now has one: entities are resolved inside the
# enrichment transaction, so the event is consumed rather than logged for a
# worker that does not exist. Edges followed it there -- `record_edges` runs in
# the same transaction -- so `graph.build.requested` is **not** evidence that
# the edge layer is missing. It is not. The event now marks each write as a
# candidate for a rebuild pass that has not been written, and its unconsumed
# status has twice been read as a broken graph.
# `work.abandoned` is an obituary, not a request: the queue already gave up, so
# there is nothing to dispatch it to. It exists so the row can say why it
# stopped -- the queue's own dead-letter list is in memory and dies with the
# process, which on a scale-to-zero platform is the same as never recording it.
NO_CONSUMER = {"graph.build.requested", "enrichment.refused", "work.abandoned"}

MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class Event:
    event_id: str
    event_type: str
    data_id: str | None
    payload: dict


async def emit(
    conn,
    *,
    event_type: str,
    org_id: str,
    project_id: str | None = None,
    data_id: str | None = None,
    caused_by: str | None = None,
    payload: dict | None = None,
    actor_user_id: str | None = None,
    actor_key_id: str | None = None,
) -> str:
    """Append an event **inside the caller's transaction**.

    That is the whole point: an item cannot be recorded without its event, and
    an event cannot exist for a write that rolled back.
    """
    event_id = new_id("evt")
    status = "no_consumer" if event_type in NO_CONSUMER else "pending"
    await conn.execute(
        """
        INSERT INTO domain_events (event_id, event_type, org_id, project_id, data_id,
                                   caused_by, payload, actor_user_id, actor_key_id, status)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        event_id, event_type, org_id, project_id, data_id, caused_by,
        payload or {}, actor_user_id, actor_key_id, status,
    )
    return event_id


async def emit_audited(
    conn,
    principal,
    *,
    event_type: str,
    org_id: str,
    project_id: str | None = None,
    data_id: str | None = None,
    caused_by: str | None = None,
    payload: dict | None = None,
) -> str:
    """Emit and audit together.

    The event log is operational -- it drains as work completes. The audit
    record is evidence and is never purged. Both are written here so an event
    cannot be raised without a trace of who raised it.
    """
    event_id = await emit(
        conn, event_type=event_type, org_id=org_id, project_id=project_id,
        data_id=data_id, caused_by=caused_by, payload=payload,
        actor_user_id=principal.user_id, actor_key_id=principal.key_id,
    )
    await record_audit(
        conn, principal, action=f"event.{event_type}", project_id=project_id,
        target_type="event", target_id=event_id,
        detail={"data_id": data_id, "caused_by": caused_by, **(payload or {})},
    )
    return event_id


async def dispatch_pending(
    pool: asyncpg.Pool,
    queue: Queue,
    limit: int = 500,
    *,
    redeliver_after_seconds: int | None = None,
) -> int:
    """Publish events onto their topics, in sequence order.

    An event is held back while its cause is unfinished. That is what makes
    "enrichment never runs before the data exists" true even when the two events
    are handled by different processes at the same moment.

    `redeliver_after_seconds` also picks up events that were **dispatched and
    never consumed**. Without it, a message lost between publish and handler
    leaves its event stranded at `dispatched` forever -- the same lost-work
    problem the log was meant to solve, moved one layer up. The reconciler
    passes a value; ordinary request-path dispatch does not, because an event
    published seconds ago is in flight rather than lost.
    """
    rows = await pool.fetch(
        """
        SELECT e.event_id, e.event_type, e.data_id, e.payload, e.org_id, e.project_id
        FROM domain_events e
        LEFT JOIN domain_events cause ON cause.event_id = e.caused_by
        WHERE e.attempts < $2
          AND (
                e.status = 'pending'
             OR (e.status = 'dispatched'
                 AND $3::int IS NOT NULL
                 AND e.dispatched_at < now() - make_interval(secs => $3))
          )
          AND (e.caused_by IS NULL OR cause.status = 'consumed')
        ORDER BY e.sequence
        LIMIT $1
        """,
        limit, MAX_ATTEMPTS, redeliver_after_seconds,
    )
    dispatched = 0
    for row in rows:
        topic = TOPIC_FOR_EVENT.get(row["event_type"])
        if topic is None:
            await pool.execute(
                "UPDATE domain_events SET status = 'no_consumer' WHERE event_id = $1",
                row["event_id"],
            )
            continue
        await pool.execute(
            """
            UPDATE domain_events SET status = 'dispatched', dispatched_at = now(),
                                     attempts = attempts + 1
            WHERE event_id = $1
            """,
            row["event_id"],
        )
        await queue.publish(topic, {
            "event_id": row["event_id"],
            "data_id": row["data_id"],
            "payload": dict(row["payload"]),
            "org_id": row["org_id"],
            "project_id": row["project_id"],
        })
        dispatched += 1
    return dispatched


async def mark_consumed(pool: asyncpg.Pool, event_id: str) -> None:
    await pool.execute(
        """
        UPDATE domain_events SET status = 'consumed', consumed_at = now()
        WHERE event_id = $1
        """,
        event_id,
    )


async def mark_failed(pool: asyncpg.Pool, event_id: str, error: str) -> None:
    """Back to pending unless it has been tried too often.

    A failed dispatch is not a lost intention: the event stays in the log and is
    picked up again, which is the property the queue alone never had.
    """
    await pool.execute(
        """
        UPDATE domain_events
        SET status = CASE WHEN attempts >= $3 THEN 'failed' ELSE 'pending' END,
            last_error = $2
        WHERE event_id = $1
        """,
        event_id, error[:500], MAX_ATTEMPTS,
    )


async def mark_deferred(pool: asyncpg.Pool, event_id: str, reason: str) -> None:
    """Waiting, not failing -- and it does not cost an attempt.

    A daily quota does not reset inside a retry budget. Counting these would
    walk a perfectly good request to `failed` in a few seconds and require a
    human to notice and requeue it, when the correct behaviour is simply to try
    again later.
    """
    await pool.execute(
        """
        UPDATE domain_events
        SET status = 'pending', last_error = $2,
            attempts = GREATEST(attempts - 1, 0)
        WHERE event_id = $1
        """,
        event_id, reason[:500],
    )


async def list_events(
    pool: asyncpg.Pool,
    org_id: str,
    *,
    project_id: str | None = None,
    data_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT event_id, sequence, event_type, data_id, caused_by, status, attempts,
               last_error, payload, occurred_at, consumed_at
        FROM domain_events
        WHERE org_id = $1
          AND ($2::text IS NULL OR project_id = $2)
          AND ($3::text IS NULL OR data_id = $3)
        ORDER BY sequence DESC LIMIT $4
        """,
        org_id, project_id, data_id, limit,
    )
    return [dict(r) for r in rows]
