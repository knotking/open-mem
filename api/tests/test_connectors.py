"""The connector catalog.

The crawler could already reach any REST API with a token; nobody could find
that out, because the console offered three presets shaped like strategies. What
this adds is not plumbing — it is the knowledge somebody would otherwise have to
look up, written down as data.

Which makes the interesting tests the ones about honesty. Every entry must
render into a config the crawler validator accepts, must declare what only the
operator knows rather than guessing it, and must not claim to work when the
thing it needs does not exist.
"""

from __future__ import annotations

import pytest

from memdog import connectors
from memdog.connectors import CATALOG, ConnectorError
from memdog.crawlers import CrawlerConfig

# No module-level asyncio mark: `asyncio_mode = "auto"` runs the async tests
# already, and most of these are pure — a catalog entry either renders into a
# valid config or it does not, and that needs no database.


def _scope_for(connector) -> dict[str, str]:
    return {s.key: f"value-for-{s.key}" for s in connector.scopes}


def test_every_available_entry_renders_a_config_the_crawler_accepts():
    """A catalog entry that does not validate is worse than no entry: it turns
    a working feature into a form that fails on submit."""
    available = [c for c in CATALOG if c.requires is None]
    assert available, "the catalog has nothing usable in it"

    for connector in available:
        config = connectors.build(connector.key, _scope_for(connector))
        # The validator the ordinary create path uses. Nothing here is a
        # shortcut around it.
        parsed = CrawlerConfig.model_validate(config)
        assert parsed.strategy == "http"
        assert parsed.request is not None
        assert parsed.extract.items_path
        assert parsed.extract.id_path, f"{connector.key} has no stable id"


def test_no_placeholder_survives_into_a_rendered_config():
    """A `{database}` left in a URL authenticates, 404s, and reads as a broken
    integration rather than an unfinished form."""
    import json

    for connector in [c for c in CATALOG if c.requires is None]:
        config = connectors.build(connector.key, _scope_for(connector))
        rendered = json.dumps(config)
        for scope in connector.scopes:
            assert "{" + scope.key + "}" not in rendered, connector.key


def test_a_missing_scope_value_is_refused_rather_than_guessed():
    needs_scope = next(c for c in CATALOG if c.requires is None and c.scopes)
    with pytest.raises(ConnectorError) as exc:
        connectors.build(needs_scope.key, {})
    assert needs_scope.scopes[0].key in str(exc.value)


def test_a_blocked_connector_says_what_is_missing_and_refuses_to_build():
    """"We do not support Google" and "Google needs a consent flow nobody has
    built" are different sentences, and only one of them is true."""
    blocked = [c for c in CATALOG if c.requires]
    assert {c.key for c in blocked} >= {
        "google_drive", "gmail", "sharepoint", "outlook", "salesforce",
    }
    for connector in blocked:
        assert connector.notes, f"{connector.key} is blocked and says nothing"
        with pytest.raises(ConnectorError) as exc:
            connectors.build(connector.key, {})
        assert exc.value.status == 409
        assert connector.requires in str(exc.value)


def test_the_catalog_lists_blocked_entries_rather_than_hiding_them():
    """Hiding them would make the catalog look complete."""
    listed = connectors.catalog()
    keys = {c["key"] for c in listed}
    assert "google_drive" in keys
    entry = next(c for c in listed if c["key"] == "google_drive")
    assert entry["available"] is False
    assert entry["requires"] == "oauth"
    assert "not built" in entry["notes"]


def test_nothing_claims_to_be_verified_that_has_not_been():
    """`verified` means somebody ran it against a live account. Almost nothing
    has, because that needs a credential — and an entry that implies a test
    which never happened is worse than one that admits it."""
    for connector in CATALOG:
        assert connector.verified is False, (
            f"{connector.key} claims verification — if that is real, say who "
            "ran it and against what"
        )


def test_every_entry_declares_how_its_credential_is_presented():
    from memdog.connections import AUTH_STYLES, NEEDS_NAME

    for connector in CATALOG:
        assert connector.auth_style in AUTH_STYLES, connector.key
        if connector.auth_style in NEEDS_NAME:
            assert connector.auth_name, (
                f"{connector.key} uses {connector.auth_style} and names no "
                "header or parameter, so the credential would go nowhere"
            )


def test_keys_and_labels_are_unique():
    keys = [c.key for c in CATALOG]
    assert len(keys) == len(set(keys))
    labels = [c.label for c in CATALOG]
    assert len(labels) == len(set(labels))


def test_rendering_does_not_break_on_a_value_containing_braces():
    """JQL and GraphQL both contain braces. A format-string call over the whole
    template would raise on them, or interpolate something nobody meant."""
    config = connectors.build(
        "jira", {"site": "https://acme.atlassian.net",
                 "jql": "project = ENG AND labels in ({x})"}
    )
    assert "{x}" in config["request"]["query"]["jql"]
    CrawlerConfig.model_validate(config)


# --- through the API ---------------------------------------------------------


@pytest.fixture
async def client(pool, tenant):
    import httpx

    from memdog.app import app

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield c


async def test_the_catalog_is_readable_without_a_credential(client):
    """Someone deciding whether this is worth an account should see what it
    supports before they have one."""
    response = await client.get("/api/v1/connectors")
    assert response.status_code == 200
    assert len(response.json()["connectors"]) == len(CATALOG)


async def test_creating_a_crawler_from_a_catalog_entry(client, tenant, pool):
    auth = {"Authorization": f"Bearer {tenant.api_key}"}
    response = await client.post(
        "/api/v1/crawlers/from-connector", headers=auth,
        json={"project_id": tenant.project_id, "connector": "github_issues",
              "scope": {"repo": "knotking/mem-dog"}},
    )
    assert response.status_code == 200, response.text
    created = response.json()

    # A catalog entry is a shortcut through the configuration, never around the
    # gate: it is still draft until a dry run passes.
    assert created["status"] == "draft"
    assert created["dry_run_required"] is True

    config = await pool.fetchval(
        "SELECT config FROM crawlers WHERE crawler_id = $1", created["crawler_id"]
    )
    stored = config if isinstance(config, dict) else __import__("json").loads(config)
    assert stored["request"]["url"] == (
        "https://api.github.com/repos/knotking/mem-dog/issues"
    )


async def test_creating_a_blocked_connector_is_refused_with_the_reason(
    client, tenant
):
    response = await client.post(
        "/api/v1/crawlers/from-connector",
        headers={"Authorization": f"Bearer {tenant.api_key}"},
        json={"project_id": tenant.project_id, "connector": "google_drive",
              "scope": {}},
    )
    assert response.status_code == 409
    assert "oauth" in response.json()["detail"].lower()


async def test_a_connection_can_be_attached_as_it_is_created(
    client, tenant, pool, settings
):
    import os

    from memdog import connections
    from memdog.auth import ApiKeyVerifier
    from memdog.crypto import Envelope

    envelope = Envelope(os.urandom(32))
    made = await connections.create(
        pool, await ApiKeyVerifier(pool).verify(tenant.api_key), envelope,
        project_id=tenant.project_id, provider="github", credential="ghp_x",
        auth_style="bearer",
    )
    response = await client.post(
        "/api/v1/crawlers/from-connector",
        headers={"Authorization": f"Bearer {tenant.api_key}"},
        json={"project_id": tenant.project_id, "connector": "github_issues",
              "scope": {"repo": "a/b"}, "connection_id": made["connection_id"]},
    )
    assert response.status_code == 200
    assert await pool.fetchval(
        "SELECT connection_id FROM crawlers WHERE crawler_id = $1",
        response.json()["crawler_id"],
    ) == made["connection_id"]
