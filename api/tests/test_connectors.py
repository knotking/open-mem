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

    from memdog.crawlers import validate_config

    for connector in available:
        config = connectors.build(connector.key, _scope_for(connector))
        # The validators the ordinary create path uses. Nothing here is a
        # shortcut around them.
        parsed = CrawlerConfig.model_validate(config)
        if parsed.strategy == "tree":
            # Runs the create path's second validator too. It is skipped for
            # `http` only because the synthetic scope values here are not URLs.
            validate_config(parsed)
            assert parsed.tree is not None
            continue
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
    """Only what genuinely cannot authenticate without a person stays blocked.

    Google, Microsoft and Salesforce were all listed as needing OAuth and none
    of them does: a service-account assertion and a client-credentials grant
    have no browser and no consent screen in them. Zoho is the real case — it
    issues a refresh token only through a one-time interactive authorization.
    """
    blocked = [c for c in CATALOG if c.requires]
    assert {c.key for c in blocked} == {"zoho_crm"}
    for connector in blocked:
        assert connector.notes, f"{connector.key} is blocked and says nothing"
        with pytest.raises(ConnectorError) as exc:
            connectors.build(connector.key, {})
        assert exc.value.status == 409
        assert connector.requires in str(exc.value)


def test_the_catalog_lists_blocked_entries_rather_than_hiding_them():
    """Hiding one would make the catalog look complete."""
    listed = connectors.catalog()
    entry = next(c for c in listed if c["key"] == "zoho_crm")
    assert entry["available"] is False
    assert entry["requires"] == "oauth"
    assert entry["notes"], "blocked and says nothing about why"


def test_google_and_microsoft_authenticate_without_a_person():
    """The correction this file records. Both were listed as needing an OAuth
    consent flow; both have a grant with no human step in it."""
    for key in ("google_drive", "gmail", "google_calendar"):
        assert connectors.BY_KEY[key].auth_style == "google_service_account"
        assert connectors.BY_KEY[key].requires is None
    for key in ("sharepoint", "onedrive", "outlook", "teams", "salesforce"):
        assert connectors.BY_KEY[key].auth_style == "client_credentials"
        assert connectors.BY_KEY[key].requires is None


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


def test_every_placeholder_in_a_template_is_a_scope_the_form_asks_for():
    """Both directions, because both failures are silent.

    A `{tenant}` nobody declares survives rendering and goes out in a URL
    literally. A scope nobody uses is a field the form demands and then throws
    away. Neither shows up as an error -- the first 404s, the second wastes
    somebody's time -- so they are asserted rather than noticed.

    The placeholder pattern is deliberately narrow: a GraphQL body is full of
    braces, and only `{lower_snake}` is a substitution here.
    """
    import json
    import re

    placeholder = re.compile(r"\{([a-z][a-z0-9_]*)\}")
    for connector in CATALOG:
        used = set(placeholder.findall(json.dumps(connector.template)))
        declared = {s.key for s in connector.scopes}
        assert not used - declared, (
            f"{connector.key} substitutes {sorted(used - declared)}, which the "
            "form never asks for"
        )
        if connector.requires is None:
            assert not declared - used, (
                f"{connector.key} asks for {sorted(declared - used)} and does "
                "nothing with it"
            )


def test_an_id_the_operator_names_is_asked_for_rather_than_guessed():
    """Dataverse names a primary key after its table and a Workday report names
    its columns after their labels. There is no id to default to, and defaulting
    one would produce a crawler that pulls rows and hashes every one of them
    into a fresh record on the next run."""
    for key in ("dynamics365", "workday_report"):
        connector = connectors.BY_KEY[key]
        assert connector.template["extract"]["id_path"] == "{id_field}"
        assert "id_field" in {s.key for s in connector.scopes}
        rendered = connectors.build(key, _scope_for(connector))
        assert rendered["extract"]["id_path"] == "value-for-id_field"


def test_workday_offers_both_ways_in_and_says_which_one_is_conditional():
    """The report is the one that always works -- an ISU with basic auth on
    RaaS is how bulk data leaves Workday. The REST API is nicer and depends on
    a grant the tenant may not permit, so it says so rather than failing at the
    token request with no explanation."""
    report = connectors.BY_KEY["workday_report"]
    rest = connectors.BY_KEY["workday_workers"]
    assert report.auth_style == "basic"
    assert rest.auth_style == "client_credentials"
    assert report.requires is None and rest.requires is None
    assert "jwt bearer" in rest.notes.lower(), (
        "the conditional grant is the whole reason this entry needs a note"
    )
    for connector in (report, rest):
        CrawlerConfig.model_validate(
            connectors.build(connector.key, _scope_for(connector)))


def test_the_crm_category_covers_what_someone_would_actually_name():
    """A catalog missing Dynamics is a catalog somebody bounces off. This is a
    coverage assertion, not a behaviour one: it fails when an entry is dropped
    without the decision being made on purpose."""
    crm = {c.key for c in CATALOG if c.category == "CRM"}
    assert {"salesforce", "hubspot", "dynamics365", "pipedrive", "zoho_crm",
            "close", "copper", "freshsales", "zendesk_sell", "capsule",
            "attio", "affinity"} <= crm


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
        json={"project_id": tenant.project_id, "connector": "zoho_crm",
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


def test_a_tree_connector_walks_rather_than_lists():
    """The pair exists because they differ in kind: one stores what a folder
    contains, the other stores what the documents say."""
    listing = CrawlerConfig.model_validate(
        connectors.build("google_drive", {"folder": "1AbC"}))
    walk = CrawlerConfig.model_validate(
        connectors.build("google_drive_tree", {"folder": "1AbC"}))
    assert listing.strategy == "http" and walk.strategy == "tree"
    assert walk.tree is not None and walk.tree.api == "google_drive"
    assert walk.tree.root == "1AbC"


def test_the_graph_tree_roots_are_paths_graph_actually_serves():
    """`sites/<id>/drive` and `users/<upn>/drive` are the two Graph accepts;
    a root that is not one of them 404s on the first request."""
    for key, scope, expected in (
        ("sharepoint_tree", {"site": "acme,1,2"}, "sites/acme,1,2/drive"),
        ("onedrive_tree", {"user": "a@acme.com"}, "users/a@acme.com/drive"),
    ):
        config = CrawlerConfig.model_validate(connectors.build(key, scope))
        assert config.tree is not None
        assert config.tree.root == expected
        assert config.tree.api == "microsoft_graph"
