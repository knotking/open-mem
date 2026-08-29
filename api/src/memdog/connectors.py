"""The connector catalog: an app, and the config that pulls from it.

The crawler could already reach any REST API with a token. Nobody could find
that out. The console offered three presets shaped like *strategies* -- feed,
website, JSON API -- so pointing it at Jira meant knowing the search endpoint,
that results live at `issues[*]`, that the cursor is `startAt`, and which fields
are the title and the body. A capability nobody can reach is not a capability.

So each entry here is the knowledge somebody would otherwise have to look up,
written once as data: the URL, the pagination shape, the field mapping, the way
that provider wants its credential, and the one or two things only the operator
knows -- their site, their board, their database.

Three things this file is careful about.

**`verified` is honest.** Every entry is written from the provider's documented
API and almost none has been exercised against a live account, because that
needs somebody's credential. `verified: False` says so rather than implying a
test that never happened -- and the dry-run gate every crawler already passes
through is where an entry stops being a guess.

**A blocked connector is listed, not hidden.** Drive, Gmail, SharePoint and
Salesforce are here with `requires: "oauth"`, because "we do not support Google"
and "Google needs a consent flow nobody has built yet" are different sentences
and only one of them is true. Hiding them would make the catalog look complete.

**Scope fields are named, not guessed.** An entry declares what it needs from
the operator. Defaulting a Jira site or a Notion database id would produce a
crawler that authenticates and returns nothing, which is the failure that reads
as a broken product rather than an unfinished form.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Scope:
    """Something only the operator knows."""

    key: str
    label: str
    placeholder: str
    help: str = ""


@dataclass(frozen=True)
class Connector:
    key: str
    label: str
    category: str
    # What the crawler pulls. Named for the reader, not the endpoint.
    pulls: str
    auth_style: str
    auth_name: str | None = None
    auth_help: str = ""
    scopes: tuple[Scope, ...] = ()
    # Rendered with the scope values to produce a CrawlerConfig.
    template: dict[str, Any] = field(default_factory=dict)
    # `None` means it works today. Anything else names what is missing.
    requires: str | None = None
    # Exercised against a live account by somebody. Almost nothing is.
    verified: bool = False
    notes: str = ""


OAUTH = "oauth"

# The note every OAuth-blocked entry carries. One sentence, one place, so the
# reason cannot drift between them.
OAUTH_NOTE = (
    "Needs an OAuth consent flow with token refresh, which is not built. A "
    "connection holds one static secret and these providers issue tokens that "
    "expire within the hour."
)


def _http(url: str, *, items: str, id_path: str, title: str | None = None,
          content: str | None = None, url_path: str | None = None,
          version: str | None = None, pagination: dict | None = None,
          query: dict | None = None, headers: dict | None = None,
          method: str = "GET", body: dict | None = None) -> dict:
    """One templated request. `{placeholders}` are filled from scope values."""
    request: dict[str, Any] = {"method": method, "url": url}
    if query:
        request["query"] = query
    if headers:
        request["headers"] = headers
    if body:
        request["body"] = body
    extract = {"items_path": items, "id_path": id_path}
    for name, value in (("title_path", title), ("content_path", content),
                        ("url_path", url_path), ("version_path", version)):
        if value:
            extract[name] = value
    config: dict[str, Any] = {"strategy": "http", "request": request,
                              "extract": extract}
    if pagination:
        config["pagination"] = pagination
    return config


CATALOG: tuple[Connector, ...] = (
    # ---------------------------------------------------------------- issues
    Connector(
        key="jira", label="Jira", category="Issues and projects",
        pulls="Issues matching a JQL query",
        auth_style="basic",
        auth_help="An Atlassian API token, as `you@example.com:token`.",
        scopes=(
            Scope("site", "Site URL", "https://acme.atlassian.net"),
            Scope("jql", "JQL", "project = ENG ORDER BY updated DESC",
                  "What to pull. Narrow it first — a whole instance is a lot."),
        ),
        template=_http(
            "{site}/rest/api/3/search",
            query={"jql": "{jql}", "maxResults": "50"},
            items="issues[*]", id_path="key",
            title="fields.summary", content="fields.description",
            version="fields.updated",
            pagination={"type": "offset", "page_param": "startAt",
                        "size_param": "maxResults", "page_size": 50},
        ),
    ),
    Connector(
        key="github_issues", label="GitHub", category="Issues and projects",
        pulls="Issues and pull requests in one repository",
        auth_style="bearer",
        auth_help="A fine-grained personal access token with read access.",
        scopes=(Scope("repo", "Repository", "owner/name"),),
        template=_http(
            "https://api.github.com/repos/{repo}/issues",
            query={"state": "all", "per_page": "100"},
            items="@", id_path="number", title="title", content="body",
            url_path="html_url", version="updated_at",
            headers={"Accept": "application/vnd.github+json"},
            pagination={"type": "page", "page_param": "page", "page_size": 100},
        ),
    ),
    Connector(
        key="linear", label="Linear", category="Issues and projects",
        pulls="Issues from a team",
        auth_style="header", auth_name="Authorization",
        auth_help="A Linear API key. Sent as-is, without a `Bearer` prefix.",
        scopes=(Scope("team", "Team key", "ENG"),),
        template=_http(
            "https://api.linear.app/graphql", method="POST",
            body={"query": "query($t:String!){issues(filter:{team:{key:{eq:$t}}},"
                           "first:50){nodes{identifier title description updatedAt url}}}",
                  "variables": {"t": "{team}"}},
            items="data.issues.nodes[*]", id_path="identifier",
            title="title", content="description", url_path="url",
            version="updatedAt",
        ),
        notes="GraphQL. Pagination is not templated here, so this pulls the "
              "most recent page rather than the whole backlog.",
    ),
    Connector(
        key="asana", label="Asana", category="Issues and projects",
        pulls="Tasks in a project",
        auth_style="bearer", auth_help="A personal access token.",
        scopes=(Scope("project", "Project ID", "1201234567890123"),),
        template=_http(
            "https://app.asana.com/api/1.0/projects/{project}/tasks",
            query={"opt_fields": "name,notes,modified_at,permalink_url",
                   "limit": "100"},
            items="data[*]", id_path="gid", title="name", content="notes",
            url_path="permalink_url", version="modified_at",
            pagination={"type": "cursor", "cursor_path": "next_page.offset",
                        "cursor_param": "offset"},
        ),
    ),

    # ------------------------------------------------------------------- CRM
    Connector(
        key="hubspot", label="HubSpot", category="CRM",
        pulls="Contacts, companies or deals",
        auth_style="bearer",
        auth_help="A private app access token — not the deprecated API key.",
        scopes=(Scope("object", "Object", "contacts",
                      "contacts · companies · deals · tickets"),),
        template=_http(
            "https://api.hubapi.com/crm/v3/objects/{object}",
            query={"limit": "100", "properties": "name,subject,content,notes"},
            items="results[*]", id_path="id", version="updatedAt",
            pagination={"type": "cursor", "cursor_path": "paging.next.after",
                        "cursor_param": "after"},
        ),
    ),
    Connector(
        key="pipedrive", label="Pipedrive", category="CRM",
        pulls="Deals with their notes",
        auth_style="query", auth_name="api_token",
        auth_help="A personal API token. Pipedrive takes it as a query "
                  "parameter rather than a header.",
        scopes=(Scope("domain", "Company domain", "acme",
                      "The subdomain in your Pipedrive URL."),),
        template=_http(
            "https://{domain}.pipedrive.com/api/v1/deals",
            query={"limit": "100"},
            items="data[*]", id_path="id", title="title",
            version="update_time",
            pagination={"type": "offset", "page_param": "start",
                        "size_param": "limit", "page_size": 100},
        ),
    ),
    Connector(
        key="attio", label="Attio", category="CRM",
        pulls="Records from an object",
        auth_style="bearer", auth_help="An Attio access token.",
        scopes=(Scope("object", "Object slug", "companies"),),
        template=_http(
            "https://api.attio.com/v2/objects/{object}/records/query",
            method="POST", body={"limit": 100},
            items="data[*]", id_path="id.record_id",
            version="created_at",
        ),
    ),
    Connector(
        key="salesforce", label="Salesforce", category="CRM",
        pulls="Any object, by SOQL", auth_style="bearer",
        requires=OAUTH, notes=OAUTH_NOTE,
    ),
    Connector(
        key="zoho_crm", label="Zoho CRM", category="CRM",
        pulls="Modules and their records", auth_style="bearer",
        requires=OAUTH, notes=OAUTH_NOTE,
    ),

    # -------------------------------------------------------- docs and notes
    Connector(
        key="notion", label="Notion", category="Documents and knowledge",
        pulls="Pages in a database",
        auth_style="bearer",
        auth_help="An internal integration secret. The database must be shared "
                  "with the integration, or it returns nothing.",
        scopes=(Scope("database", "Database ID", "a1b2c3d4e5f6..."),),
        template=_http(
            "https://api.notion.com/v1/databases/{database}/query",
            method="POST", body={"page_size": 100},
            headers={"Notion-Version": "2022-06-28"},
            items="results[*]", id_path="id", url_path="url",
            version="last_edited_time",
            pagination={"type": "cursor", "cursor_path": "next_cursor",
                        "cursor_param": "start_cursor"},
        ),
        notes="Returns page properties, not page bodies. Blocks are a second "
              "request per page and are not templated here.",
    ),
    Connector(
        key="confluence", label="Confluence", category="Documents and knowledge",
        pulls="Pages in a space",
        auth_style="basic",
        auth_help="An Atlassian API token, as `you@example.com:token`.",
        scopes=(
            Scope("site", "Site URL", "https://acme.atlassian.net/wiki"),
            Scope("space", "Space key", "ENG"),
        ),
        template=_http(
            "{site}/rest/api/content",
            query={"spaceKey": "{space}", "expand": "body.storage,version",
                   "limit": "50"},
            items="results[*]", id_path="id", title="title",
            content="body.storage.value", version="version.when",
            pagination={"type": "offset", "page_param": "start",
                        "size_param": "limit", "page_size": 50},
        ),
    ),

    # ---------------------------------------------------------------- support
    Connector(
        key="zendesk", label="Zendesk", category="Support",
        pulls="Tickets",
        auth_style="basic",
        auth_help="`you@example.com/token:your_api_token`.",
        scopes=(Scope("subdomain", "Subdomain", "acme"),),
        template=_http(
            "https://{subdomain}.zendesk.com/api/v2/tickets.json",
            query={"page[size]": "100"},
            items="tickets[*]", id_path="id", title="subject",
            content="description", version="updated_at",
            pagination={"type": "cursor", "cursor_path": "meta.after_cursor",
                        "cursor_param": "page[after]"},
        ),
    ),
    Connector(
        key="intercom", label="Intercom", category="Support",
        pulls="Conversations",
        auth_style="bearer", auth_help="An access token from your app.",
        scopes=(),
        template=_http(
            "https://api.intercom.io/conversations",
            query={"per_page": "50"},
            headers={"Intercom-Version": "2.11"},
            items="conversations[*]", id_path="id", version="updated_at",
            pagination={"type": "cursor",
                        "cursor_path": "pages.next.starting_after",
                        "cursor_param": "starting_after"},
        ),
    ),
    Connector(
        key="freshdesk", label="Freshdesk", category="Support",
        pulls="Tickets",
        auth_style="basic", auth_help="`your_api_key:X` — the password is ignored.",
        scopes=(Scope("domain", "Domain", "acme",
                      "The subdomain in your Freshdesk URL."),),
        template=_http(
            "https://{domain}.freshdesk.com/api/v2/tickets",
            query={"per_page": "100"},
            items="@", id_path="id", title="subject", content="description_text",
            version="updated_at",
            pagination={"type": "page", "page_param": "page", "page_size": 100},
        ),
    ),

    # --------------------------------------------------------------- commerce
    Connector(
        key="shopify", label="Shopify", category="Commerce",
        pulls="Orders",
        auth_style="header", auth_name="X-Shopify-Access-Token",
        auth_help="An admin API access token from a custom app.",
        scopes=(Scope("shop", "Shop domain", "acme.myshopify.com"),),
        template=_http(
            "https://{shop}/admin/api/2024-10/orders.json",
            query={"status": "any", "limit": "100"},
            items="orders[*]", id_path="id", title="name", version="updated_at",
            pagination={"type": "link_header"},
        ),
    ),
    Connector(
        key="stripe", label="Stripe", category="Commerce",
        pulls="Charges, invoices or customers",
        auth_style="bearer", auth_help="A restricted key with read access.",
        scopes=(Scope("object", "Object", "invoices",
                      "invoices · charges · customers · subscriptions"),),
        template=_http(
            "https://api.stripe.com/v1/{object}",
            query={"limit": "100"},
            items="data[*]", id_path="id", version="created",
            pagination={"type": "cursor", "cursor_path": "data[-1].id",
                        "cursor_param": "starting_after",
                        "stop_when": "has_more == `false`"},
        ),
    ),

    # ------------------------------------------------------------------ people
    Connector(
        key="greenhouse", label="Greenhouse", category="People",
        pulls="Candidates and applications",
        auth_style="basic", auth_help="`your_harvest_key:` — no password.",
        scopes=(),
        template=_http(
            "https://harvest.greenhouse.io/v1/candidates",
            query={"per_page": "100"},
            items="@", id_path="id", version="updated_at",
            pagination={"type": "page", "page_param": "page", "page_size": 100},
        ),
    ),
    Connector(
        key="bamboohr", label="BambooHR", category="People",
        pulls="Employee directory",
        auth_style="basic", auth_help="`your_api_key:x`.",
        scopes=(Scope("subdomain", "Subdomain", "acme"),),
        template=_http(
            "https://api.bamboohr.com/api/gateway.php/{subdomain}/v1/employees/directory",
            headers={"Accept": "application/json"},
            items="employees[*]", id_path="id", title="displayName",
        ),
    ),

    # ------------------------------------------------------------ blocked: oauth
    Connector(
        key="google_drive", label="Google Drive", category="Google",
        pulls="Files in a folder", auth_style="bearer",
        requires=OAUTH,
        notes=OAUTH_NOTE + " Drive also needs a hierarchy walk and a fetcher "
              "that exports Google-native formats — a file listing returns "
              "names, not documents.",
    ),
    Connector(
        key="gmail", label="Gmail", category="Google",
        pulls="Messages matching a query", auth_style="bearer",
        requires=OAUTH, notes=OAUTH_NOTE,
    ),
    Connector(
        key="google_calendar", label="Google Calendar", category="Google",
        pulls="Events", auth_style="bearer",
        requires=OAUTH, notes=OAUTH_NOTE,
    ),
    Connector(
        key="sharepoint", label="SharePoint", category="Microsoft",
        pulls="Documents in a site", auth_style="bearer",
        requires=OAUTH, notes=OAUTH_NOTE,
    ),
    Connector(
        key="onedrive", label="OneDrive", category="Microsoft",
        pulls="Files in a drive", auth_style="bearer",
        requires=OAUTH, notes=OAUTH_NOTE,
    ),
    Connector(
        key="outlook", label="Outlook", category="Microsoft",
        pulls="Mail and calendar", auth_style="bearer",
        requires=OAUTH, notes=OAUTH_NOTE,
    ),
    Connector(
        key="teams", label="Microsoft Teams", category="Microsoft",
        pulls="Channel messages", auth_style="bearer",
        requires=OAUTH,
        notes=OAUTH_NOTE + " Teams also arrives as a webhook today — see "
              "Inbound, which does not need this.",
    ),
)

BY_KEY = {c.key: c for c in CATALOG}


class ConnectorError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def catalog() -> list[dict]:
    """The catalog, grouped-ready and honest about what each entry is."""
    return [
        {
            "key": c.key, "label": c.label, "category": c.category,
            "pulls": c.pulls, "auth_style": c.auth_style,
            "auth_name": c.auth_name, "auth_help": c.auth_help,
            "available": c.requires is None,
            "requires": c.requires, "verified": c.verified, "notes": c.notes,
            "scopes": [
                {"key": s.key, "label": s.label, "placeholder": s.placeholder,
                 "help": s.help}
                for s in c.scopes
            ],
        }
        for c in CATALOG
    ]


def _render(value, scope: dict[str, str]):
    """Substitute `{placeholders}` anywhere in the template.

    Deliberately not a format-string call over the whole structure: a value that
    happens to contain a brace -- a JQL fragment, a GraphQL query -- would raise
    or, worse, interpolate something nobody meant.
    """
    if isinstance(value, str):
        for key, given in scope.items():
            value = value.replace("{" + key + "}", given)
        return value
    if isinstance(value, dict):
        return {k: _render(v, scope) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, scope) for v in value]
    return value


def build(key: str, scope: dict[str, str], *, name: str | None = None,
          enrich: bool = False) -> dict:
    """A crawler config for this connector, with the operator's scope filled in.

    Refuses on a missing scope value rather than rendering `{database}` into a
    URL. That request would authenticate, return a 404, and read as a broken
    integration rather than an unfinished form.
    """
    connector = BY_KEY.get(key)
    if connector is None:
        raise ConnectorError(f"no such connector: {key}", status=404)
    if connector.requires:
        raise ConnectorError(
            f"{connector.label} needs {connector.requires}, which is not built. "
            f"{connector.notes}",
            status=409,
        )

    missing = [s.key for s in connector.scopes if not (scope.get(s.key) or "").strip()]
    if missing:
        raise ConnectorError(
            f"{connector.label} needs: {', '.join(missing)}"
        )

    config = _render(dict(connector.template), {k: v.strip() for k, v in scope.items()})
    config["name"] = name or connector.label
    config["enrich"] = enrich
    # Everything a connector pulls is a record from one source, so it belongs in
    # one memory named for that source rather than scattered across the project.
    config["memory_key"] = f"{connector.key}"
    return config
