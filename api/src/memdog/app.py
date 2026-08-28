"""HTTP surface for the spine.

Three endpoints, because three is what it takes to prove both halves of the
system are real: write an item, fetch it by id, find it by meaning. Everything
else in the design widens this.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .auth import ApiKeyVerifier, AuthError, Principal, TokenVerifier
from .blobs import build_blob_store
from .config import load_settings
from .contracts import RetrieveRequest, RetrieveResponse, WriteRequest, WriteResponse
from .crypto import Envelope
from .db import create_pool, migrate
from .inference import build_embedder
from .queue import InProcessQueue
from .retrieval import NotFound, get_artifacts, get_item, retrieve, stale_artifacts
from .extraction import build_extractor
from .workers import EmbedWorker, EnrichWorker, verify_index_dimension
from .write import EMBED_TOPIC, AdmissionError, write_items


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    pool = await create_pool(settings)
    await migrate(pool, settings)

    embedder = build_embedder(settings)
    # Refuse to serve against an index this engine cannot write to.
    await verify_index_dimension(pool, embedder)
    queue = InProcessQueue()
    embed_worker = EmbedWorker(pool, embedder, settings, queue=queue)
    await embed_worker.ensure_generator()
    embed_worker.register(queue, EMBED_TOPIC)

    extractor = build_extractor(settings)
    enrich_worker = EnrichWorker(pool, extractor, settings)
    await enrich_worker.ensure_generator()
    enrich_worker.register(queue)

    app.state.settings = settings
    app.state.pool = pool
    app.state.queue = queue
    app.state.embedder = embedder
    app.state.extractor = extractor
    # What "current" means for staleness: the generator each purpose is
    # assigned right now.
    app.state.current_generators = {
        "embedding": embed_worker.generator_version,
        "extraction": enrich_worker.generator_version,
    }
    app.state.blobs = build_blob_store(settings)
    app.state.envelope = Envelope.from_settings(settings)
    app.state.verifier: TokenVerifier = ApiKeyVerifier(pool)
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
            request.app.state.pool, request.app.state.embedder, actor, body
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
