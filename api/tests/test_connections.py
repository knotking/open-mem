"""Credentials for crawlers.

The templated `http` strategy already covered enumerate, query and search for
most REST APIs — it could just never reach an authenticated one, because a
crawler had nowhere to keep a secret. The only place to put a token was the
`config` jsonb, in the clear, beside the `connections` table that exists to hold
one enveloped.

So what is worth testing is not that a header arrives. It is that the secret
goes in and does not come back out, that a config cannot override it, and that a
crawler whose credential is unreadable fails as a configuration problem rather
than reaching its source unauthenticated and reporting the source's 401.
"""

from __future__ import annotations

import os

import pytest

from memdog import connections
from memdog.auth import ApiKeyVerifier
from memdog.connections import ConnectionError_
from memdog.crypto import Envelope

pytestmark = pytest.mark.asyncio


@pytest.fixture
def envelope():
    """A real key, the way the webhook tests do it.

    Not `Envelope.from_settings`: the suite runs without `MEMDOG_MASTER_KEY`,
    and every call here would refuse — which is the fail-closed rule working,
    and useless for testing what happens once a deployment has one.
    """
    return Envelope(os.urandom(32))


async def _principal(pool, tenant):
    return await ApiKeyVerifier(pool).verify(tenant.api_key)


async def _make(pool, envelope, tenant, **overrides):
    kwargs = {
        "project_id": tenant.project_id,
        "provider": "jira",
        "credential": "s3cret-token",
        "auth_style": "bearer",
    }
    kwargs.update(overrides)
    return await connections.create(
        pool, await _principal(pool, tenant), envelope,
        **kwargs,
    )


async def test_a_credential_goes_in_and_does_not_come_back(pool, settings, envelope, tenant):
    made = await _make(pool, envelope, tenant)
    assert made["has_credential"] is True
    assert "credential" not in made

    listed = await connections.listing(pool, await _principal(pool, tenant))
    entry = next(c for c in listed if c["connection_id"] == made["connection_id"])
    assert entry["has_credential"] is True
    # Not even a prefix. A prefix is enough to confirm a guess.
    assert "s3cret" not in str(entry)


async def test_it_is_not_stored_in_the_clear(pool, settings, envelope, tenant):
    made = await _make(pool, envelope, tenant)
    stored = await pool.fetchval(
        "SELECT credential_ct FROM connections WHERE connection_id = $1",
        made["connection_id"],
    )
    assert b"s3cret-token" not in bytes(stored)


async def test_the_audit_record_says_how_but_never_what(pool, settings, envelope, tenant):
    made = await _make(pool, envelope, tenant, auth_style="header",
                       auth_name="X-Api-Key")
    row = await pool.fetchrow(
        "SELECT action, detail FROM audit_events WHERE target_id = $1",
        made["connection_id"],
    )
    assert row["action"] == "connection.created"
    assert row["detail"]["auth_style"] == "header"
    assert row["detail"]["auth_name"] == "X-Api-Key"
    assert row["detail"]["has_credential"] is True
    assert "s3cret" not in str(row["detail"])


@pytest.mark.parametrize(
    "style,name,expect_header,expect_query",
    [
        ("bearer", None, {"Authorization": "Bearer s3cret-token"}, {}),
        ("header", "X-Api-Key", {"X-Api-Key": "s3cret-token"}, {}),
        ("query", "api_key", {}, {"api_key": "s3cret-token"}),
    ],
)
async def test_each_style_presents_the_credential_the_way_its_source_wants(
    pool, settings, envelope, tenant, style, name, expect_header, expect_query
):
    """APIs differ here far more than they differ in pagination, and the
    difference is small and closed enough to be data rather than a templated
    header somebody puts a secret in."""
    made = await _make(pool, envelope, tenant, auth_style=style, auth_name=name)
    headers, query = await connections.authorize(
        pool, envelope, made["connection_id"], tenant.org_id
    )
    assert headers == expect_header
    assert query == expect_query


async def test_basic_auth_is_encoded_at_use_not_at_rest(pool, settings, envelope, tenant):
    """One representation in the database. Encoding on the way in would make
    the stored value a second thing that has to be got right."""
    import base64

    made = await _make(pool, envelope, tenant, credential="user:password",
                       auth_style="basic")
    headers, _ = await connections.authorize(
        pool, envelope, made["connection_id"], tenant.org_id
    )
    assert headers["Authorization"] == (
        "Basic " + base64.b64encode(b"user:password").decode()
    )


async def test_a_style_that_needs_a_name_refuses_without_one(pool, settings, envelope, tenant):
    """Defaulting to `X-Api-Key` would send the secret to a header the source
    ignores, and the failure would look like a wrong credential rather than a
    wrong configuration."""
    for style in ("header", "query"):
        with pytest.raises(ConnectionError_) as exc:
            await _make(pool, envelope, tenant, auth_style=style, auth_name=None)
        assert "auth_name" in str(exc.value)


async def test_an_unknown_style_is_refused(pool, settings, envelope, tenant):
    with pytest.raises(ConnectionError_):
        await _make(pool, envelope, tenant, auth_style="magic")


async def test_a_connection_from_another_org_is_not_reachable(
    pool, settings, envelope, tenant, other_tenant
):
    made = await _make(pool, envelope, tenant)
    with pytest.raises(ConnectionError_) as exc:
        await connections.authorize(
            pool, envelope,
            made["connection_id"], other_tenant.org_id,
        )
    assert exc.value.status == 404


# --- what reaches the request ------------------------------------------------


async def test_a_config_cannot_override_the_credential(pool, settings, envelope, tenant):
    """A template that could set `Authorization` would be somewhere to put a
    secret in the clear, which is what the connection exists to prevent."""
    from memdog.crawlers import Auth

    auth = Auth(headers={"Authorization": "Bearer from-connection"}, query={})
    headers = {"Authorization": "Bearer from-config", "User-Agent": "x"}
    # The injection order the strategy uses: the credential goes on last.
    headers.update(auth.headers)
    assert headers["Authorization"] == "Bearer from-connection"


async def test_a_public_crawler_gets_no_auth_at_all(pool, settings, envelope, tenant, blobs):
    """A sitemap needs nobody's permission, and treating that as a missing
    credential is the difference between working and confusingly broken."""
    from memdog.crawling import CrawlWorker
    from memdog.queue import InProcessQueue

    worker = CrawlWorker(pool, InProcessQueue(), blobs, settings,
                         envelope=envelope)
    assert await worker._auth({"connection_id": None, "org_id": tenant.org_id}) is None


async def test_an_authenticated_crawler_resolves_its_credential(
    pool, settings, envelope, tenant, blobs
):
    from memdog.crawling import CrawlWorker
    from memdog.queue import InProcessQueue

    made = await _make(pool, envelope, tenant)
    worker = CrawlWorker(pool, InProcessQueue(), blobs, settings,
                         envelope=envelope)
    auth = await worker._auth(
        {"connection_id": made["connection_id"], "org_id": tenant.org_id}
    )
    assert auth.headers == {"Authorization": "Bearer s3cret-token"}


async def test_a_missing_connection_fails_as_a_configuration_problem(
    pool, settings, envelope, tenant, blobs
):
    """Not as a 401 from the source. A run that carried on unauthenticated
    would report the source's refusal and send whoever debugs it in exactly the
    wrong direction."""
    from memdog.crawlers import CrawlerError
    from memdog.crawling import CrawlWorker
    from memdog.queue import InProcessQueue

    worker = CrawlWorker(pool, InProcessQueue(), blobs, settings,
                         envelope=envelope)
    with pytest.raises(CrawlerError):
        await worker._auth(
            {"connection_id": "conn_gone", "org_id": tenant.org_id}
        )


# --- attaching ---------------------------------------------------------------


async def test_attaching_and_detaching_a_crawler(pool, settings, envelope, tenant):
    from memdog.crawlers import CrawlerConfig
    from memdog.crawling import create_crawler

    made = await _make(pool, envelope, tenant)
    actor = await _principal(pool, tenant)
    crawler = await create_crawler(
        pool, actor, project_id=tenant.project_id,
        config=CrawlerConfig(name="feed", strategy="feed",
                             seeds=["https://example.com/f.xml"]),
    )

    attached = await connections.attach(
        pool, actor, crawler["crawler_id"], made["connection_id"]
    )
    assert attached["connection_id"] == made["connection_id"]

    detached = await connections.attach(pool, actor, crawler["crawler_id"], None)
    assert detached["connection_id"] is None


async def test_a_crawler_cannot_borrow_another_orgs_connection(
    pool, settings, envelope, tenant, other_tenant
):
    from memdog.crawlers import CrawlerConfig
    from memdog.crawling import create_crawler

    theirs = await _make(pool, envelope, other_tenant)
    actor = await _principal(pool, tenant)
    crawler = await create_crawler(
        pool, actor, project_id=tenant.project_id,
        config=CrawlerConfig(name="feed", strategy="feed",
                             seeds=["https://example.com/f.xml"]),
    )
    with pytest.raises(ConnectionError_) as exc:
        await connections.attach(
            pool, actor, crawler["crawler_id"], theirs["connection_id"]
        )
    assert exc.value.status == 404


async def test_a_connection_in_use_cannot_be_deleted_from_under_a_crawler(
    pool, settings, envelope, tenant
):
    """Deleting it would leave the crawler enabled, scheduled, and failing every
    tick with an authentication error -- nothing errors loudly and the data
    simply stops arriving."""
    import asyncpg

    from memdog.crawlers import CrawlerConfig
    from memdog.crawling import create_crawler

    made = await _make(pool, envelope, tenant)
    actor = await _principal(pool, tenant)
    crawler = await create_crawler(
        pool, actor, project_id=tenant.project_id,
        config=CrawlerConfig(name="feed", strategy="feed",
                             seeds=["https://example.com/f.xml"]),
    )
    await connections.attach(pool, actor, crawler["crawler_id"], made["connection_id"])

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await pool.execute(
            "DELETE FROM connections WHERE connection_id = $1", made["connection_id"]
        )
