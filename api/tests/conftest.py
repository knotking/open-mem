from __future__ import annotations

import os
from urllib.parse import urlparse

import pytest

# A small dimension keeps the suite fast; the code path is identical.
os.environ.setdefault("EMBED_DIM", "64")

# The local compose instance, and the only database this suite will touch
# without being told otherwise in as many words.
TEST_DATABASE_URL = "postgresql://memdog:memdog@localhost:54329/memdog"

# Hosts a test database can live on. Everything else is somebody's data.
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres", "db"}


def _guard(url: str) -> str:
    """Refuse to run against anything that is not obviously a test database.

    Every `pool` fixture below begins with `DROP SCHEMA public CASCADE`. That is
    correct for a scratch database and catastrophic for any other, and until now
    the only thing standing between the two was `setdefault` -- so an exported
    `DATABASE_URL`, of the kind anyone running a deploy or opening a psql
    session has, silently became the thing the suite dropped.

    It nearly cost a seeded corpus in development. Against Cloud SQL it would
    have cost the corpus.

    The rule is deliberately narrow: a local host, or an explicit opt-in for the
    CI service container that is neither. Naming the variable
    `I_KNOW_THIS_DATABASE_IS_DISPOSABLE` is not decoration -- a guard people can
    switch off by accident is not a guard, and this one cannot be satisfied by a
    variable anybody sets for another purpose.
    """
    if os.environ.get("I_KNOW_THIS_DATABASE_IS_DISPOSABLE") == "yes":
        return url

    host = urlparse(url).hostname or ""
    database = (urlparse(url).path or "").lstrip("/")
    if host in LOCAL_HOSTS:
        return url
    # A hostname that merely *contains* "test" is not evidence -- plenty of
    # production hosts do. A database named for testing, on a local-looking
    # host, is. Anything else stops here.
    raise pytest.UsageError(
        f"refusing to run the test suite against {host or url!r} "
        f"(database {database!r}).\n\n"
        "Every test drops and recreates the public schema, so this suite only "
        "runs against a database on localhost. Start the local one with "
        "`docker compose up -d` in api/, or unset DATABASE_URL to use it.\n\n"
        "If this really is a disposable database on a remote host -- a CI "
        "service container -- set I_KNOW_THIS_DATABASE_IS_DISPOSABLE=yes."
    )


os.environ["DATABASE_URL"] = _guard(
    os.environ.get("DATABASE_URL") or TEST_DATABASE_URL
)

from memdog.bootstrap import bootstrap_tenant  # noqa: E402
from memdog.config import load_settings  # noqa: E402
from memdog.db import create_pool, migrate  # noqa: E402
from memdog.inference import build_embedder  # noqa: E402
from memdog.queue import InProcessQueue  # noqa: E402
from memdog.extraction import build_extractor  # noqa: E402
from memdog.workers import (  # noqa: E402
    EmbedWorker,
    EnrichWorker,
    EventWorker,
    ParseWorker,
)
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
def extractor():
    return build_extractor(load_settings())


@pytest.fixture
async def queue(pool, embedder, extractor, settings, blobs):
    queue = InProcessQueue()
    parse = ParseWorker(pool, blobs, queue=queue)
    parse.register(queue)
    embed = EmbedWorker(pool, embedder, settings, queue=queue)
    await embed.ensure_generator()
    embed.register(queue, EMBED_TOPIC)
    enrich = EnrichWorker(pool, extractor, settings)
    await enrich.ensure_generator()
    enrich.register(queue)
    # The log is the record of work; the event worker turns it into pipeline
    # calls, exactly as the deployed service does.
    EventWorker(
        pool, queue, parse_worker=parse, embed_worker=embed, enrich_worker=enrich
    ).register(queue)
    queue.generators = {
        "embedding": embed.generator_version,
        "extraction": enrich.generator_version,
    }
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
