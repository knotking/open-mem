"""The reconciler -- what makes the staircase self-healing.

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


@dataclass(frozen=True)
class Swept:
    embed: int
    enrich: int

    @property
    def total(self) -> int:
        return self.embed + self.enrich


async def reconcile(
    pool: asyncpg.Pool,
    queue: Queue,
    *,
    embed_generator: str,
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
    stored = await pool.fetch(
        """
        SELECT data_id FROM data_items
        WHERE state = 'stored'
          AND indexable_text IS NOT NULL
          AND deleted_at IS NULL
          AND updated_at < now() - make_interval(secs => $1)
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

    to_embed = {r["data_id"] for r in stored} | {r["data_id"] for r in stale_vectors}
    to_enrich = {r["data_id"] for r in searchable} - to_embed

    for data_id in sorted(to_embed):
        await queue.publish(EMBED_TOPIC, {"data_id": data_id})
    for data_id in sorted(to_enrich):
        await queue.publish(ENRICH_TOPIC, {"data_id": data_id})

    swept = Swept(embed=len(to_embed), enrich=len(to_enrich))
    if swept.total:
        log.info("reconciler re-enqueued %d embed, %d enrich", swept.embed, swept.enrich)
    return swept
