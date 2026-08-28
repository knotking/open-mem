"""W2 -- resolving a Pending reference.

Most of this file is about refusing things. Fetching a caller-supplied URL from
inside the deployment is server-side request forgery by construction: the
request comes from a host that can reach the VPC, the database's private IP,
and a metadata server that hands out credentials.
"""

from __future__ import annotations

import pytest

from memdog.contracts import Pending, WriteItem, WriteOptions, WriteRequest
from memdog.events import list_events
from memdog.fetching import FetchError, FetchWorker, validate_url
from memdog.queue import InProcessQueue
from memdog.retrieval import get_item
from memdog.write import write_items

pytestmark = pytest.mark.asyncio


# ----------------------------------------------------------- the boundary


@pytest.mark.parametrize(
    "url,because",
    [
        ("http://169.254.169.254/computeMetadata/v1/", "the metadata address"),
        ("http://metadata.google.internal/token", "the metadata name"),
        ("http://localhost:8080/admin", "loopback"),
        ("http://127.0.0.1/", "loopback by address"),
        ("http://10.100.0.3:5432/", "the database's private IP"),
        ("http://192.168.1.1/", "a private range"),
        ("file:///etc/passwd", "a scheme that reads files"),
        ("gopher://evil/", "a scheme that is not fetchable"),
        ("https:///nohost", "no host at all"),
    ],
)
async def test_urls_that_reach_inside_are_refused(url, because):
    with pytest.raises(FetchError):
        validate_url(url)


async def test_an_ordinary_public_url_is_allowed():
    assert validate_url("https://example.com/report.pdf")


async def test_the_check_runs_on_every_resolved_address():
    """A name with one public and one private record would otherwise pass the
    check and then connect to the private one."""
    import memdog.fetching as fetching

    real = fetching.socket.getaddrinfo
    try:
        fetching.socket.getaddrinfo = lambda host, port: [
            (2, 1, 6, "", ("93.184.216.34", 0)),
            (2, 1, 6, "", ("169.254.169.254", 0)),
        ]
        with pytest.raises(FetchError) as exc:
            validate_url("https://looks-fine.example/x")
        assert "private or link-local" in str(exc.value)
    finally:
        fetching.socket.getaddrinfo = real


# -------------------------------------------------------------- ordering


async def test_enrichment_waits_for_the_fetch_not_the_write(
    pool, queue, blobs, settings, tenant, principal_for
):
    """A Pending ref has no bytes yet. Making the fetch the *cause* of the
    enrichment reuses the ordering gate instead of inventing a second one."""
    actor = await principal_for(tenant.api_key)
    idle = InProcessQueue()          # nothing consumes, so nothing is released
    await write_items(
        pool, idle, blobs, settings, actor,
        WriteRequest(
            producer_id=tenant.producer_id,
            items=[WriteItem(external_id="remote-1",
                             content=Pending(provider="url",
                                             resource_id="https://example.com/a.pdf"))],
            options=WriteOptions(enrich=True),
        ),
    )
    await idle.close()

    events = {e["event_type"]: e for e in await list_events(pool, tenant.org_id)}
    assert "fetch.requested" in events
    fetch, enrich = events["fetch.requested"], events["enrichment.requested"]
    # Not the write: the enrichment names the fetch, so it cannot start until
    # the bytes are actually here.
    assert enrich["caused_by"] == fetch["event_id"]
    assert enrich["status"] == "pending"


async def test_a_pending_item_reports_that_it_has_no_bytes(
    pool, queue, blobs, settings, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    written = await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=tenant.producer_id, items=[
            WriteItem(external_id="remote-2",
                      content=Pending(provider="google-drive", resource_id="1AbC")),
        ]),
    )
    item = await get_item(pool, actor, written.results[0].data_id)
    assert item["is_downloaded"] is False
    assert item["pending_ref"]["provider"] == "google-drive"


async def test_a_provider_with_no_fetcher_says_which(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Every other provider needs a credential from its connection. Failing
    opaquely would look identical to a bug."""
    actor = await principal_for(tenant.api_key)
    written = await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=tenant.producer_id, items=[
            WriteItem(external_id="drive-1",
                      content=Pending(provider="google-drive", resource_id="1AbC")),
        ]),
    )
    worker = FetchWorker(pool, blobs, settings)
    with pytest.raises(FetchError) as exc:
        await worker.fetch(written.results[0].data_id)
    assert "google-drive" in str(exc.value)


async def test_fetching_twice_is_the_same_as_fetching_once(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Redelivery is at-least-once in every queue implementation."""
    actor = await principal_for(tenant.api_key)
    written = await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=tenant.producer_id, items=[
            WriteItem(external_id="idem-fetch",
                      content=Pending(provider="url", resource_id="https://example.com/x")),
        ]),
    )
    data_id = written.results[0].data_id
    await pool.execute(
        """
        UPDATE data_items SET pending_ref = NULL, storage_ref = 'file://already/there'
        WHERE data_id = $1
        """,
        data_id,
    )
    worker = FetchWorker(pool, blobs, settings)
    await worker.fetch(data_id)      # must not raise, must not re-fetch
    assert await pool.fetchval(
        "SELECT storage_ref FROM data_items WHERE data_id = $1", data_id
    ) == "file://already/there"


async def test_a_refused_url_records_why_on_the_item(
    pool, queue, blobs, settings, tenant, principal_for
):
    """Retrying a refused URL never succeeds, so the reason belongs on the row
    rather than in a retry loop."""
    from memdog.queue import Message

    actor = await principal_for(tenant.api_key)
    written = await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=tenant.producer_id, items=[
            WriteItem(external_id="ssrf-1",
                      content=Pending(provider="url",
                                      resource_id="http://169.254.169.254/token")),
        ]),
    )
    data_id = written.results[0].data_id
    worker = FetchWorker(pool, blobs, settings)
    await worker.handle(Message("fetch", {"data_id": data_id}))

    row = await pool.fetchrow(
        "SELECT parse_status, parse_detail FROM data_items WHERE data_id = $1", data_id
    )
    assert row["parse_status"] == "unsupported"
    assert row["parse_detail"]["stage"] == "fetch"
    assert "metadata" in row["parse_detail"]["reason"]
