"""HTTP surface for the spine.

Three endpoints, because three is what it takes to prove both halves of the
system are real: write an item, fetch it by id, find it by meaning. Everything
else in the design widens this.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import Response
from fastapi.responses import JSONResponse

from .auth import ApiKeyVerifier, AuthError, Principal, TokenVerifier
from .blobs import build_blob_store
from .config import load_settings
from .contracts import RetrieveRequest, RetrieveResponse, WriteRequest, WriteResponse
from . import agents, cases, control, memories as memories_mod, models, normalize, sharing
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
from .inference import build_embedder
from .queue import InProcessQueue
from .reprocess import REPROCESS_TOPIC, ReprocessWorker, request_reprocess
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
from .multimodal import build_multimodal
from .workers import EmbedWorker, EnrichWorker, ParseWorker, verify_index_dimension
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
    queue = InProcessQueue()
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
    enrich_worker = EnrichWorker(pool, extractor, settings)
    await enrich_worker.ensure_generator()
    enrich_worker.register(queue)

    app.state.settings = settings
    app.state.pool = pool
    app.state.queue = queue
    app.state.embedder = embedder
    app.state.extractor = extractor
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
    try:
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


@app.get("/api/v1/projects/{project_id}/data")
async def get_project_data(
    request: Request,
    project_id: str,
    actor: Principal = Depends(principal),
    limit: int = 50,
    before: str | None = None,
    state: str | None = None,
) -> dict:
    """Browse what is actually in a project.

    Without this the only ways to find an item are knowing its id or matching a
    search -- neither of which answers "what did I put in here?".
    """
    try:
        return await list_items(
            request.app.state.pool, actor, project_id,
            limit=limit, before=before, state=state,
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/api/v1/projects/{project_id}/overview")
async def get_overview(
    request: Request, project_id: str, actor: Principal = Depends(principal)
) -> dict:
    """The numbers that answer "is this working?" in one call."""
    try:
        return await project_overview(request.app.state.pool, actor, project_id)
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
    )


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
                MemoryError) as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        except AuthError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    return wrapped


@app.get("/api/v1/projects")
async def get_projects(request: Request, actor: Principal = Depends(principal)) -> dict:
    return {"projects": await _control(control.list_projects)(request.app.state.pool, actor)}


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

    write_request = WriteRequest(
        producer_id=session["producer_id"],
        items=[{
            "external_id": body.get("external_id") or session["external_id"],
            "content": {
                "kind": "stored",
                "storage_ref": session["storage_key"],
                "mime_type": session["mime_type"],
                "size": session["received_bytes"],
                "checksum": session["checksum"],
            },
            "memory": body.get("memory"),
        }],
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


@app.post("/api/v1/retrieve", response_model=RetrieveResponse)
async def retrieve_endpoint(
    request: Request, body: RetrieveRequest, actor: Principal = Depends(principal)
) -> RetrieveResponse:
    try:
        return await retrieve(
            request.app.state.pool,
            request.app.state.embedder,
            actor,
            body,
            embed_generator=request.app.state.current_generators["embedding"],
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
