"""Crawlers.

Nearly every test here is about a way a crawler loses or duplicates data. The
discovery code is the easy part; the run lifecycle around it is where the
damage happens -- a watermark advanced past records nobody processed, a nightly
crawl that re-enriches its whole corpus, a traverse with no bound.

These run against a real HTTP server on a loopback port rather than mocked
transports, so pagination, robots.txt and redirects are exercised as written.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from memdog.crawlers import (
    CrawlerConfig,
    CrawlerError,
    Extract,
    HttpRequest,
    Limits,
    Pagination,
    Transform,
    fingerprint,
    in_scope,
    validate_config,
)
from memdog.crawling import (
    CrawlWorker,
    control_run,
    create_crawler,
    delete_crawler,
    get_run,
    list_crawlers,
    list_runs,
    reap,
    set_enabled,
    start_run,
    tick,
    update_crawler,
)
from memdog.retrieval import list_items

pytestmark = pytest.mark.asyncio

PAGES = {
    0: {"data": [{"id": "a1", "body": "Quarterly revenue rose twelve percent.",
                  "updated_at": "2026-01-01", "status": "active", "cents": 4800},
                 {"id": "a2", "body": "Headcount is flat against plan.",
                  "updated_at": "2026-01-02", "status": "active", "cents": 100}],
        "meta": {"next_cursor": "c2"}},
    1: {"data": [{"id": "a3", "body": "The vendor contract renews in March.",
                  "updated_at": "2026-01-03", "status": "deleted", "cents": 50}],
        "meta": {"next_cursor": None}},
}

FEED = """<?xml version="1.0"?><rss><channel>
<item><title>Release 4.2</title><link>https://example.invalid/r42</link>
<description>Adds retry budgets.</description><pubDate>2026-02-01</pubDate></item>
<item><title>Release 4.3</title><link>https://example.invalid/r43</link>
<description>Fixes the scheduler drift.</description><pubDate>2026-02-08</pubDate></item>
</channel></rss>"""

SITE = {
    "/": "<html><title>Home</title><body>Start here. <a href='/docs'>Docs</a> "
         "<a href='/styles/site.css'>Style</a> "
         "<a href='https://elsewhere.invalid/x'>Away</a></body></html>",
    "/docs": "<html><title>Docs</title><body>The retry budget is per host.</body></html>",
    "/private": "<html><title>Private</title><body>Should never be crawled.</body></html>",
}

ROBOTS = "User-agent: *\nDisallow: /private\n"

STYLESHEET = "body { color: rebeccapurple; }"


class Handler(BaseHTTPRequestHandler):
    hits: dict[str, int] = {}

    def log_message(self, *args):  # silence
        pass

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        Handler.hits[path] = Handler.hits.get(path, 0) + 1
        if path == "/robots.txt":
            return self._send(ROBOTS, "text/plain")
        if path == "/api/items":
            cursor = ""
            if "?" in self.path and "cursor=" in self.path:
                cursor = self.path.split("cursor=")[1].split("&")[0]
            page = 1 if cursor == "c2" else 0
            return self._send(json.dumps(PAGES[page]), "application/json")
        if path == "/feed.xml":
            return self._send(FEED, "application/rss+xml")
        if path == "/denied":
            self.send_response(401)
            self.end_headers()
            return
        if path == "/styles/site.css":
            return self._send(STYLESHEET, "text/css")
        if path in SITE:
            return self._send(SITE[path], "text/html")
        self.send_response(404)
        self.end_headers()

    def _send(self, body: str, content_type: str):
        payload = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture(scope="module")
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


@pytest.fixture(autouse=True)
def _allow_loopback(monkeypatch):
    """The SSRF guard refuses loopback, correctly. The test server is on
    loopback, so the guard is relaxed only for 127.0.0.1 and only here -- the
    guard itself is tested separately, and must keep refusing everything else.
    """
    import memdog.crawlers as crawlers
    import memdog.fetching as fetching

    real = fetching.validate_url

    def permissive(url: str) -> str:
        if "127.0.0.1" in url:
            return url
        return real(url)

    monkeypatch.setattr(crawlers, "validate_url", permissive)


def http_config(base: str, **overrides) -> CrawlerConfig:
    defaults = dict(
        name="items",
        strategy="http",
        request=HttpRequest(url=f"{base}/api/items"),
        pagination=Pagination(type="cursor", cursor_path="meta.next_cursor",
                              cursor_param="cursor", max_pages=5),
        extract=Extract(items_path="data[*]", id_path="id", content_path="body",
                        version_path="updated_at"),
        limits=Limits(rate_per_sec=50, max_items=100),
    )
    defaults.update(overrides)
    return CrawlerConfig(**defaults)


async def _worker(pool, queue, blobs, settings):
    return CrawlWorker(pool, queue, blobs, settings)


# ------------------------------------------------------------- configuration

async def test_a_traverse_crawler_must_declare_a_bound(server):
    """An unbounded link crawl ingests the public internet on the customer's
    inference budget, and it does not stop on its own."""
    with pytest.raises(CrawlerError) as exc:
        validate_config(CrawlerConfig(name="open", strategy="traverse",
                                      seeds=[f"{server}/"]))
    assert exc.value.status == 422
    assert "allow_hosts" in str(exc.value)


async def test_an_invalid_expression_is_refused_at_configuration_time(server):
    """A JMESPath error at 3am inside a six-hour run is a worse way to learn
    the config is wrong than a 422 at the moment of saving."""
    with pytest.raises(CrawlerError) as exc:
        validate_config(http_config(server, filter_include="status ==== 'x'"))
    assert exc.value.status == 422


async def test_a_crawler_is_created_disabled(pool, tenant, principal_for, server):
    actor = await principal_for(tenant.api_key)
    created = await create_crawler(pool, actor, project_id=tenant.project_id,
                                   config=http_config(server))
    assert created["enabled"] is False
    assert created["dry_run_required"] is True
    # ACL is reported, never accepted: a crawler cannot widen access to the
    # data it produces.
    assert created["acl"] == "inherited:personal"


async def test_enabling_without_a_dry_run_is_refused(pool, tenant, principal_for, server):
    actor = await principal_for(tenant.api_key)
    created = await create_crawler(pool, actor, project_id=tenant.project_id,
                                   config=http_config(server))
    with pytest.raises(CrawlerError) as exc:
        await set_enabled(pool, actor, created["crawler_id"], True)
    assert exc.value.status == 409


async def test_editing_the_scope_invalidates_the_dry_run(
    pool, queue, blobs, settings, tenant, principal_for, server
):
    """Otherwise 'dry-run before enabling' is a formality you satisfy once and
    then edit around -- which is how an approved narrow crawl becomes an
    unapproved broad one."""
    actor = await principal_for(tenant.api_key)
    created = await create_crawler(pool, actor, project_id=tenant.project_id,
                                   config=http_config(server))
    worker = await _worker(pool, queue, blobs, settings)
    dry = await start_run(pool, actor, created["crawler_id"], mode="dry")
    await worker.execute(dry["run_id"])
    await set_enabled(pool, actor, created["crawler_id"], True)

    await update_crawler(pool, actor, created["crawler_id"],
                         config=http_config(server, limits=Limits(max_items=99999)))
    with pytest.raises(CrawlerError) as exc:
        await set_enabled(pool, actor, created["crawler_id"], True)
    assert exc.value.status == 409


async def test_another_org_cannot_see_or_touch_a_crawler(
    pool, tenant, other_tenant, principal_for, server
):
    owner = await principal_for(tenant.api_key)
    created = await create_crawler(pool, owner, project_id=tenant.project_id,
                                   config=http_config(server))
    intruder = await principal_for(other_tenant.api_key)
    with pytest.raises(CrawlerError) as exc:
        await set_enabled(pool, intruder, created["crawler_id"], True)
    # 404, not 403: another org's crawler is indistinguishable from one that
    # does not exist.
    assert exc.value.status == 404


# ------------------------------------------------------------------ dry run

async def test_a_dry_run_discovers_but_writes_nothing(
    pool, queue, blobs, settings, tenant, principal_for, server
):
    """The point at which someone learns the job will create forty-seven
    thousand items -- before it runs, not after."""
    actor = await principal_for(tenant.api_key)
    created = await create_crawler(pool, actor, project_id=tenant.project_id,
                                   config=http_config(server))
    worker = await _worker(pool, queue, blobs, settings)
    run = await start_run(pool, actor, created["crawler_id"], mode="dry")
    result = await worker.execute(run["run_id"])

    assert result["discovered"] == 3
    assert result["emitted"] == 0
    items = await list_items(pool, actor, tenant.project_id, limit=50)
    assert not [i for i in items["items"] if i["external_id"].startswith("a")]

    # And it reports a sample, so the estimate is inspectable rather than a
    # bare count.
    detail = await get_run(pool, actor, run["run_id"])
    assert detail["sample"]
    assert any(s["payload"]["preview"] for s in detail["sample"])


async def test_a_dry_run_does_not_advance_the_watermark(
    pool, queue, blobs, settings, tenant, principal_for, server
):
    actor = await principal_for(tenant.api_key)
    created = await create_crawler(
        pool, actor, project_id=tenant.project_id,
        config=http_config(server, incremental="watermark"),
    )
    worker = await _worker(pool, queue, blobs, settings)
    run = await start_run(pool, actor, created["crawler_id"], mode="dry")
    await worker.execute(run["run_id"])
    assert await pool.fetchval(
        "SELECT watermark FROM crawlers WHERE crawler_id = $1", created["crawler_id"]
    ) is None


# --------------------------------------------------------------- discovery

async def test_it_paginates_and_writes_through_the_ordinary_write_path(
    pool, queue, blobs, settings, tenant, principal_for, server
):
    """Nothing downstream can tell an item came from a crawler."""
    actor = await principal_for(tenant.api_key)
    created = await create_crawler(pool, actor, project_id=tenant.project_id,
                                   config=http_config(server))
    worker = await _worker(pool, queue, blobs, settings)
    dry = await start_run(pool, actor, created["crawler_id"], mode="dry")
    await worker.execute(dry["run_id"])
    await set_enabled(pool, actor, created["crawler_id"], True)
    run = await start_run(pool, actor, created["crawler_id"])
    result = await worker.execute(run["run_id"])

    # Both pages: the cursor was followed.
    assert result["discovered"] == 3
    assert result["emitted"] == 3
    items = await list_items(pool, actor, tenant.project_id, limit=50)
    external = {i["external_id"] for i in items["items"]}
    assert {"a1", "a2", "a3"} <= external


async def test_a_filter_expression_excludes_items_before_they_are_written(
    pool, queue, blobs, settings, tenant, principal_for, server
):
    actor = await principal_for(tenant.api_key)
    created = await create_crawler(
        pool, actor, project_id=tenant.project_id,
        config=http_config(server, filter_include="status != 'deleted'"),
    )
    worker = await _worker(pool, queue, blobs, settings)
    dry = await start_run(pool, actor, created["crawler_id"], mode="dry")
    await worker.execute(dry["run_id"])
    await set_enabled(pool, actor, created["crawler_id"], True)
    result = await worker.execute(
        (await start_run(pool, actor, created["crawler_id"]))["run_id"]
    )
    assert result["discovered"] == 2  # a3 is status=deleted


async def test_transforms_are_expressions_over_the_item(
    pool, queue, blobs, settings, tenant, principal_for, server
):
    """JMESPath, so a tenant-supplied transform has no side effects, no I/O and
    no loops -- it cannot hang a worker or reach the network."""
    actor = await principal_for(tenant.api_key)
    created = await create_crawler(
        pool, actor, project_id=tenant.project_id,
        config=http_config(server, transform=[
            Transform(target="amount", expr="cents"),
        ]),
    )
    worker = await _worker(pool, queue, blobs, settings)
    run = await start_run(pool, actor, created["crawler_id"], mode="dry")
    await worker.execute(run["run_id"])
    detail = await get_run(pool, actor, run["run_id"])
    amounts = [s["payload"]["fields"].get("amount") for s in detail["sample"]]
    assert 4800 in amounts


async def test_a_feed_is_read_as_an_index(
    pool, queue, blobs, settings, tenant, principal_for, server
):
    actor = await principal_for(tenant.api_key)
    created = await create_crawler(
        pool, actor, project_id=tenant.project_id,
        config=CrawlerConfig(name="releases", strategy="feed",
                             seeds=[f"{server}/feed.xml"],
                             limits=Limits(rate_per_sec=50)),
    )
    worker = await _worker(pool, queue, blobs, settings)
    run = await start_run(pool, actor, created["crawler_id"], mode="dry")
    result = await worker.execute(run["run_id"])
    assert result["discovered"] == 2
    detail = await get_run(pool, actor, run["run_id"])
    assert any("Release 4.2" in (s["payload"]["preview"] or "") for s in detail["sample"])


async def test_traverse_honours_robots_and_stays_in_the_allowlist(
    pool, queue, blobs, settings, tenant, principal_for, server
):
    """Two separate obligations: robots.txt is not configurable off, and the
    allowlist is what stops the first outbound link becoming a crawl of the
    internet."""
    actor = await principal_for(tenant.api_key)
    Handler.hits.clear()
    created = await create_crawler(
        pool, actor, project_id=tenant.project_id,
        config=CrawlerConfig(
            name="site", strategy="traverse",
            seeds=[f"{server}/", f"{server}/private"],
            allow_hosts=["127.0.0.1"],
            limits=Limits(rate_per_sec=50, max_depth=2),
        ),
    )
    worker = await _worker(pool, queue, blobs, settings)
    run = await start_run(pool, actor, created["crawler_id"], mode="dry")
    await worker.execute(run["run_id"])
    detail = await get_run(pool, actor, run["run_id"])
    urls = {s["url"] for s in detail["sample"]}

    assert any(u.endswith("/docs") for u in urls), "it followed the in-scope link"
    assert not any("/private" in u for u in urls), "robots.txt disallowed /private"
    assert Handler.hits.get("/private", 0) == 0, "and it was never even fetched"
    assert not any("elsewhere.invalid" in u for u in urls), "the offsite link is out of scope"
    # Every page on a site links its stylesheet. `text/css` passes a bare
    # `text/` prefix check, so without an explicit document-type rule the crawl
    # budget goes on stylesheets and they are stored as records.
    assert not any(u.endswith(".css") for u in urls), "a stylesheet is not a document"
    assert Handler.hits.get("/styles/site.css", 0) == 0, "and it was not even fetched"


async def test_asset_urls_are_recognised_before_they_are_fetched():
    """Checked at both ends: the extension before fetching, so the budget is
    never spent, and the content type after, because an extension is a hint."""
    from memdog.crawlers import looks_like_an_asset

    for asset in ["https://x.test/a/style.css", "https://x.test/app.js",
                  "https://x.test/logo.SVG", "https://x.test/f.woff2"]:
        assert looks_like_an_asset(asset), asset
    for page in ["https://x.test/", "https://x.test/docs",
                 "https://x.test/posts/css-tricks"]:
        assert not looks_like_an_asset(page), page


async def test_scope_is_an_allowlist_not_a_blocklist():
    config = CrawlerConfig(name="s", strategy="traverse", seeds=[],
                           allow_hosts=["docs.example.com"])
    assert in_scope(config, "https://docs.example.com/a")
    assert not in_scope(config, "https://evil.example.com/a")
    assert not in_scope(config, "file:///etc/passwd")


# ---------------------------------------------------------------- lifecycle

async def test_an_unchanged_item_is_skipped_on_the_next_run(
    pool, queue, blobs, settings, tenant, principal_for, server
):
    """Without change detection a nightly crawl of 50,000 records re-embeds
    50,000 records every night."""
    actor = await principal_for(tenant.api_key)
    created = await create_crawler(pool, actor, project_id=tenant.project_id,
                                   config=http_config(server))
    worker = await _worker(pool, queue, blobs, settings)
    await worker.execute((await start_run(pool, actor, created["crawler_id"],
                                          mode="dry"))["run_id"])
    await set_enabled(pool, actor, created["crawler_id"], True)

    first = await worker.execute(
        (await start_run(pool, actor, created["crawler_id"]))["run_id"])
    second = await worker.execute(
        (await start_run(pool, actor, created["crawler_id"]))["run_id"])

    assert first["emitted"] == 3 and first["skipped"] == 0
    assert second["emitted"] == 0 and second["skipped"] == 3


async def test_a_partial_run_does_not_advance_the_watermark(
    pool, queue, blobs, settings, tenant, principal_for, server
):
    """The one that bites. Advancing on a partial run starts the next run after
    records it never processed, and nothing ever revisits them."""
    actor = await principal_for(tenant.api_key)
    created = await create_crawler(
        pool, actor, project_id=tenant.project_id,
        # max_items below the corpus size, so the run stops early by design.
        config=http_config(server, incremental="watermark",
                           limits=Limits(max_items=1, rate_per_sec=50)),
    )
    worker = await _worker(pool, queue, blobs, settings)
    await worker.execute((await start_run(pool, actor, created["crawler_id"],
                                          mode="dry"))["run_id"])
    await set_enabled(pool, actor, created["crawler_id"], True)
    result = await worker.execute(
        (await start_run(pool, actor, created["crawler_id"]))["run_id"])

    assert result["status"] == "partial"
    assert "max_items" in result["reason"]
    assert await pool.fetchval(
        "SELECT watermark FROM crawlers WHERE crawler_id = $1", created["crawler_id"]
    ) is None


async def test_a_completed_run_advances_the_watermark_to_what_it_saw(
    pool, queue, blobs, settings, tenant, principal_for, server
):
    """The max of what was discovered, not 'now' -- so a source whose clock
    differs from ours does not open a silent gap."""
    actor = await principal_for(tenant.api_key)
    created = await create_crawler(
        pool, actor, project_id=tenant.project_id,
        config=http_config(server, incremental="watermark"),
    )
    worker = await _worker(pool, queue, blobs, settings)
    await worker.execute((await start_run(pool, actor, created["crawler_id"],
                                          mode="dry"))["run_id"])
    await set_enabled(pool, actor, created["crawler_id"], True)
    await worker.execute((await start_run(pool, actor, created["crawler_id"]))["run_id"])
    assert await pool.fetchval(
        "SELECT watermark FROM crawlers WHERE crawler_id = $1", created["crawler_id"]
    ) == "2026-01-03"


async def test_an_auth_failure_fails_the_run_and_holds_the_watermark(
    pool, queue, blobs, settings, tenant, principal_for, server
):
    """A quiet completion would advance the watermark over a window the crawler
    never actually read."""
    actor = await principal_for(tenant.api_key)
    created = await create_crawler(
        pool, actor, project_id=tenant.project_id,
        config=http_config(server, incremental="watermark",
                           request=HttpRequest(url=f"{server}/denied"),
                           pagination=Pagination(type="none")),
    )
    worker = await _worker(pool, queue, blobs, settings)
    result = await worker.execute(
        (await start_run(pool, actor, created["crawler_id"], mode="dry"))["run_id"])
    assert result["status"] == "failed"
    assert "401" in result["reason"]
    assert await pool.fetchval(
        "SELECT watermark FROM crawlers WHERE crawler_id = $1", created["crawler_id"]
    ) is None


async def test_a_second_run_is_refused_while_one_is_in_flight(
    pool, tenant, principal_for, server
):
    actor = await principal_for(tenant.api_key)
    created = await create_crawler(pool, actor, project_id=tenant.project_id,
                                   config=http_config(server))
    await start_run(pool, actor, created["crawler_id"], mode="dry")
    with pytest.raises(CrawlerError) as exc:
        await start_run(pool, actor, created["crawler_id"], mode="dry")
    assert exc.value.status == 409


async def test_cancelling_keeps_the_checkpoint(pool, tenant, principal_for, server):
    """Discarding progress on cancel would make cancelling a six-hour job an
    irreversible decision, so people would not cancel jobs they should."""
    actor = await principal_for(tenant.api_key)
    created = await create_crawler(pool, actor, project_id=tenant.project_id,
                                   config=http_config(server))
    run = await start_run(pool, actor, created["crawler_id"], mode="dry")
    result = await control_run(pool, actor, run["run_id"], "cancel")
    assert result["status"] == "cancelled"
    assert result["checkpoint_retained"] is True


async def test_a_dead_worker_is_reaped_rather_than_waited_on(
    pool, tenant, principal_for, server
):
    actor = await principal_for(tenant.api_key)
    created = await create_crawler(pool, actor, project_id=tenant.project_id,
                                   config=http_config(server))
    run = await start_run(pool, actor, created["crawler_id"], mode="dry")
    await pool.execute(
        "UPDATE crawl_runs SET status = 'running', heartbeat_at = now() - interval '1 hour' "
        "WHERE run_id = $1",
        run["run_id"],
    )
    assert await reap(pool) == 1
    assert await pool.fetchval(
        "SELECT status FROM crawl_runs WHERE run_id = $1", run["run_id"]
    ) == "interrupted"


async def test_the_scheduler_skips_rather_than_queues_an_overlapping_tick(
    pool, queue, blobs, settings, tenant, principal_for, server
):
    """Queueing a crawl that runs longer than its interval guarantees a backlog
    that never drains."""
    actor = await principal_for(tenant.api_key)
    created = await create_crawler(
        pool, actor, project_id=tenant.project_id, config=http_config(server),
        schedule={"type": "interval", "every_seconds": 3600},
    )
    worker = await _worker(pool, queue, blobs, settings)
    await worker.execute((await start_run(pool, actor, created["crawler_id"],
                                          mode="dry"))["run_id"])
    await set_enabled(pool, actor, created["crawler_id"], True)
    # A run already in flight when the tick lands.
    await start_run(pool, actor, created["crawler_id"])

    result = await tick(pool, worker)
    assert created["crawler_id"] in result["skipped"]
    assert result["started"] == []


async def test_the_scheduler_runs_a_due_crawler(
    pool, queue, blobs, settings, tenant, principal_for, server
):
    actor = await principal_for(tenant.api_key)
    created = await create_crawler(
        pool, actor, project_id=tenant.project_id, config=http_config(server),
        schedule={"type": "interval", "every_seconds": 3600},
    )
    worker = await _worker(pool, queue, blobs, settings)
    await worker.execute((await start_run(pool, actor, created["crawler_id"],
                                          mode="dry"))["run_id"])
    await set_enabled(pool, actor, created["crawler_id"], True)

    result = await tick(pool, worker)
    assert len(result["started"]) == 1
    assert result["runs"][0]["emitted"] == 3
    # And the next tick is scheduled, so it does not run again immediately.
    assert await pool.fetchval(
        "SELECT next_due_at > now() FROM crawlers WHERE crawler_id = $1",
        created["crawler_id"],
    )


async def test_deleting_a_crawler_keeps_the_data_it_wrote(
    pool, queue, blobs, settings, tenant, principal_for, server
):
    """The items belong to the project, not to the mechanism that found them."""
    actor = await principal_for(tenant.api_key)
    created = await create_crawler(pool, actor, project_id=tenant.project_id,
                                   config=http_config(server))
    worker = await _worker(pool, queue, blobs, settings)
    await worker.execute((await start_run(pool, actor, created["crawler_id"],
                                          mode="dry"))["run_id"])
    await set_enabled(pool, actor, created["crawler_id"], True)
    await worker.execute((await start_run(pool, actor, created["crawler_id"]))["run_id"])

    result = await delete_crawler(pool, actor, created["crawler_id"])
    assert result["data_retained"] is True
    # The producer survives so crawled items can still say where they came from.
    assert await pool.fetchval(
        "SELECT status FROM producers WHERE producer_id = $1", result["producer_disabled"]
    ) == "disabled"
    items = await list_items(pool, actor, tenant.project_id, limit=50)
    assert {"a1", "a2", "a3"} <= {i["external_id"] for i in items["items"]}


async def test_a_crawl_run_id_is_distinguishable_from_a_deletion_run_id(
    pool, tenant, principal_for, server
):
    """The deletion and reprocess tables already mint `run_`. A prefix two
    tables share stops being able to say what the id identifies -- and it let
    the crawl-run route be silently shadowed by the deletion-run route, which
    is exactly the failure a distinct prefix prevents."""
    actor = await principal_for(tenant.api_key)
    created = await create_crawler(pool, actor, project_id=tenant.project_id,
                                   config=http_config(server))
    run = await start_run(pool, actor, created["crawler_id"], mode="dry")
    assert run["run_id"].startswith("crun_")
    assert not await pool.fetchval(
        "SELECT 1 FROM runs WHERE run_id = $1", run["run_id"]
    ), "a crawl run must not be addressable as a deletion run"


async def test_the_routes_for_the_two_kinds_of_run_do_not_collide():
    """Registered paths are checked directly, because FastAPI resolves a
    duplicate by silently preferring whichever was registered first."""
    from memdog.app import app

    paths = [r.path for r in app.routes if hasattr(r, "path")]
    assert paths.count("/api/v1/runs/{run_id}") == 1
    assert "/api/v1/crawl-runs/{run_id}" in paths


async def test_enrichment_is_off_unless_the_crawler_asks_for_it(
    pool, queue, blobs, settings, tenant, principal_for, server
):
    """A crawler is the one producer that can discover fifty thousand records
    unattended. Enriching all of them by default is a model call per chunk on
    data nobody has asked a question about."""
    actor = await principal_for(tenant.api_key)
    quiet = await create_crawler(pool, actor, project_id=tenant.project_id,
                                 config=http_config(server))
    worker = await _worker(pool, queue, blobs, settings)
    await worker.execute((await start_run(pool, actor, quiet["crawler_id"],
                                          mode="dry"))["run_id"])
    await set_enabled(pool, actor, quiet["crawler_id"], True)
    await worker.execute((await start_run(pool, actor, quiet["crawler_id"]))["run_id"])
    await queue.drain()

    states = await pool.fetch(
        "SELECT state FROM data_items WHERE external_id = ANY($1::text[])",
        ["a1", "a2", "a3"],
    )
    assert {r["state"] for r in states} == {"stored"}

    # Asking for it is one field, and it changes the outcome.
    eager = await create_crawler(
        pool, actor, project_id=tenant.project_id,
        config=http_config(server, name="eager", enrich=True,
                           extract=Extract(items_path="data[*]", id_path="id",
                                           content_path="body")),
    )
    await worker.execute((await start_run(pool, actor, eager["crawler_id"],
                                          mode="dry"))["run_id"])
    await set_enabled(pool, actor, eager["crawler_id"], True)
    await worker.execute((await start_run(pool, actor, eager["crawler_id"]))["run_id"])
    await queue.drain()
    enriched = await pool.fetchval(
        "SELECT count(*) FROM data_items WHERE state <> 'stored'"
    )
    assert enriched > 0


async def test_the_run_history_is_listed_with_its_counters(
    pool, queue, blobs, settings, tenant, principal_for, server
):
    actor = await principal_for(tenant.api_key)
    created = await create_crawler(pool, actor, project_id=tenant.project_id,
                                   config=http_config(server))
    worker = await _worker(pool, queue, blobs, settings)
    await worker.execute((await start_run(pool, actor, created["crawler_id"],
                                          mode="dry"))["run_id"])
    runs = await list_runs(pool, actor, created["crawler_id"])
    assert runs and runs[0]["mode"] == "dry" and runs[0]["discovered"] == 3

    listed = await list_crawlers(pool, actor, tenant.project_id)
    assert listed[0]["dry_run_current"] is True
