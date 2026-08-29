"""The reconciler -- what makes the staircase self-healing.

One rule governs everything here: **it repairs requested work, it never invents
it.** Now that enrichment is opt-in, an item sitting at `stored` with no
enrichment request is not behind -- it is exactly where its owner left it, and
sweeping it would perform a model call nobody asked for and bill someone for it.
So every row-level tier is qualified by the existence of an enrichment event.

The write commits and the enrichment is queued. If the process dies in between,
the row is durable and the job is not: an in-process queue loses it outright,
and every broker has a redelivery limit after which it stops trying. Either way
the item sits one rung below where it belongs, forever, and looks exactly like
an item that is merely behind.

So the queue is not the record of what still needs doing -- **the rows are**.
This sweeps for items whose state lags what their content warrants and
republishes the work. It is idempotent by construction: both workers rebuild
rather than append, so a spurious re-enqueue costs a little compute and changes
nothing else.

A grace period keeps it from racing the worker that is already handling an item.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import asyncpg

from .queue import Queue

log = logging.getLogger(__name__)

EMBED_TOPIC = "embed"
ENRICH_TOPIC = "enrich"
PARSE_TOPIC = "parse"


@dataclass(frozen=True)
class Swept:
    parse: int
    embed: int
    enrich: int
    events: int = 0

    @property
    def total(self) -> int:
        return self.parse + self.embed + self.enrich + self.events


async def reconcile(
    pool: asyncpg.Pool,
    queue: Queue,
    *,
    embed_generator: str,
    enrich_generator: str | None = None,
    grace_seconds: int = 300,
    limit: int = 500,
) -> Swept:
    """Re-enqueue what is behind. Returns what it found.

    The two queries are deliberately different shapes:

    - **Stuck at `stored`** is a state question. The item has text and never
      became searchable.
    - **Stuck at `searchable`** is *not* a state question, because an item can
      be searchable under a stale embedding generator. It is a join against what
      is currently assigned -- the same staleness join `GET /artifacts/stale`
      uses, applied to the tier below artifacts.
    """
    # Bytes that were never read. This tier was missing, and its absence was
    # invisible: the "stuck at stored" query below requires text, which by
    # definition excludes exactly the items that still need parsing. A media
    # file whose parse job was lost would sit at `stored` forever, looking
    # identical to one that had been examined and declined.
    unparsed = await pool.fetch(
        """
        SELECT d.data_id FROM data_items d
        WHERE d.state = 'stored'
          AND d.storage_ref IS NOT NULL
          AND d.indexable_text IS NULL
          AND d.parse_status IS NULL
          AND d.deleted_at IS NULL
          AND d.updated_at < now() - make_interval(secs => $1)
          AND EXISTS (
                SELECT 1 FROM domain_events e
                WHERE e.data_id = d.data_id AND e.event_type = 'enrichment.requested'
              )

        ORDER BY data_id
        LIMIT $2
        """,
        grace_seconds,
        limit,
    )

    stored = await pool.fetch(
        """
        SELECT d.data_id FROM data_items d
        WHERE d.state = 'stored'
          AND d.indexable_text IS NOT NULL
          AND d.deleted_at IS NULL
          AND d.updated_at < now() - make_interval(secs => $1)
          AND EXISTS (
                SELECT 1 FROM domain_events e
                WHERE e.data_id = d.data_id AND e.event_type = 'enrichment.requested'
              )

        ORDER BY data_id
        LIMIT $2
        """,
        grace_seconds,
        limit,
    )
    searchable = await pool.fetch(
        """
        SELECT d.data_id FROM data_items d
        WHERE d.state = 'searchable'
          AND d.indexable_text IS NOT NULL
          AND d.deleted_at IS NULL
          AND d.updated_at < now() - make_interval(secs => $1)
          AND EXISTS (
                SELECT 1 FROM domain_events e
                WHERE e.data_id = d.data_id AND e.event_type = 'enrichment.requested'
              )

        ORDER BY d.data_id
        LIMIT $2
        """,
        grace_seconds,
        limit,
    )
    # Embeddings written by a generator that is no longer assigned: the item is
    # searchable, but in a vector space retrieval no longer queries.
    stale_vectors = await pool.fetch(
        """
        SELECT DISTINCT e.data_id FROM embeddings e
        JOIN data_items d ON d.data_id = e.data_id
        WHERE e.generator_version <> $1
          AND d.deleted_at IS NULL
        ORDER BY e.data_id
        LIMIT $2
        """,
        embed_generator,
        limit,
    )

    # Enriched, but by a fallback engine. Without this the item is finished
    # forever: its generator_version matches the current one, so every other
    # staleness check considers it done, and the degraded summary it got during
    # a provider outage is the summary it keeps.
    degraded = await pool.fetch(
        """
        SELECT DISTINCT s.data_id FROM artifacts a
        JOIN artifact_sources s ON s.artifact_id = a.artifact_id
        JOIN data_items d ON d.data_id = s.data_id
        WHERE a.fallback_depth > 0
          AND a.generator_version = $1
          AND d.deleted_at IS NULL
          AND d.state = 'enriched'
        ORDER BY s.data_id
        LIMIT $2
        """,
        enrich_generator,
        limit,
    ) if enrich_generator else []

    to_parse = {r["data_id"] for r in unparsed}
    to_embed = ({r["data_id"] for r in stored} | {r["data_id"] for r in stale_vectors}) - to_parse
    to_enrich = (
        {r["data_id"] for r in searchable} | {r["data_id"] for r in degraded}
    ) - to_embed - to_parse

    for data_id in sorted(to_parse):
        await queue.publish(PARSE_TOPIC, {"data_id": data_id})
    for data_id in sorted(to_embed):
        await queue.publish(EMBED_TOPIC, {"data_id": data_id})
    for data_id in sorted(to_enrich):
        await queue.publish(ENRICH_TOPIC, {"data_id": data_id})

    # Undelivered events are the other way work goes missing now that the log
    # is the record. A dispatch that never happened looks exactly like an
    # enrichment nobody asked for.
    from .events import dispatch_pending

    redispatched = await dispatch_pending(
        pool, queue, limit=limit, redeliver_after_seconds=grace_seconds
    )

    swept = Swept(parse=len(to_parse), embed=len(to_embed), enrich=len(to_enrich),
                  events=redispatched)
    if swept.total:
        log.info(
            "reconciler re-enqueued %d parse, %d embed, %d enrich, %d events",
            swept.parse, swept.embed, swept.enrich, swept.events,
        )
    return swept
