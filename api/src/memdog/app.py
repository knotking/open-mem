"""HTTP surface for the spine.

Three endpoints, because three is what it takes to prove both halves of the
system are real: write an item, fetch it by id, find it by meaning. Everything
else in the design widens this.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .auth import DATA_READ, ApiKeyVerifier, AuthError, Principal, TokenVerifier
from .blobs import build_blob_store
from .config import load_settings
from .contracts import (
    AskRequest,
    AskResponse,
    RetrieveRequest,
    RetrieveResponse,
    WriteRequest,
    WriteResponse,
)
from .chat import ask, build_answerer
from .crawlers import CrawlerConfig, CrawlerError
from . import crawling
from . import entities as entities_mod
from . import graph as graph_mod
from .fetching import FetchError
from .graph import GraphError
from .entities import EntityError
from . import account, agents, cases, connections, connectors, control, memories as memories_mod, models, normalize, sharing
from .account import AccountError
from .agents import AgentConfigError
from .memories import MemoryError
from .models import ModelError
from .cases import CaseError
from .control import ControlError
from .sharing import ShareError
from .crypto import Envelope
from .db import create_pool, migrate
from .firebase import CompositeVerifier, FirebaseVerifier
from .deletion import DELETE_TOPIC, DeleteWorker, request_deletion, unpurged_tombstones
from .engines import EngineRegistry
from .inference import build_embedder
from .queue import InProcessQueue
from .reprocess import REPROCESS_TOPIC, ReprocessWorker, request_reprocess
from .webhooks import WebhookError, receive as receive_webhook
from .uploads import UploadError, authorise, complete as complete_upload, create_session
from .telemetry import setup as setup_telemetry
from .settings_store import SettingError, effective as effective_settings, put as put_setting, resolve as resolve_setting
from .retrieval import (
    NotFound,
    audit_trail,
    get_artifacts,
    get_item,
    get_versions,
    item_memories,
    list_items,
    list_memories,
    memory_members,
    project_overview,
    retrieve,
    staircase,
    stale_artifacts,
)
from .extraction import build_extractor
from .fetching import FetchWorker
from .multimodal import build_multimodal
from .events import dispatch_pending, emit, emit_audited, list_events
from .workers import (
    EmbedWorker,
    EnrichWorker,
    EventWorker,
    ParseWorker,
    verify_index_dimension,
)
from . import invites as invites_mod
from . import quota, usage
from .invites import InviteError
from .quota import BudgetExhausted, QuotaExceeded
from .write import EMBED_TOPIC, AdmissionError, write_items


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    setup_telemetry()
    pool = await create_pool(settings)
    await migrate(pool, settings)

    embedder = build_embedder(settings)
    # Refuse to serve against an index this engine cannot write to.
    await verify_index_dimension(pool, embedder)
    async def _record_dead_letter(message, error: str) -> None:
        """A job the queue gave up on, written where the item can show it.

        The queue's own list is in memory and dies with the process, which on a
        platform that scales to zero means an abandoned job leaves the record
        sitting at its current state with nothing anywhere saying why. The
        console already reads the event log to explain a stalled climb, so that
        is where this belongs.
        """
        data_id = (message.body or {}).get("data_id")
        if not data_id:
            return
        owner = await pool.fetchrow(
            "SELECT org_id, project_id FROM data_items WHERE data_id = $1", data_id
        )
        if owner is None:
            return
        async with pool.acquire() as conn, conn.transaction():
            await emit(
                conn,
                event_type="work.abandoned",
                org_id=owner["org_id"],
                project_id=owner["project_id"],
                data_id=data_id,
                payload={"topic": message.topic, "attempts": message.attempt,
                         "error": error[:600]},
            )

    queue = InProcessQueue(on_dead_letter=_record_dead_letter)
    embed_worker = EmbedWorker(pool, embedder, settings, queue=queue)
    await embed_worker.ensure_generator()
    embed_worker.register(queue, EMBED_TOPIC)

    app.state.blobs = build_blob_store(settings)
    DeleteWorker(pool, app.state.blobs).register(queue)
    ReprocessWorker(pool, queue).register(queue)

    multimodal = build_multimodal(settings)
    parse_worker = ParseWorker(
        pool, app.state.blobs, queue=queue, multimodal=multimodal
    )
    parse_worker.register(queue)

    extractor = build_extractor(settings)
    answerer = build_answerer(settings)
    enrich_worker = EnrichWorker(pool, extractor, settings)
    await enrich_worker.ensure_generator()
    enrich_worker.register(queue)

    # The event worker is what turns the log into work. The individual workers
    # stay subscribed to their own topics too, because the reconciler still
    # publishes to them directly when repairing a corpus.
    fetch_worker = FetchWorker(pool, app.state.blobs, settings, queue=queue,
                               envelope=Envelope.from_settings(settings))
    EventWorker(
        pool, queue,
        parse_worker=parse_worker, embed_worker=embed_worker,
        enrich_worker=enrich_worker, fetch_worker=fetch_worker,
        extractor=extractor,
    ).register(queue)
    # Wakes on a transition and then waits. The waiting is the design: a
    # consumer that evaluated one message at a time would be per-write
    # evaluation with a queue in front of it.
    from .alerts import AlertWorker

    app.state.alert_worker = AlertWorker(pool)
    app.state.alert_worker.register(queue)

    # Repo analysis. Registered whether or not a job is configured: without one
    # the worker marks the snapshot failed with that as the reason, which is a
    # far better outcome than a message with no consumer, where the snapshot
    # sits `pending` forever and reads as still running.
    from .repos import RepoAnalysisWorker

    RepoAnalysisWorker(pool, settings).register(queue)

    # The meter needs somewhere to write before the first model call, which
    # the workers above can make as soon as they are registered.
    usage.configure(pool)
    app.state.limiter = quota.Limiter(pool)

    app.state.settings = settings
    app.state.pool = pool
    app.state.queue = queue
    app.state.embedder = embedder
    app.state.extractor = extractor
    app.state.answerer = answerer
    app.state.graph = graph_mod.build_graph(pool, settings)
    app.state.crawl_worker = crawling.CrawlWorker(
        pool, queue, app.state.blobs, settings,
        envelope=Envelope.from_settings(settings),
    )
    app.state.multimodal = multimodal
    await models.ensure_catalog(pool)
    # Seed the platform-scope value from the deployment's configuration, so
    # `GET /settings/effective` reports what is actually happening rather than
    # a default the runtime is ignoring.
    await pool.execute(
        """
        INSERT INTO settings (setting_id, scope, scope_id, key, value, set_by)
        VALUES ('set_platform_media', 'platform', NULL, 'media_interpretation', $1, 'deployment')
        ON CONFLICT (scope, scope_id, key) DO UPDATE
            SET value = EXCLUDED.value, updated_at = now()
        """,
        settings.media_interpretation,
    )
    # What "current" means for staleness: the generator each purpose is
    # assigned right now.
    app.state.current_generators = {
        "embedding": embed_worker.generator_version,
        "extraction": enrich_worker.generator_version,
    }
    app.state.envelope = Envelope.from_settings(settings)
    # What turns `model_assignments` from a table into a control. The engines
    # above stay the deployment's default; this is how an org's own choice is
    # resolved per request, when it made one.
    app.state.engines = EngineRegistry(
        settings, app.state.envelope, extractor=extractor, answerer=answerer
    )
    enrich_worker.attach_registry(app.state.engines)
    # One seam, two credential shapes. A browser signs in and presents an
    # identity token; a script presents an API key. Both land on the same
    # Principal, so there is one authorization path rather than two.
    api_keys = ApiKeyVerifier(pool)
    firebase = (
        FirebaseVerifier(pool, settings.firebase_project_id)
        if settings.firebase_project_id
        else None
    )
    app.state.verifier: TokenVerifier = CompositeVerifier(api_keys, firebase)
    app.state.auth_modes = {
        "api_key": True,
        "identity_platform": firebase is not None,
        "project_id": settings.firebase_project_id or None,
    }
    try:
        yield
    finally:
        await queue.close()
        await pool.close()


app = FastAPI(title="mem-dog", version="0.1.0", lifespan=lifespan)


@app.exception_handler(AuthError)
async def _auth_error(request: Request, exc: AuthError) -> JSONResponse:
    """A permission failure is 401 or 403, from anywhere.

    `_control` already translated these for the control plane, but ten handlers
    call `actor.require()` in their own body and are not wrapped by it -- so a
    credential lacking a capability escaped as a 500. That is worse than an
    unhelpful status: it tells whoever is looking that the server is broken,
    when the truth is that their key cannot do this, and those two send you to
    completely different places.

    Registered on the app rather than fixed in ten handlers, because the
    eleventh would have reintroduced it.
    """
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


async def principal(
    request: Request,
    authorization: str = Header(default=""),
    x_api_key: str = Header(default="", alias="X-API-Key"),
) -> Principal:
    """The credential, from either header.

    `Authorization: Bearer` is the contract. `X-API-Key` exists because the
    platform in front of the service may own the Authorization header itself --
    Cloud Run IAM puts its own identity token there, and a request cannot carry
    two. This is not a second auth path: both land on the same verifier and the
    same principal.
    """
    token = x_api_key
    if not token:
        scheme, _, bearer = authorization.partition(" ")
        if scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="bearer credential required")
        token = bearer
    if not token:
        raise HTTPException(status_code=401, detail="bearer credential required")
    try:
        return await request.app.state.verifier.verify(token)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@asynccontextmanager
async def admitted(request: Request, actor: Principal, cost: int, *, project_id: str | None = None):
    """Charge a request's estimated cost, hold a concurrency slot, and name who
    pays for whatever it goes on to spend.

    The estimate is charged to the burst bucket, never to the budget: the budget
    is decremented by what the meter actually recorded, and charging both would
    bill every request twice. What the two share is the weighting -- a request is
    priced on the work it authorises, because a limiter that counts requests
    cannot tell a vector search from a generation.
    """
    state = request.app.state
    try:
        await state.limiter.charge(actor, cost)
        slot = await state.limiter.hold(actor)
    except QuotaExceeded as exc:
        raise HTTPException(
            status_code=exc.status, detail=str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    with slot, usage.attributed(
        org_id=actor.org_id,
        project_id=project_id or actor.project_id,
        user_id=actor.user_id,
    ):
        yield


# `/healthz` is served for local and Kubernetes use, but it is NOT the health
# path behind Google Front End: GFE intercepts exactly `/healthz` and answers
# 404 before the request reaches the container. Verified against Google's own
# hello image, where every path returns 200 except that one. The canonical path
# is therefore `/api/v1/health`.
@app.get("/api/v1/health")
@app.get("/healthz")
async def healthz(request: Request) -> dict:
    await request.app.state.pool.fetchval("SELECT 1")
    return {
        "status": "ok",
        "embed_model": request.app.state.embedder.model_id,
        "embed_dim": request.app.state.embedder.dim,
        "queue_depth": await request.app.state.queue.depth(),
        "auth": request.app.state.auth_modes,
        "media_interpretation": request.app.state.multimodal.enabled,
        # A cascade that quietly stopped looks like nothing at all without this.
        "unpurged_tombstones": await unpurged_tombstones(request.app.state.pool),
        "multimodal_model": request.app.state.multimodal.model_id,
    }


@app.post("/api/v1/write", response_model=WriteResponse, status_code=207)
async def write(
    request: Request,
    body: WriteRequest,
    actor: Principal = Depends(principal),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    state = request.app.state
    cost = quota.estimate_write(
        # `None` means the project's setting decides, which this cannot know
        # without a lookup it should not do on the hot path. Unspecified is
        # charged as expensive: for a burst limiter the conservative direction
        # is to assume the costly case, and under-charging is how a retry loop
        # gets through.
        items=len(body.items), enrich=body.options.enrich is not False,
    )
    try:
        async with admitted(request, actor, cost):
            response = await write_items(
                state.pool,
                state.queue,
                state.blobs,
                state.settings,
                actor,
                body,
                idempotency_key,
            )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except AdmissionError as exc:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        raise HTTPException(status_code=exc.status, detail=str(exc), headers=headers) from exc
    # Always 207: batch is not a separate verb, so it is not a separate status.
    return JSONResponse(status_code=207, content=response.model_dump(mode="json"))


@app.get("/api/v1/data/{data_id}")
async def read_item(
    request: Request, data_id: str, actor: Principal = Depends(principal)
) -> dict:
    try:
        row = await get_item(request.app.state.pool, actor, data_id)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    return {k: v for k, v in row.items()}


@app.get("/api/v1/data/{data_id}/artifacts")
async def read_artifacts(
    request: Request, data_id: str, actor: Principal = Depends(principal)
) -> dict:
    try:
        return {"artifacts": await get_artifacts(request.app.state.pool, actor, data_id)}
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/data/{data_id}/enrich")
async def request_enrichment(
    request: Request, data_id: str, body: dict, actor: Principal = Depends(principal)
) -> dict:
    """Ask for AI enrichment on data that already exists.

    This is the normal path, because enrichment is off by default: record now,
    decide later whether it is worth spending on. The request is an event, so it
    is ordered behind the data it refers to and audited like any other.
    """
    from .auth import DATA_WRITE

    state = request.app.state
    try:
        actor.require(DATA_WRITE)
        item = await get_item(state.pool, actor, data_id)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc

    async with state.pool.acquire() as conn, conn.transaction():
        event_id = await emit_audited(
            conn, actor,
            event_type="enrichment.requested",
            org_id=actor.org_id, project_id=item["project_id"], data_id=data_id,
            payload={
                "embed": bool(body.get("embed", True)),
                "summarize": bool(body.get("summarize", True)),
                "prompt_override": body.get("prompt"),
                "model_override": body.get("model_id"),
                "needs_parse": item["content_text"] is None and item["extracted_text"] is None,
                "requested_after_the_fact": True,
            },
        )
    await dispatch_pending(state.pool, state.queue)
    return {"event_id": event_id, "data_id": data_id, "status": "requested"}


@app.get("/api/v1/events")
async def get_events(
    request: Request,
    actor: Principal = Depends(principal),
    project_id: str | None = None,
    data_id: str | None = None,
    limit: int = 100,
) -> dict:
    """The log itself.

    Includes events nothing consumes yet — `graph.build.requested` sits at
    `no_consumer`, which is a state rather than a failure.
    """
    from .auth import DATA_READ

    actor.require(DATA_READ)
    return {
        "events": await list_events(
            request.app.state.pool, actor.org_id,
            project_id=project_id, data_id=data_id, limit=limit,
        )
    }


@app.get("/api/v1/data/{data_id}/content")
async def read_content(
    request: Request, data_id: str, actor: Principal = Depends(principal)
) -> Response:
    """The original bytes, back out again.

    Confidence in a memory layer is mostly the ability to check it: an item
    whose stored bytes you cannot see is one you have to take on trust. The ACL
    is applied first and the read is audited, exactly as for the text.

    Served inline with `Content-Disposition: inline` so a browser can render an
    image, play audio or scrub video — but with `X-Content-Type-Options:
    nosniff`, because serving user-supplied bytes under a type the browser
    guesses is how a stored file becomes stored script.
    """
    from .audit import record_access

    state = request.app.state
    try:
        item = await get_item(state.pool, actor, data_id)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc

    if not item["storage_ref"]:
        raise HTTPException(status_code=404, detail="this item has no stored bytes")

    payload = await state.blobs.get(item["storage_ref"])
    await record_access(
        state.pool, actor, action="content.read",
        project_id=item["project_id"], data_id=data_id,
    )
    return Response(
        content=payload,
        media_type=item["mime_type"] or "application/octet-stream",
        headers={
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@app.get("/api/v1/data/{data_id}/versions")
async def read_versions(
    request: Request, data_id: str, actor: Principal = Depends(principal)
) -> dict:
    try:
        return {"versions": await get_versions(request.app.state.pool, actor, data_id)}
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc


@app.get("/api/v1/data/{data_id}/versions/{version_id}")
async def one_version_endpoint(
    request: Request, data_id: str, version_id: str,
    actor: Principal = Depends(principal)
) -> dict:
    """One revision, with its text in full.

    The listing returns a 400-character preview on purpose — forty revisions of
    a long document is a response nobody wants, and most callers are choosing
    which one to read rather than reading all of them. This is the one they
    chose, and it carries its own access record because it discloses content.
    """
    from .retrieval import one_version

    row = await one_version(request.app.state.pool, actor, data_id, version_id)
    if not row:
        raise HTTPException(status_code=404, detail="revision not found")
    return row


@app.get("/api/v1/projects/{project_id}/data")
async def get_project_data(
    request: Request,
    project_id: str,
    actor: Principal = Depends(principal),
    limit: int = 50,
    before: str | None = None,
    state: str | None = None,
    include_archived: bool = False,
) -> dict:
    """Browse what is actually in a project.

    Without this the only ways to find an item are knowing its id or matching a
    search -- neither of which answers "what did I put in here?".
    """
    try:
        return await list_items(
            request.app.state.pool, actor, project_id,
            limit=limit, before=before, state=state,
            include_archived=include_archived,
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/projects/{project_id}/overview")
async def get_overview(
    request: Request, project_id: str, actor: Principal = Depends(principal)
) -> dict:
    """The numbers that answer "is this working?" in one call."""
    try:
        return await project_overview(
            request.app.state.pool, actor, project_id,
            embedder=request.app.state.embedder,
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/projects/{project_id}/staircase")
async def read_staircase(
    request: Request, project_id: str, actor: Principal = Depends(principal)
) -> dict:
    try:
        return await staircase(request.app.state.pool, actor, project_id)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/projects/{project_id}/memories")
async def read_memories(
    request: Request, project_id: str, actor: Principal = Depends(principal)
) -> dict:
    try:
        return {"memories": await list_memories(request.app.state.pool, actor, project_id)}
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/memories")
async def post_memory(request: Request, body: dict, actor: Principal = Depends(principal)) -> dict:
    """Create the container first, fill it deliberately.

    Writing an item with a `memory` key also creates one -- that is the
    producer's path. This is the person's.
    """
    return await _control(memories_mod.create_memory)(
        request.app.state.pool, actor,
        project_id=body.get("project_id", ""),
        type_name=body.get("type", "default"),
        memory_key=body.get("key"),
        title=body.get("title"),
    )


@app.post("/api/v1/memories/{memory_id}/members")
async def post_memory_members(
    request: Request, memory_id: str, body: dict, actor: Principal = Depends(principal)
) -> dict:
    return await _control(memories_mod.add_members)(
        request.app.state.pool, actor, memory_id, body.get("data_ids", [])
    )


@app.delete("/api/v1/memories/{memory_id}/members/{data_id}")
async def delete_memory_member(
    request: Request, memory_id: str, data_id: str, actor: Principal = Depends(principal)
) -> dict:
    """Unmapping is not deletion, and losing the last membership files the item
    in the applicable default rather than leaving it invisible."""
    return await _control(memories_mod.remove_member)(
        request.app.state.pool, actor, memory_id, data_id
    )


@app.patch("/api/v1/memories/{memory_id}")
async def patch_memory(
    request: Request, memory_id: str, body: dict, actor: Principal = Depends(principal)
) -> dict:
    return await _control(memories_mod.retype_memory)(
        request.app.state.pool, actor, memory_id, body.get("type", "default"),
        preview=bool(body.get("preview")),
    )


@app.delete("/api/v1/memories/{memory_id}")
async def delete_memory_endpoint(
    request: Request,
    memory_id: str,
    actor: Principal = Depends(principal),
    preview: bool = False,
) -> dict:
    """Delete a container, applying its type's expiry policy to the contents.

    Pass `?preview=true` first: the interesting number is how many members
    survive because another memory still holds them.
    """
    return await _control(memories_mod.delete_memory)(
        request.app.state.pool, actor, request.app.state.queue, memory_id, preview=preview
    )



@app.get("/api/v1/memories/{memory_id}/links")
async def get_memory_links(
    request: Request, memory_id: str, actor: Principal = Depends(principal)
) -> dict:
    """Both directions, kept apart -- the direction is the claim."""
    return await _control(memories_mod.links_for)(
        request.app.state.pool, actor, memory_id
    )


@app.post("/api/v1/memories/{memory_id}/links")
async def post_memory_link(
    request: Request, memory_id: str, body: dict,
    actor: Principal = Depends(principal),
) -> dict:
    return await _control(memories_mod.link)(
        request.app.state.pool, actor,
        from_memory=memory_id,
        to_memory=body.get("to_memory", ""),
        relation=body.get("relation", ""),
        created_by=body.get("created_by", "explicit"),
        confidence=body.get("confidence"),
    )


@app.delete("/api/v1/memories/{memory_id}/links")
async def delete_memory_link(
    request: Request, memory_id: str, to_memory: str, relation: str,
    actor: Principal = Depends(principal),
) -> dict:
    return await _control(memories_mod.unlink)(
        request.app.state.pool, actor,
        from_memory=memory_id, to_memory=to_memory, relation=relation,
    )


@app.get("/api/v1/projects/{project_id}/memory-types")
async def get_memory_types(
    request: Request, project_id: str, actor: Principal = Depends(principal)
) -> dict:
    return {"types": await memories_mod.list_types(request.app.state.pool, project_id)}


@app.post("/api/v1/projects/{project_id}/memory-types")
async def post_memory_type(
    request: Request, project_id: str, body: dict, actor: Principal = Depends(principal)
) -> dict:
    return await _control(memories_mod.create_type)(
        request.app.state.pool, actor, project_id=project_id,
        name=body.get("name", ""), ttl_seconds=body.get("ttl_seconds"),
        on_expiry=body.get("on_expiry", "orphan_delete"),
        checkpoints=bool(body.get("checkpoints")),
    )


@app.get("/api/v1/memories/{memory_id}/checkpoints")
async def read_checkpoints(
    request: Request, memory_id: str, actor: Principal = Depends(principal)
) -> dict:
    """One memory's timeline, newest first, with what changed at each point.

    A checkpoint whose check has not finished says so rather than appearing as
    one that found nothing -- for a change detector those are the two answers
    that must never look alike.
    """
    from .checkpoints import CheckpointError, timeline

    try:
        return {"checkpoints": await timeline(request.app.state.pool, actor, memory_id)}
    except (CheckpointError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/memories/{memory_id}/checkpoints/{checkpoint_id}/recheck")
async def recheck_checkpoint(
    request: Request, memory_id: str, checkpoint_id: str,
    actor: Principal = Depends(principal),
) -> dict:
    """Run one checkpoint's check again.

    The way out of `incomparable`: it re-describes the record at the *current*
    generator version, which is what makes the pair comparable again after a
    prompt or a model changed underneath a timeline.
    """
    from .checkpoints import CheckpointError, recheck

    try:
        return await recheck(request.app.state.pool, actor,
                             request.app.state.queue, checkpoint_id)
    except (CheckpointError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/memories/{memory_id}/enrich")
async def enrich_memory(
    request: Request, memory_id: str, body: dict | None = None,
    actor: Principal = Depends(principal),
) -> dict:
    """Enrich every member of a memory, on demand.

    Enrichment is opt-in and resolves off by default, which is right -- it costs
    money per record and a default that quietly bills people is the wrong
    default. A checkpoint timeline in particular is written without it: the
    change check reads the record's text and needs neither embeddings nor the
    graph.

    This is how that decision is reversed later. Per-item forcing already
    exists; a timeline with fifty entries makes doing it fifty times the reason
    nobody does it at all.
    """
    from .auth import DATA_WRITE

    state = request.app.state
    body = body or {}
    try:
        actor.require(DATA_WRITE)
        members = await memory_members(state.pool, actor, memory_id)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    # The same event `POST /data/{id}/enrich` emits, once per member, in one
    # transaction. Emitting the event rather than calling the workers is what
    # makes this resumable: the log is the record, so a request that outlives
    # the instance handling it is picked up by the reconciler.
    requested = []
    async with state.pool.acquire() as conn, conn.transaction():
        for member in members:
            item = await conn.fetchrow(
                "SELECT project_id, content_text, extracted_text FROM data_items "
                "WHERE data_id = $1 AND deleted_at IS NULL",
                member["data_id"])
            if item is None:
                continue
            await emit_audited(
                conn, actor,
                event_type="enrichment.requested",
                org_id=actor.org_id, project_id=item["project_id"],
                data_id=member["data_id"],
                payload={
                    "embed": bool(body.get("embed", True)),
                    "summarize": bool(body.get("summarize", True)),
                    "needs_parse": (item["content_text"] is None
                                    and item["extracted_text"] is None),
                    "requested_after_the_fact": True,
                },
            )
            requested.append(member["data_id"])
    await dispatch_pending(state.pool, state.queue)
    # The count, not just "requested". A memory whose members are all archived
    # returns zero, and that is a different outcome from fifty jobs queued.
    return {"memory_id": memory_id, "requested": len(requested)}


@app.get("/api/v1/memories/{memory_id}/members")
async def read_memory_members(
    request: Request, memory_id: str, actor: Principal = Depends(principal)
) -> dict:
    try:
        return {"members": await memory_members(request.app.state.pool, actor, memory_id)}
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/data/{data_id}/memories")
async def read_item_memories(
    request: Request, data_id: str, actor: Principal = Depends(principal)
) -> dict:
    try:
        return await item_memories(request.app.state.pool, actor, data_id)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc


@app.delete("/api/v1/data/{data_id}")
async def delete_item(
    request: Request,
    data_id: str,
    actor: Principal = Depends(principal),
    reason: str | None = None,
) -> dict:
    """Tombstone now, reclaim eventually. Returns before the cascade finishes,
    because it must -- but the item is invisible before it returns."""
    state = request.app.state
    try:
        result = await request_deletion(
            state.pool, state.queue, actor,
            selector={"data_ids": [data_id]}, reason=reason,
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    if not result.data_ids and not result.retained:
        raise HTTPException(status_code=404, detail="not found")
    return {
        "run_id": result.run_id,
        "deleted": result.data_ids,
        "retained": [{"data_id": d, "reason": r} for d, r in result.retained],
    }


@app.post("/api/v1/deletions")
async def create_deletion(
    request: Request, body: dict, actor: Principal = Depends(principal)
) -> dict:
    state = request.app.state
    try:
        result = await request_deletion(
            state.pool, state.queue, actor,
            selector=body.get("selector", {}),
            reason=body.get("reason"),
            dry_run=bool(body.get("dry_run")),
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "run_id": result.run_id,
        "mode": "dry_run" if body.get("dry_run") else "execute",
        # Both counts, separately: what would go and what is held back.
        "deleting": len(result.data_ids),
        "retained": [{"data_id": d, "reason": r} for d, r in result.retained],
    }


@app.post("/api/v1/users/{user_id}/deletion")
async def delete_account_data(
    request: Request, user_id: str, body: dict, actor: Principal = Depends(principal)
) -> dict:
    """Delete an account's data — the widest scope, and the one with a line in it.

    Personal data goes; data that arrived through a *shared* connection, or that
    the person published to the organisation, stays and is reported as retained
    with the reason. Deleting a colleague's work as a side effect of someone
    leaving is the failure this exists to avoid.

    Pass `dry_run` first: it reports both counts separately.
    """
    state = request.app.state
    target = actor.user_id if user_id == "me" else user_id
    return await _control(account.delete_account)(
        state.pool, state.queue, actor,
        user_id=target, dry_run=bool(body.get("dry_run")), reason=body.get("reason"),
    )


@app.get("/api/v1/data/{data_id}/erasure")
async def erasure_certificate(
    request: Request, data_id: str, actor: Principal = Depends(principal)
) -> dict:
    """Evidence, not a claim: re-checks every table that holds item-scoped data.

    Deliberately readable after the item is gone — the whole point is to be able
    to answer for a deletion later.
    """
    from .deletion import verify_erasure

    return await verify_erasure(request.app.state.pool, data_id)


@app.get("/api/v1/runs/{run_id}")
async def read_run(
    request: Request, run_id: str, actor: Principal = Depends(principal)
) -> dict:
    row = await request.app.state.pool.fetchrow(
        """
        SELECT run_id, kind, status, mode, selector, reason, total, done, failed,
               retained, created_at, finished_at
        FROM runs WHERE run_id = $1 AND org_id = $2
        """,
        run_id, actor.org_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    items = await request.app.state.pool.fetch(
        "SELECT data_id, status, reason FROM run_items WHERE run_id = $1 ORDER BY data_id LIMIT 500",
        run_id,
    )
    return {**dict(row), "items": [dict(i) for i in items]}


def _control(handler):
    """One error translation for the whole control plane."""

    async def wrapped(*args, **kwargs):
        try:
            return await handler(*args, **kwargs)
        except (ControlError, ShareError, CaseError, AgentConfigError, ModelError,
                MemoryError, AccountError) as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        except AuthError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    return wrapped


@app.get("/api/v1/projects")
async def get_projects(request: Request, actor: Principal = Depends(principal)) -> dict:
    return {"projects": await _control(control.list_projects)(request.app.state.pool, actor)}



@app.post("/api/v1/invites")
async def post_invite(
    request: Request, body: dict, actor: Principal = Depends(principal)
) -> dict:
    """Issue an invite. The token comes back once and is never recoverable."""
    try:
        created = await invites_mod.create(
            request.app.state.pool, actor,
            email=body.get("email"),
            role=body.get("role", "member"),
            expires_in_days=body.get("expires_in_days"),
            transferable=bool(body.get("transferable")),
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except InviteError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return {
        "invite_id": created.invite_id,
        "prefix": created.prefix,
        # Shown once, like an API key, because that is what it is.
        "token": created.token,
        "role": created.role,
        "email": created.email,
        "expires_at": created.expires_at.isoformat(),
    }


@app.get("/api/v1/invites")
async def get_invites(request: Request, actor: Principal = Depends(principal)) -> dict:
    try:
        return {"invites": await invites_mod.listing(request.app.state.pool, actor)}
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.delete("/api/v1/invites/{invite_id}")
async def delete_invite(
    request: Request, invite_id: str, actor: Principal = Depends(principal)
) -> dict:
    try:
        return await invites_mod.revoke(request.app.state.pool, actor, invite_id)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except InviteError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/invites/redeem")
async def redeem_invite(request: Request, body: dict) -> dict:
    """Unauthenticated by necessity: whoever is redeeming has no account yet.

    That is the situation an invite exists for, and the invite is the credential
    that covers it.

    Rate limited on the **peer address**, not on anything in the request. Keying
    on the presented token would hand the bucket to the guesser -- vary the
    prefix, get a fresh allowance, and the limiter limits nothing. The peer is
    the one identifier a caller cannot choose. Behind a proxy that address is
    the proxy's, which makes this a shared ceiling on redemption rather than a
    per-client one; `X-Forwarded-For` is deliberately not trusted, since a
    header the client writes is a bucket the client picks.

    The token itself is 32 bytes of entropy, so this is defence in depth rather
    than the thing standing between an attacker and an org.
    """
    token = (body.get("token") or "").strip()
    peer = request.client.host if request.client else "unknown"
    try:
        await request.app.state.limiter.charge_key(f"redeem:{peer}", 100)
    except QuotaExceeded as exc:
        raise HTTPException(
            status_code=exc.status, detail=str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    try:
        redeemed = await invites_mod.redeem(
            request.app.state.pool, token=token, email=body.get("email")
        )
    except InviteError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return {
        "user_id": redeemed.user_id,
        "org_id": redeemed.org_id,
        "role": redeemed.role,
        # The durable credential the single-use one is exchanged for. Also
        # shown once.
        "api_key": redeemed.api_key,
    }


@app.post("/api/v1/projects")
async def post_project(
    request: Request, body: dict, actor: Principal = Depends(principal)
) -> dict:
    return await _control(control.create_project)(
        request.app.state.pool, actor, name=body.get("name", "untitled")
    )


@app.get("/api/v1/organizations/members")
async def get_members(request: Request, actor: Principal = Depends(principal)) -> dict:
    return {"members": await _control(control.list_members)(request.app.state.pool, actor)}


@app.post("/api/v1/organizations/members")
async def post_member(
    request: Request, body: dict, actor: Principal = Depends(principal)
) -> dict:
    return await _control(control.add_member)(
        request.app.state.pool, actor,
        email=body.get("email", ""), role=body.get("role", "member"),
    )


@app.delete("/api/v1/organizations/members/{user_id}")
async def delete_member(
    request: Request, user_id: str, actor: Principal = Depends(principal)
) -> dict:
    return await _control(control.remove_member)(request.app.state.pool, actor, user_id)


@app.get("/api/v1/groups")
async def get_groups(request: Request, actor: Principal = Depends(principal)) -> dict:
    return {"groups": await _control(control.list_groups)(request.app.state.pool, actor)}


@app.post("/api/v1/groups")
async def post_group(request: Request, body: dict, actor: Principal = Depends(principal)) -> dict:
    return await _control(control.create_group)(
        request.app.state.pool, actor, name=body.get("name", "untitled")
    )


@app.put("/api/v1/groups/{group_id}/members")
async def put_group_members(
    request: Request, group_id: str, body: dict, actor: Principal = Depends(principal)
) -> dict:
    return await _control(control.set_group_members)(
        request.app.state.pool, actor, group_id, body.get("user_ids", [])
    )


@app.get("/api/v1/users/me")
async def get_me(request: Request, actor: Principal = Depends(principal)) -> dict:
    row = await request.app.state.pool.fetchrow(
        "SELECT user_id, email, display_name, created_at FROM users WHERE user_id = $1",
        actor.user_id,
    )
    role = await request.app.state.pool.fetchval(
        "SELECT role FROM memberships WHERE user_id = $1 AND org_id = $2",
        actor.user_id, actor.org_id,
    )
    return {
        **(dict(row) if row else {}),
        "org_id": actor.org_id,
        "role": role,
        "capabilities": sorted(actor.capabilities),
        "groups": sorted(actor.groups),
    }


@app.get("/api/v1/users/me/api-keys")
async def get_keys(request: Request, actor: Principal = Depends(principal)) -> dict:
    return {"keys": await _control(control.list_keys)(request.app.state.pool, actor)}


@app.post("/api/v1/users/me/api-keys")
async def post_key(request: Request, body: dict, actor: Principal = Depends(principal)) -> dict:
    return await _control(control.create_key)(
        request.app.state.pool, actor,
        name=body.get("name", ""),
        capabilities=body.get("capabilities", ["data:read"]),
        project_id=body.get("project_id"),
    )


@app.delete("/api/v1/users/me/api-keys/{key_id}")
async def delete_key(
    request: Request, key_id: str, actor: Principal = Depends(principal)
) -> dict:
    return await _control(control.revoke_key)(request.app.state.pool, actor, key_id)


@app.get("/api/v1/producers")
async def get_producers(request: Request, actor: Principal = Depends(principal)) -> dict:
    return {"producers": await _control(control.list_producers)(request.app.state.pool, actor)}


@app.post("/api/v1/producers")
async def post_producer(
    request: Request, body: dict, actor: Principal = Depends(principal)
) -> dict:
    return await _control(control.create_producer)(
        request.app.state.pool, actor,
        project_id=body.get("project_id", ""),
        producer_type=body.get("type", "client"),
        connection_id=body.get("connection_id"),
    )


@app.patch("/api/v1/producers/{producer_id}")
async def patch_producer(
    request: Request, producer_id: str, body: dict, actor: Principal = Depends(principal)
) -> dict:
    return await _control(control.set_producer_status)(
        request.app.state.pool, actor, producer_id, body.get("status", "enabled")
    )


@app.patch("/api/v1/connections/{connection_id}")
async def patch_connection(
    request: Request, connection_id: str, body: dict, actor: Principal = Depends(principal)
) -> dict:
    return await _control(control.set_connection_scope)(
        request.app.state.pool, actor, connection_id, body.get("scope", "personal")
    )


# ------------------------------------------------------------------- cases


@app.get("/api/v1/projects/{project_id}/cases")
async def get_cases(
    request: Request, project_id: str, actor: Principal = Depends(principal)
) -> dict:
    return {"cases": await _control(cases.list_cases)(request.app.state.pool, actor, project_id)}


@app.put("/api/v1/cases")
async def put_case(request: Request, body: dict, actor: Principal = Depends(principal)) -> dict:
    return await _control(cases.create_case)(
        request.app.state.pool, actor,
        project_id=body.get("project_id", ""),
        case_type=body.get("case_type", ""),
        external_id=body.get("external_id", ""),
        title=body.get("title"),
        identifiers=body.get("identifiers"),
    )


@app.get("/api/v1/cases/{case_id}/timeline")
async def get_timeline(
    request: Request, case_id: str, actor: Principal = Depends(principal)
) -> dict:
    return await _control(cases.timeline)(request.app.state.pool, actor, case_id)


# ------------------------------------------------------------------ sharing


@app.post("/api/v1/shares")
async def post_share(request: Request, body: dict, actor: Principal = Depends(principal)) -> dict:
    return await _control(sharing.create_share)(
        request.app.state.pool, actor,
        data_id=body.get("data_id"), case_id=body.get("case_id"),
        expires_in_days=int(body.get("expires_in_days", 7)),
        password=body.get("password"),
    )


@app.get("/api/v1/shares")
async def get_shares(request: Request, actor: Principal = Depends(principal)) -> dict:
    """Everything currently shared publicly -- the view that catches the
    mistake made six months ago."""
    return {"shares": await _control(sharing.inventory)(request.app.state.pool, actor)}


@app.delete("/api/v1/shares/{share_id}")
async def delete_share(
    request: Request, share_id: str, actor: Principal = Depends(principal)
) -> dict:
    return await _control(sharing.revoke_share)(request.app.state.pool, actor, share_id)


# ------------------------------------------------------------- public demo


@app.get("/api/v1/public/demo")
async def public_demo_info(request: Request) -> dict:
    """What the public demo is, or that there is not one.

    Served unauthenticated so the landing page can decide whether to render the
    demo at all, rather than hardcoding a corpus that may not be deployed.
    `available: false` is the ordinary answer on a deployment that has not
    switched it on, and is not an error.
    """
    settings = request.app.state.settings
    if not settings.public_project_id:
        return {"available": False}

    counts = await request.app.state.pool.fetchrow(
        """
        SELECT count(*) FILTER (WHERE answered
                                AND asked_at > date_trunc('day', now())) AS spent
          FROM public_asks
        """
    )
    spent = int(counts["spent"] or 0)
    return {
        "available": True,
        "title": settings.public_title or "Ask the corpus",
        "subtitle": settings.public_subtitle or "",
        "remaining_today": max(0, settings.public_daily_cap - spent),
        "daily_cap": settings.public_daily_cap,
    }


@app.post("/api/v1/public/ask")
async def public_ask(request: Request, body: dict) -> dict:
    """One question, one corpus, no login.

    Every other route decides what you may read from who you are. This one has
    no caller to identify, so it cannot borrow that machinery -- what replaces
    it is narrowness. The project and memory are named in configuration, never
    in the request, so there is no scope for a caller to widen. The principal is
    synthetic, carries `DATA_READ` alone, and has a `user_id` that matches no
    real user, so private records stay invisible exactly as they would to a
    stranger.

    Metered before the model call, not after: the failure mode of a public
    endpoint is a flood of requests that error, and counting afterwards gives
    every one of them a free call.
    """
    from .auth import DATA_READ, Principal
    from .contracts import AskRequest, RetrieveFilter
    from .public_demo import DemoUnavailable, check_and_count, client_ip, release

    settings = request.app.state.settings
    if not settings.public_project_id:
        raise HTTPException(status_code=404, detail="no public demo on this deployment")

    question = (body or {}).get("question") or ""
    if not question.strip():
        raise HTTPException(status_code=400, detail="a question is required")
    if len(question) > 500:
        raise HTTPException(status_code=400, detail="question too long")

    pool = request.app.state.pool
    org_id = await pool.fetchval(
        "SELECT org_id FROM projects WHERE project_id = $1", settings.public_project_id
    )
    if org_id is None:
        raise HTTPException(status_code=404, detail="no public demo on this deployment")

    try:
        ask_id = await check_and_count(
            pool, ip=client_ip(request), secret=settings.master_key_b64,
            question=question, rate_per_hour=settings.public_rate_per_hour,
            daily_cap=settings.public_daily_cap,
        )
    except DemoUnavailable as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    # Matches no real user, so `private` records are as invisible here as they
    # are to any stranger. Only what the org made org-visible is reachable.
    visitor = Principal(
        user_id="public", org_id=org_id, project_id=settings.public_project_id,
        capabilities=frozenset({DATA_READ}), mode="public",
    )
    filters = RetrieveFilter(project_id=settings.public_project_id)
    if settings.public_memory_id:
        filters.memory_ids = [settings.public_memory_id]

    try:
        answer = await ask(
            pool, request.app.state.embedder, request.app.state.answerer, visitor,
            AskRequest(question=question, filter=filters,
                       match=["vector", "lexical"]),
            embed_generator=request.app.state.current_generators["embedding"],
            graph=request.app.state.graph,
        )
    except Exception:
        # The reservation is released rather than kept: a visitor who got no
        # answer has not spent the day's budget, and an endpoint that charges
        # for its own failures runs out fastest exactly when it is broken.
        await release(pool, ask_id)
        raise

    # Deliberately not the full answer shape. `query_id`, model identity,
    # generator versions and corpus counts are operational facts about the
    # deployment, and an anonymous caller has no use for them and no business
    # knowing them.
    return {
        "question": answer.question,
        "answer": answer.answer,
        "grounded": answer.grounded,
        "citations": [
            {"marker": c.marker, "text": c.text} for c in answer.citations
        ],
    }


@app.get("/s/{token}")
async def read_share(request: Request, token: str, password: str | None = None) -> dict:
    """The public surface. Deliberately unauthenticated -- the token *is* the
    credential -- and it returns the item alone, never the derived layer."""
    try:
        return await sharing.resolve_share(request.app.state.pool, token, password)
    except ShareError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


# ------------------------------------------------------------ agent configs


@app.get("/api/v1/agents/{data_type}/config")
async def get_agent_config(
    request: Request,
    data_type: str,
    actor: Principal = Depends(principal),
    project_id: str | None = None,
) -> dict:
    return await agents.effective_config(
        request.app.state.pool, data_type=data_type,
        org_id=actor.org_id, project_id=project_id or actor.project_id,
    )


@app.put("/api/v1/agents/{data_type}/config")
async def put_agent_config(
    request: Request, data_type: str, body: dict, actor: Principal = Depends(principal)
) -> dict:
    return await _control(agents.set_config)(
        request.app.state.pool, actor, data_type=data_type,
        prompt=body.get("prompt"), flags=body.get("flags"),
        scope=body.get("scope", "project"),
        project_id=body.get("project_id") or actor.project_id,
        lock=bool(body.get("lock")),
    )


@app.post("/api/v1/agents/{data_type}/config/test")
async def test_agent_config(
    request: Request, data_type: str, body: dict, actor: Principal = Depends(principal)
) -> dict:
    """Test before save: editing a prompt blind and finding out across a corpus
    is the failure this prevents."""
    return await _control(agents.test_config)(
        request.app.state.pool, actor, request.app.state.extractor,
        data_type=data_type, sample=body.get("sample", ""),
        prompt=body.get("prompt"), project_id=body.get("project_id"),
    )


# ------------------------------------------------------------ normalization


@app.get("/api/v1/schemas")
async def get_schemas(
    request: Request, actor: Principal = Depends(principal), project_id: str | None = None
) -> dict:
    return {"schemas": await _control(normalize.list_schemas)(
        request.app.state.pool, actor, project_id or actor.project_id
    )}


@app.post("/api/v1/schemas")
async def post_schema(request: Request, body: dict, actor: Principal = Depends(principal)) -> dict:
    return await _control(normalize.create_schema)(
        request.app.state.pool, actor,
        project_id=body.get("project_id") or actor.project_id,
        target_type=body.get("target_type", ""),
        fields=body.get("fields", {}), mapping=body.get("mapping", {}),
    )


# -------------------------------------------------------------- ephemeral


@app.post("/api/v1/tokens/ephemeral")
async def post_ephemeral_token(
    request: Request, body: dict, actor: Principal = Depends(principal)
) -> dict:
    """A short-lived, project-bound, read-only credential for embedded widgets.

    Deliberately cannot exceed the capabilities of the credential that minted
    it, and deliberately cannot outlive a browser session.
    """
    from datetime import datetime, timedelta, timezone

    from .auth import DATA_READ, issue_key

    actor.require(DATA_READ)
    minutes = min(int(body.get("expires_in_minutes", 30)), 240)
    token = await issue_key(
        request.app.state.pool,
        user_id=actor.user_id, org_id=actor.org_id,
        project_id=body.get("project_id") or actor.project_id,
        capabilities=[DATA_READ],
        name="ephemeral",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=minutes),
    )
    return {"token": token, "expires_in_minutes": minutes, "capabilities": [DATA_READ]}


# --------------------------------------------------------------- platform


@app.get("/api/v1/platform/health")
async def platform_health(request: Request, actor: Principal = Depends(principal)) -> dict:
    """Operational shape only.

    A platform admin does not get tenant data by default -- support tooling
    that shows customer content is a privacy violation arriving disguised as a
    feature request. Counts and queue depth, never content.
    """
    from .auth import ADMIN

    actor.require(ADMIN)
    pool = request.app.state.pool
    return {
        "queue_depth": await request.app.state.queue.depth(),
        "unpurged_tombstones": await unpurged_tombstones(pool),
        "orgs": await pool.fetchval("SELECT count(*) FROM organizations"),
        "projects": await pool.fetchval("SELECT count(*) FROM projects"),
        "items": await pool.fetchval("SELECT count(*) FROM data_items WHERE deleted_at IS NULL"),
        "items_by_state": {
            r["state"]: r["n"] for r in await pool.fetch(
                "SELECT state, count(*) AS n FROM data_items WHERE deleted_at IS NULL GROUP BY state"
            )
        },
        "parse_problems": {
            r["parse_status"]: r["n"] for r in await pool.fetch(
                "SELECT parse_status, count(*) AS n FROM data_items "
                "WHERE parse_status IS NOT NULL AND parse_status <> 'parsed' GROUP BY parse_status"
            )
        },
        "live_shares": await pool.fetchval(
            "SELECT count(*) FROM share_links WHERE revoked_at IS NULL AND expires_at > now()"
        ),
    }


# ---------------------------------------------------------- model catalog


@app.get("/api/v1/models")
async def get_models(request: Request, actor: Principal = Depends(principal)) -> dict:
    """Cards, assignments and engines. Never a credential -- only whether one
    is held."""
    return await _control(models.list_catalog)(request.app.state.pool, actor)


@app.post("/api/v1/models/assignments")
async def post_assignment(
    request: Request, body: dict, actor: Principal = Depends(principal)
) -> dict:
    """Assign a model per (purpose, data type).

    Checked against the model's card, so assigning a text-only model to
    transcribe audio fails now rather than when a file arrives.
    """
    assignment = await _control(models.assign)(
        request.app.state.pool, actor,
        purpose=body.get("purpose", ""), model_id=body.get("model_id", ""),
        data_type=body.get("data_type", "*"), scope=body.get("scope", "org"),
        engine_id=body.get("engine_id"),
    )
    return assignment.__dict__


@app.post("/api/v1/models/cards")
async def post_card(request: Request, body: dict, actor: Principal = Depends(principal)) -> dict:
    return await _control(models.upsert_card)(request.app.state.pool, actor, body)


@app.post("/api/v1/engines")
async def post_engine(request: Request, body: dict, actor: Principal = Depends(principal)) -> dict:
    return await _control(models.register_engine)(
        request.app.state.pool, actor, request.app.state.envelope,
        provider=body.get("provider", ""), base_url=body.get("base_url"),
        credential=body.get("credential"),
    )


def _public_url(request: Request) -> str:
    """The URL the *provider* sent to, not the one this process received.

    Cloud Run terminates TLS and forwards over plain HTTP, so `request.url`
    reconstructs as `http://` while Twilio signed the `https://` address the
    customer configured. The signature then fails on every single delivery, and
    it fails as `signature verification failed` -- which reads as a wrong
    secret, so the time goes on rotating a secret that was always correct.

    Found by firing `tools/fake_inbound.py` at the deployed service: the same
    request verified locally and was refused in production, because a unit test
    hands the same URL to the signer and the verifier and can never see this.

    Trusting a client-supplied header is safe *here* specifically: the platform
    overwrites `X-Forwarded-Proto` on every request, and spoofing it changes
    only which string gets signed -- an attacker still needs the secret, so this
    grants nothing that forging the whole signature would not already require.
    """
    forwarded = request.headers.get("x-forwarded-proto")
    if not forwarded:
        return str(request.url)
    # A proxy chain sends a list; the first entry is the original client.
    scheme = forwarded.split(",")[0].strip()
    return str(request.url.replace(scheme=scheme)) if scheme else str(request.url)


@app.post("/webhooks/{producer_id}")
async def inbound_webhook(request: Request, producer_id: str) -> JSONResponse:
    """The provider-facing surface.

    Deliberately outside `/api/v1` and deliberately unauthenticated by the
    platform's own scheme: the *producer* declares how its provider proves
    itself, because not every provider can present a bearer token.

    Always answers quickly and, where it can, with 2xx — a provider that sees a
    non-2xx retries, and retrying a payload that will never be accepted turns
    one bad delivery into a permanent storm.
    """
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    state = request.app.state
    # The one endpoint with no credential in front of it, which makes it the
    # place an unbounded caller is expected rather than anomalous. Keyed on the
    # producer because that is the only identity the request carries before its
    # signature is checked -- and answered with 429 rather than a soothing 2xx,
    # because a provider reading `Retry-After` backs off, where one told
    # "accepted" keeps sending at the rate that caused the problem.
    try:
        await state.limiter.charge_key(f"whk:{producer_id}", 1)
    except QuotaExceeded as exc:
        raise HTTPException(
            status_code=exc.status, detail=str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    try:
        result = await receive_webhook(
            state.pool, state.queue, state.blobs, state.settings, state.envelope,
            producer_id=producer_id, raw_body=raw, headers=headers,
            # Twilio signs the URL, and Graph validates via a query parameter,
            # so the adapter needs more than the bytes.
            url=_public_url(request), query=dict(request.query_params),
        )
    except WebhookError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    if result.handshake is not None:
        # A registration challenge. Graph demands plain text and rejects a
        # JSON-wrapped echo, so the shape is the provider's to decide.
        if getattr(result.handshake, "plain_text", False):
            return Response(content=str(result.handshake.body), media_type="text/plain")
        return JSONResponse(status_code=200, content=result.handshake.body)

    return JSONResponse(
        status_code=200,
        content={"status": result.status, "items": result.items,
                 "data_ids": result.data_ids, "reason": result.reason},
    )


@app.post("/api/v1/producers/{producer_id}/test-delivery")
async def send_test_delivery(
    request: Request, producer_id: str, body: dict, actor: Principal = Depends(principal)
) -> dict:
    """Send a delivery to this producer as its provider would.

    It signs with the stored secret and goes through the **real** receive path,
    signature verification included — a test that skipped verification would
    pass for a producer whose signing is broken, which is exactly the case worth
    catching before a provider is pointed at it.
    """
    import json as jsonlib
    import time as timelib

    from . import providers as providers_mod
    from .auth import CONFIG_WRITE
    from .webhooks import WebhookError, receive as receive_webhook

    state = request.app.state
    try:
        actor.require(CONFIG_WRITE)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    producer = await state.pool.fetchrow(
        """
        SELECT producer_id, org_id, inbound_auth, signing_secret_ct, inbound_mapping
        FROM producers WHERE producer_id = $1 AND org_id = $2 AND type = 'webhook'
        """,
        producer_id, actor.org_id,
    )
    if producer is None:
        raise HTTPException(status_code=404, detail="not found")

    payload = body.get("payload")
    raw = jsonlib.dumps(payload if payload is not None else {"test": True}).encode()
    headers = {"content-type": "application/json",
               "x-delivery-id": body.get("delivery_id") or f"test-{int(timelib.time()*1000)}"}

    if producer["inbound_auth"] == "signature":
        if not producer["signing_secret_ct"]:
            raise HTTPException(
                status_code=409,
                detail="this producer signs its deliveries but has no secret yet — rotate one first",
            )
        secret = state.envelope.decrypt(
            bytes(producer["signing_secret_ct"]), aad=actor.org_id.encode()
        )
        # Signed the way this producer's provider signs, not the way the
        # generic scheme does. Hand-rolling it here meant every preset --
        # Slack, Zoom, Linear, Shopify, Twilio, Stripe, Graph -- got a generic
        # signature its own scheme then refused, so the button reported 401 for
        # a perfectly good secret. `sign` is the mirror of the `verify` this
        # request is about to run.
        mapping = producer["inbound_mapping"] or {}
        if isinstance(mapping, str):
            mapping = jsonlib.loads(mapping)
        provider = providers_mod.get(mapping.get("provider"))
        headers.update(providers_mod.sign(
            provider,
            request=providers_mod.Request(
                raw_body=raw, headers=headers, url=_public_url(request),
            ),
            secret=secret,
        ))

    try:
        result = await receive_webhook(
            state.pool, state.queue, state.blobs, state.settings, state.envelope,
            producer_id=producer_id, raw_body=raw, headers=headers,
        )
    except WebhookError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    return {"status": result.status, "items": result.items, "data_ids": result.data_ids,
            "reason": result.reason, "signed": producer["inbound_auth"] == "signature"}


@app.get("/api/v1/producers/{producer_id}/deliveries")
async def webhook_deliveries(
    request: Request, producer_id: str, actor: Principal = Depends(principal), limit: int = 50
) -> dict:
    """What actually arrived — the answer to "we sent it, did you get it?"."""
    rows = await request.app.state.pool.fetch(
        """
        SELECT delivery_id, external_delivery_id, status, reason, items,
               payload_bytes, signature_verified, received_at
        FROM webhook_deliveries WHERE producer_id = $1 AND org_id = $2
        ORDER BY received_at DESC LIMIT $3
        """,
        producer_id, actor.org_id, min(limit, 200),
    )
    return {"deliveries": [dict(r) for r in rows]}


@app.post("/api/v1/producers/{producer_id}/signing-secret")
async def rotate_signing_secret(
    request: Request, producer_id: str, body: dict | None = None,
    actor: Principal = Depends(principal),
) -> dict:
    """Rotate with an overlap: the previous secret keeps verifying until the
    next rotation, or a rotation is an outage for everything in flight.

    **A supplied `secret` is stored instead of a minted one**, and that is not a
    convenience. Zoom, Stripe, GitHub and Slack each generate their own secret
    and expect you to hold it — so a producer using one of those presets could
    be configured as `signature`, look correct in every listing, and reject
    every real delivery, because the stored secret was one we invented and the
    provider had never seen. The presets existed; the only way to use them did
    not.

    A supplied secret is never echoed back. There is nothing to show: whoever
    pasted it already has it, and returning it would put a provider's
    credential in a response body for no reason.
    """
    import secrets as secrets_module

    from .auth import CONFIG_WRITE
    from .crypto import CryptoUnavailable

    state = request.app.state
    try:
        actor.require(CONFIG_WRITE)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    producer = await state.pool.fetchrow(
        "SELECT producer_id, org_id, signing_secret_ct FROM producers "
        "WHERE producer_id = $1 AND org_id = $2",
        producer_id, actor.org_id,
    )
    if producer is None:
        raise HTTPException(status_code=404, detail="not found")

    supplied = (body or {}).get("secret")
    if supplied is not None and (not isinstance(supplied, str) or len(supplied) < 8):
        raise HTTPException(
            status_code=400,
            detail="a supplied signing secret must be a string of at least 8 characters")
    secret = supplied or secrets_module.token_urlsafe(32)
    try:
        ciphertext = state.envelope.encrypt(secret.encode(), aad=actor.org_id.encode())
    except CryptoUnavailable as exc:
        raise HTTPException(
            status_code=503, detail="cannot store a signing secret: encryption is not configured"
        ) from exc

    await state.pool.execute(
        """
        UPDATE producers
        SET previous_signing_secret_ct = signing_secret_ct,
            signing_secret_ct = $2,
            signing_secret_rotated_at = now(),
            inbound_auth = 'signature'
        WHERE producer_id = $1
        """,
        producer_id, ciphertext,
    )
    if supplied:
        # Nothing to show. Echoing a provider's own credential back into a
        # response body would put it in one more log for no reason at all.
        return {"producer_id": producer_id, "stored": True, "source": "provider",
                "previous_secret_valid_until_next_rotation":
                    producer["signing_secret_ct"] is not None}
    return {
        "producer_id": producer_id,
        # Shown once, like any other credential.
        "signing_secret": secret,
        "source": "minted",
        "algorithm": "hmac-sha256",
        "header": "X-Signature",
        "timestamp_header": "X-Signature-Timestamp",
        "signed_payload": "{timestamp}.{raw_body}",
        "previous_secret_valid_until_next_rotation": producer["signing_secret_ct"] is not None,
    }


@app.patch("/api/v1/producers/{producer_id}/inbound")
async def set_inbound(
    request: Request, producer_id: str, body: dict, actor: Principal = Depends(principal)
) -> dict:
    """How this provider proves itself, and how its payload maps onto items."""
    from .auth import CONFIG_WRITE

    try:
        actor.require(CONFIG_WRITE)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    method = body.get("inbound_auth", "signature")
    if method not in ("api_key", "signature", "url_secret", "none"):
        raise HTTPException(status_code=400, detail="unknown inbound_auth")

    updated = await request.app.state.pool.execute(
        """
        UPDATE producers
        SET inbound_auth = $3,
            inbound_mapping = COALESCE($4::jsonb, inbound_mapping),
            api_key_id = COALESCE($5, api_key_id),
            defaults = COALESCE($6::jsonb, defaults)
        WHERE producer_id = $1 AND org_id = $2
        """,
        producer_id, actor.org_id, method, body.get("mapping"),
        body.get("api_key_id"), body.get("defaults"),
    )
    if updated.endswith("0"):
        raise HTTPException(status_code=404, detail="not found")
    return {"producer_id": producer_id, "inbound_auth": method,
            "mapping": body.get("mapping"), "url": f"/webhooks/{producer_id}"}




@app.get("/api/v1/connectors")
async def get_connectors() -> dict:
    """The catalog of apps a crawler can pull from.

    Unauthenticated: it is a list of what this build supports and discloses
    nothing about anyone's data. Someone deciding whether this is worth an
    account should be able to see it before they have one.
    """
    return {"connectors": connectors.catalog()}


@app.post("/api/v1/crawlers/from-connector")
async def post_crawler_from_connector(
    request: Request, body: dict, actor: Principal = Depends(principal)
) -> dict:
    """Create a crawler from a catalog entry plus the scope only you know.

    The config it produces is the ordinary one, so the crawler that comes out is
    indistinguishable from a hand-written one -- including still being `draft`
    until a dry run passes. A catalog entry is a shortcut through the
    configuration, never around the gate.
    """
    state = request.app.state
    try:
        config = connectors.build(
            body.get("connector", ""),
            body.get("scope") or {},
            name=body.get("name"),
            enrich=bool(body.get("enrich")),
        )
    except connectors.ConnectorError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    try:
        created = await crawling.create_crawler(
            state.pool, actor,
            project_id=body.get("project_id", ""),
            config=CrawlerConfig.model_validate(config),
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except CrawlerError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    if body.get("connection_id"):
        try:
            await connections.attach(
                state.pool, actor, created["crawler_id"], body["connection_id"]
            )
        except connections.ConnectionError_ as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        created["connection_id"] = body["connection_id"]
    return created


@app.post("/api/v1/connections")
async def post_connection(
    request: Request, body: dict, actor: Principal = Depends(principal)
) -> dict:
    """Register a credential a crawler can authenticate with.

    The credential is enveloped on the way in and there is no endpoint that
    reads one back out. Listing reports whether one is held, never a prefix --
    a prefix is enough to confirm a guess.
    """
    state = request.app.state
    try:
        return await connections.create(
            state.pool, actor, state.envelope,
            project_id=body.get("project_id", ""),
            provider=body.get("provider", "generic"),
            credential=body.get("credential"),
            auth_style=body.get("auth_style", "bearer"),
            auth_name=body.get("auth_name"),
            auth_config=body.get("auth_config") or {},
            scope=body.get("scope", "personal"),
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except connections.ConnectionError_ as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/connections")
async def get_connections(
    request: Request, project_id: str | None = None,
    actor: Principal = Depends(principal),
) -> dict:
    return {"connections": await connections.listing(
        request.app.state.pool, actor, project_id
    )}


@app.patch("/api/v1/crawlers/{crawler_id}/connection")
async def set_crawler_connection(
    request: Request, crawler_id: str, body: dict,
    actor: Principal = Depends(principal),
) -> dict:
    """Point a crawler at a connection, or pass null to detach it."""
    try:
        return await connections.attach(
            request.app.state.pool, actor, crawler_id, body.get("connection_id")
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except connections.ConnectionError_ as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/uploads")
async def create_upload(
    request: Request, body: dict, actor: Principal = Depends(principal)
) -> dict:
    """Grants a capability, not a write. The completion is an ordinary write."""
    from .auth import DATA_WRITE

    try:
        actor.require(DATA_WRITE)
        session = await create_session(
            request.app.state.pool,
            producer_id=body["producer_id"],
            principal=actor,
            external_id=body.get("external_id") or "upload",
            mime_type=body.get("mime_type"),
            size_bytes=body.get("size"),
            base_url=str(request.base_url).rstrip("/"),
            max_bytes=request.app.state.settings.max_upload_bytes,
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except UploadError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=400, detail="producer_id is required") from exc
    return {
        "upload_id": session.upload_id,
        "url": session.url,
        # Shown once. It is a write primitive for one storage key, so it is
        # hashed at rest exactly like an API key.
        "token": session.token,
        "expires_at": session.expires_at.isoformat(),
        "method": "PUT",
    }


@app.put("/api/v1/uploads/{upload_id}/bytes")
async def upload_bytes(
    request: Request,
    upload_id: str,
    x_upload_token: str = Header(default="", alias="X-Upload-Token"),
) -> dict:
    """The local signer's endpoint.

    Deliberately not authenticated with an API key: the *token* is the
    capability, which is what makes this the same shape as a GCS signed URL.
    """
    state = request.app.state
    try:
        session = await authorise(state.pool, upload_id, x_upload_token)
    except UploadError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    payload = await request.body()
    if session["size_bytes"] is not None and len(payload) != session["size_bytes"]:
        raise HTTPException(
            status_code=400,
            detail=f"declared {session['size_bytes']} bytes, received {len(payload)}",
        )
    if len(payload) > state.settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="payload exceeds the configured maximum")

    from .classify import sniff_mime

    mime = sniff_mime(payload, None, session["mime_type"])
    storage_ref, checksum = await state.blobs.put(
        org_id=session["org_id"], project_id=session["project_id"],
        data_id=upload_id, kind="raw", payload=payload, mime_type=mime,
    )
    await complete_upload(
        state.pool, upload_id, storage_ref=storage_ref, checksum=checksum, received=len(payload)
    )
    return {
        "upload_id": upload_id,
        "storage_ref": storage_ref,
        "checksum": checksum,
        "size": len(payload),
        "mime_type": mime,
    }


@app.post("/api/v1/uploads/{upload_id}/complete")
async def finish_upload(
    request: Request, upload_id: str, body: dict, actor: Principal = Depends(principal)
) -> JSONResponse:
    """Turn a completed session into a data item -- an ordinary write with a
    Stored ref, through the same admission control as everything else."""
    state = request.app.state
    session = await state.pool.fetchrow(
        "SELECT * FROM upload_sessions WHERE upload_id = $1", upload_id
    )
    if session is None or session["org_id"] != actor.org_id:
        raise HTTPException(status_code=404, detail="unknown upload session")
    if session["status"] != "completed":
        raise HTTPException(status_code=409, detail=f"upload is {session['status']}")

    # Everything an inline write can say about an item, an uploaded one must be
    # able to say too. Without `access`, `template` and `options` here, choosing
    # the upload path silently dropped the visibility, the graph template and
    # the request to enrich -- so a large document would arrive stored, public
    # to the project's default, and never become searchable, while the same file
    # inlined honoured all three. A path that quietly means something different
    # is worse than one that is missing.
    item: dict = {
        "external_id": body.get("external_id") or session["external_id"],
        "content": {
            "kind": "stored",
            "storage_ref": session["storage_key"],
            "mime_type": session["mime_type"],
            "size": session["received_bytes"],
            "checksum": session["checksum"],
        },
        "memory": body.get("memory"),
    }
    if body.get("access"):
        item["access"] = body["access"]
    if body.get("template"):
        item["template"] = body["template"]
    write_request = WriteRequest(
        producer_id=session["producer_id"],
        items=[item],
        **({"options": body["options"]} if body.get("options") else {}),
    )
    try:
        response = await write_items(
            state.pool, state.queue, state.blobs, state.settings, actor, write_request
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except AdmissionError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return JSONResponse(status_code=207, content=response.model_dump(mode="json"))


@app.get("/api/v1/settings/effective")
async def read_effective_settings(
    request: Request,
    actor: Principal = Depends(principal),
    project_id: str | None = None,
) -> dict:
    """Every setting with its value **and where it came from**.

    Provenance is the point: "it is set to X" is not actionable without "by
    whom, at which level, and can I change it".
    """
    return {
        "settings": await effective_settings(
            request.app.state.pool,
            org_id=actor.org_id,
            project_id=project_id or actor.project_id,
            user_id=actor.user_id,
        )
    }


@app.put("/api/v1/settings/{scope}/{key}")
async def write_setting(
    request: Request,
    scope: str,
    key: str,
    body: dict,
    actor: Principal = Depends(principal),
) -> dict:
    from .auth import ADMIN, CONFIG_WRITE

    # A user setting their own preference needs no config capability; changing
    # policy for anyone else does.
    if scope != "user":
        actor.require(CONFIG_WRITE)
    if body.get("lock") and not actor.can(ADMIN) and not actor.can(CONFIG_WRITE):
        raise HTTPException(status_code=403, detail="locking requires config:write")

    scope_id = {
        "platform": None,
        "org": actor.org_id,
        "project": body.get("project_id") or actor.project_id,
        "user": actor.user_id,
    }.get(scope)
    if scope in ("project",) and scope_id is None:
        raise HTTPException(status_code=400, detail="project_id is required at project scope")

    try:
        resolved = await put_setting(
            request.app.state.pool, key, body.get("value"),
            scope=scope, scope_id=scope_id, set_by=actor.user_id,
            lock=bool(body.get("lock")),
            org_id=actor.org_id,
            project_id=body.get("project_id") or actor.project_id,
        )
    except SettingError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    await record_audit_setting(request.app.state.pool, actor, scope, key, body)
    return {"key": resolved.key, "value": resolved.value,
            "source": resolved.source, "locked_by": resolved.locked_by}


async def record_audit_setting(pool, actor, scope, key, body) -> None:
    from .audit import record_audit

    await record_audit(
        pool, actor, action="settings.set", target_type="setting", target_id=key,
        detail={"scope": scope, "value": body.get("value"), "lock": bool(body.get("lock"))},
    )


@app.get("/api/v1/audit")
async def read_audit(
    request: Request,
    actor: Principal = Depends(principal),
    project_id: str | None = None,
    limit: int = 100,
) -> dict:
    try:
        return await audit_trail(
            request.app.state.pool, actor, project_id=project_id, limit=limit
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/reprocess")
async def create_reprocess(
    request: Request, body: dict, actor: Principal = Depends(principal)
) -> dict:
    """Rebuild what a stale generator produced. Note it cannot un-redact:
    anything removed before storage is not recoverable here."""
    state = request.app.state
    try:
        return await request_reprocess(
            state.pool, state.queue, actor,
            selector=body.get("selector", {}),
            stage=body.get("stage", "enrich"),
            dry_run=bool(body.get("dry_run")),
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/memories/{memory_id}/tree")
async def read_memory_tree(
    request: Request, memory_id: str, relation: str = "part_of",
    actor: Principal = Depends(principal),
) -> dict:
    """What this memory contains and what contains it, both directions.

    `part_of` is containment: the parent's members *are* the children's, so
    there is nothing to keep in sync. An alert scoped to a memory reads through
    exactly this walk, which is why it is worth being able to see.
    """
    from .memories import tree

    try:
        return await tree(request.app.state.pool, actor, memory_id, relation=relation)
    except MemoryError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/expiry/sweep")
async def sweep_expiry(
    request: Request, body: dict, actor: Principal = Depends(principal)
) -> dict:
    """Apply every memory type's `on_expiry` to whatever is due.

    Runs on the ordinary reconcile schedule; this is the same pass, on demand
    and previewable. `dry_run` defaults to **true**: the difference between the
    two is a deletion cascade, and a sweep is the one operation here whose
    default should not do anything.

    What it reports is scoped to what the caller can see, because so is what it
    does — a private record is invisible to everyone but its owner, including
    here. The scheduled pass covers each owner in turn and therefore covers more
    than any single person's run of this will.
    """
    from .memories import sweep_expired

    state = request.app.state
    try:
        return await sweep_expired(
            state.pool, state.queue, actor,
            project_id=body.get("project_id"),
            limit=int(body.get("limit", 500)),
            dry_run=bool(body.get("dry_run", True)),
        )
    except MemoryError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/projects/{project_id}/expiring")
async def read_expiring(
    request: Request, project_id: str, limit: int = 200,
    actor: Principal = Depends(principal),
) -> dict:
    """What is already due, and under which policy.

    Separate from the sweep because *what would happen* is a question people ask
    without wanting anything to happen -- and a retention policy nobody can
    inspect before it runs is one nobody will turn on.
    """
    from .memories import due_for_expiry

    try:
        actor.require(DATA_READ)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    rows = await due_for_expiry(request.app.state.pool, project_id=project_id, limit=limit)
    return {
        "due": [
            {"data_id": r["data_id"], "memory_id": r["memory_id"], "type": r["type"],
             "on_expiry": r["on_expiry"], "expired_at": r["expires_at"].isoformat()}
            for r in rows
        ],
        "capped": len(rows) == limit,
    }


@app.post("/api/v1/standing-queries", status_code=201)
async def create_standing(
    request: Request, body: dict, actor: Principal = Depends(principal)
) -> dict:
    """Say once what you want to be told about.

    Created **disabled**, like a crawler: enabling requires a backtest, because
    a selector that matches everything looks exactly like one that works until
    somebody reads what it caught.
    """
    from .standing import StandingError, create

    try:
        return await create(
            request.app.state.pool, actor,
            project_id=body.get("project_id", ""), name=body.get("name", "untitled"),
            selector=body.get("selector") or {}, delivery=body.get("delivery"),
            kind=body.get("kind", "arrival"),
            date_field=body.get("date_field"),
            offset_days=body.get("offset_days"),
            window_days=int(body.get("window_days", 1)),
        )
    except (StandingError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/projects/{project_id}/standing-queries")
async def list_standing(
    request: Request, project_id: str, actor: Principal = Depends(principal)
) -> dict:
    from .standing import StandingError, listing

    try:
        return {"queries": await listing(request.app.state.pool, actor, project_id)}
    except (StandingError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/standing-queries/{query_id}")
async def read_standing(
    request: Request, query_id: str, actor: Principal = Depends(principal)
) -> dict:
    from .standing import StandingError, get

    try:
        return await get(request.app.state.pool, actor, query_id)
    except (StandingError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.patch("/api/v1/standing-queries/{query_id}")
async def patch_standing(
    request: Request, query_id: str, body: dict, actor: Principal = Depends(principal)
) -> dict:
    """Editing what matches drops the approval, as editing a crawler drops its
    dry run: an approval belongs to the question that was asked."""
    from .standing import StandingError, update

    try:
        return await update(
            request.app.state.pool, actor, query_id,
            selector=body.get("selector"), delivery=body.get("delivery"),
            name=body.get("name"),
        )
    except (StandingError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.delete("/api/v1/standing-queries/{query_id}")
async def delete_standing(
    request: Request, query_id: str, actor: Principal = Depends(principal)
) -> dict:
    from .standing import StandingError, delete

    try:
        return await delete(request.app.state.pool, actor, query_id)
    except (StandingError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/standing-queries/{query_id}/backtest")
async def backtest_standing(
    request: Request, query_id: str, body: dict | None = None,
    actor: Principal = Depends(principal),
) -> dict:
    """What it *would* have caught, with the matches themselves.

    A count cannot tell a selector that works from one that caught the whole
    corpus, which is the lesson the alert backtest had to learn twice.
    """
    from .standing import StandingError, evaluate

    from .standing import evaluate_date

    body = body or {}
    try:
        actor.require(DATA_READ)
        kind = await request.app.state.pool.fetchval(
            "SELECT kind FROM standing_queries WHERE query_id = $1", query_id)
        # A date rule has no sequence to walk from: what would have caught
        # something is the window, so the backtest is the same window with its
        # writes withheld.
        if kind == "date":
            return await evaluate_date(
                request.app.state.pool, query_id, trigger="backtest", record=False)
        return await evaluate(
            request.app.state.pool, query_id, trigger="backtest", record=False,
            from_sequence=int(body.get("from_sequence", 0)),
        )
    except (StandingError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/standing-queries/{query_id}/enabled")
async def enable_standing(
    request: Request, query_id: str, body: dict, actor: Principal = Depends(principal)
) -> dict:
    from .standing import StandingError, set_enabled

    try:
        return await set_enabled(
            request.app.state.pool, actor, query_id, bool(body.get("enabled", True)))
    except (StandingError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/standing-queries/{query_id}/matches")
async def standing_matches(
    request: Request, query_id: str, since: int = 0, limit: int = 100,
    actor: Principal = Depends(principal),
) -> dict:
    """The feed, from a cursor.

    Visibility is the reader's, resolved now rather than copied when the match
    was recorded -- a record unshared since is not in their feed today.
    """
    from .standing import StandingError, matches_for

    try:
        return await matches_for(
            request.app.state.pool, actor, query_id, since=since, limit=limit)
    except (StandingError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.put("/api/v1/workflows")
async def put_workflow(
    request: Request, body: dict, actor: Principal = Depends(principal)
) -> dict:
    """Define a workflow, or redefine it.

    A redefinition bumps the version and leaves running instances where they
    are: each pinned the version it started on, and moving a thousand live
    instances onto a graph they never entered is what the pin prevents.
    """
    from .workflows import WorkflowError, upsert_definition

    try:
        return await upsert_definition(
            request.app.state.pool, actor,
            project_id=body["project_id"], external_id=body["external_id"],
            name=body.get("name", body["external_id"]), config=body["config"],
            description=body.get("description"),
            max_transitions=int(body.get("max_transitions", 1000)),
        )
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"missing {exc}") from exc
    except (WorkflowError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/projects/{project_id}/workflows")
async def get_workflows(
    request: Request, project_id: str, actor: Principal = Depends(principal)
) -> dict:
    from .workflows import WorkflowError, list_definitions

    try:
        return {"workflows": await list_definitions(request.app.state.pool, actor, project_id)}
    except (WorkflowError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/workflows/{definition_id}/instances", status_code=201)
async def post_instance(
    request: Request, definition_id: str, body: dict,
    actor: Principal = Depends(principal),
) -> dict:
    """Start one. Idempotent on `external_id`, because an engine retrying a
    start after a timeout is the ordinary case rather than the exception."""
    from .workflows import WorkflowError, start_instance

    try:
        return await start_instance(
            request.app.state.pool, actor, definition_id,
            external_id=body["external_id"], case_id=body.get("case_id"),
            payload=body.get("payload"),
            access_level=body.get("access_level", "private"),
            shared_with=body.get("shared_with"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"missing {exc}") from exc
    except (WorkflowError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/instances/{instance_id}/input")
async def post_input(
    request: Request, instance_id: str, body: dict,
    actor: Principal = Depends(principal),
) -> dict:
    """The one verb that moves state.

    `expected_seq` is optional and is how a caller says which version of the
    instance it decided against. Losing that race is a **409 carrying the
    current state**, so the caller can decide rather than re-read.
    """
    from .workflows import Conflict, WorkflowError, apply_input

    try:
        return await apply_input(
            request.app.state.pool, actor, instance_id,
            trigger=body["trigger"],
            expected_seq=body.get("expected_seq"),
            data_id=body.get("data_id"), payload=body.get("payload"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"missing {exc}") from exc
    except Conflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": str(exc), "current_state": exc.current_state,
                    "current_seq": exc.current_seq},
        ) from exc
    except (WorkflowError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/instances/{instance_id}")
async def get_instance(
    request: Request, instance_id: str, actor: Principal = Depends(principal)
) -> dict:
    from .workflows import WorkflowError, get_state

    try:
        return await get_state(request.app.state.pool, actor, instance_id)
    except (WorkflowError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/instances/{instance_id}/history")
async def get_instance_history(
    request: Request, instance_id: str, limit: int = 200,
    actor: Principal = Depends(principal),
) -> dict:
    from .workflows import WorkflowError, history

    try:
        return await history(request.app.state.pool, actor, instance_id, limit=limit)
    except (WorkflowError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/instances/{instance_id}/verify")
async def get_instance_verify(
    request: Request, instance_id: str, actor: Principal = Depends(principal)
) -> dict:
    """Re-fold the log and compare it to the cached state.

    `current_state` is a denormalisation, and one nobody can check is one people
    stop trusting the first time something looks wrong.
    """
    from .workflows import WorkflowError, verify_state

    try:
        return await verify_state(request.app.state.pool, actor, instance_id)
    except (WorkflowError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/projects/{project_id}/instances")
async def get_instances(
    request: Request, project_id: str, state: str | None = None,
    definition_id: str | None = None, overdue: bool = False, limit: int = 100,
    actor: Principal = Depends(principal),
) -> dict:
    """The dashboard query: what is running, and what is past its deadline.

    `overdue` is the one that matters. In a graph with no topological order,
    *stuck* is not derivable from position — a deadline is the only thing that
    can say it.
    """
    from .workflows import WorkflowError, list_instances

    try:
        return {"instances": await list_instances(
            request.app.state.pool, actor, project_id, state=state,
            definition_id=definition_id, overdue=overdue, limit=limit)}
    except (WorkflowError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/meetings/attendees")
async def resolve_attendees(
    request: Request, body: dict, actor: Principal = Depends(principal)
) -> dict:
    """Who in this room is a principal here, and who is not.

    Answered **before** the write rather than after it, because the number that
    matters is how many attendees did not resolve: a transcript restricted to
    one of six people is technically correct and practically useless, and the
    moment to see that is while deciding, not while wondering why nobody can
    find it.

    Discloses nothing new — a member can already list the organisation's
    members. It reports which of *these* addresses are among them.
    """
    from .meetings import meeting_access

    try:
        actor.require(DATA_READ)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    attendees = body.get("attendees") or []
    if not isinstance(attendees, list):
        raise HTTPException(status_code=400, detail="attendees must be a list of addresses")
    return await meeting_access(request.app.state.pool, actor.org_id, attendees)


@app.get("/api/v1/generators")
async def list_generators(actor: Principal = Depends(principal)) -> dict:
    """What can be made from a set of records.

    Served rather than documented twice, for the same reason the predicates and
    the compaction algorithms are: a list typed into a console is a second copy
    of a vocabulary, and the copy is the one that goes stale.
    """
    from .derive import GENERATORS

    return {"generators": [
        {"name": name, "label": spec["label"], "describe": spec["describe"],
         # Only a summary may archive what it read. A flashcard deck that folded
         # the course away would leave itself as the only remaining copy of it.
         "archivable": spec["archivable"]}
        for name, spec in sorted(GENERATORS.items())
    ]}


@app.post("/api/v1/memories/{memory_id}/derive")
async def derive_from_memory(
    request: Request, memory_id: str, body: dict | None = None,
    actor: Principal = Depends(principal),
) -> dict:
    """Make an artifact from a memory's members.

    `compress` was this with `generator=summary` and `archive=true` welded
    together. Archiving is a policy about the originals, not a property of
    having derived something, and separating them is what turns a study guide,
    a flashcard deck and an obligations extract into configuration rather than
    three more endpoints.
    """
    from .derive import DeriveError, derive
    from .memories import MemoryError as MemErr

    body = body or {}
    state = request.app.state
    try:
        return await derive(
            state.pool, actor, memory_id,
            generator=body.get("generator", "summary"),
            extractor=getattr(state, "extractor", None),
            archive=bool(body.get("archive")),
            max_members=int(body.get("max_members", 200)),
            dry_run=bool(body.get("dry_run")),
        )
    except (DeriveError, MemErr, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/memories/{memory_id}/artifacts")
async def memory_artifacts(
    request: Request, memory_id: str, actor: Principal = Depends(principal)
) -> dict:
    from .derive import artifacts_for

    try:
        return {"artifacts": await artifacts_for(request.app.state.pool, actor, memory_id)}
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/repos/analyze")
async def analyze_repo(
    request: Request, body: dict, actor: Principal = Depends(principal)
) -> dict:
    """Analyse a GitHub repository at one commit.

    Returns immediately with a snapshot in `pending`: the clone and the parse
    are minutes of CPU and belong in a job, not in the process that also serves
    every read. Asking twice for the same commit returns the first snapshot
    rather than starting a second run, and says so with `reused`.

    The quota gate is *before* the enqueue on purpose. An enqueued job cannot be
    un-spent, so charging on completion would let a caller stack fifty clones
    against a budget that refuses only the first one to come back.
    """
    from .repos import RepoError, request_snapshot

    state = request.app.state
    project_id = body.get("project_id") or actor.project_id
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id is required")
    if not body.get("repo_url"):
        raise HTTPException(status_code=400, detail="repo_url is required")
    try:
        async with admitted(
            request, actor, quota.estimate_repo_analysis(), project_id=project_id
        ):
            return await request_snapshot(
                state.pool, state.queue, actor,
                project_id=project_id,
                repo_url=body["repo_url"],
                ref=body.get("ref"),
            )
    except (RepoError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/projects/{project_id}/repos")
async def list_project_repos(
    request: Request, project_id: str, actor: Principal = Depends(principal)
) -> dict:
    """Analysed repositories, each carrying its latest snapshot's state."""
    from .repos import RepoError, repos_for_project

    try:
        return {"repos": await repos_for_project(request.app.state.pool, actor, project_id)}
    except (RepoError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/repos/snapshots/{snapshot_id}")
async def read_repo_snapshot(
    request: Request, snapshot_id: str, actor: Principal = Depends(principal)
) -> dict:
    """One snapshot, with its reports and whatever it could not do.

    The reports are read through the artifacts path rather than copied onto the
    row, so what is returned here is what `generator_version` currently says is
    current -- a snapshot whose prompt has since changed shows a stale report as
    stale rather than as fact.
    """
    from .derive import artifacts_for
    from .repos import RepoError, get

    try:
        snapshot = await get(request.app.state.pool, actor, snapshot_id)
        artifacts = await artifacts_for(
            request.app.state.pool, actor, snapshot["memory_id"]
        )
    except (RepoError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return {**snapshot, "reports": artifacts}


@app.patch("/api/v1/repos/snapshots/{snapshot_id}")
async def update_repo_snapshot(
    request: Request, snapshot_id: str, body: dict,
    actor: Principal = Depends(principal),
) -> dict:
    """How the analysis went, reported by the job that ran it.

    The job is an ordinary API client, so this is an ordinary authenticated
    write. Without it a finished analysis reads as one still running, which is
    worse than a failure that says so.
    """
    from .repos import RepoError, report_result

    try:
        return await report_result(
            request.app.state.pool, actor, snapshot_id,
            status=body.get("status", ""),
            reason=body.get("reason"),
            stats=body.get("stats"),
        )
    except (RepoError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.delete("/api/v1/repos/{case_id}")
async def delete_repo_endpoint(
    request: Request, case_id: str, actor: Principal = Depends(principal)
) -> dict:
    """Stop tracking a repository, taking every snapshot of it.

    Analysis is the one thing in here that creates containers as a side effect
    of a button, and until this existed it was also the one thing with no way
    back: a repository added by mistake stayed on the list for good. The
    snapshots go out through the ordinary memory deletion, so they get the same
    tombstone and reclamation as anything else.
    """
    from .repos import RepoError, delete_repo

    try:
        return await delete_repo(
            request.app.state.pool, request.app.state.queue, actor, case_id
        )
    except (RepoError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/repos/{case_id}/snapshots")
async def list_repo_snapshots(
    request: Request, case_id: str, actor: Principal = Depends(principal)
) -> dict:
    """Every snapshot of one repository, newest first.

    A list, not a comparison. The case correlates snapshots of the same
    repository; nothing here claims two of them are comparable, because deciding
    what "comparable" means across two commits is the feature this deliberately
    does not have.
    """
    from .repos import RepoError, snapshots_for

    try:
        return {"snapshots": await snapshots_for(request.app.state.pool, actor, case_id)}
    except (RepoError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/artifacts/stale")
async def read_stale(
    request: Request, actor: Principal = Depends(principal), limit: int = 100
) -> dict:
    try:
        rows = await stale_artifacts(
            request.app.state.pool, actor, request.app.state.current_generators, limit
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return {"stale": rows, "current": request.app.state.current_generators}


class CrawlerBody(BaseModel):
    project_id: str
    config: CrawlerConfig
    schedule: dict | None = None
    overlap: str = "skip"


class CrawlerPatch(BaseModel):
    config: CrawlerConfig | None = None
    schedule: dict | None = None
    overlap: str | None = None
    enabled: bool | None = None


def _crawler_error(exc: CrawlerError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


@app.post("/api/v1/crawlers", status_code=201)
async def create_crawler_endpoint(
    request: Request, body: CrawlerBody, actor: Principal = Depends(principal)
) -> dict:
    try:
        return await crawling.create_crawler(
            request.app.state.pool, actor, project_id=body.project_id,
            config=body.config, schedule=body.schedule, overlap=body.overlap,
        )
    except CrawlerError as exc:
        raise _crawler_error(exc) from exc
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/projects/{project_id}/crawlers")
async def list_crawlers_endpoint(
    request: Request, project_id: str, actor: Principal = Depends(principal)
) -> dict:
    try:
        return {"crawlers": await crawling.list_crawlers(
            request.app.state.pool, actor, project_id)}
    except (CrawlerError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.patch("/api/v1/crawlers/{crawler_id}")
async def patch_crawler_endpoint(
    request: Request, crawler_id: str, body: CrawlerPatch,
    actor: Principal = Depends(principal),
) -> dict:
    pool = request.app.state.pool
    try:
        if body.config is not None or body.schedule is not None or body.overlap is not None:
            await crawling.update_crawler(
                pool, actor, crawler_id, config=body.config,
                schedule=body.schedule, overlap=body.overlap,
            )
        if body.enabled is not None:
            return await crawling.set_enabled(pool, actor, crawler_id, body.enabled)
        return await crawling.update_crawler(pool, actor, crawler_id)
    except (CrawlerError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.delete("/api/v1/crawlers/{crawler_id}")
async def delete_crawler_endpoint(
    request: Request, crawler_id: str, actor: Principal = Depends(principal)
) -> dict:
    try:
        return await crawling.delete_crawler(request.app.state.pool, actor, crawler_id)
    except (CrawlerError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/crawlers/{crawler_id}/dry-run", status_code=202)
async def dry_run_endpoint(
    request: Request, crawler_id: str, actor: Principal = Depends(principal)
) -> dict:
    """Enumerate and report. Fetches nothing, writes nothing, spends nothing --
    and walks the same code a live run would, so the estimate cannot drift."""
    state = request.app.state
    try:
        run = await crawling.start_run(state.pool, actor, crawler_id, mode="dry")
        return await state.crawl_worker.execute(run["run_id"])
    except (CrawlerError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/crawlers/{crawler_id}/run", status_code=202)
async def run_crawler_endpoint(
    request: Request, crawler_id: str, actor: Principal = Depends(principal)
) -> dict:
    state = request.app.state
    try:
        run = await crawling.start_run(state.pool, actor, crawler_id, mode="live")
        return await state.crawl_worker.execute(run["run_id"])
    except (CrawlerError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/capabilities")
async def capabilities(request: Request) -> dict:
    """What this build can do, counted from the registries themselves.

    Unauthenticated on purpose -- it is shown on the sign-in page, and every
    number is derived from shipped code rather than from tenant data. Nothing
    here says anything about who is using the system or what they stored.

    Counted rather than written down: a landing page that claims a number a
    maintainer typed will be wrong within a month, and wrong in the direction
    that overstates.
    """
    from .alerts import SURFACES
    from .classify import _EXTENSION_MAP, _MIME_MAP
    from .connectors import CATALOG
    from .derive import GENERATORS
    from .crawlers import STRATEGIES
    from .parsers import supported_formats
    from .prompts import BY_DATA_TYPE, registry
    from .providers import PROVIDERS

    rows = registry()
    return {
        "formats": len(supported_formats()),
        "data_types": len(rows),
        "prompts": len({r["prompt"] for r in rows}),
        "mime_types": len(_MIME_MAP),
        "extensions": len(_EXTENSION_MAP),
        "webhook_providers": len(PROVIDERS),
        "crawler_strategies": len(STRATEGIES),
        # Counted from the vocabulary itself, so a surface that is added
        # without being documented still shows up, and one that is removed
        # stops being claimed.
        "alert_surfaces": len(SURFACES),
        # Both, because the difference is the honest part: an entry that needs
        # something not built is listed rather than hidden, and a count that
        # silently dropped it would read as complete coverage.
        "connectors": len(CATALOG),
        "connectors_available": sum(1 for c in CATALOG if c.requires is None),
        # What can be derived from a memory. Served for the same reason the
        # alert surfaces are: a console that types its own list is a second
        # copy of a vocabulary.
        "generators": len(GENERATORS),
        "embed_model": request.app.state.embedder.model_id,
        "media_interpretation": request.app.state.multimodal.enabled,
        # Two capabilities that are configuration rather than code, so the page
        # claims them only where they are switched on. Both spend a model call
        # on a path that otherwise costs an HTTP request, and both are off by
        # default -- claiming them everywhere would claim a bill nobody agreed to.
        "url_context": request.app.state.settings.url_context,
        "repo_analysis": bool(request.app.state.settings.repo_analysis_job),
        # How somebody gets an account here. Unauthenticated on purpose: the
        # sign-in page needs it *before* anyone signs in, and it discloses
        # nothing an attempt to register would not.
        #
        # Without it the page had to describe both possibilities and commit to
        # neither, which is the copy-that-hedges the rest of this build avoids
        # by counting things.
        "registration_mode": (await resolve_setting(
            request.app.state.pool, "registration_mode")).value,
    }


@app.get("/api/v1/prompts")
async def prompt_registry_endpoint(actor: Principal = Depends(principal)) -> dict:
    """Every data type the classifier can produce, and the prompt it reaches."""
    from .prompts import registry

    rows = registry()
    return {
        "prompts": rows,
        "data_types": len(rows),
        "distinct_prompts": len({r["prompt"] for r in rows}),
    }


@app.get("/api/v1/projects/{project_id}/tags")
async def list_tags_endpoint(
    request: Request, project_id: str, limit: int = 200,
    actor: Principal = Depends(principal),
) -> dict:
    """Tags in use, so a screen can offer them rather than ask them to be typed."""
    from .retrieval import project_tags

    try:
        return {"tags": await project_tags(
            request.app.state.pool, actor, project_id, limit=limit)}
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/projects/{project_id}/keywords")
async def list_keywords_endpoint(
    request: Request, project_id: str, limit: int = 200,
    actor: Principal = Depends(principal),
) -> dict:
    """What this project is about, counted over what the caller can see.

    Keywords were extracted from the first enrichment onwards and read in
    exactly one place -- beside a record you had already found. This is the
    endpoint that turns them from a field into a way in.
    """
    from .retrieval import project_keywords

    try:
        return {"keywords": await project_keywords(
            request.app.state.pool, actor, project_id, limit=limit)}
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/projects/{project_id}/entities")
async def list_entities_endpoint(
    request: Request, project_id: str, type: str | None = None,
    q: str | None = None, actor: Principal = Depends(principal),
) -> dict:
    """Entities the caller can see evidence for. An entity whose every mention
    is hidden does not appear -- listing it would disclose the record."""
    try:
        return {"entities": await entities_mod.list_entities(
            request.app.state.pool, actor, project_id, kind=type, query=q)}
    except (EntityError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/entities/{entity_id}")
async def get_entity_endpoint(
    request: Request, entity_id: str, actor: Principal = Depends(principal)
) -> dict:
    try:
        return await entities_mod.get_entity(request.app.state.pool, actor, entity_id)
    except (EntityError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/entities/{entity_id}/graph")
async def entity_graph_endpoint(
    request: Request, entity_id: str, depth: int = 1,
    predicates: str | None = None, limit: int = 120,
    valid_at: datetime | None = None, as_of: datetime | None = None,
    template: str | None = None,
    actor: Principal = Depends(principal),
) -> dict:
    """The neighbourhood around an entity, as asserted edges.

    Visibility is enforced on every hop rather than on the result, so a path
    cannot pass through a record the caller cannot read — the endpoints of such
    a path would disclose that the record exists.

    `valid_at` asks what was true then. `as_of` asks what we believed then. They
    are different questions and a backfill separates them: a document imported
    today about last year is visible at `valid_at=last year` and invisible at
    `as_of=last month`. Both default to now.

    `template` narrows the traversal to edges a given template drew, and it is
    applied inside the recursion rather than to the result: a path is within a
    template only if every hop of it is, and filtering afterwards would return
    endpoints joined by edges the filter excluded.
    """
    try:
        result = await request.app.state.graph.neighbourhood(
            actor, entity_id=entity_id, depth=depth,
            predicates=[p for p in (predicates or "").split(",") if p] or None,
            limit=limit, valid_at=valid_at, as_of=as_of, template=template,
        )
    except (GraphError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return {
        "root": vars(result.root),
        "nodes": [vars(n) for n in result.nodes],
        # `confidence_class` is a property rather than a field, so `vars()`
        # does not reach it -- and it is the one thing on an edge that says
        # whether a claim was read off the page or read into it.
        "edges": [{**vars(e), "confidence_class": e.confidence_class}
                  for e in result.edges],
        "truncated": result.truncated,
    }


@app.get("/api/v1/entities/{entity_id}/co-mentions")
async def co_mentions_endpoint(
    request: Request, entity_id: str, limit: int = 25,
    actor: Principal = Depends(principal),
) -> dict:
    """Entities named in the same records as this one.

    Weak evidence, reported as a count so a reader can judge it — but it needs
    no extraction, so it works before a model has read anything for
    relationships.
    """
    try:
        return {"co_mentions": await request.app.state.graph.co_mentioned(
            actor, entity_id=entity_id, limit=limit)}
    except (GraphError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/memories/{memory_id}/context")
async def memory_context_endpoint(
    request: Request, memory_id: str, limit: int = 30,
    actor: Principal = Depends(principal),
) -> dict:
    """Everything known about one memory, in one request.

    A memory is the container people think in — "the Acme thread", "the Gita" —
    and the console could say how many records were in one and nothing else. To
    learn what it was *about* you opened Data, filtered, opened a record, read
    its keywords, then opened Entities and guessed which came from here. The
    information existed in four places and belonged in one.

    Counts are scoped to what the caller can read, so two people may
    legitimately see different totals for the same memory: a memory you can see
    may hold records you cannot, and summarising those would report a corpus you
    are not allowed to read.
    """
    from .memories import context

    try:
        return await context(request.app.state.pool, actor, memory_id, limit=limit)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/graph/predicates")
async def graph_predicates_endpoint(actor: Principal = Depends(principal)) -> dict:
    """The closed predicate vocabulary, served rather than documented twice.

    `single_valued` is served with it because it is not a detail: it decides
    which claims supersede one another, so a caller writing facts needs to know
    that a second `located_in` closes the first and a second `works_for` does not.
    """
    from . import predicates as predicates_mod
    from .graph import MAX_DEPTH, PREDICATES, SINGLE_VALUED

    # `describe()` carries the domain, range and confidence class alongside the
    # name. A caller writing facts needs the first two to know which edge it may
    # assert, and a caller reading them needs the third to know whether an edge
    # was stated or interpreted -- neither is inferable from the name.
    return {"predicates": list(PREDICATES), "max_depth": MAX_DEPTH,
            "single_valued": sorted(SINGLE_VALUED),
            "vocabulary": predicates_mod.describe()}


@app.get("/api/v1/templates")
async def templates_endpoint(actor: Principal = Depends(principal)) -> dict:
    """The templates a write may declare, served rather than documented twice.

    Each carries the questions it exists to answer and the predicates it
    offers, because a template is chosen by what you want to ask of the content
    later — and a name alone cannot tell you that `scripture` will record what
    the text claims leads to what while `incident` will record what caused what.
    """
    from . import graph_templates

    return {"templates": graph_templates.registry()}


@app.get("/api/v1/entities/{entity_id}/history")
async def entity_history_endpoint(
    request: Request, entity_id: str, limit: int = 200,
    actor: Principal = Depends(principal),
) -> dict:
    """Every claim that has touched this entity, closed and open alike.

    Deliberately not time-filtered: this is the view that answers *how did we
    come to believe this*, so a superseded fact is the point rather than noise.
    """
    from .graph import fact_history

    try:
        return {"facts": await fact_history(
            request.app.state.pool, actor, entity_id, limit=limit)}
    except (GraphError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/graph/conflicts")
async def graph_conflicts_endpoint(
    request: Request, project_id: str, limit: int = 50,
    actor: Principal = Depends(principal),
) -> dict:
    """Single-valued predicates holding more than one open value.

    A document and a later thread disagreeing is a query here, not a model call:
    two open `located_in` claims for one subject cannot both be true, and
    supersession refuses to choose between claims that begin at the same instant.
    """
    from .graph import conflicts

    try:
        return {"conflicts": await conflicts(
            request.app.state.pool, actor, project_id, limit=limit)}
    except (GraphError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/facts", status_code=201)
async def assert_fact_endpoint(
    request: Request, body: dict, actor: Principal = Depends(principal),
) -> dict:
    """State a fact outright — no document, no model, no cost.

    Every claim used to need a record to descend from, because
    `entity_edges.source_data_id` is NOT NULL. An agent that already knew
    something had to write a document for an extractor to read it back out.
    `basis` keeps an asserted claim distinguishable from an inferred one, which
    is the first thing anyone auditing the graph asks.
    """
    from .graph import assert_fact

    try:
        return await assert_fact(
            request.app.state.pool, actor,
            project_id=body["project_id"], subject_id=body["subject_id"],
            predicate=body["predicate"], object_id=body["object_id"],
            valid_from=body.get("valid_from"),
            confidence=float(body.get("confidence", 1.0)),
            access_level=body.get("access_level", "private"),
            shared_with=body.get("shared_with"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"missing {exc}") from exc
    except (GraphError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/facts/{fact_id}/retract")
async def retract_fact_endpoint(
    request: Request, fact_id: str, body: dict | None = None,
    actor: Principal = Depends(principal),
) -> dict:
    """Withdraw a claim without erasing that it was made.

    Distinct from supersession: `valid_to` says the claim stopped being true,
    `retracted_at` says we should not have recorded it. Neither deletes a row —
    an earlier `as_of` must still return what we believed at the time.
    """
    from .graph import retract_fact

    try:
        return await retract_fact(
            request.app.state.pool, actor, fact_id, (body or {}).get("reason"))
    except (GraphError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/projects/{project_id}/source-lag")
async def source_lag_endpoint(
    request: Request, project_id: str, actor: Principal = Depends(principal)
) -> dict:
    """How far behind each source is, per scope.

    The number every project signal depends on. A signal computed over a source
    that stopped syncing is confidently wrong, and *"no activity for seven
    days"* is indistinguishable from *"the connector broke seven days ago"*
    without it.
    """
    from .crawling import source_lag

    actor.require(DATA_READ)
    return {"sources": await source_lag(request.app.state.pool, project_id)}


@app.get("/api/v1/compaction/algorithms")
async def compaction_algorithms_endpoint(actor: Principal = Depends(principal)) -> dict:
    """What a job can be set to do, served rather than hardcoded in a console.

    `needs_model` is on each one because it decides whether a job can run at all
    on a deployment with no extractor configured — and that is better answered
    before somebody schedules it than by a failed run at three in the morning.
    """
    from .compaction import ALGORITHMS

    return {"algorithms": ALGORITHMS}


@app.post("/api/v1/compaction/jobs", status_code=201)
async def create_compaction_job_endpoint(
    request: Request, body: dict, actor: Principal = Depends(principal)
) -> dict:
    """Schedule a compaction: one memory, one algorithm.

    Created stopped. Compaction moves records out of the working set, so it has
    to be previewed before it can be scheduled — see `/preview`.
    """
    from .compaction import CompactionError, create_job

    try:
        return await create_job(
            request.app.state.pool, actor,
            project_id=body["project_id"], name=body["name"],
            memory_id=body["memory_id"], algorithm=body["algorithm"],
            options=body.get("options"), schedule=body.get("schedule"))
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"missing {exc}") from exc
    except (CompactionError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/projects/{project_id}/compaction/jobs")
async def list_compaction_jobs_endpoint(
    request: Request, project_id: str, actor: Principal = Depends(principal)
) -> dict:
    from .compaction import CompactionError, list_jobs

    try:
        return {"jobs": await list_jobs(request.app.state.pool, actor, project_id)}
    except (CompactionError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.patch("/api/v1/compaction/jobs/{job_id}")
async def update_compaction_job_endpoint(
    request: Request, job_id: str, body: dict, actor: Principal = Depends(principal)
) -> dict:
    """Editing what it would do drops the preview and stops the job."""
    from .compaction import CompactionError, update_job

    try:
        return await update_job(request.app.state.pool, actor, job_id, body)
    except (CompactionError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/compaction/jobs/{job_id}/preview")
async def preview_compaction_endpoint(
    request: Request, job_id: str, actor: Principal = Depends(principal)
) -> dict:
    """What a live run would archive, having archived nothing.

    The same code path with its writes withheld, so what it reports is what
    would actually happen — and it is what unlocks scheduling.
    """
    from .compaction import CompactionError, run

    state = request.app.state
    try:
        return await run(state.pool, actor, job_id=job_id, mode="dry",
                         trigger="manual", extractor=state.extractor)
    except (CompactionError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/compaction/jobs/{job_id}/run")
async def run_compaction_endpoint(
    request: Request, job_id: str, actor: Principal = Depends(principal)
) -> dict:
    """Run it now. Members are archived, never deleted."""
    from .compaction import CompactionError, run

    state = request.app.state
    try:
        return await run(state.pool, actor, job_id=job_id, mode="live",
                         trigger="manual", extractor=state.extractor)
    except (CompactionError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/compaction/jobs/{job_id}/enabled")
async def enable_compaction_endpoint(
    request: Request, job_id: str, body: dict, actor: Principal = Depends(principal)
) -> dict:
    """409 until this version has been previewed."""
    from .compaction import CompactionError, set_enabled

    try:
        return await set_enabled(
            request.app.state.pool, actor, job_id, bool(body.get("enabled", True)))
    except (CompactionError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/compaction/jobs/{job_id}/runs")
async def compaction_runs_endpoint(
    request: Request, job_id: str, limit: int = 20,
    actor: Principal = Depends(principal)
) -> dict:
    """Every run, with what it looked at, folded away and cost."""
    from .compaction import CompactionError, runs_for

    try:
        return {"runs": await runs_for(request.app.state.pool, actor, job_id, limit)}
    except (CompactionError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.delete("/api/v1/compaction/jobs/{job_id}")
async def delete_compaction_job_endpoint(
    request: Request, job_id: str, actor: Principal = Depends(principal)
) -> dict:
    from .compaction import CompactionError, delete_job

    try:
        return await delete_job(request.app.state.pool, actor, job_id)
    except (CompactionError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/alerts/surfaces")
async def alert_surfaces_endpoint(actor: Principal = Depends(principal)) -> dict:
    """The closed vocabulary, served rather than documented twice.

    A console building a condition form needs to know which fields a surface
    accepts, and hardcoding that list in the UI is how it drifts from the one
    the server validates against.
    """
    from .alerts import OPERATORS, SCOPES, SURFACES

    return {
        "surfaces": {k: sorted(v) for k, v in SURFACES.items()},
        # Served rather than hardcoded in a console, for the same reason the
        # predicate list is: two copies of a vocabulary drift, and the one that
        # drifts is never the one the server validates against.
        "operators": sorted(OPERATORS),
        "scopes": SCOPES,
    }


@app.post("/api/v1/alerts", status_code=201)
async def create_alert_endpoint(
    request: Request, body: dict, actor: Principal = Depends(principal)
) -> dict:
    """Declare an event worth knowing about.

    Starts watching from **now**, never from the beginning of the log: an alert
    that fires a hundred notifications about last month the moment it is saved
    is an alert someone switches off.
    """
    from .alerts import AlertError, create_alert

    try:
        return await create_alert(
            request.app.state.pool, actor,
            project_id=body["project_id"], name=body["name"],
            surface=body["surface"], mode=body.get("mode", "rule"),
            where=body.get("where"), describe=body.get("describe"),
            model_id=body.get("model_id"),
            debounce_seconds=int(body.get("debounce_seconds", 5)),
            batch_cap=int(body.get("batch_cap", 500)),
            scope=body.get("scope"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"missing {exc}") from exc
    except (AlertError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/projects/{project_id}/alerts")
async def list_alerts_endpoint(
    request: Request, project_id: str, actor: Principal = Depends(principal)
) -> dict:
    from .alerts import AlertError, list_alerts

    try:
        return {"alerts": await list_alerts(request.app.state.pool, actor, project_id)}
    except (AlertError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.patch("/api/v1/alerts/{alert_id}")
async def update_alert_endpoint(
    request: Request, alert_id: str, body: dict,
    actor: Principal = Depends(principal)
) -> dict:
    """Editing what matches bumps the version, and un-approves the alert.

    It is also disabled by the same edit. Leaving it on would keep firing a
    question nobody has looked at since it changed.
    """
    from .alerts import AlertError, update_alert

    try:
        return await update_alert(request.app.state.pool, actor, alert_id, body)
    except (AlertError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/alerts/{alert_id}/enabled")
async def set_alert_enabled_endpoint(
    request: Request, alert_id: str, body: dict,
    actor: Principal = Depends(principal)
) -> dict:
    """409 until this version has been backtested. The dry-run gate, renamed."""
    from .alerts import AlertError, set_enabled

    try:
        return await set_enabled(
            request.app.state.pool, actor, alert_id, bool(body.get("enabled", True)))
    except (AlertError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/alerts/{alert_id}/backtest")
async def backtest_alert_endpoint(
    request: Request, alert_id: str, body: dict | None = None,
    actor: Principal = Depends(principal)
) -> dict:
    """Run history through the live path. Records nothing, delivers nothing.

    What it reports is what a live run would do, because it *is* the live run
    with its writes withheld — the crawler's dry-run discipline.
    """
    from .alerts import AlertError, backtest

    try:
        return await backtest(
            request.app.state.pool, actor, alert_id,
            int((body or {}).get("since_sequence", 0)))
    except (AlertError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/alerts/{alert_id}/runs")
async def alert_runs_endpoint(
    request: Request, alert_id: str, limit: int = 20,
    actor: Principal = Depends(principal)
) -> dict:
    """What each evaluation did — including what it deferred."""
    from .alerts import AlertError, runs_for

    try:
        return {"runs": await runs_for(request.app.state.pool, actor, alert_id, limit)}
    except (AlertError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.delete("/api/v1/alerts/{alert_id}")
async def delete_alert_endpoint(
    request: Request, alert_id: str, actor: Principal = Depends(principal)
) -> dict:
    from .alerts import AlertError, delete_alert

    try:
        return await delete_alert(request.app.state.pool, actor, alert_id)
    except (AlertError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


# Not `/api/v1/events`: that path was already the domain event log, and FastAPI
# matches the first registration -- so a second one there is silently shadowed,
# which is how this shipped once and returned pipeline events to a caller asking
# what its alerts had caught.
@app.get("/api/v1/alert-events")
async def poll_events_endpoint(
    request: Request, since: int = 0, alert_id: str | None = None,
    limit: int = 100, actor: Principal = Depends(principal)
) -> dict:
    """What fired, from a cursor.

    Ordered by `sequence` rather than time: two events in the same millisecond
    would otherwise come back in whichever order the planner liked, and a poller
    that re-read from a timestamp would skip one of them.

    Visibility is the subject's, resolved here against the caller's rights now —
    never a copy taken when the alert matched.
    """
    from .alerts import AlertError, poll_events

    try:
        return await poll_events(
            request.app.state.pool, actor, since=since, alert_id=alert_id, limit=limit)
    except (AlertError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/event-subscriptions", status_code=201)
async def create_subscription_endpoint(
    request: Request, body: dict, actor: Principal = Depends(principal)
) -> dict:
    """Register an endpoint to be told at. The secret is returned **once**.

    `https` only, and the URL is refused here *and* re-checked on every send —
    a host that resolves to a public address today can resolve to a private one
    tomorrow, and this service reaches Cloud SQL over the VPC.
    """
    from .event_delivery import DeliveryError, create_subscription

    state = request.app.state
    try:
        return await create_subscription(
            state.pool, actor, state.envelope,
            project_id=body["project_id"], url=body["url"],
            alert_id=body.get("alert_id"),
            standing_query_id=body.get("standing_query_id"))
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"missing {exc}") from exc
    except (DeliveryError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except FetchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/projects/{project_id}/event-subscriptions")
async def list_subscriptions_endpoint(
    request: Request, project_id: str, actor: Principal = Depends(principal)
) -> dict:
    """Never includes the signing secret. It is shown once and rotated, not read."""
    from .event_delivery import DeliveryError, list_subscriptions

    try:
        return {"subscriptions": await list_subscriptions(
            request.app.state.pool, actor, project_id)}
    except (DeliveryError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/event-subscriptions/{subscription_id}/rotate")
async def rotate_subscription_endpoint(
    request: Request, subscription_id: str, actor: Principal = Depends(principal)
) -> dict:
    """New secret; the previous one keeps verifying for the overlap window."""
    from .event_delivery import DeliveryError, rotate_secret

    state = request.app.state
    try:
        return await rotate_secret(state.pool, actor, state.envelope, subscription_id)
    except (DeliveryError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/event-subscriptions/{subscription_id}/deliveries")
async def subscription_deliveries_endpoint(
    request: Request, subscription_id: str, limit: int = 50,
    actor: Principal = Depends(principal)
) -> dict:
    """Attempts, statuses and last error — the "is my endpoint healthy" view."""
    from .event_delivery import DeliveryError, deliveries_for

    try:
        return {"deliveries": await deliveries_for(
            request.app.state.pool, actor, subscription_id, limit)}
    except (DeliveryError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/event-subscriptions/{subscription_id}/replay")
async def replay_deliveries_endpoint(
    request: Request, subscription_id: str, actor: Principal = Depends(principal)
) -> dict:
    """Re-arm dead letters once the endpoint is fixed.

    The rows were kept for exactly this: an hour of downtime is recoverable
    rather than gone.
    """
    from .event_delivery import DeliveryError, replay_dead

    try:
        return await replay_dead(request.app.state.pool, actor, subscription_id)
    except (DeliveryError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/entities/merge")
async def merge_entities_endpoint(
    request: Request, body: dict, actor: Principal = Depends(principal)
) -> dict:
    try:
        return await entities_mod.merge(
            request.app.state.pool, actor,
            source_id=body.get("source_id", ""), target_id=body.get("target_id", ""),
            reason=body.get("reason"),
        )
    except (EntityError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/entities/merges/{merge_id}/undo")
async def unmerge_endpoint(
    request: Request, merge_id: str, actor: Principal = Depends(principal)
) -> dict:
    try:
        return await entities_mod.unmerge(request.app.state.pool, actor, merge_id)
    except (EntityError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/crawl-tick", status_code=202)
async def crawl_tick_endpoint(
    request: Request, body: dict | None = None, actor: Principal = Depends(principal)
) -> dict:
    """Run one scheduler pass now, for this organization's due crawlers.

    The same code Cloud Scheduler drives, scoped to the caller's org -- so
    "what would the scheduler do" is answerable without waiting for the next
    tick, and without being able to start somebody else's crawls.
    """
    state = request.app.state
    try:
        return await crawling.tick_for(
            state.pool, actor, state.crawl_worker,
            limit=int((body or {}).get("limit", 5)),
        )
    except (CrawlerError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/crawlers/{crawler_id}/runs")
async def list_runs_endpoint(
    request: Request, crawler_id: str, actor: Principal = Depends(principal)
) -> dict:
    try:
        return {"runs": await crawling.list_runs(request.app.state.pool, actor, crawler_id)}
    except (CrawlerError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/crawl-runs/{run_id}")
async def get_run_endpoint(
    request: Request, run_id: str, actor: Principal = Depends(principal)
) -> dict:
    try:
        return await crawling.get_run(request.app.state.pool, actor, run_id)
    except (CrawlerError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.patch("/api/v1/crawl-runs/{run_id}")
async def control_run_endpoint(
    request: Request, run_id: str, body: dict, actor: Principal = Depends(principal)
) -> dict:
    try:
        return await crawling.control_run(
            request.app.state.pool, actor, run_id, body.get("action", ""))
    except (CrawlerError, AuthError) as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/v1/ask", response_model=AskResponse)
async def ask_endpoint(
    request: Request, body: AskRequest, actor: Principal = Depends(principal)
) -> AskResponse:
    """Read the corpus by asking it. Retrieval, then generation over exactly
    what retrieval returned -- never a second context-assembly path."""
    from .chat import AnswerFailed, AnswerRateLimited

    cost = quota.estimate_ask(match=list(body.match), passages=body.passages)
    try:
        async with admitted(request, actor, cost, project_id=body.filter.project_id):
            return await ask(
                request.app.state.pool,
                request.app.state.embedder,
                # Resolved for the caller's org rather than the process. An org
                # that assigned nothing gets the deployment's answerer, which is
                # the same object it would have been handed before.
                await request.app.state.engines.answerer_for(
                    request.app.state.pool, org_id=actor.org_id
                ),
                actor,
                body,
                embed_generator=request.app.state.current_generators["embedding"],
                graph=request.app.state.graph,
            )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except BudgetExhausted as exc:
        # Distinguished from a burst refusal by its retry window: a minute for a
        # rate limit, the rest of the day for a budget. A client that cannot
        # tell them apart retries the second one every minute until midnight.
        raise HTTPException(
            status_code=exc.status, detail=str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except AnswerRateLimited as exc:
        # The same shape admission control uses for a deep queue, so a client
        # has one back-off rule rather than one per subsystem.
        raise HTTPException(
            status_code=429, detail=str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except AnswerFailed as exc:
        # The retrieval happened and is recorded; only the generation failed.
        # 502 rather than 500: the fault is the upstream model's.
        raise HTTPException(status_code=502, detail=str(exc)) from exc



@app.get("/api/v1/usage")
async def get_usage(
    request: Request,
    project_id: str | None = None,
    actor: Principal = Depends(principal),
) -> dict:
    """What has been spent today, against what ceiling.

    A spending control nobody can see is a spending control nobody trusts, and
    the first question after a refusal is always "spent on what". So this
    reports the rollup enforcement actually reads, the ceilings that bind, and
    the calls that crossed from a free engine to a paid one -- which is the
    line item that surprises people, because nothing about a successful
    fallback looks like a decision to start paying.
    """
    state = request.app.state
    actor.require(DATA_READ)
    spend = await quota.spend_today(
        state.pool, org_id=actor.org_id,
        project_id=project_id or actor.project_id,
        user_id=actor.user_id,
    )
    rows = await state.pool.fetch(
        """
        -- Cast every sum: `sum()` over bigint returns numeric, which arrives as
        -- a Decimal and serialises to a JSON *string*. A cost that is sometimes
        -- a number and sometimes a string is a client-side bug waiting to be
        -- written, and it would be written against the billing figures.
        SELECT purpose, serving_engine, status,
               count(*)::bigint AS calls,
               sum(credits)::bigint AS credits,
               sum(tokens_in)::bigint AS tokens_in,
               sum(tokens_out)::bigint AS tokens_out,
               sum(tokens_cached)::bigint AS tokens_cached,
               count(*) FILTER (WHERE crossed_to_paid)::bigint AS crossed_to_paid
        FROM usage_events
        WHERE org_id = $1 AND occurred_at >= CURRENT_DATE
        GROUP BY purpose, serving_engine, status
        ORDER BY sum(credits) DESC
        """,
        actor.org_id,
    )
    return {
        "day": "today",
        "budgets": [
            {
                "scope": s.scope,
                "scope_id": s.scope_id,
                "credits": s.credits,
                "limit": s.limit,
                "exhausted": s.exhausted,
            }
            for s in spend
        ],
        "by_engine": [dict(r) for r in rows],
    }



@app.post("/api/v1/mcp")
@app.post("/api/v1/mcp/sse")
async def mcp_endpoint(
    request: Request, actor: Principal = Depends(principal)
) -> Response:
    """The MCP surface, authenticated exactly like everything else.

    Two paths for one handler: `/mcp` is what the streamable transport calls,
    and `/mcp/sse` exists because that is the path the requirement names and a
    configuration written against it should resolve rather than 404.

    Stateless by design -- see `mcp.py` for why a session-bearing transport and
    a service that scales to zero do not combine.
    """
    from . import mcp as mcp_mod

    try:
        message = await request.json()
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"jsonrpc": "2.0", "id": None,
                     "error": {"code": mcp_mod.PARSE_ERROR,
                               "message": "invalid JSON"}},
        )

    # A batch is a list. Notifications inside it produce nothing, so a batch of
    # only notifications correctly answers with no body at all.
    batch = isinstance(message, list)
    messages = message if batch else [message]
    replies = []
    for one in messages:
        reply = await mcp_mod.dispatch(request.app.state, actor, one)
        if reply is not None:
            replies.append(reply)

    if not replies:
        return Response(status_code=202)

    payload = replies if batch else replies[0]
    if "text/event-stream" in (request.headers.get("accept") or ""):
        return Response(
            content=mcp_mod.as_sse(payload),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )
    return JSONResponse(content=payload)


@app.get("/api/v1/mcp")
@app.get("/api/v1/mcp/sse")
async def mcp_manifest(request: Request) -> dict:
    """What this server is, for a human pointing a client at it.

    Unauthenticated on purpose: it names the transport and the tools and
    discloses nothing about anyone's data. Someone configuring a client needs to
    know the URL works before they have a credential in it.
    """
    from . import mcp as mcp_mod

    return {
        "protocolVersion": mcp_mod.PROTOCOL_VERSION,
        "serverInfo": mcp_mod.SERVER_INFO,
        "transport": "streamable-http",
        "endpoint": str(request.url_for("mcp_endpoint")),
        "authentication": "Authorization: Bearer <api key>",
        "tools": [
            {"name": t["name"], "description": t["description"]} for t in mcp_mod.TOOLS
        ],
    }


@app.post("/api/v1/retrieve", response_model=RetrieveResponse)
async def retrieve_endpoint(
    request: Request, body: RetrieveRequest, actor: Principal = Depends(principal)
) -> RetrieveResponse:
    cost = quota.estimate_retrieve(match=list(body.match), limit=body.limit)
    try:
        async with admitted(request, actor, cost, project_id=body.filter.project_id):
            return await retrieve(
                request.app.state.pool,
                request.app.state.embedder,
                actor,
                body,
                embed_generator=request.app.state.current_generators["embedding"],
                # The configured store, not one built per request: the seam is
                # only worth having if the deployment's choice is what search
                # actually traverses.
                graph=request.app.state.graph,
            )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
