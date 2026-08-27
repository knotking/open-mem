from __future__ import annotations

import os

import pytest

# A small dimension keeps the suite fast; the code path is identical.
os.environ.setdefault("EMBED_DIM", "64")
os.environ.setdefault(
    "DATABASE_URL", "postgresql://memdog:memdog@localhost:54329/memdog"
)

from memdog.bootstrap import bootstrap_tenant  # noqa: E402
from memdog.config import load_settings  # noqa: E402
from memdog.db import create_pool, migrate  # noqa: E402
from memdog.inference import build_embedder  # noqa: E402
from memdog.queue import InProcessQueue  # noqa: E402
from memdog.workers import EmbedWorker  # noqa: E402
from memdog.write import EMBED_TOPIC  # noqa: E402


@pytest.fixture(scope="session")
def settings():
    return load_settings()


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def pool(settings):
    pool = await create_pool(settings)
    # A fresh schema per test: the invariants being checked are about what the
    # database enforces, so leftover rows would make them meaningless.
    async with pool.acquire() as conn:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    await migrate(pool, settings)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
def embedder(settings):
    return build_embedder(settings)


@pytest.fixture
async def queue(pool, embedder, settings):
    queue = InProcessQueue()
    worker = EmbedWorker(pool, embedder, settings)
    await worker.ensure_generator()
    worker.register(queue, EMBED_TOPIC)
    try:
        yield queue
    finally:
        await queue.close()


@pytest.fixture
def blobs(tmp_path):
    from memdog.blobs import FilesystemBlobStore

    return FilesystemBlobStore(tmp_path / "blobs")


@pytest.fixture
async def tenant(pool):
    return await bootstrap_tenant(pool, org_name="acme", email="a@example.com")


@pytest.fixture
async def other_tenant(pool):
    return await bootstrap_tenant(pool, org_name="globex", email="b@example.com")


@pytest.fixture
async def principal_for(pool):
    from memdog.auth import ApiKeyVerifier

    verifier = ApiKeyVerifier(pool)

    async def _resolve(token: str):
        return await verifier.verify(token)

    return _resolve
