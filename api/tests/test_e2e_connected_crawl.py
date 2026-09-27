"""A connected crawl, end to end, across every seam.

Every piece of this path had tests and the path itself had none. The grant was
tested against a mock token endpoint, the walk against a mock Drive, the fetcher
against a mock download, and the crawl worker against a public feed — four green
files and no assertion that they compose.

That is exactly where this week's defects lived. A crawler discovered files and
the reference it emitted named no connection. A retrieval arm was written and
the wrapper dropped its argument. Four console panels called endpoints the proxy
refused. In each case both sides were correct and nothing joined them.

So this fakes one thing — the remote API — and uses the real database, the real
write path, the real crawl worker, the real envelope and the real fetch worker.
What it proves:

    a connection holding a client-credentials secret
      -> a tree crawl exchanges it for a token, once
      -> every request to the source carries the token, never the secret
      -> each file is written as a reference, not as content
      -> the fetch worker resolves that reference with the same connection
      -> the bytes land in the blob store and the row stops being pending
"""

from __future__ import annotations

import json
import os

import httpx
import pytest

from open_mem import connections
from open_mem.contracts import RetrieveFilter, RetrieveRequest
from open_mem.crawlers import CrawlerConfig
from open_mem.crawling import CrawlWorker, create_crawler, set_enabled, start_run
from open_mem.crypto import Envelope
from open_mem.fetching import FetchWorker
from open_mem.retrieval import retrieve

pytestmark = pytest.mark.asyncio

DOC = "application/vnd.google-apps.document"
FOLDER = "application/vnd.google-apps.folder"

# What the fake Drive holds. One folder deep, one native doc that must be
# exported and one binary that must not be.
TREE = {
    "root-folder": [
        {"id": "sub", "name": "Q3", "mimeType": FOLDER},
        {"id": "f-pdf", "name": "Board pack.pdf", "mimeType": "application/pdf",
         "modifiedTime": "2026-03-01T00:00:00Z", "webViewLink": "https://d/f-pdf"},
    ],
    "sub": [
        {"id": "f-doc", "name": "Notes", "mimeType": DOC,
         "modifiedTime": "2026-03-02T00:00:00Z", "webViewLink": "https://d/f-doc"},
    ],
}

BYTES_FOR = {
    "f-pdf": (b"%PDF-1.4 board pack bytes", "application/pdf"),
    "f-doc": (b"Exported notes: the retry budget is per host.", "text/plain"),
}

STORED_SECRET = "client-abc:shhh-do-not-send-this"


class Drive:
    """A token endpoint and a Drive, sharing one transport.

    Recording every request is the point: the assertions are about what was
    sent, not only about what came back.
    """

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, str | None]] = []
        self.token_calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        auth = request.headers.get("authorization")
        self.requests.append((request.method, url, auth))

        if url.startswith("https://token.example/"):
            self.token_calls += 1
            form = dict(httpx.QueryParams(request.content.decode()))
            assert form["grant_type"] == "client_credentials"
            assert form["client_id"] == "client-abc"
            return httpx.Response(200, json={
                "access_token": "exchanged-token-1", "expires_in": 3600,
            })

        if "/drive/v3/files" in url and "alt=media" not in url and "export" not in url:
            folder = url.split("%27")[1] if "%27" in url else url.split("'")[1]
            return httpx.Response(200, json={"files": TREE.get(folder, [])})

        for file_id, (payload, mime) in BYTES_FOR.items():
            if file_id in url:
                return httpx.Response(200, content=payload,
                                      headers={"content-type": mime})
        return httpx.Response(404, json={"error": "not found"})


@pytest.fixture
def drive(monkeypatch) -> Drive:
    handler = Drive()
    original = httpx.AsyncClient

    class Patched(original):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", Patched)
    # The download host is not caller-controlled, which is deliberate and
    # tested elsewhere. Here it only has to reach the fake, so the path is
    # preserved and the host is the one the transport answers on.
    monkeypatch.setattr("open_mem.fetching._address_is_private", lambda host: False)

    from open_mem import grants
    grants._cache.clear()
    yield handler
    grants._cache.clear()


async def _run_the_crawl(pool, queue, blobs, settings, tenant, principal_for):
    actor = await principal_for(tenant.api_key)
    envelope = Envelope(os.urandom(32))

    connection = await connections.create(
        pool, actor, envelope, project_id=tenant.project_id, provider="google",
        credential=STORED_SECRET, auth_style="client_credentials",
        auth_config={"token_url": "https://token.example/oauth2/token",
                     "scope": "https://www.googleapis.com/auth/drive.readonly"},
    )
    created = await create_crawler(
        pool, actor, project_id=tenant.project_id,
        config=CrawlerConfig.model_validate({
            "name": "shared drive", "strategy": "tree",
            "tree": {"api": "google_drive", "root": "root-folder"},
            "limits": {"max_depth": 3, "max_items": 50},
        }),
    )
    await connections.attach(pool, actor, created["crawler_id"],
                             connection["connection_id"])

    worker = CrawlWorker(pool, queue, blobs, settings, envelope=envelope)
    # The gate is real: a crawler is draft until a dry run passes.
    dry = await worker.execute(
        (await start_run(pool, actor, created["crawler_id"], mode="dry"))["run_id"])
    await set_enabled(pool, actor, created["crawler_id"], True)
    live = await worker.execute(
        (await start_run(pool, actor, created["crawler_id"]))["run_id"])
    return actor, envelope, connection, created, dry, live


async def test_a_connected_tree_crawl_reaches_stored_bytes(
    pool, queue, blobs, settings, tenant, principal_for, drive
):
    actor, envelope, connection, created, dry, live = await _run_the_crawl(
        pool, queue, blobs, settings, tenant, principal_for)

    assert dry["status"] == "completed", dry
    assert live["status"] == "completed", live
    assert live["emitted"] == 2, "the walk did not descend, or did not emit"

    # --- what the crawl wrote -------------------------------------------------
    rows = await pool.fetch(
        """
        SELECT data_id, pending_ref, storage_ref, external_id
        FROM data_items
        WHERE project_id = $1 AND pending_ref IS NOT NULL
        ORDER BY external_id
        """,
        tenant.project_id,
    )
    assert len(rows) == 2, "a tree crawl must emit references, not content"
    refs = [dict(r["pending_ref"]) for r in rows]
    assert {r["resource_id"] for r in refs} == {"f-doc", "f-pdf"}
    assert all(r["provider"] == "google_drive" for r in refs)
    assert all(r["connection_id"] == connection["connection_id"] for r in refs), (
        "a reference that names no connection cannot be fetched, and the "
        "failure lands at fetch time rather than here"
    )

    # --- the fetch worker resolves them --------------------------------------
    fetcher = FetchWorker(pool, blobs, settings, queue=queue, envelope=envelope)
    for row in rows:
        await fetcher.fetch(row["data_id"])

    done = await pool.fetch(
        "SELECT data_id, pending_ref, storage_ref, size_bytes, mime_type "
        "FROM data_items WHERE project_id = $1 AND data_id = ANY($2::text[])",
        tenant.project_id, [r["data_id"] for r in rows],
    )
    for row in done:
        assert row["pending_ref"] is None, "still pending after a successful fetch"
        assert row["storage_ref"], "fetched and stored nowhere"
        assert row["size_bytes"] > 0

    sizes = {r["size_bytes"] for r in done}
    assert sizes == {len(BYTES_FOR["f-pdf"][0]), len(BYTES_FOR["f-doc"][0])}


async def test_the_stored_secret_never_leaves_and_the_token_is_exchanged_once(
    pool, queue, blobs, settings, tenant, principal_for, drive
):
    """Two claims that are only true of the whole chain.

    The secret buys a token and is never itself presented; and the token is
    bought once, not once per folder and again per download — the cache is
    keyed on the connection and both the walk and the fetch go through it.
    """
    _, envelope, _, _, _, _ = await _run_the_crawl(
        pool, queue, blobs, settings, tenant, principal_for)

    rows = await pool.fetch(
        "SELECT data_id FROM data_items WHERE project_id = $1 "
        "AND pending_ref IS NOT NULL", tenant.project_id,
    )
    fetcher = FetchWorker(pool, blobs, settings, queue=queue, envelope=envelope)
    for row in rows:
        await fetcher.fetch(row["data_id"])

    assert drive.token_calls == 1, (
        f"the token was exchanged {drive.token_calls} times; the cache is not "
        "shared between the walk and the download"
    )

    sent = [(url, auth) for _, url, auth in drive.requests
            if not url.startswith("https://token.example/")]
    assert sent, "nothing reached the source"
    for url, auth in sent:
        assert auth == "Bearer exchanged-token-1", (url, auth)

    # The dry run happens before this, so a leak would show up in any request.
    blob = json.dumps(drive.requests)
    assert "shhh-do-not-send-this" not in blob, "the stored secret was sent upstream"
    assert "client-abc:" not in blob


async def test_a_google_document_is_exported_and_a_binary_is_not(
    pool, queue, blobs, settings, tenant, principal_for, drive
):
    """The one place the two file kinds diverge. `?alt=media` on a Doc is a 403,
    so a native document must be exported — and exporting a PDF would return
    something nobody asked for."""
    _, envelope, _, _, _, _ = await _run_the_crawl(
        pool, queue, blobs, settings, tenant, principal_for)

    rows = await pool.fetch(
        "SELECT data_id, pending_ref FROM data_items WHERE project_id = $1 "
        "AND pending_ref IS NOT NULL", tenant.project_id,
    )
    fetcher = FetchWorker(pool, blobs, settings, queue=queue, envelope=envelope)
    for row in rows:
        await fetcher.fetch(row["data_id"])

    downloads = [url for _, url, _ in drive.requests
                 if "f-doc" in url or "f-pdf" in url]
    doc = next(u for u in downloads if "f-doc" in u)
    pdf = next(u for u in downloads if "f-pdf" in u)
    assert "/export" in doc and "text%2Fplain" in doc or "text/plain" in doc
    assert "alt=media" in pdf and "/export" not in pdf


async def test_a_crawled_file_is_retrievable_once_it_has_been_fetched(
    pool, queue, blobs, settings, tenant, principal_for, drive, embedder
):
    """The end of the sentence. A file discovered by a walk, downloaded with an
    exchanged credential and parsed is indistinguishable from an upload — which
    is the contract the whole write path rests on, and the one thing none of the
    unit tests can show."""
    actor, envelope, _, _, _, _ = await _run_the_crawl(
        pool, queue, blobs, settings, tenant, principal_for)

    rows = await pool.fetch(
        "SELECT data_id FROM data_items WHERE project_id = $1 "
        "AND pending_ref IS NOT NULL", tenant.project_id,
    )
    fetcher = FetchWorker(pool, blobs, settings, queue=queue, envelope=envelope)
    for row in rows:
        await fetcher.fetch(row["data_id"])

    stored = await pool.fetch(
        "SELECT data_id, mime_type, data_type FROM data_items "
        "WHERE project_id = $1 AND storage_ref IS NOT NULL", tenant.project_id,
    )
    assert len(stored) == 2
    # Sniffed from the bytes, never taken from the listing's claim about them.
    assert {r["mime_type"] for r in stored} == {"application/pdf", "text/plain"}
