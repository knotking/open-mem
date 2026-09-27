"""Reading a page with the model instead of downloading it.

The property worth guarding is not that URL Context works -- `test_urlcontext`
covers the reader itself -- but that **the choice between the two readers is
made deliberately and reported honestly**. Both failure modes here are silent:

A page that should have been read by the model and was fetched instead lands
`stored` with no text, and looks exactly like a page that was read and had
nothing to say. That is the hole this exists to close: a JavaScript-rendered
page answers 200 with an empty shell, so the GET *succeeds* and the old
fallback never fired.

And a model's account stored under a header claiming its bytes were
unretrievable, when they were never requested, is an untruth sitting inside the
corpus in the one sentence written to keep an account from being mistaken for
the page.
"""

from __future__ import annotations

import pytest

from open_mem.contracts import Pending, WriteItem, WriteOptions, WriteRequest
from open_mem.fetching import FetchError, FetchWorker
from open_mem.ids import new_id
from open_mem.urlcontext import Read, UrlNotRead
from open_mem.write import write_items

pytestmark = pytest.mark.asyncio

PAGE = "https://example.com/app"


class Reader:
    """A URL reader whose answer the test chooses, counting what it was asked."""

    enabled = True
    model_id = "fake-url-reader"

    def __init__(self, *, account: str | None = "The page says hello.") -> None:
        self._account = account
        self.calls: list[str] = []

    async def read(self, url: str) -> Read:
        self.calls.append(url)
        if self._account is None:
            raise UrlNotRead("the model would not read it", retryable=False)
        return Read(url=url, account=self._account, model_id=self.model_id)


class NeverReads:
    enabled = True
    model_id = "never"

    async def read(self, url: str):  # noqa: ANN201
        raise AssertionError("the model was asked to read a page it should not have")


async def _type(pool, tenant, *, name: str, url_reader: str = "fetch",
                enrich: bool = False):
    await pool.execute(
        """
        INSERT INTO memory_types (type_id, org_id, project_id, name, ttl_seconds,
                                  on_expiry, url_reader, enrich)
        VALUES ($1, $2, $3, $4, NULL, 'keep_members', $5, $6)
        ON CONFLICT (project_id, name) DO UPDATE SET
            url_reader = EXCLUDED.url_reader, enrich = EXCLUDED.enrich
        """,
        new_id("mty"), tenant.org_id, tenant.project_id, name, url_reader, enrich)
    return name


async def _add_page(pool, queue, blobs, settings, actor, tenant, *, type_name,
                    external_id="page-1", options=None):
    written = await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(
            producer_id=tenant.producer_id,
            options=options or WriteOptions(),
            items=[WriteItem(external_id=external_id,
                             memory={"key": "pages", "type": type_name},
                             content=Pending(provider="url", resource_id=PAGE))],
        ),
    )
    return written.results[0].data_id


async def _text(pool, blobs, data_id) -> str:
    ref = await pool.fetchval(
        "SELECT storage_ref FROM data_items WHERE data_id = $1", data_id)
    return (await blobs.get(ref)).decode("utf-8")


# ------------------------------------------------------------- which reader

async def test_a_context_type_reads_with_the_model_and_never_fetches(
    pool, queue, blobs, settings, tenant, principal_for, monkeypatch
):
    """The whole point: the GET is not attempted, so a page that would have
    answered 200 with an empty shell never gets the chance to look successful."""
    actor = await principal_for(tenant.api_key)
    name = await _type(pool, tenant, name="research", url_reader="context")
    data_id = await _add_page(pool, queue, blobs, settings, actor, tenant,
                              type_name=name)

    async def refuse(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("an HTTP GET was made where the model should have read")

    monkeypatch.setattr("open_mem.fetching.fetch_url", refuse)
    worker = FetchWorker(pool, blobs, settings)
    reader = Reader()
    worker._url_reader = reader
    await worker.fetch(data_id)

    assert reader.calls == [PAGE]
    assert "The page says hello." in await _text(pool, blobs, data_id)


async def test_an_ordinary_type_never_asks_the_model(
    pool, queue, blobs, settings, tenant, principal_for, monkeypatch
):
    """Unchanged by default. A model call behind every URL in the project is
    exactly the cost this column exists to keep opt-in."""
    from open_mem.fetching import Fetched

    actor = await principal_for(tenant.api_key)
    name = await _type(pool, tenant, name="inbox", url_reader="fetch")
    data_id = await _add_page(pool, queue, blobs, settings, actor, tenant,
                              type_name=name)

    async def ok(url, **kwargs):  # noqa: ANN001, ANN003
        return Fetched(payload=b"<html>real bytes</html>",
                       mime_type="text/html", final_url=url, redirects=0)

    monkeypatch.setattr("open_mem.fetching.fetch_url", ok)
    worker = FetchWorker(pool, blobs, settings)
    worker._url_reader = NeverReads()
    await worker.fetch(data_id)

    assert "real bytes" in await _text(pool, blobs, data_id)


async def test_a_refusal_falls_through_to_an_ordinary_get(
    pool, queue, blobs, settings, tenant, principal_for, monkeypatch
):
    """The backup is the reason inverting the order is safe: a model that will
    not read the page costs a slower path, not an empty record."""
    from open_mem.fetching import Fetched

    actor = await principal_for(tenant.api_key)
    name = await _type(pool, tenant, name="research", url_reader="context")
    data_id = await _add_page(pool, queue, blobs, settings, actor, tenant,
                              type_name=name)

    async def ok(url, **kwargs):  # noqa: ANN001, ANN003
        return Fetched(payload=b"fetched anyway", mime_type="text/plain",
                       final_url=url, redirects=0)

    monkeypatch.setattr("open_mem.fetching.fetch_url", ok)
    worker = FetchWorker(pool, blobs, settings)
    worker._url_reader = Reader(account=None)
    await worker.fetch(data_id)

    assert "fetched anyway" in await _text(pool, blobs, data_id)


async def test_the_model_is_not_asked_twice(
    pool, queue, blobs, settings, tenant, principal_for, monkeypatch
):
    """Refused as the primary reader and then retried as the fallback is a
    second bill for an answer that cannot change."""
    actor = await principal_for(tenant.api_key)
    name = await _type(pool, tenant, name="research", url_reader="context")
    data_id = await _add_page(pool, queue, blobs, settings, actor, tenant,
                              type_name=name)

    async def refuse(*args, **kwargs):  # noqa: ANN002, ANN003
        raise FetchError("403 from the site")

    monkeypatch.setattr("open_mem.fetching.fetch_url", refuse)
    worker = FetchWorker(pool, blobs, settings)
    reader = Reader(account=None)
    worker._url_reader = reader
    with pytest.raises(FetchError):
        await worker.fetch(data_id)

    assert len(reader.calls) == 1, "the model was asked again after refusing"


async def test_one_memory_asking_is_enough(
    pool, queue, blobs, settings, tenant, principal_for, monkeypatch
):
    """A record can be in several memories. Reading it depends on what its
    containers say, not on which of them happens to sort first."""
    actor = await principal_for(tenant.api_key)
    plain = await _type(pool, tenant, name="inbox", url_reader="fetch")
    await _type(pool, tenant, name="research", url_reader="context")
    data_id = await _add_page(pool, queue, blobs, settings, actor, tenant,
                              type_name=plain)

    from open_mem.memories import add_member, upsert_memory

    async with pool.acquire() as conn, conn.transaction():
        other = await upsert_memory(
            conn, org_id=tenant.org_id, project_id=tenant.project_id,
            type_name="research", memory_key="also-here")
        await add_member(conn, other, data_id, "explicit")

    async def refuse(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("a GET was made though one memory asked for the model")

    monkeypatch.setattr("open_mem.fetching.fetch_url", refuse)
    worker = FetchWorker(pool, blobs, settings)
    worker._url_reader = Reader()
    await worker.fetch(data_id)
    assert "The page says hello." in await _text(pool, blobs, data_id)


# -------------------------------------------------------------- what it says

async def test_the_header_never_claims_bytes_were_unavailable(
    pool, queue, blobs, settings, tenant, principal_for, monkeypatch
):
    """The stored account carries one sentence explaining why it is an account
    and not the page. As the chosen reader the bytes were never requested, and
    saying they "were not retrievable" would be an untruth inside the corpus."""
    actor = await principal_for(tenant.api_key)
    name = await _type(pool, tenant, name="research", url_reader="context")
    data_id = await _add_page(pool, queue, blobs, settings, actor, tenant,
                              type_name=name)

    monkeypatch.setattr("open_mem.fetching.fetch_url", NeverReads().read)
    worker = FetchWorker(pool, blobs, settings)
    worker._url_reader = Reader()
    await worker.fetch(data_id)

    stored = await _text(pool, blobs, data_id)
    assert "reading of the page rather than the page itself" in stored
    assert "not retrievable" not in stored


async def test_the_fallback_header_still_says_the_bytes_were_unavailable(
    pool, queue, blobs, settings, tenant, principal_for, monkeypatch
):
    """The other order, and the other true sentence."""
    actor = await principal_for(tenant.api_key)
    name = await _type(pool, tenant, name="inbox", url_reader="fetch")
    data_id = await _add_page(pool, queue, blobs, settings, actor, tenant,
                              type_name=name)

    async def refuse(*args, **kwargs):  # noqa: ANN002, ANN003
        raise FetchError("403 from the site")

    monkeypatch.setattr("open_mem.fetching.fetch_url", refuse)
    worker = FetchWorker(pool, blobs, settings)
    worker._url_reader = Reader()
    await worker.fetch(data_id)

    assert "not retrievable" in await _text(pool, blobs, data_id)


# -------------------------------------------------------------- enrichment

async def _enrichments(pool, data_id) -> int:
    return await pool.fetchval(
        "SELECT count(*) FROM domain_events "
        "WHERE data_id = $1 AND event_type = 'enrichment.requested'", data_id)


async def test_an_enriching_memory_enriches_what_lands_in_it(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Per-write opt-in is the right grain for an inbox and the wrong one for a
    container that exists to be searched. Every record in a corpus of papers
    sitting at `stored` is not a saving, it is the feature not working."""
    actor = await principal_for(tenant.api_key)
    name = await _type(pool, tenant, name="papers", enrich=True)
    data_id = await _add_page(pool, queue, blobs, settings, actor, tenant,
                              type_name=name)
    assert await _enrichments(pool, data_id) == 1


async def test_reading_with_a_model_does_not_imply_enriching(
    pool, queue, blobs, settings, tenant, principal_for
):
    """They were one rule for a day, because the first memory that wanted the
    model reader also wanted enrichment. They are different decisions, and the
    coupling broke a real case: a memory of papers wants enrichment and wants
    its PDFs downloaded, so asking for one asked for the other."""
    actor = await principal_for(tenant.api_key)
    name = await _type(pool, tenant, name="research", url_reader="context")
    data_id = await _add_page(pool, queue, blobs, settings, actor, tenant,
                              type_name=name)
    assert await _enrichments(pool, data_id) == 0


async def test_an_explicit_no_still_wins(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A per-request option is the most specific level of the settings chain. A
    type that overrode a stated "no" would spend money against instruction."""
    actor = await principal_for(tenant.api_key)
    name = await _type(pool, tenant, name="papers", enrich=True)
    data_id = await _add_page(
        pool, queue, blobs, settings, actor, tenant, type_name=name,
        external_id="page-quiet", options=WriteOptions(enrich=False))
    assert await _enrichments(pool, data_id) == 0


async def test_a_file_the_source_named_is_downloaded_not_read(
    pool, queue, blobs, settings, tenant, principal_for, monkeypatch
):
    """"Read pages with the model" is about pages.

    A crawler that found an open-access PDF named a *document to download*.
    Handing its URL to a model and storing the reading throws away the paper to
    keep a summary of it — and the summary cannot be re-parsed, re-chunked, or
    quoted from. The reference says which it is; the extension would be a guess.
    """
    from open_mem.fetching import Fetched

    actor = await principal_for(tenant.api_key)
    name = await _type(pool, tenant, name="research", url_reader="context")
    written = await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=tenant.producer_id, items=[
            WriteItem(external_id="paper-1",
                      memory={"key": "pages", "type": name},
                      content=Pending(provider="url",
                                      resource_id="https://arxiv.org/pdf/1234.pdf",
                                      hints={"kind": "file"}))]))
    data_id = written.results[0].data_id

    async def ok(url, **kwargs):  # noqa: ANN001, ANN003
        return Fetched(payload=b"%PDF-1.4 the actual paper",
                       mime_type="application/pdf", final_url=url, redirects=0)

    monkeypatch.setattr("open_mem.fetching.fetch_url", ok)
    worker = FetchWorker(pool, blobs, settings)
    worker._url_reader = NeverReads()
    await worker.fetch(data_id)

    assert "the actual paper" in await _text(pool, blobs, data_id)


async def test_a_crawler_that_never_chose_lets_the_memory_decide(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A default is not a decision.

    `CrawlerConfig.enrich` was `False` and was passed as an explicit `false` on
    every write, so a memory type asking for enrichment could never get it —
    a corpus of papers pulled into a type that exists to be searched arrived
    entirely at `stored`, and the rule that did it is the one protecting a
    stated "no" from being overridden. The crawler had not stated anything.
    """
    from open_mem.contracts import Inline
    from open_mem.crawlers import CrawlerConfig

    # Unset, not false: the distinction this test exists for.
    assert CrawlerConfig(name="c", strategy="http").enrich is None

    actor = await principal_for(tenant.api_key)
    name = await _type(pool, tenant, name="papers", enrich=True)
    written = await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(
            producer_id=tenant.producer_id,
            options=WriteOptions(enrich=CrawlerConfig(name="c", strategy="http").enrich),
            items=[WriteItem(external_id="paper.txt",
                             memory={"key": "corpus", "type": name},
                             content=Inline(text="An abstract about deep networks."))],
        ))
    assert await _enrichments(pool, written.results[0].data_id) == 1
