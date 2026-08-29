"""`POST /api/v1/write` -- the one write endpoint.

Earlier designs had four write paths, which meant four sets of admission
control, four places to derive an ACL and four ways to be disabled. Two
abstractions collapse them: `ContentRef` unifies the payload, and the producer
unifies the caller.

The write is synchronous and the enrichment is not. **This commits.** The item is
durable and has an id before the response returns; what is queued is the work
that makes it findable.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

import asyncpg

from . import acl as acl_mod
from .audit import record_audit
from .auth import ADMIN, DATA_WRITE, Principal
from .blobs import BlobStore
from .classify import classify, sniff_mime
from .config import Settings
from .contracts import (
    EnrichmentOptions,
    Inline,
    Pending,
    Stored,
    WriteItem,
    WriteRequest,
    WriteOptions,
    WriteResponse,
    WriteResult,
)
from .events import emit, emit_audited
from .ids import new_id
from . import normalize
from .cases import route_case
from .memories import route_write
from .workers import record_version
from .queue import Queue
from .telemetry import record, span

EMBED_TOPIC = "embed"
PARSE_TOPIC = "parse"


class AdmissionError(Exception):
    """Rejecting a write is cheap. Accepting one you cannot process is not."""

    def __init__(self, message: str, status: int = 400, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


@dataclass(frozen=True)
class ProducerRow:
    producer_id: str
    type: str
    user_id: str
    org_id: str
    project_id: str
    connection_id: str | None
    connection_scope: str | None
    status: str
    inbound_auth: str
    api_key_id: str | None
    defaults: dict


async def load_producer(conn: asyncpg.Connection, producer_id: str) -> ProducerRow | None:
    row = await conn.fetchrow(
        """
        SELECT p.producer_id, p.type, p.user_id, p.org_id, p.project_id,
               p.connection_id, p.status, p.inbound_auth, p.api_key_id, p.defaults,
               c.scope AS connection_scope
        FROM producers p
        LEFT JOIN connections c ON c.connection_id = p.connection_id
        WHERE p.producer_id = $1
        """,
        producer_id,
    )
    return ProducerRow(**dict(row)) if row else None


def _request_hash(request: WriteRequest) -> str:
    payload = request.model_dump(mode="json", exclude_none=True)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _payload_bytes(item: WriteItem) -> int:
    content = item.content
    if isinstance(content, Inline):
        if content.text is not None:
            return len(content.text.encode())
        return len(content.bytes_b64 or "") * 3 // 4
    if isinstance(content, Stored):
        return content.size or 0
    return 0


async def _admit(
    conn: asyncpg.Connection,
    queue: Queue,
    settings: Settings,
    principal: Principal,
    request: WriteRequest,
) -> ProducerRow:
    """One place: credential, producer, caps, quota, backlog."""
    principal.require(DATA_WRITE)

    producer = await load_producer(conn, request.producer_id)
    if producer is None or producer.org_id != principal.org_id:
        # Same response for missing and cross-tenant: a producer id is not an
        # oracle for what exists in another organization.
        raise AdmissionError("unknown producer", status=404)
    # Who may write through a producer is decided by the connection it carries,
    # not by who registered it. A producer bound to a *personal* connection is
    # that person's -- writing through it would attribute data to their
    # mailbox or drive. A producer with a shared connection, or none at all, is
    # a project-level entry point any member may use, and tying it to its
    # registrant makes a shared producer unusable by everyone else.
    personal = producer.connection_scope == "personal"
    if personal and producer.user_id != principal.user_id and not principal.can(ADMIN):
        raise AdmissionError(
            "this producer is bound to another user's personal connection", status=403
        )
    if producer.inbound_auth == "api_key" and producer.api_key_id:
        if principal.key_id != producer.api_key_id:
            raise AdmissionError("credential is not the producer\'s configured key", status=403)

    if len(request.items) == 0:
        raise AdmissionError("items must not be empty")
    if len(request.items) > settings.max_items_per_write:
        raise AdmissionError(
            f"item count exceeds {settings.max_items_per_write}", status=413
        )
    total = sum(_payload_bytes(i) for i in request.items)
    if total > settings.max_payload_bytes:
        raise AdmissionError("payload exceeds the configured maximum", status=413)

    if await queue.depth() > settings.max_queue_depth:
        raise AdmissionError("enrichment backlog is deep", status=429, retry_after=30)

    return producer


async def write_items(
    pool: asyncpg.Pool,
    queue: Queue,
    blobs: BlobStore,
    settings: Settings,
    principal: Principal,
    request: WriteRequest,
    idempotency_key: str | None = None,
) -> WriteResponse:
    with span("write", producer_id=request.producer_id, items=len(request.items)):
        return await _write(pool, queue, blobs, settings, principal, request, idempotency_key)


async def _write(pool, queue, blobs, settings, principal, request, idempotency_key):
    async with pool.acquire() as conn:
        producer = await _admit(conn, queue, settings, principal, request)

        if idempotency_key:
            replay = await conn.fetchrow(
                "SELECT request_hash, response FROM idempotency_keys WHERE producer_id = $1 AND key = $2",
                producer.producer_id,
                idempotency_key,
            )
            if replay is not None:
                if replay["request_hash"] != _request_hash(request):
                    raise AdmissionError(
                        "idempotency key reused with a different payload", status=409
                    )
                return WriteResponse(**replay["response"])

        # A disabled producer accepts and drops, uniformly -- the same shape as
        # a successful write, so a caller cannot distinguish "off" from "broken".
        if producer.status != "enabled":
            response = WriteResponse(
                accepted=0,
                failed=0,
                results=[
                    WriteResult(index=i, status="dropped", error="producer is not enabled")
                    for i in range(len(request.items))
                ],
            )
            await record_audit(
                conn,
                principal,
                action="write.dropped",
                project_id=producer.project_id,
                target_type="producer",
                target_id=producer.producer_id,
                detail={"items": len(request.items), "status": producer.status},
            )
            return response

        results: list[WriteResult] = []
        for index, item in enumerate(request.items):
            try:
                # Each item is its own transaction: one bad item in a batch of
                # five hundred must not roll back the other four hundred and
                # ninety-nine. That is what 207 is for.
                async with conn.transaction():
                    (
                        data_id, created, downloaded, has_text, memories, case_ids, events
                    ) = await _write_one(
                        conn, blobs, principal, producer, item, request.options
                    )
                results.append(
                    WriteResult(
                        index=index,
                        status="created" if created else "updated",
                        data_id=data_id,
                        state="stored",
                        is_downloaded=downloaded,
                        memories=memories,
                        cases=case_ids,
                        events=events,
                    )
                )
            except Exception as exc:  # noqa: BLE001 -- reported per item, not raised
                results.append(WriteResult(index=index, status="failed", error=str(exc)))

        await conn.execute(
            "UPDATE producers SET last_item_at = now() WHERE producer_id = $1",
            producer.producer_id,
        )

        response = WriteResponse(
            accepted=sum(1 for r in results if r.status in ("created", "updated")),
            failed=sum(1 for r in results if r.status == "failed"),
            results=results,
        )
        if idempotency_key:
            await conn.execute(
                """
                INSERT INTO idempotency_keys (key, producer_id, request_hash, response)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT DO NOTHING
                """,
                idempotency_key,
                producer.producer_id,
                _request_hash(request),
                response.model_dump(mode="json"),
            )

    # Published after commit: a job that arrives before its row is a race the
    # worker would have to defend against forever.
    record("items_written", response.accepted, producer_type=producer.type)

    # Dispatch happens after the transaction commits, and reads the log rather
    # than a list held in memory -- so a process that dies here loses nothing.
    # The events are already durable; only their delivery is pending.
    from .events import dispatch_pending

    await dispatch_pending(pool, queue)
    return response


async def _write_one(
    conn: asyncpg.Connection,
    blobs: BlobStore,
    principal: Principal,
    producer: ProducerRow,
    item: WriteItem,
    options: "WriteOptions",
) -> tuple[str, bool, bool, bool, list[str], list[str], list[str]]:
    content = item.content
    data_id = new_id("data")
    content_text = storage_ref = checksum = None
    pending_ref = None
    size_bytes = None
    payload: bytes | None = None
    payload_keys: list[str] | None = None

    if isinstance(content, Inline):
        if content.text is not None:
            content_text = content.text
            size_bytes = len(content.text.encode())
            try:
                parsed = json.loads(content.text)
                payload_keys = list(parsed) if isinstance(parsed, dict) else None
            except (ValueError, TypeError):
                payload_keys = None
        else:
            payload = base64.b64decode(content.bytes_b64 or "")
            size_bytes = len(payload)
    elif isinstance(content, Stored):
        storage_ref = content.storage_ref
        size_bytes = content.size
        checksum = content.checksum
    else:
        assert isinstance(content, Pending)
        pending_ref = content.model_dump(mode="json", exclude={"kind"})

    # MIME is sniffed from the bytes; the declared type is only ever a hint.
    declared = getattr(content, "mime_type", None)
    mime_type = sniff_mime(payload, content_text, declared)

    if payload is not None:
        storage_ref, checksum = await blobs.put(
            org_id=producer.org_id,
            project_id=producer.project_id,
            data_id=data_id,
            kind="raw",
            payload=payload,
            mime_type=mime_type,
        )
        # The bytes stay in the blob store whatever they are -- the parser
        # reads them from there, so the raw original always survives its own
        # extraction and can be re-parsed by a better handler later.

    data_type, layer = classify(
        explicit_data_type=item.data_type,
        source_type=item.source_type,
        mime_type=mime_type,
        payload_keys=payload_keys,
        external_id=item.external_id,
    )

    # Sealed. This runs before any customizable phase, and no caller-supplied
    # metadata reaches it.
    requested = item.access
    assigned = acl_mod.acl_for_write(
        connection_scope=producer.connection_scope,
        requested_level=requested.level if requested else None,
        requested_principals=requested.principals if requested else None,
    )

    event_time = item.event_time or datetime.now(timezone.utc)
    row = await conn.fetchrow(
        """
        INSERT INTO data_items (
            data_id, org_id, project_id, producer_id, connection_id, owner_id,
            external_id, access_level, shared_with, content_text, storage_ref,
            pending_ref, mime_type, source_type, data_type, classified_by_layer,
            size_bytes, checksum, event_time, state, identifiers, tags)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15,
                $16, $17, $18, $19, 'stored', $20, $21)
        ON CONFLICT (project_id, producer_id, external_id) DO UPDATE SET
            content_text = EXCLUDED.content_text,
            storage_ref = EXCLUDED.storage_ref,
            pending_ref = EXCLUDED.pending_ref,
            mime_type = EXCLUDED.mime_type,
            source_type = EXCLUDED.source_type,
            data_type = EXCLUDED.data_type,
            classified_by_layer = EXCLUDED.classified_by_layer,
            size_bytes = EXCLUDED.size_bytes,
            checksum = EXCLUDED.checksum,
            event_time = EXCLUDED.event_time,
            access_level = EXCLUDED.access_level,
            shared_with = EXCLUDED.shared_with,
            identifiers = EXCLUDED.identifiers,
            tags = EXCLUDED.tags,
            state = 'stored',
            updated_at = now()
        RETURNING data_id, (xmax = 0) AS created
        """,
        data_id,
        producer.org_id,
        producer.project_id,
        producer.producer_id,
        producer.connection_id,
        # The owner is whoever authenticated, not whoever registered the
        # producer. They are the same person for a webhook or a crawler -- but
        # for a shared client producer they are not, and using the producer's
        # owner means a second person writing through it cannot see their own
        # private items. Nothing writes anonymously, so there is always a
        # principal to attribute to.
        principal.user_id,
        item.external_id,
        assigned.access_level,
        assigned.shared_with,
        content_text,
        storage_ref,
        pending_ref,
        mime_type,
        item.source_type,
        data_type,
        layer,
        size_bytes,
        checksum,
        event_time,
        item.identifiers,
        item.tags,
    )
    data_id, created = row["data_id"], row["created"]

    if not created:
        # Content may have changed under the same natural key. Dropping the
        # derived rows is what stops a stale vector outliving its source.
        await conn.execute("DELETE FROM chunks WHERE data_id = $1", data_id)

    # The deterministic projection, before correlation rather than after it.
    # Normalization is where `identifiers` comes from for a structured record --
    # the writer sends a payload, not a list of keys -- so running it afterwards
    # would correlate on the identifiers the caller happened to restate and miss
    # the ones the schema exists to extract.
    #
    # No schema registered for this project means no projection and nothing
    # written, which is the ordinary case.
    identifiers = list(item.identifiers)
    if content_text is not None:
        projected = await normalize.project(
            conn,
            data_id=data_id,
            project_id=producer.project_id,
            text=content_text,
            data_type=data_type,
        )
        if projected:
            identifiers = projected["merged_identifiers"] or identifiers

    # Subject correlation, alongside lifecycle routing. An explicit `case` is
    # asserted; identifier matches are inferred and record what they matched on.
    case_ids = await route_case(
        conn,
        org_id=producer.org_id,
        project_id=producer.project_id,
        data_id=data_id,
        case_type=item.case.case_type if item.case else None,
        external_id=item.case.external_id if item.case else None,
        identifiers=identifiers,
    )

    memories = await route_write(
        conn,
        org_id=producer.org_id,
        project_id=producer.project_id,
        data_id=data_id,
        owner_id=principal.user_id,
        connection_scope=producer.connection_scope,
        requested_type=item.memory.type if item.memory else None,
        requested_key=item.memory.key if item.memory else None,
    )

    # The write is revision 1. A later parse or interpretation appends; nothing
    # is mutated in place, so "why does this say something different than last
    # week" always has an answer.
    await record_version(
        conn,
        data_id,
        source="write",
        content_text=content_text,
        mime_type=mime_type,
        detail={"external_id": item.external_id, "producer_id": producer.producer_id},
    )

    # Event one: the data exists. Always, in this transaction, regardless of
    # what anyone wants done with it afterwards.
    recorded = await emit_audited(
        conn, principal,
        event_type="data.recorded",
        org_id=producer.org_id, project_id=producer.project_id, data_id=data_id,
        payload={
            "external_id": item.external_id,
            "created": created,
            "has_text": content_text is not None,
            "has_bytes": storage_ref is not None,
            "pending_fetch": pending_ref is not None,
            "mime_type": mime_type,
            "data_type": data_type,
        },
    )
    events = [recorded]

    # Event two: only if asked. `caused_by` is what stops it being processed
    # before the data it refers to.
    enrichment: EnrichmentOptions = options.enrichment

    # A Pending ref has no bytes yet, so a fetch has to happen first. Making the
    # fetch the *cause* of the enrichment reuses the ordering gate rather than
    # inventing a second mechanism: enrichment cannot start until the bytes are
    # actually here.
    fetch_event = None
    if pending_ref is not None:
        fetch_event = await emit_audited(
            conn, principal,
            event_type="fetch.requested",
            org_id=producer.org_id, project_id=producer.project_id, data_id=data_id,
            caused_by=recorded,
            payload={"provider": pending_ref.get("provider"),
                     "resource_id": pending_ref.get("resource_id")},
        )
        events.append(fetch_event)

    if options.enrich:
        events.append(await emit_audited(
            conn, principal,
            event_type="enrichment.requested",
            org_id=producer.org_id, project_id=producer.project_id, data_id=data_id,
            caused_by=fetch_event or recorded,
            payload={
                "embed": enrichment.embed,
                "summarize": enrichment.summarize,
                "prompt_override": enrichment.prompt,
                "model_override": enrichment.model_id,
                "needs_parse": content_text is None,
            },
        ))

    # Emitted with no consumer. The graph is not built yet, and a graph that
    # begins on the day someone writes the consumer has lost everything before
    # it -- so the intent is recorded now and drained later.
    events.append(await emit(
        conn,
        event_type="graph.build.requested",
        org_id=producer.org_id, project_id=producer.project_id, data_id=data_id,
        caused_by=recorded,
        payload={"data_type": data_type, "identifiers": item.identifiers},
        actor_user_id=principal.user_id, actor_key_id=principal.key_id,
    ))

    await record_audit(
        conn,
        principal,
        action="write.created" if created else "write.updated",
        project_id=producer.project_id,
        target_type="data_item",
        target_id=data_id,
        detail={
            "producer_id": producer.producer_id,
            "external_id": item.external_id,
            "access_level": assigned.access_level,
            "data_type": data_type,
            "classified_by_layer": layer,
        },
    )
    return (data_id, created, pending_ref is None, content_text is not None,
            memories, case_ids, events)
