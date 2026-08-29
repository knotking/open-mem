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
from datetime import datetime, timezone

import asyncpg

from .chunking import chunk_text
from .config import Settings
from .db import vector_literal
from .ids import new_id
from .acl import Acl, strictest
from .entities import resolve_mentions
from .extraction import EXTRACT_PURPOSE, Extractor
from .inference import EmbeddingEngine, generator_version
from .telemetry import continue_trace, record, span
from .queue import Message, Queue

EMBED_PURPOSE = "embedding"
ENRICH_TOPIC = "enrich"
PARSE_TOPIC = "parse"

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
        with continue_trace("embed.message", message.headers, data_id=data_id):
            await self._embed(data_id)

    async def _embed(self, data_id: str) -> None:
        # The span lives here rather than in handle(), because the event worker
        # calls this directly -- and a step that is only traced on one of its
        # two call paths is worse than not tracing it at all.
        with span("embed", data_id=data_id, model_id=self._embedder.model_id):
            await self._embed_inner(data_id)

    async def _embed_inner(self, data_id: str) -> None:
        row = await self._pool.fetchrow(
            "SELECT indexable_text, deleted_at, ingested_at FROM data_items WHERE data_id = $1",
            data_id,
        )
        if row is None or row["deleted_at"] is not None or row["indexable_text"] is None:
            return  # deleted, or not yet downloaded -- W2's job, not this one

        chunks = chunk_text(
            row["indexable_text"],
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
        record(
            "ingest_to_searchable",
            (datetime.now(timezone.utc) - row["ingested_at"]).total_seconds(),
            model_id=self._embedder.model_id,
        )
        # Deliberately no chain to enrichment. The enrichment *event* decides
        # whether to summarise, and chaining here would run it a second time --
        # and would also summarise for callers who asked only to embed.


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
        with continue_trace("enrich.message", message.headers, data_id=data_id):
            await self._enrich(data_id)

    async def _enrich(
        self,
        data_id: str,
        *,
        prompt_override: str | None = None,
        model_override: str | None = None,
    ) -> None:
        with span("enrich", data_id=data_id, model_id=self._extractor.model_id):
            await self._enrich_inner(
                data_id, prompt_override=prompt_override, model_override=model_override
            )

    async def _enrich_inner(
        self,
        data_id: str,
        *,
        prompt_override: str | None = None,
        model_override: str | None = None,
    ) -> None:
        row = await self._pool.fetchrow(
            """
            SELECT org_id, project_id, owner_id, indexable_text, data_type,
                   access_level, shared_with, deleted_at, ingested_at
            FROM data_items WHERE data_id = $1
            """,
            data_id,
        )
        if row is None or row["deleted_at"] is not None or row["indexable_text"] is None:
            return

        # A per-request override applies to this call only and is never
        # persisted as configuration: an override that quietly became the
        # default would change a project's behaviour with no audit trail on the
        # setting that appears to control it.
        extractor = self._extractor
        if model_override and hasattr(extractor, "model_id"):
            import copy

            extractor = copy.copy(extractor)
            extractor.model_id = model_override

        data_type = row["data_type"] or "unknown"
        if prompt_override:
            import memdog.prompts as prompt_module

            original = prompt_module.BY_DATA_TYPE.get(data_type)
            prompt_module.BY_DATA_TYPE[data_type] = prompt_override
            try:
                envelope = await extractor.extract(row["indexable_text"], data_type=data_type)
            finally:
                if original is None:
                    prompt_module.BY_DATA_TYPE.pop(data_type, None)
                else:
                    prompt_module.BY_DATA_TYPE[data_type] = original
        else:
            envelope = await extractor.extract(row["indexable_text"], data_type=data_type)

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
                    access_level, shared_with, model_version, response_id)
                VALUES ($1, $2, $3, $4, 'envelope', $5, $6, $7, $8, $9, $10, $11,
                        $12, $13, $18, $14, $15, $16, $17)
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
                extractor.model_id,
                self.generator_version,
                # The chain may have served this from a fallback, in which
                # case the assigned model and the serving one differ -- which
                # is the whole reason these are two columns.
                envelope.fields.get("served_by_engine") or extractor.model_id,
                acl.access_level,
                acl.shared_with,
                envelope.model_version,
                envelope.response_id,
                # The real depth, not zero. An artifact produced by a fallback
                # is stamped with the primary's generator_version -- because
                # the prompt and schema really were the primary's -- so this
                # column is the only thing that distinguishes a degraded
                # artifact from a good one, and the reconciler needs it to know
                # there is anything to come back for.
                int(envelope.fields.get("fallback_depth") or 0),
            )
            await conn.execute(
                """
                INSERT INTO artifact_sources (artifact_id, data_id, span_start, span_end)
                VALUES ($1, $2, 0, $3)
                """,
                artifact_id,
                data_id,
                len(row["indexable_text"]),
            )
            # Resolved in the same transaction as the artifact. An item that is
            # enriched but whose entities were not recorded, or the reverse, is
            # a state nothing downstream can reason about.
            await resolve_mentions(
                conn,
                data_id=data_id,
                org_id=row["org_id"],
                project_id=row["project_id"],
                candidates=envelope.entities,
                generator_version=self.generator_version,
            )
            await conn.execute(
                "UPDATE data_items SET state = 'enriched', updated_at = now() WHERE data_id = $1",
                data_id,
            )
        record(
            "ingest_to_enriched",
            (datetime.now(timezone.utc) - row["ingested_at"]).total_seconds(),
            model_id=extractor.model_id,
        )


async def record_version(
    conn,
    data_id: str,
    *,
    source: str,
    content_text: str | None,
    mime_type: str | None = None,
    model_id: str | None = None,
    generator_version: str | None = None,
    tokens: int = 0,
    model_version: str | None = None,
    response_id: str | None = None,
    detail: dict | None = None,
    attempts: int = 5,
) -> int:
    """Append a revision. Never overwrite one, and never silently drop one.

    Revision numbers come from the table rather than a counter on the row. Two
    writers can still compute the same next number, and the unique constraint
    catches it -- so the conflict is **retried**, not swallowed. `ON CONFLICT DO
    NOTHING` here would turn a race into a missing revision, which is the one
    failure a version history cannot have: it looks exactly like the change
    never happened.
    """
    import hashlib

    checksum = "sha256:" + hashlib.sha256((content_text or "").encode()).hexdigest()
    for attempt in range(attempts):
        try:
            # A savepoint, because a unique violation aborts the surrounding
            # transaction: without this the retry would run inside a failed
            # transaction and fail differently.
            async with conn.transaction():
                return await conn.fetchval(
                """
                    INSERT INTO data_versions (version_id, data_id, revision, source,
                        content_text, content_chars, checksum, mime_type, model_id,
                        generator_version, tokens, detail, model_version, response_id)
                    SELECT $1, $2,
                           coalesce(max(revision), 0) + 1,
                           $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13
                    FROM data_versions WHERE data_id = $2
                    RETURNING revision
                    """,
                    new_id("ver"), data_id, source, content_text,
                    len(content_text) if content_text else 0, checksum, mime_type,
                    model_id, generator_version, tokens, detail or {},
                    model_version, response_id,
                )
        except asyncpg.UniqueViolationError:
            if attempt == attempts - 1:
                raise
            # Someone else took that number. Recompute and try again.
            continue
    raise RuntimeError("unreachable")


class ParseWorker:
    """Bytes to text, so the rest of the spine can do its job.

    This sits *below* embedding on the staircase: an item with a storage_ref and
    no text cannot be chunked, so it cannot be searchable. Parsing is what
    promotes it.

    Failures here are terminal by design. A password-protected PDF will not
    become readable on the fourth attempt, so the reason is recorded on the row
    and the message is acknowledged rather than retried -- retrying a file that
    can never parse is a busy loop wearing a failure's clothes.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        blobs,
        queue: Queue | None = None,
        multimodal=None,
    ) -> None:
        self._pool = pool
        self._blobs = blobs
        self._queue = queue
        # Media and scanned pages route here. Absent, they store with a reason.
        self._multimodal = multimodal

    def register(self, queue: Queue, topic: str = PARSE_TOPIC) -> None:
        queue.subscribe(topic, self.handle)

    async def handle(self, message: Message) -> None:
        data_id = message.body["data_id"]
        with continue_trace("parse", message.headers, data_id=data_id):
            await self._parse(data_id)

    async def _parse(self, data_id: str) -> None:
        from .parsers import NeedsModel, ParseFailed, parse

        row = await self._pool.fetchrow(
            """
            SELECT storage_ref, mime_type, external_id, content_text,
                   extracted_text, deleted_at
            FROM data_items WHERE data_id = $1
            """,
            data_id,
        )
        if row is None or row["deleted_at"] is not None or row["storage_ref"] is None:
            return
        if row["content_text"] is not None or row["extracted_text"] is not None:
            return  # already text, or already parsed; redelivery

        payload = await self._blobs.get(row["storage_ref"])
        mime = row["mime_type"] or ""
        model_id = generator = model_version = response_id = None
        tokens = 0
        try:
            parsed = parse(payload, mime=mime, name=row["external_id"] or "")
        except NeedsModel as exc:
            # An image, a recording, or a scanned page. Whether this is a dead
            # end or a transcript depends entirely on whether the deployment
            # has been given a model and permission to spend on it.
            interpreted = await self._interpret(data_id, payload, mime, exc.capability)
            if interpreted is None:
                return
            parsed, model_id, generator, tokens, model_version, response_id = interpreted
        except ParseFailed as exc:
            await self._record(data_id, exc.code, {"reason": str(exc)})
            return

        status = "truncated" if parsed.truncated else "parsed"
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                UPDATE data_items
                SET extracted_text = $2, parse_status = $3, parse_detail = $4,
                    updated_at = now()
                WHERE data_id = $1
                """,
                data_id,
                parsed.text,
                status,
                {"tier": parsed.tier, "structure": parsed.structure,
                 "warnings": parsed.warnings},
            )
            await record_version(
                conn,
                data_id,
                source="interpret" if model_id else "parse",
                content_text=parsed.text,
                mime_type=mime,
                model_id=model_id,
                generator_version=generator,
                tokens=tokens,
                model_version=model_version,
                response_id=response_id,
                detail={"tier": parsed.tier, "structure": parsed.structure,
                        "warnings": parsed.warnings},
            )
        if self._queue is not None and parsed.text.strip():
            await self._queue.publish("embed", {"data_id": data_id})

    async def _interpret(self, data_id: str, payload: bytes, mime: str, capability: str):
        """Hand the bytes to a model, or record precisely why we did not."""
        from .multimodal import MediaDisabled, MediaTooLarge, QuotaExhausted, modality_for

        engine = self._multimodal

        # The org's setting governs, with the deployment's configuration as the
        # platform default. Previously the env var decided outright, which made
        # the setting decorative -- it reported a value that had no effect, and
        # an org could not decline the expensive tier.
        org_id = await self._pool.fetchval(
            "SELECT org_id FROM data_items WHERE data_id = $1", data_id
        )
        if org_id is not None:
            from .settings_store import resolve

            policy = await resolve(self._pool, "media_interpretation", org_id=org_id)
            if policy.source != "default" and not policy.value:
                await self._record(
                    data_id, "needs_model",
                    {"capability": capability,
                     "reason": f"media interpretation is disabled at {policy.source} scope"},
                )
                return None

        if engine is None or not getattr(engine, "enabled", False):
            await self._record(
                data_id, "needs_model",
                {"capability": capability,
                 "reason": "media interpretation is not enabled for this deployment"},
            )
            return None

        modality = "ocr" if capability == "ocr" else (modality_for(mime) or "image")

        # The assignment decides which model runs, resolved per request rather
        # than baked in at deploy time. Absent an assignment the deployment
        # default applies, which is why most installations never configure this.
        purpose = "transcription" if modality in ("audio", "video") else "vision"
        if org_id and hasattr(engine, "model_for"):
            from .models import resolve_model

            assignment = await resolve_model(
                self._pool, purpose=purpose, data_type=modality,
                org_id=org_id, default_model=engine.model_for(modality),
            )
            if assignment.source == "assignment":
                engine._per_modality[modality] = assignment.model_id

        try:
            result = await engine.interpret(payload, mime=mime, modality=modality)
        except QuotaExhausted as exc:
            # Leave the row untouched -- no parse_status -- so the reconciler
            # picks it up on a later sweep. Recording a status here would mark
            # it examined, and it has not been. The item is not lost; it is
            # waiting for quota.
            log.warning("deferring %s: provider quota exhausted (%s)", data_id, exc)
            return None
        except MediaTooLarge as exc:
            await self._record(data_id, "needs_model",
                               {"capability": capability, "reason": str(exc)})
            return None
        except MediaDisabled as exc:
            await self._record(data_id, "needs_model",
                               {"capability": capability, "reason": str(exc)})
            return None

        if not result.text.strip():
            # An empty transcript is a *correct* answer for a recording with no
            # speech, and it must not be dressed up as one. A general model
            # asked to transcribe a tone will invent a plausible conversation;
            # a purpose-built transcriber returns nothing. Recording the
            # emptiness is what keeps the corpus free of invented content.
            await self._record(
                data_id, "needs_model",
                {"capability": capability, "model_id": result.model_id,
                 "reason": "no interpretable content found in the media"},
            )
            return None

        from .parsers import Parsed

        parsed = Parsed(
            text=result.text,
            tier="A",
            structure={"modality": result.modality, **result.structure},
        ).capped()
        return (parsed, result.model_id, f"multimodal:{result.model_id}", result.tokens,
                result.model_version, result.response_id)

    async def _record(self, data_id: str, status: str, detail: dict) -> None:
        record("parse_failures", 1, status=status)
        await self._pool.execute(
            """
            UPDATE data_items SET parse_status = $2, parse_detail = $3, updated_at = now()
            WHERE data_id = $1
            """,
            data_id,
            status,
            detail,
        )


class EventWorker:
    """Consumes domain events and turns them into pipeline work.

    Two handlers, matching the two commitments a write makes:

    **`data.recorded`** does nothing but acknowledge. That is deliberate -- it
    exists so that `enrichment.requested`, which names it as its cause, has
    something definite to wait for. Recording is already complete when the event
    is written; the consumption marks it *observed*, which is what unblocks the
    dependent event.

    **`enrichment.requested`** runs the expensive part: parse bytes if needed,
    embed if asked, summarise if asked -- with whatever prompt and model the
    request named, for that request only.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        queue: Queue,
        *,
        parse_worker=None,
        embed_worker=None,
        enrich_worker=None,
        fetch_worker=None,
    ) -> None:
        self._pool = pool
        self._queue = queue
        self._parse = parse_worker
        self._embed = embed_worker
        self._enrich = enrich_worker
        # W2. Absent, a Pending item simply waits -- which is the correct state,
        # not a failure.
        self._fetch = fetch_worker

    def register(self, queue: Queue) -> None:
        queue.subscribe("record", self.handle_recorded)
        queue.subscribe("enrich_request", self.handle_enrichment)
        if self._fetch is not None:
            self._fetch.register(queue)

    async def handle_recorded(self, message: Message) -> None:
        from .events import dispatch_pending, mark_consumed

        # Nothing to do: the data is already durable. Marking it consumed is
        # what releases the enrichment event that names it.
        #
        # The dispatch happens *inside* the resumed trace, or the follow-on
        # event starts a new one -- and then the interesting question, how long
        # from write to searchable, has no single trace that answers it.
        with continue_trace("event.recorded", message.headers,
                            data_id=message.body.get("data_id")):
            await mark_consumed(self._pool, message.body["event_id"])
            await dispatch_pending(self._pool, self._queue)

    async def handle_enrichment(self, message: Message) -> None:
        from .events import mark_consumed, mark_failed

        event_id = message.body["event_id"]
        data_id = message.body["data_id"]
        payload = message.body.get("payload") or {}

        try:
            with continue_trace("enrichment", message.headers, data_id=data_id):
                # Bytes first: nothing can be embedded or summarised until
                # there is text, and for media that means a model call.
                if payload.get("needs_parse") and self._parse is not None:
                    await self._parse._parse(data_id)

                has_text = await self._pool.fetchval(
                    "SELECT indexable_text IS NOT NULL FROM data_items WHERE data_id = $1",
                    data_id,
                )
                if not has_text:
                    # Not a failure: an image with interpretation switched off,
                    # or a file awaiting quota. The reason is already on the row.
                    await mark_consumed(self._pool, event_id)
                    return

                if payload.get("embed", True) and self._embed is not None:
                    await self._embed._embed(data_id)
                if payload.get("summarize", True) and self._enrich is not None:
                    await self._enrich._enrich(
                        data_id,
                        prompt_override=payload.get("prompt_override"),
                        model_override=payload.get("model_override"),
                    )
            await mark_consumed(self._pool, event_id)
        except Exception as exc:  # noqa: BLE001 -- the event survives the failure
            from .events import mark_deferred
            from .multimodal import QuotaExhausted

            # A provider quota is a "come back later", not a defect. Treating
            # it as a failure walks a good request to `failed` within seconds.
            if isinstance(exc, QuotaExhausted) or "429" in str(exc):
                log.warning("deferring enrichment for %s: provider quota", data_id)
                await mark_deferred(self._pool, event_id, repr(exc))
                return

            log.error("enrichment failed for %s: %r", data_id, exc)
            await mark_failed(self._pool, event_id, repr(exc))
            raise
