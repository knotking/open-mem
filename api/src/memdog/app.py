"""HTTP surface for the spine.

Three endpoints, because three is what it takes to prove both halves of the
system are real: write an item, fetch it by id, find it by meaning. Everything
else in the design widens this.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .auth import ApiKeyVerifier, AuthError, Principal, TokenVerifier
from .blobs import FilesystemBlobStore
from .config import load_settings
from .contracts import RetrieveRequest, RetrieveResponse, WriteRequest, WriteResponse
from .crypto import Envelope
from .db import create_pool, migrate
from .inference import build_embedder
from .queue import InProcessQueue
from .retrieval import NotFound, get_item, retrieve
from .workers import EmbedWorker, verify_index_dimension
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
    worker = EmbedWorker(pool, embedder, settings)
    await worker.ensure_generator()
    worker.register(queue, EMBED_TOPIC)

    app.state.settings = settings
    app.state.pool = pool
    app.state.queue = queue
    app.state.embedder = embedder
    app.state.blobs = FilesystemBlobStore(Path(os.environ.get("BLOB_ROOT", "./.blobs")))
    app.state.envelope = Envelope.from_settings(settings)
    app.state.verifier: TokenVerifier = ApiKeyVerifier(pool)
    try:
        yield
    finally:
        await queue.close()
        await pool.close()


app = FastAPI(title="mem-dog", version="0.1.0", lifespan=lifespan)


async def principal(
    request: Request, authorization: str = Header(default="")
) -> Principal:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="bearer credential required")
    try:
        return await request.app.state.verifier.verify(token)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


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
