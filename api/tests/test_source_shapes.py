"""Every pagination mechanism, crawled rather than reviewed.

`test_connector_salesforce.py` established the method and the reason: paging is
where templates are wrong, and being wrong at paging produces a run that looks
successful. It fetches a plausible number of items, raises no error, advances no
alarm, and quietly omits most of the source.

Salesforce covered one mechanism. `tools/fake_sources.py` covers the other four,
because each is a *different code path* through `discover_http` and each has a
connector depending on it:

- **link_header** — the next URL arrives in a header, alongside other relations
- **offset** — `startAt` arithmetic the client does itself
- **next_url, absolute** — Graph's `@odata.nextLink`, which resolves differently
  from Salesforce's path even though both are `next_url`
- **page + stop_when** — no cursor, no total; only a flag ends the crawl
- **feed** — a regex parser, pointed at a feed that is not well-formed

Every one of these also asserts the *second* run is smaller than the first,
because 36 of 37 catalog entries have no incremental clause and a template that
claims one and does not have it is the expensive kind of wrong.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memdog.crawlers import (Auth, CrawlerConfig, Extract, HttpRequest,  # noqa: E402
                             Limits, Pagination, discover, next_watermark)
from tools.fake_sources import REQUESTS, reset, serve                    # noqa: E402

pytestmark = pytest.mark.asyncio

AUTH = Auth(headers={"Authorization": "Bearer test"})

# The third record's timestamp. Used as a watermark so a second run should see
# exactly the two records after it.
MIDPOINT = "2026-08-02T16:45:00Z"


@pytest.fixture(autouse=True)
def _allow_loopback(monkeypatch):
    """The SSRF guard refuses loopback, correctly, and the simulator is on it.

    Relaxed for `127.0.0.1` and nothing else, only here. The guard is what stops
    a caller-supplied URL reaching inside the deployment, it is tested on its
    own, and a simulator is not worth weakening it for.
    """
    import memdog.crawlers as crawlers_mod
    import memdog.fetching as fetching

    real = fetching.validate_url
    monkeypatch.setattr(
        crawlers_mod, "validate_url",
        lambda url: url if "127.0.0.1" in url else real(url))


@pytest.fixture
def sources():
    reset()
    server = serve(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()


def _crawl(config: CrawlerConfig, watermark: str | None = None):
    return discover(config, watermark=watermark, checkpoint={}, auth=AUTH)


# ------------------------------------------------------------- link_header

def _github(base: str) -> CrawlerConfig:
    return CrawlerConfig(
        name="issues", strategy="http",
        request=HttpRequest(url=f"{base}/gh/repos/acme/widgets/issues"),
        pagination=Pagination(type="link_header", max_pages=10),
        extract=Extract(items_path="@", id_path="number", title_path="title",
                        content_path="body", version_path="updated_at",
                        url_path="html_url"),
        incremental="watermark", watermark_param="since",
        limits=Limits(rate_per_sec=50, max_items=100),
    )


async def test_a_link_header_is_read_by_relation_not_by_position(sources):
    """The trap that makes this worth a simulator.

    GitHub sends several relations in one comma-separated header, and this
    source deliberately puts `rel="last"` *first*. A parser that takes the first
    `<...>` it finds jumps to the final page and stops — reporting two items out
    of five, with no error anywhere.
    """
    found, _budget, _capped = await _crawl(_github(sources))
    numbers = sorted(int(f.external_id) for f in found)
    assert numbers == [1, 2, 3, 4, 5], f"followed the wrong relation: {numbers}"
    assert REQUESTS["/gh/repos/acme/widgets/issues"] == 3, (
        "walked to the last page and back, or re-requested one it already had")


async def test_the_watermark_reaches_a_link_header_source_as_a_parameter(sources):
    """`watermark_param` is the other incremental mechanism — the watermark goes
    in the query rather than being rendered into a template variable, and until
    now nothing crawled a source that actually honoured it."""
    config = _github(sources)
    found, _b, _c = await _crawl(config, watermark=MIDPOINT)
    numbers = sorted(int(f.external_id) for f in found)
    assert numbers == [4, 5], f"the source was re-read from the top: {numbers}"


# ------------------------------------------------------------------ offset

async def test_offset_paging_walks_the_whole_result_set(sources):
    """`startAt` arithmetic is done by the client, so an off-by-one repeats a
    record on every page boundary or skips one — and both look like a working
    crawl with a slightly odd count."""
    config = CrawlerConfig(
        name="issues", strategy="http",
        request=HttpRequest(url=f"{sources}/jira/rest/api/3/search",
                            query={"jql": 'project = ACME AND updated > "{{ watermark_or_epoch }}"'}),
        pagination=Pagination(type="offset", page_param="startAt", page_size=2,
                              size_param="maxResults", max_pages=10,
                              stop_when="length(issues) == `0`"),
        extract=Extract(items_path="issues[*]", id_path="key",
                        title_path="fields.summary", content_path="fields.description",
                        version_path="fields.updated"),
        incremental="watermark",
        limits=Limits(rate_per_sec=50, max_items=100),
    )
    found, _b, _c = await _crawl(config)
    keys = sorted(f.external_id for f in found)
    assert keys == ["ACME-1", "ACME-2", "ACME-3", "ACME-4", "ACME-5"], keys
    assert len(keys) == len(set(keys)), f"a page boundary repeated a record: {keys}"

    mark = next_watermark(config, found, None)
    again, _b, _c = await _crawl(config, watermark=mark)
    assert again == [], f"re-read after the watermark: {[f.external_id for f in again]}"


# -------------------------------------------------- next_url, absolute form

async def test_an_absolute_next_link_resolves_differently_from_a_path(sources):
    """Salesforce returns its continuation as a path and Graph returns an
    absolute URL. Both are `next_url`; they take different branches through
    `urljoin`, so testing one proves nothing whatsoever about the other."""
    config = CrawlerConfig(
        name="messages", strategy="http",
        request=HttpRequest(url=f"{sources}/graph/v1.0/messages"),
        pagination=Pagination(type="next_url", cursor_path='"@odata.nextLink"',
                              max_pages=10),
        extract=Extract(items_path="value[*]", id_path="id", title_path="subject",
                        content_path="bodyPreview",
                        version_path="lastModifiedDateTime"),
        incremental="watermark", watermark_param="since",
        limits=Limits(rate_per_sec=50, max_items=100),
    )
    found, _b, _c = await _crawl(config)
    assert len(found) == 5, [f.external_id for f in found]

    # The continuation carries `since` forward itself. A crawler that re-applied
    # the query on top of the URL the source handed back would double it.
    later, _b, _c = await _crawl(config, watermark=MIDPOINT)
    assert sorted(f.external_id for f in later) == ["graph-4", "graph-5"]


# ------------------------------------------------------------ page + flag

async def test_a_page_number_source_stops_on_its_flag_not_on_the_page_limit(sources):
    """No cursor and no total: `has_more` is the only thing that ends this
    crawl. A config that omits `stop_when` runs to `max_pages` on every single
    run and nothing reports it, because over-fetching is not an error."""
    config = CrawlerConfig(
        name="items", strategy="http",
        request=HttpRequest(url=f"{sources}/pages/items"),
        pagination=Pagination(type="page", page_param="page", page_size=2,
                              max_pages=10, stop_when="has_more == `false`"),
        extract=Extract(items_path="results[*]", id_path="uid", title_path="name",
                        content_path="text", version_path="changed"),
        incremental="watermark", watermark_param="updated_since",
        limits=Limits(rate_per_sec=50, max_items=100),
    )
    found, _budget, _capped = await _crawl(config)
    assert sorted(f.external_id for f in found) == [
        "pg-1", "pg-2", "pg-3", "pg-4", "pg-5"]
    # Three pages for five records at two a page. The count is the assertion:
    # over-fetching returns the right items and raises nothing, so item counts
    # cannot distinguish a crawl that stopped from one that ran to `max_pages`.
    assert REQUESTS["/pages/items"] == 3, (
        f"kept asking after has_more went false: {REQUESTS['/pages/items']} requests")


# -------------------------------------------------------------------- feed

async def test_a_feed_that_is_not_well_formed_still_yields_its_entries(sources):
    """The feed parser is regex-based deliberately — a strict XML parser turns a
    slightly broken feed into a crawler that reports zero items forever. This
    feed contains an unescaped ampersand, which is invalid XML and extremely
    common, so the decision is under test rather than merely documented."""
    config = CrawlerConfig(
        name="changes", strategy="feed", seeds=[f"{sources}/feed.xml"],
        limits=Limits(rate_per_sec=50, max_items=100),
    )
    found, _b, _c = await _crawl(config)
    assert len(found) == 5, f"the malformed entry took the feed with it: {len(found)}"
    assert any("&" in (f.title or "") for f in found), (
        "the unescaped entry parsed but lost its title — the point of this test")


# ---------------------------------------------------------------- shared

async def test_every_source_refuses_an_unauthenticated_crawl(sources):
    """A template that forgot its credential should fail here rather than
    against somebody's account, and it should fail as 401 — which the crawler
    treats as fatal, because a quiet completion advances the watermark past
    records it never read."""
    from memdog.crawlers import CrawlerError

    config = CrawlerConfig(
        name="issues", strategy="http",
        request=HttpRequest(url=f"{sources}/gh/repos/acme/widgets/issues"),
        pagination=Pagination(type="none"),
        extract=Extract(items_path="@", id_path="number"),
        limits=Limits(rate_per_sec=50),
    )
    with pytest.raises(CrawlerError) as exc:
        await discover(config, watermark=None, checkpoint={}, auth=None)
    assert exc.value.status == 401
