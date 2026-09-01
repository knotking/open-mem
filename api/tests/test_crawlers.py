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


async def test_a_manual_tick_cannot_start_another_orgs_crawls(
    pool, queue, blobs, settings, tenant, other_tenant, principal_for, server
):
    """The advisory lock stops two ticks running at once; it does nothing about
    *whose* crawlers a tick picks up. A person triggering a pass from the
    console must not be able to start, or spend budget on, somebody else's."""
    from memdog.crawling import tick_for

    owner = await principal_for(tenant.api_key)
    created = await create_crawler(
        pool, owner, project_id=tenant.project_id, config=http_config(server),
        schedule={"type": "interval", "every_seconds": 3600},
    )
    worker = await _worker(pool, queue, blobs, settings)
    await worker.execute((await start_run(pool, owner, created["crawler_id"],
                                          mode="dry"))["run_id"])
    await set_enabled(pool, owner, created["crawler_id"], True)

    intruder = await principal_for(other_tenant.api_key)
    result = await tick_for(pool, intruder, worker)
    assert result["started"] == [], "another org's due crawler must not be picked up"

    # The owner's own tick still finds it.
    mine = await tick_for(pool, owner, worker)
    assert len(mine["started"]) == 1


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


async def test_a_run_emits_the_signals_that_detect_a_dead_crawler(
    pool, queue, blobs, settings, tenant, principal_for, server, monkeypatch
):
    """A crawler that finds nothing fails at nothing, so an error rate stays
    flat while the data goes stale. `crawl.discovered` against its own baseline
    is the only thing that catches it -- which makes the instrumentation itself
    worth a test, because nothing else would notice if it stopped firing.
    """
    import memdog.crawling as crawling_mod

    emitted: list[tuple] = []
    monkeypatch.setattr(crawling_mod, "record",
                        lambda metric, value, **labels: emitted.append((metric, value)))

    actor = await principal_for(tenant.api_key)
    created = await create_crawler(
        pool, actor, project_id=tenant.project_id, config=http_config(server),
        schedule={"type": "interval", "every_seconds": 3600},
    )
    worker = await _worker(pool, queue, blobs, settings)
    await worker.execute((await start_run(pool, actor, created["crawler_id"],
                                          mode="dry"))["run_id"])

    names = {m for m, _ in emitted}
    assert {"crawl_runs", "crawl_discovered", "crawl_emitted", "crawl_dedupe_hits",
            "crawl_duration"} <= names
    assert dict((m, v) for m, v in emitted)["crawl_discovered"] == 3
    # Duration against the schedule, because whether a run is too slow is a
    # question about its interval. Above 1.0 it overlaps forever.
    assert "crawl_duration_vs_interval" in names


async def test_freshness_measures_the_last_success_not_the_last_attempt(
    pool, queue, blobs, settings, tenant, principal_for, server
):
    """A crawler failing every tick has a recent run and stale data."""
    actor = await principal_for(tenant.api_key)
    created = await create_crawler(pool, actor, project_id=tenant.project_id,
                                   config=http_config(server))
    worker = await _worker(pool, queue, blobs, settings)
    await worker.execute((await start_run(pool, actor, created["crawler_id"],
                                          mode="dry"))["run_id"])
    await set_enabled(pool, actor, created["crawler_id"], True)

    # A dry run is not a success for freshness purposes -- it wrote nothing.
    listed = await list_crawlers(pool, actor, tenant.project_id)
    assert listed[0]["seconds_since_last_success"] is None

    await worker.execute((await start_run(pool, actor, created["crawler_id"]))["run_id"])
    listed = await list_crawlers(pool, actor, tenant.project_id)
    assert listed[0]["seconds_since_last_success"] is not None


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


async def test_a_401_drops_the_cached_token_for_that_connection(
    pool, queue, blobs, settings, tenant, principal_for, server
):
    """An exchanged credential is cached until shortly before it expires. A
    source that starts refusing — consent revoked, scope changed, the secret
    rotated at the provider — would go on being refused with the same dead
    token for up to an hour after somebody fixed it, and the fix would look
    like it had not worked."""
    import os

    from memdog import connections, grants
    from memdog.crypto import Envelope

    actor = await principal_for(tenant.api_key)
    envelope = Envelope(os.urandom(32))
    connection = await connections.create(
        pool, actor, envelope, project_id=tenant.project_id, provider="acme",
        credential="tok", auth_style="bearer",
    )
    created = await create_crawler(
        pool, actor, project_id=tenant.project_id,
        config=http_config(server, request=HttpRequest(url=f"{server}/denied"),
                           pagination=Pagination(type="none")),
    )
    await connections.attach(pool, actor, created["crawler_id"],
                             connection["connection_id"])

    grants._cache[connection["connection_id"]] = grants.Token("dead", 1e12)
    worker = CrawlWorker(pool, queue, blobs, settings, envelope=envelope)
    result = await worker.execute(
        (await start_run(pool, actor, created["crawler_id"], mode="dry"))["run_id"])

    assert result["status"] == "failed"
    assert connection["connection_id"] not in grants._cache, (
        "the refused token is still cached and the next run will reuse it"
    )


async def test_a_crawled_item_carries_the_tag_that_says_which_crawler_pulled_it(
    pool, queue, blobs, settings, tenant, principal_for, server
):
    """The provenance that makes a crawl reprocessable.

    This was built and then thrown away: `_emit` put the tags inside
    `WriteItem.metadata`, the write path reads `item.tags`, and `data_items` had
    no metadata column at all -- so the tag, the title and the source URL were
    all dropped between the crawler and the insert. Nothing errored. The items
    were durable and searchable and simply could not be attributed, which is
    only discovered by someone asking which crawler pulled them.
    """
    actor = await principal_for(tenant.api_key)
    created = await create_crawler(
        pool, actor, project_id=tenant.project_id,
        config=http_config(server, tags=["source:acme"]),
    )
    crawler_id = created["crawler_id"]
    worker = await _worker(pool, queue, blobs, settings)
    await worker.execute(
        (await start_run(pool, actor, crawler_id, mode="dry"))["run_id"])
    await set_enabled(pool, actor, crawler_id, True)
    run = await start_run(pool, actor, crawler_id)
    await worker.execute(run["run_id"])

    rows = await pool.fetch(
        "SELECT tags, metadata, run_id FROM data_items WHERE project_id = $1 "
        "AND external_id = ANY($2::text[])",
        tenant.project_id, ["a1", "a2", "a3"],
    )
    assert rows, "the crawl wrote nothing"
    for row in rows:
        assert f"crawler:{crawler_id}" in row["tags"]
        assert "source:acme" in row["tags"], "the configured tags went nowhere"
        assert row["run_id"] == run["run_id"]
        metadata = json.loads(row["metadata"]) if isinstance(row["metadata"], str) \
            else row["metadata"]
        assert "source_url" in metadata


async def test_metadata_tags_are_lifted_for_a_producer_following_the_docs(
    pool, queue, blobs, settings, tenant, principal_for
):
    """The write-api example has always shown `metadata: {tags: [...]}`, so an
    external producer sending that shape lost them exactly as the crawler did.
    Merged rather than substituted: a producer sending both keeps both."""
    from memdog.contracts import Inline, WriteItem, WriteRequest
    from memdog.write import write_items

    actor = await principal_for(tenant.api_key)
    await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=tenant.producer_id, items=[WriteItem(
            external_id="doc-tagged",
            content=Inline(text="a record with tags in both places"),
            tags=["top-level"],
            metadata={"tags": ["source:salesforce", "top-level"], "note": "kept"},
        )]),
    )
    row = await pool.fetchrow(
        "SELECT tags, metadata FROM data_items WHERE project_id = $1 AND external_id = $2",
        tenant.project_id, "doc-tagged",
    )
    assert sorted(row["tags"]) == ["source:salesforce", "top-level"], (
        "a tag sent in both places must not appear twice"
    )
    metadata = json.loads(row["metadata"]) if isinstance(row["metadata"], str) \
        else row["metadata"]
    assert metadata["note"] == "kept"


async def test_a_crawl_run_can_be_reprocessed_by_run_id_or_by_tag(
    pool, queue, blobs, settings, tenant, principal_for, server
):
    """The loop `enrich: false` is supposed to leave open.

    The default tells you to crawl, look at the dry run's count, and enrich only
    if it looks right. But `stale_only` and `stale_generator` both match on an
    existing artifact, and an item that was never enriched has none -- so the
    corpus that default produces was the one corpus reprocess could not select.
    Enumerating ten thousand data_ids by hand is not the answer.
    """
    from memdog.reprocess import request_reprocess

    actor = await principal_for(tenant.api_key)
    created = await create_crawler(
        pool, actor, project_id=tenant.project_id,
        config=http_config(server, tags=["source:acme"]),
    )
    crawler_id = created["crawler_id"]
    worker = await _worker(pool, queue, blobs, settings)
    await worker.execute(
        (await start_run(pool, actor, crawler_id, mode="dry"))["run_id"])
    await set_enabled(pool, actor, crawler_id, True)
    run = await start_run(pool, actor, crawler_id)
    result = await worker.execute(run["run_id"])
    assert result["emitted"] == 3

    selector = {"project_id": tenant.project_id}
    by_run = await request_reprocess(
        pool, queue, actor, selector={**selector, "run_id": run["run_id"]},
        stage="embed", dry_run=True)
    assert by_run["items"] == 3

    by_tag = await request_reprocess(
        pool, queue, actor, selector={**selector, "tags": [f"crawler:{crawler_id}"]},
        stage="embed", dry_run=True)
    assert by_tag["items"] == 3

    # Overlap, not containment: one matching tag out of two is still a match.
    either = await request_reprocess(
        pool, queue, actor,
        selector={**selector, "tags": ["source:acme", "source:nothing-here"]},
        stage="embed", dry_run=True)
    assert either["items"] == 3

    # The gap this closes, asserted directly rather than described.
    stale = await request_reprocess(
        pool, queue, actor, selector={**selector, "stale_only": True},
        stage="embed", dry_run=True)
    assert stale["items"] == 0

    # A selector must still narrow something -- project_id alone is not a
    # selector, or "reprocess everything" becomes one missing key away.
    with pytest.raises(ValueError):
        await request_reprocess(pool, queue, actor, selector=selector,
                                stage="embed", dry_run=True)


async def test_two_schedulers_do_not_both_start_the_same_crawl(
    pool, tenant, principal_for, queue, blobs, settings
):
    """The guard is an advisory lock across the whole pass, not a row lock.

    Two schedulers electing themselves is how a nightly crawl becomes two
    nightly crawls. A second tick selects nothing rather than racing for rows —
    and a row lock would not help here anyway, since the selection runs outside
    an explicit transaction and would release at statement end.

    **Written against a held lock rather than as two racing ticks**, which is
    what this was and why it failed intermittently in a full run while passing
    alone. `tick` takes the lock on a *pooled* connection, and a Postgres
    advisory lock is session-scoped and re-entrant: when both ticks happened to
    be served the same connection, the second `pg_try_advisory_lock` returned
    true, neither declined, and the assertion failed on scheduling rather than
    on behaviour.

    That re-entrancy is worth knowing beyond this test. The guard holds between
    *processes* — which is how the scheduler actually runs — and does **not**
    hold between two ticks sharing one connection inside a single process.
    """
    from memdog import crawling

    class Idle:
        async def execute(self, run_id):
            return {"run_id": run_id}

    # Held on its own checked-out connection, so `tick` cannot be handed the
    # same session and cannot re-enter the lock.
    async with pool.acquire() as holder:
        taken = await holder.fetchval(
            "SELECT pg_try_advisory_lock(hashtext('memdog.crawl'))"
        )
        assert taken, "the lock was already held — the test cannot mean anything"
        try:
            declined = await crawling.tick(pool, Idle())
            assert declined.get("skipped_lock"), (
                "a second pass ran while the first held the lock — a nightly "
                "crawl becomes two nightly crawls"
            )
        finally:
            await holder.execute("SELECT pg_advisory_unlock(hashtext('memdog.crawl'))")

    # And once it is released, a pass runs again rather than being wedged shut.
    resumed = await crawling.tick(pool, Idle())
    assert not resumed.get("skipped_lock"), "the lock was not released"


async def test_the_heartbeat_moves_during_emission(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A crawl used to reap itself while still running.

    `reap()` marks any run whose heartbeat is older than
    STALE_HEARTBEAT_SECONDS as interrupted, and the emit phase wrote none at
    all — so an emit taking more than five minutes killed its own run. With
    `max_items` defaulting to 1000 that is the ordinary case for a real source,
    and it presented as noise rather than as a bug: runs randomly interrupted,
    a watermark that never advanced, and a next run that re-fetched everything.

    Driven through `_emit` directly, with the run's heartbeat backdated, so the
    only thing that can move it is the loop itself.
    """
    from memdog import crawling
    from memdog.crawlers import Discovered
    from memdog.ids import new_id

    crawler = await pool.fetchrow(
        """
        INSERT INTO crawlers (crawler_id, producer_id, org_id, project_id, user_id,
            name, strategy, config)
        VALUES ($1, $2, $3, $4, $5, 'heartbeat', 'http', '{}'::jsonb)
        RETURNING *
        """,
        new_id("crw"), tenant.producer_id, tenant.org_id, tenant.project_id,
        tenant.user_id,
    )
    run_id = new_id("crun")
    await pool.execute(
        """
        INSERT INTO crawl_runs (run_id, crawler_id, org_id, pinned_config,
            pinned_version, status, heartbeat_at)
        VALUES ($1, $2, $3, '{}'::jsonb, 1, 'running', now() - interval '1 hour')
        """,
        run_id, crawler["crawler_id"], tenant.org_id,
    )

    # More than one progress interval, so the loop must report at least once.
    found = [
        Discovered(external_id=f"item-{i}", title=f"Item {i}", text="body",
                   url=None, fields={}, depth=0)
        for i in range(crawling.PROGRESS_EVERY * 2)
    ]

    run = await pool.fetchrow("SELECT * FROM crawl_runs WHERE run_id = $1", run_id)
    worker = CrawlWorker(pool, queue, blobs, settings)
    # Dry, so nothing is written and the loop is the only thing under test.
    await worker._emit(run, crawler, http_config("http://example.invalid"), found, dry=True)

    beat = await pool.fetchval(
        "SELECT heartbeat_at FROM crawl_runs WHERE run_id = $1", run_id)
    stale_before = await pool.fetchval(
        "SELECT now() - interval '1 hour' > $1", beat)
    assert stale_before is False, "the emit loop must move the heartbeat"

    # And with it moved, the reaper leaves the run alone.
    assert await crawling.reap(pool) == 0


# ------------------------------------------------------------- sync state


async def _bare_crawler(pool, tenant, name="synced", connection_id=None):
    from memdog.ids import new_id

    return await pool.fetchrow(
        """
        INSERT INTO crawlers (crawler_id, producer_id, org_id, project_id, user_id,
            name, strategy, config, connection_id)
        VALUES ($1, $2, $3, $4, $5, $6, 'http', '{}'::jsonb, $7)
        RETURNING *
        """,
        new_id("crw"), tenant.producer_id, tenant.org_id, tenant.project_id,
        tenant.user_id, name, connection_id,
    )


async def test_a_cursor_is_kept_per_scope(pool, tenant):
    """One crawler over forty channels needs forty positions.

    With a single watermark a busy channel drags it forward and the quiet ones
    are re-scanned from that point forever — or the reverse, and the busy one is
    skipped.
    """
    from memdog.crawling import advance_cursor, cursor_for

    crawler = await _bare_crawler(pool, tenant)
    cid = crawler["crawler_id"]

    await advance_cursor(pool, cid, "#eng", "2026-08-01T00:00:00Z", items=40)
    await advance_cursor(pool, cid, "#random", "2026-06-01T00:00:00Z", items=2)

    assert await cursor_for(pool, cid, "#eng") == "2026-08-01T00:00:00Z"
    assert await cursor_for(pool, cid, "#random") == "2026-06-01T00:00:00Z"


async def test_a_crawler_without_scopes_keeps_the_position_it_had(pool, tenant):
    """`scope = ''` is today's behaviour, and it must not need migrating."""
    from memdog.crawling import cursor_for

    crawler = await _bare_crawler(pool, tenant)
    await pool.execute("UPDATE crawlers SET watermark = $2 WHERE crawler_id = $1",
                       crawler["crawler_id"], "2026-07-04T00:00:00Z")
    assert await cursor_for(pool, crawler["crawler_id"]) == "2026-07-04T00:00:00Z"


async def test_a_failed_scope_keeps_its_cursor_and_says_why(pool, tenant):
    """The next run retries the same range rather than skipping it, and a reader
    can tell a failure from a quiet source."""
    from memdog.crawling import advance_cursor, cursor_for, record_scope_failure

    crawler = await _bare_crawler(pool, tenant)
    cid = crawler["crawler_id"]
    await advance_cursor(pool, cid, "#eng", "2026-08-01T00:00:00Z")
    await record_scope_failure(pool, cid, "#eng", "upstream 503")

    assert await cursor_for(pool, cid, "#eng") == "2026-08-01T00:00:00Z"
    assert await pool.fetchval(
        "SELECT last_error FROM crawl_cursors WHERE crawler_id = $1 AND scope = '#eng'",
        cid) == "upstream 503"


async def test_a_cooling_credential_is_not_a_failed_crawl(pool, connected_tenant, principal_for):
    """Two crawlers sharing one token draw on the same quota.

    So the limit is recorded on the connection, and a tick that fires while it
    is cooling records that rather than burning a run — a throttled credential
    and a broken crawler must not look the same.
    """
    from memdog.crawling import is_limited, mark_limited

    connection_id = await pool.fetchval(
        "SELECT connection_id FROM connections LIMIT 1")
    assert connection_id, "the tenant fixture provides one"

    assert await is_limited(pool, connection_id) is None
    await mark_limited(pool, connection_id, seconds=120, reason="Retry-After: 120")
    reason = await is_limited(pool, connection_id)
    assert reason and "Retry-After" in reason


async def test_lag_per_source_is_reportable(pool, tenant):
    """Every project signal depends on it: one computed over a source that
    stopped syncing is confidently wrong, and "no activity for 7 days" is
    indistinguishable from "the connector broke 7 days ago"."""
    from memdog.crawling import advance_cursor, source_lag

    crawler = await _bare_crawler(pool, tenant, name="lagging")
    await advance_cursor(pool, crawler["crawler_id"], "#eng", "x")
    await pool.execute(
        "UPDATE crawl_cursors SET last_ok_at = now() - interval '3 days' "
        "WHERE crawler_id = $1", crawler["crawler_id"])

    rows = await source_lag(pool, tenant.project_id)
    mine = [r for r in rows if r["crawler_id"] == crawler["crawler_id"]]
    assert mine and mine[0]["behind_seconds"] > 60 * 60 * 24 * 2


@pytest.mark.asyncio
async def test_a_budget_capped_run_is_progress_not_failure(pool, tenant):
    """1000 items emitted is not an outage.

    Caught live: a run that hit `max_items` was filed through
    `record_scope_failure`, so a crawler that had just successfully emitted a
    thousand items reported `last_ok_at: null` and an error -- reading as a
    source that had never once worked. That is the exact false alarm source lag
    exists to prevent, inverted.
    """
    from memdog.crawling import record_scope_failure, record_scope_progress

    crawler = await _bare_crawler(pool, tenant)
    cid = crawler["crawler_id"]
    await record_scope_failure(pool, cid, "", "earlier outage")

    await record_scope_progress(pool, cid, "", items=1000)
    row = await pool.fetchrow(
        "SELECT * FROM crawl_cursors WHERE crawler_id = $1 AND scope = ''", cid)

    assert row["last_ok_at"] is not None, "it reached the source"
    assert row["items_seen"] == 1000, "and it moved a thousand items"
    assert row["last_error"] is None, "a source that just answered is not failing"
    assert row["cursor"] is None, "but it must not resume past what it did not read"


@pytest.mark.asyncio
async def test_enabling_a_manual_crawler_does_not_schedule_it(
    pool, tenant, principal_for, server
):
    """Manual means manual.

    Enabling set `next_due_at = now()` whatever the schedule said, and the
    scheduler's selection never checked the type -- so a crawler someone had
    deliberately marked manual was picked up and run once before they ever
    triggered it themselves. Found on a live crawler sitting due with
    `schedule: {"type": "manual"}`.
    """
    actor = await principal_for(tenant.api_key)
    created = await create_crawler(
        pool, actor, project_id=tenant.project_id, config=http_config(server),
        schedule={"type": "manual"},
    )
    cid = created["crawler_id"]
    await start_run(pool, actor, cid, mode="dry")
    row = await pool.fetchrow("SELECT * FROM crawlers WHERE crawler_id = $1", cid)
    await pool.execute(
        "UPDATE crawlers SET dry_run_version = config_version WHERE crawler_id = $1",
        cid)

    await set_enabled(pool, actor, cid, True)

    row = await pool.fetchrow(
        "SELECT enabled, next_due_at FROM crawlers WHERE crawler_id = $1", cid)
    assert row["enabled"] is True, "it is runnable"
    assert row["next_due_at"] is None, "but the scheduler must not claim it"
