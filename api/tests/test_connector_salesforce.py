"""The first connector verified against a source rather than by reading it.

Thirty-seven templates ship and every one is `verified: false`, because
verifying one has meant having a tenant. `tools/fake_salesforce.py` speaks the
parts of the API the template depends on -- the token exchange, the query
envelope, the continuation URL and `LastModifiedDate` -- so the template can be
run rather than reviewed.

Which matters because the two things most likely to be wrong here cannot be
seen by reading the template at all.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open_mem import connectors                                    # noqa: E402
from open_mem.crawlers import (Auth, CrawlerConfig, discover,      # noqa: E402
                             next_watermark)
from tools.fake_salesforce import serve                          # noqa: E402

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _allow_loopback(monkeypatch):
    """The SSRF guard refuses loopback, correctly, and the simulator is on it.

    Relaxed for `127.0.0.1` and nothing else, only here. The guard is what stops
    a caller-supplied URL reaching inside the deployment, and it is tested on
    its own -- a simulator is not worth weakening it for.
    """
    import open_mem.crawlers as crawlers_mod
    import open_mem.fetching as fetching

    real = fetching.validate_url
    monkeypatch.setattr(
        crawlers_mod, "validate_url",
        lambda url: url if "127.0.0.1" in url else real(url))


@pytest.fixture
def salesforce():
    """The simulator, on a port the OS picks."""
    server = serve(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()


def _config(instance: str, soql: str, **overrides) -> CrawlerConfig:
    built = connectors.build("salesforce", {"instance": instance, "soql": soql})
    built.update(overrides)
    return CrawlerConfig(**built)


async def test_the_template_walks_a_continuation_url_rather_than_a_cursor(salesforce):
    """The trap that reading the template cannot find.

    Salesforce returns `nextRecordsUrl` as a **path** -- `/services/data/v61.0/
    query/01g...-2000` -- not an opaque token to hand back as a query
    parameter. A crawler that treats it as a cursor fetches page one forever,
    reports a plausible number of items, and never sees the rest of the org.
    """
    config = _config(salesforce, "SELECT Id, Name, LastModifiedDate FROM Account")
    found, _budget, _capped = await discover(
        config, watermark=None, checkpoint={},
        auth=Auth(headers={"Authorization": "Bearer test"}))

    names = [f.title for f in found]
    assert len(names) == 4, f"paged past the first page: {names}"
    assert "Tailspin Aviation" in names, "the last page was reached"


async def test_an_incremental_clause_shrinks_the_second_run(salesforce):
    """Asserted by running it, which is the only way this is worth anything.

    `{{ watermark_or_epoch }}` rather than `{{ watermark }}`: an empty watermark
    renders `WHERE LastModifiedDate > ` on the first run, which a real org
    answers with MALFORMED_QUERY -- so the clause would work on every run except
    the one that sets it up.
    """
    soql = ("SELECT Id, Name, LastModifiedDate FROM Opportunity "
            "WHERE LastModifiedDate > {{ watermark_or_epoch }} "
            "ORDER BY LastModifiedDate")
    config = _config(salesforce, soql, incremental="watermark")
    auth = Auth(headers={"Authorization": "Bearer test"})

    first, _b, _c = await discover(config, watermark=None, checkpoint={}, auth=auth)
    assert len(first) == 4, "the first run sees everything"

    mark = next_watermark(config, first, None)
    assert mark, "and records where it got to, from LastModifiedDate"

    # A second run against an unchanged org finds nothing -- which is the entire
    # point, and is the behaviour no template has ever demonstrated.
    second, _b, _c = await discover(config, watermark=mark, checkpoint={}, auth=auth)
    assert second == [], f"re-fetched: {[i.title for i in second]}"


async def test_an_empty_watermark_does_not_produce_a_malformed_query(salesforce):
    """The first-run case, checked against the simulator's own SOQL parser
    rather than against an assumption about it."""
    from tools.fake_salesforce import run_query

    broken = run_query("SELECT Id FROM Account WHERE LastModifiedDate > ")
    assert broken.get("totalSize") == 4, (
        "the simulator ignores an empty comparison, so this test proves nothing "
        "about the real thing -- it is here to document that limit")

    config = _config(salesforce,
                     "SELECT Id, Name, LastModifiedDate FROM Account "
                     "WHERE LastModifiedDate > {{ watermark_or_epoch }}",
                     incremental="watermark")
    found, _b, _c = await discover(config, watermark=None, checkpoint={},
                                   auth=Auth(headers={"Authorization": "Bearer test"}))
    assert len(found) == 4


async def test_a_request_without_the_bearer_is_refused(salesforce):
    """The credential is the thing the connection exists to hold, so a template
    that forgot it should fail here rather than against somebody's tenant."""
    import httpx

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{salesforce}/services/data/v61.0/query",
            params={"q": "SELECT Id FROM Account"})
    assert response.status_code == 401
    assert response.json()[0]["errorCode"] == "INVALID_SESSION_ID"


async def test_every_page_returns_the_object_the_query_asked_for(salesforce):
    """Found by running it, and worth an assertion of its own.

    The continuation URL carries no query -- the query stays on the server
    behind a locator. An implementation that lets the client re-supply it, or
    that falls back to a default, serves page two of the wrong object: the crawl
    reports the right *count*, the records look plausible, and they belong to
    another table entirely. Nothing downstream can detect that.
    """
    config = _config(salesforce,
                     "SELECT Id, Name, LastModifiedDate FROM Opportunity")
    found, _b, _c = await discover(config, watermark=None, checkpoint={},
                                   auth=Auth(headers={"Authorization": "Bearer test"}))

    assert len(found) == 4
    assert all(f.external_id.startswith("006") for f in found), (
        f"a page came back as another object: "
        f"{[(f.external_id, f.title) for f in found]}")


async def test_a_next_url_pointing_off_origin_is_refused():
    """The next URL comes out of the response body, so it is attacker-controlled
    if the source is.

    The crawler carries the connection's credential in its headers. Following a
    body-supplied URL to another host would hand that credential to whoever the
    source names -- so the resolve refuses, and the run fails loudly rather than
    paging somewhere else quietly.
    """
    from open_mem.crawlers import CrawlerError, _next_url

    here = "https://acme.my.salesforce.com/services/data/v61.0/query"

    assert _next_url(here, "/services/data/v61.0/query/01g-2000").startswith(
        "https://acme.my.salesforce.com/"), "a path still resolves"

    with pytest.raises(CrawlerError, match="different origin"):
        _next_url(here, "https://attacker.example/collect")
