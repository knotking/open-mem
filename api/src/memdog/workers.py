"""The embed worker -- the half of the spine that makes a stored item findable.

It advances an item from `stored` to `searchable`. Two properties matter more
than the code:

**Idempotent.** Redelivery is at-least-once in every queue implementation, so the
handler rebuilds the derived rows for an item rather than appending to them.

**Defers, never falls back.** If the assigned engine is unavailable the exception
propagates and the queue retries. Substituting another model would write vectors
from a different space into the same index, and the resulting corpus is not
repairable without knowing which rows came from where.
"""

from __future__ import annotations

import logging

import asyncpg

from .chunking import chunk_text
from .config import Settings
from .db import vector_literal
from .ids import new_id
from .acl import Acl, strictest
from .extraction import EXTRACT_PURPOSE, Extractor
from .inference import EmbeddingEngine, generator_version
from .queue import Message, Queue

EMBED_PURPOSE = "embedding"
ENRICH_TOPIC = "enrich"

log = logging.getLogger(__name__)


class IndexDimensionMismatch(RuntimeError):
    """The configured engine does not fit the index that already exists."""


async def verify_index_dimension(pool: asyncpg.Pool, embedder: EmbeddingEngine) -> None:
    """Fail at startup, loudly, rather than per row and silently.

    The pgvector column width is fixed when the corpus is created; increasing it
    later is a full re-embed, not a migration. A process configured for a
    different dimension cannot write a single valid row, so it must not start
    and quietly accumulate a backlog of failures that look like enrichment lag.
    """
    actual = await pool.fetchval(
        """
        SELECT atttypmod
        FROM pg_attribute
        WHERE attrelid = 'embeddings'::regclass AND attname = 'embedding'
        """
    )
    if actual is not None and actual > 0 and actual != embedder.dim:
        raise IndexDimensionMismatch(
            f"index holds vector({actual}) but {embedder.model_id} is configured "
            f"for dim {embedder.dim}. Re-embedding a corpus is not a migration -- "
            f"set EMBED_DIM={actual} or rebuild the index deliberately."
        )


class EmbedWorker:
    def __init__(
        self,
        pool: asyncpg.Pool,
        embedder: EmbeddingEngine,
        settings: Settings,
        queue: Queue | None = None,
    ) -> None:
        self._pool = pool
        self._embedder = embedder
        self._settings = settings
        # The staircase is built by handing off, not by one worker doing both:
        # an item is searchable the moment it is embedded, whether or not
        # enrichment ever succeeds.
        self._queue = queue
        self.generator_version = generator_version(
            purpose=EMBED_PURPOSE,
            model_id=embedder.model_id,
            spec={
                "dim": embedder.dim,
                "chunk_chars": settings.chunk_chars,
                "chunk_overlap": settings.chunk_overlap,
            },
        )

    def register(self, queue: Queue, topic: str) -> None:
        queue.subscribe(topic, self.handle)

    async def ensure_generator(self) -> None:
        await verify_index_dimension(self._pool, self._embedder)
        await self._pool.execute(
            """
            INSERT INTO generators (generator_version, purpose, model_id, spec)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (generator_version) DO NOTHING
            """,
            self.generator_version,
            EMBED_PURPOSE,
            self._embedder.model_id,
            {
                "dim": self._embedder.dim,
                "chunk_chars": self._settings.chunk_chars,
                "chunk_overlap": self._settings.chunk_overlap,
            },
        )

    async def handle(self, message: Message) -> None:
        data_id = message.body["data_id"]
        row = await self._pool.fetchrow(
            "SELECT content_text, deleted_at FROM data_items WHERE data_id = $1", data_id
        )
        if row is None or row["deleted_at"] is not None or row["content_text"] is None:
            return  # deleted, or not yet downloaded -- W2's job, not this one

        chunks = chunk_text(
            row["content_text"],
            max_chars=self._settings.chunk_chars,
            overlap=self._settings.chunk_overlap,
        )
        if not chunks:
            return

        vectors = await self._embedder.embed([c.text for c in chunks])
        if len(vectors) != len(chunks):
            raise RuntimeError("engine returned a different number of vectors than chunks")

        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("DELETE FROM chunks WHERE data_id = $1", data_id)
            for chunk, vector in zip(chunks, vectors, strict=True):
                chunk_id = new_id("chk")
                await conn.execute(
                    """
                    INSERT INTO chunks (chunk_id, data_id, ordinal, text, span_start, span_end)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    chunk_id,
                    data_id,
                    chunk.ordinal,
                    chunk.text,
                    chunk.span_start,
                    chunk.span_end,
                )
                await conn.execute(
                    """
                    INSERT INTO embeddings (embedding_id, chunk_id, data_id, model_id,
                                            dim, generator_version, embedding)
                    VALUES ($1, $2, $3, $4, $5, $6, $7::vector)
                    """,
                    new_id("emb"),
                    chunk_id,
                    data_id,
                    self._embedder.model_id,
                    self._embedder.dim,
                    self.generator_version,
                    vector_literal(vector),
                )
            await conn.execute(
                """
                UPDATE data_items SET state = 'searchable', updated_at = now()
                WHERE data_id = $1 AND state = 'stored'
                """,
                data_id,
            )
        if self._queue is not None:
            await self._queue.publish(ENRICH_TOPIC, {"data_id": data_id})


class EnrichWorker:
    """`searchable` -> `enriched`.

    Produces the core envelope as an `artifacts` row with `artifact_sources`
    carrying span offsets, which is what lets a citation open at the sentence
    and keeps citations working after compression archives the original.

    The artifact inherits the **strictest ACL among its sources**. A summary is
    not a new, unencumbered object because a model wrote it -- it contains the
    content of everything it read.
    """

    def __init__(self, pool: asyncpg.Pool, extractor: Extractor, settings: Settings) -> None:
        self._pool = pool
        self._extractor = extractor
        self._settings = settings
        self.generator_version = generator_version(
            purpose=EXTRACT_PURPOSE,
            model_id=extractor.model_id,
            spec={"envelope": "core-v1"},
        )

    def register(self, queue: Queue, topic: str = ENRICH_TOPIC) -> None:
        queue.subscribe(topic, self.handle)

    async def ensure_generator(self) -> None:
        await self._pool.execute(
            """
            INSERT INTO generators (generator_version, purpose, model_id, spec)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (generator_version) DO NOTHING
            """,
            self.generator_version,
            EXTRACT_PURPOSE,
            self._extractor.model_id,
            {"envelope": "core-v1"},
        )

    async def handle(self, message: Message) -> None:
        data_id = message.body["data_id"]
        row = await self._pool.fetchrow(
            """
            SELECT org_id, project_id, owner_id, content_text, data_type,
                   access_level, shared_with, deleted_at
            FROM data_items WHERE data_id = $1
            """,
            data_id,
        )
        if row is None or row["deleted_at"] is not None or row["content_text"] is None:
            return

        envelope = await self._extractor.extract(
            row["content_text"], data_type=row["data_type"] or "unknown"
        )

        # One source here, but the rule is written for the general case: an
        # artifact spanning mixed-ACL sources takes the intersection.
        acl = strictest([Acl(row["access_level"], list(row["shared_with"]))])

        async with self._pool.acquire() as conn, conn.transaction():
            # Idempotent under redelivery: one artifact per (item, generator).
            await conn.execute(
                """
                DELETE FROM artifacts a USING artifact_sources s
                WHERE s.artifact_id = a.artifact_id AND s.data_id = $1
                  AND a.generator_version = $2
                """,
                data_id,
                self.generator_version,
            )
            artifact_id = new_id("art")
            await conn.execute(
                """
                INSERT INTO artifacts (artifact_id, org_id, project_id, owner_id,
                    kind, title, description, summary, keywords, language, fields,
                    model_id, generator_version, served_by_model, fallback_depth,
                    access_level, shared_with)
                VALUES ($1, $2, $3, $4, 'envelope', $5, $6, $7, $8, $9, $10, $11,
                        $12, $13, 0, $14, $15)
                """,
                artifact_id,
                row["org_id"],
                row["project_id"],
                row["owner_id"],
                envelope.title,
                envelope.description,
                envelope.summary,
                envelope.keywords,
                envelope.language,
                envelope.fields,
                self._extractor.model_id,
                self.generator_version,
                # No router in front of the extractor yet, so the model that was
                # assigned is the model that served it. The column exists so the
                # day that stops being true, the artifact says so.
                self._extractor.model_id,
                acl.access_level,
                acl.shared_with,
            )
            await conn.execute(
                """
                INSERT INTO artifact_sources (artifact_id, data_id, span_start, span_end)
                VALUES ($1, $2, 0, $3)
                """,
                artifact_id,
                data_id,
                len(row["content_text"]),
            )
            await conn.execute(
                "UPDATE data_items SET state = 'enriched', updated_at = now() WHERE data_id = $1",
                data_id,
            )
