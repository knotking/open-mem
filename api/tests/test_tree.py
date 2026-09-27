"""Folder recursion, and the download that follows it.

A listing returns a name and an id; a corpus needs the bytes. Splitting that
across two stages — `tree` discovers references, the fetch worker resolves them
— is what lets a walk of forty thousand files cost forty thousand rows rather
than forty thousand downloads held open inside one run.

So the tests here are mostly about the seam: that a walk is bounded, that a
reference carries enough to be fetched later and no more, and that the
credential which makes the download possible does not travel further than the
host it was issued for.
"""

from __future__ import annotations

import httpx
import pytest

from open_mem import fetching
from open_mem.crawlers import Auth, Budget, CrawlerConfig, Throttle, discover
from open_mem.fetching import FetchError, download_url

def _config(**over) -> CrawlerConfig:
    base = {
        "name": "drive", "strategy": "tree",
        "tree": {"api": "google_drive", "root": "root-folder"},
        "limits": {"max_items": 100, "max_depth": 3},
    }
    base.update(over)
    return CrawlerConfig.model_validate(base)


def _drive(pages: dict[str, list[dict]]):
    """A Drive that answers `'<folder>' in parents` out of a dict."""
    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params.get("q", "")
        folder = q.split("'")[1] if "'" in q else ""
        return httpx.Response(200, json={"files": pages.get(folder, [])})
    return handler


def _patch(monkeypatch, handler):
    original = httpx.AsyncClient

    class Patched(original):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", Patched)


FOLDER = "application/vnd.google-apps.folder"


async def test_a_walk_descends_into_subfolders_and_references_the_files(
    monkeypatch
):
    _patch(monkeypatch, _drive({
        "root-folder": [
            {"id": "d1", "name": "Design", "mimeType": FOLDER},
            {"id": "f1", "name": "Charter.pdf", "mimeType": "application/pdf",
             "modifiedTime": "2026-01-01T00:00:00Z", "webViewLink": "https://d/f1"},
        ],
        "d1": [{"id": "f2", "name": "Spec", "mimeType":
                "application/vnd.google-apps.document",
                "modifiedTime": "2026-02-01T00:00:00Z"}],
    }))
    found, _, _ = await discover(_config(), watermark=None, checkpoint={},
                                 auth=Auth(headers={"Authorization": "Bearer t"}))

    # The folder itself is not a record. It is a place records were found.
    assert [d.title for d in found] == ["Charter.pdf", "Spec"]
    assert all(d.pending for d in found)
    assert found[0].pending["provider"] == "google_drive"
    assert found[0].pending["resource_id"] == "f1"
    assert found[1].depth == 1


async def test_max_depth_stops_the_descent(monkeypatch):
    _patch(monkeypatch, _drive({
        "root-folder": [{"id": "a", "name": "a", "mimeType": FOLDER}],
        "a": [{"id": "b", "name": "b", "mimeType": FOLDER}],
        "b": [{"id": "deep", "name": "deep.txt", "mimeType": "text/plain"}],
    }))
    found, _, _ = await discover(
        _config(limits={"max_items": 100, "max_depth": 1}),
        watermark=None, checkpoint={}, auth=Auth(),
    )
    assert found == [], "the walk went past max_depth"


async def test_a_cycle_does_not_walk_forever(monkeypatch):
    """A Drive shortcut can make the tree a graph, and a walk that revisits a
    folder revisits it for as long as the budget lasts."""
    _patch(monkeypatch, _drive({
        "root-folder": [{"id": "a", "name": "a", "mimeType": FOLDER}],
        # `a` contains a shortcut back to the root.
        "a": [{"id": "root-folder", "name": "up", "mimeType": FOLDER},
              {"id": "f", "name": "f.txt", "mimeType": "text/plain"}],
    }))
    found, _, _ = await discover(_config(), watermark=None, checkpoint={},
                                 auth=Auth())
    assert len(found) == 1


async def test_the_budget_stops_a_large_drive(monkeypatch):
    _patch(monkeypatch, _drive({
        "root-folder": [{"id": f"f{i}", "name": f"{i}.txt", "mimeType": "text/plain"}
                        for i in range(500)],
    }))
    found, budget, stopped = await discover(
        _config(limits={"max_items": 10, "max_depth": 3}),
        watermark=None, checkpoint={}, auth=Auth(),
    )
    assert len(found) == 10
    assert stopped and "max_items" in stopped


async def test_include_mime_is_an_allowlist(monkeypatch):
    _patch(monkeypatch, _drive({
        "root-folder": [
            {"id": "a", "name": "a.pdf", "mimeType": "application/pdf"},
            {"id": "b", "name": "b.mp4", "mimeType": "video/mp4"},
        ],
    }))
    found, _, _ = await discover(
        _config(tree={"api": "google_drive", "root": "root-folder",
                      "include_mime": ["application/pdf"]}),
        watermark=None, checkpoint={}, auth=Auth(),
    )
    assert [d.title for d in found] == ["a.pdf"]


async def test_a_tree_without_a_connection_says_so_rather_than_getting_a_401():
    """Neither API has an anonymous mode, so an unauthenticated walk is a
    configuration mistake — and reporting the source's 401 would send whoever
    debugs it at the source instead."""
    from open_mem.crawlers import CrawlerError

    with pytest.raises(CrawlerError) as exc:
        await discover(_config(), watermark=None, checkpoint={}, auth=None)
    assert exc.value.status == 401
    assert "connection" in str(exc.value)


async def test_a_graph_reference_carries_the_drive_it_came_from(monkeypatch):
    """Graph downloads by `drives/<drive>/items/<item>`. A reference holding
    only the item id is one that cannot be fetched, and the failure would land
    at fetch time rather than here."""
    def handler(request):
        return httpx.Response(200, json={"value": [
            {"id": "01ITEM", "name": "Plan.docx",
             "file": {"mimeType": "application/vnd.openxml"},
             "parentReference": {"driveId": "b!DRIVE"},
             "lastModifiedDateTime": "2026-03-01T00:00:00Z"},
        ]})
    _patch(monkeypatch, handler)
    found, _, _ = await discover(
        _config(tree={"api": "microsoft_graph", "root": "sites/s1/drive"}),
        watermark=None, checkpoint={}, auth=Auth(),
    )
    assert found[0].pending["resource_id"] == "drives/b!DRIVE/items/01ITEM"


# --- the download ------------------------------------------------------------


def test_a_google_native_document_is_exported_rather_than_downloaded():
    """`?alt=media` on a Doc is a 403: there are no bytes to serve."""
    url = download_url("google_drive", "abc",
                       {"mime_type": "application/vnd.google-apps.document"})
    assert "/export?mimeType=text%2Fplain" in url or "export?mimeType=text/plain" in url


def test_a_google_thing_with_no_export_says_what_it_is():
    with pytest.raises(FetchError) as exc:
        download_url("google_drive", "abc",
                     {"mime_type": "application/vnd.google-apps.form"})
    assert "no downloadable content" in str(exc.value)


def test_a_resource_id_cannot_address_a_different_resource():
    """The id reaches here from a listing and goes straight into a URL. A slash
    or a dot-segment in it is a path traversal against an API, and works exactly
    as well as one against a filesystem."""
    for bad in ("../../oauth2/token", "abc/../../v3/about", "abc?alt=media&x=y",
                "https://evil.example/x"):
        with pytest.raises(FetchError):
            download_url("google_drive", bad, {})
    with pytest.raises(FetchError):
        download_url("microsoft_graph", "drives/a/items/b/../../me", {})


def test_an_unknown_provider_is_refused_before_a_url_exists():
    with pytest.raises(FetchError) as exc:
        download_url("dropbox", "abc", {})
    assert "no fetcher" in str(exc.value)


async def test_a_credential_is_not_forwarded_across_a_redirect(monkeypatch):
    """Both APIs answer a download with a redirect to a pre-signed CDN URL.
    Forwarding the Authorization header there hands an access token to a host
    that never needed one."""
    seen: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.headers.get("authorization")))
        if request.url.host == "graph.microsoft.com":
            return httpx.Response(302, headers={
                "location": "https://cdn.example.com/blob"})
        return httpx.Response(200, content=b"the bytes",
                              headers={"content-type": "text/plain"})

    _patch(monkeypatch, handler)
    monkeypatch.setattr(fetching, "_address_is_private", lambda host: False)

    result = await fetching.fetch_url(
        "https://graph.microsoft.com/v1.0/drives/a/items/b/content",
        max_bytes=1000, headers={"Authorization": "Bearer secret-token"},
    )
    assert result.payload == b"the bytes"
    assert seen[0] == ("graph.microsoft.com", "Bearer secret-token")
    assert seen[1][0] == "cdn.example.com"
    assert seen[1][1] is None, "the token followed the redirect"


async def test_a_query_string_credential_is_refused_for_a_download(monkeypatch):
    """A credential in the query string ends up in the source's access log,
    which is a durable place a secret should never be."""
    worker = fetching.FetchWorker(None, None, None, envelope=object())

    async def authorize(*a, **kw):
        return {}, {"api_key": "secret"}

    import open_mem.connections as connections
    monkeypatch.setattr(connections, "authorize", authorize)

    with pytest.raises(FetchError) as exc:
        await worker._credential("conn_1", "org_1", "google_drive")
    assert "access log" in str(exc.value)


async def test_a_reference_naming_no_connection_fails_as_configuration():
    worker = fetching.FetchWorker(None, None, None, envelope=object())
    with pytest.raises(FetchError) as exc:
        await worker._credential(None, "org_1", "google_drive")
    assert "names no connection" in str(exc.value)
