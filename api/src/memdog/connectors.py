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
API and **none has been exercised against a live account**, because that needs
somebody's credential. `verified: False` says so rather than implying a test
that never happened -- and the dry-run gate every crawler already passes through
is where an entry stops being a guess.

`exercised_against` is the weaker claim that can actually be earned without a
tenant: the template was run, end-to-end, against something that speaks the
provider's API shape. Salesforce is the first, against
`tools/fake_salesforce.py`, and it was worth doing -- running it found that the
template paged by putting `nextRecordsUrl` in a query parameter, which fetches
page one until the page limit and reports a plausible number of items. Reading
the template could not have found that, and neither could a live tenant without
someone counting the records by hand.

**A blocked connector is listed, not hidden.** One entry still carries
`requires: "oauth"` — Zoho, which issues a refresh token only through a one-time
interactive authorization. Everything else authenticates without a person:
Google through a service-account assertion, Microsoft and Salesforce through
client credentials. "We do not support Google" and "Google needs a consent flow"
were both said here and neither was true.

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
    # A weaker, checkable claim: the template has been *run* end-to-end against
    # the named thing. A simulator is not a tenant -- it cannot tell you the
    # scope names are right or the permission model allows the read -- but it
    # does catch the mechanical failures, and those turn out to be most of them.
    exercised_against: str | None = None
    notes: str = ""


OAUTH = "oauth"

GRAPH_HELP = (
    "An app registration with application permissions. The credential is "
    "`client_id:client_secret`, and auth_config needs the token_url for your "
    "tenant plus scope `https://graph.microsoft.com/.default`. No consent "
    "screen and no per-user step."
)

GOOGLE_HELP = (
    "A service-account key, stored whole as the JSON file. auth_config needs "
    "the scopes it is for. No browser and no consent screen -- the assertion "
    "is signed with the key."
)

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
          method: str = "GET", body: dict | None = None,
          incremental: str | None = None) -> dict:
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
    if incremental:
        config["incremental"] = incremental
    return config


def _tree(api: str, root: str, *, include_mime: list[str] | None = None) -> dict:
    """A folder recursion. The bytes of each file are fetched, not listed."""
    tree: dict[str, Any] = {"api": api, "root": root}
    if include_mime:
        tree["include_mime"] = include_mime
    return {"strategy": "tree", "tree": tree}


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
        pulls="Any object, by SOQL",
        auth_style="client_credentials",
        auth_help="A connected app with the client-credentials flow enabled and "
                  "a run-as user. The credential is `client_id:client_secret`, "
                  "and auth_config needs your instance's token_url.",
        scopes=(
            Scope("instance", "Instance URL", "https://acme.my.salesforce.com"),
            Scope("soql", "SOQL",
                  "SELECT Id, Name, LastModifiedDate FROM Account "
                  "WHERE LastModifiedDate > {{ watermark_or_epoch }} "
                  "ORDER BY LastModifiedDate",
                  "`{{ watermark_or_epoch }}` is where the last run got to, or "
                  "1970 on the first run -- keep it, or every run re-reads the "
                  "whole object. `{{ watermark }}` alone renders empty the "
                  "first time and Salesforce answers MALFORMED_QUERY."),
        ),
        template=_http(
            "{instance}/services/data/v61.0/query",
            query={"q": "{soql}"},
            items="records[*]", id_path="Id", title="Name",
            version="LastModifiedDate", incremental="watermark",
            pagination={"type": "next_url", "cursor_path": "nextRecordsUrl",
                        "stop_when": "done == `true`"},
        ),
        exercised_against="tools/fake_salesforce.py",
        notes="Client credentials must be switched on for the connected app; "
              "it is off by default. Paging follows `nextRecordsUrl`, which "
              "Salesforce returns as a path rather than a token -- treating it "
              "as a cursor parameter re-fetches page one until the page limit, "
              "which is what this template used to do.",
    ),
    Connector(
        key="dynamics365", label="Microsoft Dynamics 365", category="CRM",
        pulls="Records from any Dataverse table",
        auth_style="client_credentials",
        auth_help="An app registration, as `client_id:client_secret`. "
                  "auth_config needs your tenant's token_url and the scope "
                  "`https://<org>.crm.dynamics.com/.default`.",
        scopes=(
            Scope("org", "Organization host", "acme.crm.dynamics.com"),
            Scope("entity", "Entity set", "accounts",
                  "accounts \u00b7 contacts \u00b7 leads \u00b7 opportunities"),
            Scope("id_field", "ID column", "accountid",
                  "Dataverse names the primary key after the singular table -- "
                  "`accountid`, `contactid`, `opportunityid`."),
        ),
        template=_http(
            "https://{org}/api/data/v9.2/{entity}",
            headers={"Accept": "application/json", "OData-Version": "4.0",
                     "OData-MaxVersion": "4.0"},
            items="value[*]", id_path="{id_field}", version="modifiedon",
            pagination={"type": "next_url", "cursor_path": "\"@odata.nextLink\""},
        ),
        notes="The app registration must also exist as an application user "
              "inside Dynamics with a security role. Without that it "
              "authenticates cleanly and then sees nothing, which reads as an "
              "empty CRM rather than a permissions problem. Paging follows "
              "`@odata.nextLink`, the whole next URL rather than a token.",
    ),
    Connector(
        key="close", label="Close", category="CRM",
        pulls="Leads, with their contacts and opportunities inline",
        auth_style="basic",
        auth_help="`your_api_key:` -- the password is empty.",
        scopes=(),
        template=_http(
            "https://api.close.com/api/v1/lead/",
            query={"_limit": "100"},
            items="data[*]", id_path="id", title="display_name",
            version="date_updated",
            pagination={"type": "offset", "page_param": "_skip",
                        "size_param": "_limit", "page_size": 100,
                        "stop_when": "has_more == `false`"},
        ),
    ),
    Connector(
        key="copper", label="Copper", category="CRM",
        pulls="Records from one object, by search",
        auth_style="header", auth_name="X-PW-AccessToken",
        auth_help="An API key from Settings \u2192 Integrations \u2192 API Keys.",
        scopes=(
            Scope("object", "Object", "opportunities",
                  "people \u00b7 companies \u00b7 opportunities \u00b7 projects"),
            Scope("email", "Account email", "you@acme.com",
                  "Copper identifies the key's owner in a second header, so "
                  "the key alone is not enough."),
        ),
        template=_http(
            "https://api.copper.com/developer_api/v1/{object}/search",
            method="POST", body={"page_size": 100},
            headers={"X-PW-Application": "developer_api",
                     "X-PW-UserEmail": "{email}",
                     "Content-Type": "application/json"},
            items="@", id_path="id", title="name", version="date_modified",
        ),
        notes="Copper pages with `page_number` in the request body, and this "
              "pagination templates only the query string, so this pulls the "
              "first page.",
    ),
    Connector(
        key="freshsales", label="Freshsales", category="CRM",
        pulls="Records in a saved view",
        auth_style="header", auth_name="Authorization",
        auth_help="Store the whole header value -- `Token token=your_api_key`. "
                  "Freshsales does not use a `Bearer` prefix.",
        scopes=(
            Scope("domain", "Domain", "acme",
                  "The subdomain in your Freshworks URL."),
            Scope("object", "Object", "contacts",
                  "contacts \u00b7 deals \u00b7 sales_accounts \u00b7 leads"),
            Scope("view", "View ID", "401000123456",
                  "Freshsales lists only through a saved view. "
                  "`GET /crm/sales/api/<object>/filters` returns yours."),
        ),
        template=_http(
            "https://{domain}.myfreshworks.com/crm/sales/api/{object}/view/{view}",
            items="{object}[*]", id_path="id", version="updated_at",
            pagination={"type": "page", "page_param": "page"},
        ),
        notes="No title is mapped: a contact calls it `display_name` and a "
              "deal calls it `name`, and guessing one would silently blank the "
              "other. The record is stored whole either way.",
    ),
    Connector(
        key="zendesk_sell", label="Zendesk Sell", category="CRM",
        pulls="Deals, contacts or leads",
        auth_style="bearer",
        auth_help="An access token from Settings \u2192 Integrations \u2192 OAuth. "
                  "Sell is a separate API from Zendesk Support and does not "
                  "take the Support token.",
        scopes=(Scope("object", "Object", "deals",
                      "deals \u00b7 contacts \u00b7 leads"),),
        template=_http(
            "https://api.getbase.com/v2/{object}",
            query={"per_page": "100"},
            items="items[*].data", id_path="id", title="name",
            version="updated_at",
            pagination={"type": "page", "page_param": "page",
                        "size_param": "per_page", "page_size": 100},
        ),
    ),
    Connector(
        key="capsule", label="Capsule", category="CRM",
        pulls="Parties, opportunities or cases",
        auth_style="bearer",
        auth_help="A personal access token from My Preferences \u2192 API "
                  "Authentication Tokens.",
        scopes=(Scope("object", "Object", "parties",
                      "parties \u00b7 opportunities \u00b7 kases \u00b7 projects"),),
        template=_http(
            "https://api.capsulecrm.com/api/v2/{object}",
            query={"perPage": "100"},
            items="{object}[*]", id_path="id", title="name",
            version="updatedAt",
            pagination={"type": "page", "page_param": "page",
                        "size_param": "perPage", "page_size": 100},
        ),
        notes="A party is a person or an organisation and only the "
              "organisation has a `name`, so a person's title comes out empty.",
    ),
    Connector(
        key="affinity", label="Affinity", category="CRM",
        pulls="Organizations, people or opportunities",
        auth_style="basic",
        auth_help="`:your_api_key` -- the username is empty and the leading "
                  "colon is not a typo.",
        scopes=(Scope("object", "Object", "organizations",
                      "organizations \u00b7 persons \u00b7 opportunities"),),
        template=_http(
            "https://api.affinity.co/{object}",
            query={"page_size": "100"},
            items="{object}[*]", id_path="id", title="name",
            pagination={"type": "cursor", "cursor_path": "next_page_token",
                        "cursor_param": "page_token",
                        "size_param": "page_size", "page_size": 100},
        ),
    ),
    Connector(
        key="zoho_crm", label="Zoho CRM", category="CRM",
        pulls="Modules and their records", auth_style="bearer",
        requires=OAUTH,
        notes="The one that genuinely needs a browser: Zoho issues a refresh "
              "token only through a one-time interactive authorization, and "
              "there is no grant that skips it. Everything else in this "
              "catalog authenticates without a person.",
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

    Connector(
        key="workday_report", label="Workday (custom report)", category="People",
        pulls="Rows of a custom report",
        auth_style="basic",
        auth_help="An integration system user, as `isu@tenant:password`. The "
                  "ISU needs a security group with Get access to the report's "
                  "data sources, and its password set not to expire.",
        scopes=(
            Scope("report_url", "Report URL",
                  "https://acme.workday.com/ccx/service/customreport2/acme/isu/Workers",
                  "From the report's Actions \u2192 Web Service \u2192 View URLs, the "
                  "JSON one, with its query string removed."),
            Scope("id_field", "ID column", "Employee_ID",
                  "A column of the report that identifies a row. Report "
                  "columns are named by their label, so only you know it."),
        ),
        template=_http(
            "{report_url}", query={"format": "json"},
            items="Report_Entry[*]", id_path="{id_field}",
        ),
        notes="RaaS is how bulk data actually leaves Workday, and it is the "
              "one endpoint an ISU reaches with basic auth alone. A report "
              "returns its whole result set in a single document with no "
              "paging, so narrow it inside Workday rather than here -- a "
              "whole-tenant worker report will pass the crawler's per-item "
              "byte limit.",
    ),
    Connector(
        key="workday_workers", label="Workday (workers)", category="People",
        pulls="The worker directory",
        auth_style="client_credentials",
        auth_help="An API client registered in Workday, as "
                  "`client_id:client_secret`. auth_config needs the token_url "
                  "`https://<host>/ccx/oauth2/<tenant>/token`.",
        scopes=(
            Scope("host", "Host", "acme.workday.com"),
            Scope("tenant", "Tenant", "acme"),
        ),
        template=_http(
            "https://{host}/ccx/api/v1/{tenant}/workers",
            query={"limit": "100"},
            items="data[*]", id_path="id", title="descriptor",
            pagination={"type": "offset", "page_param": "offset",
                        "size_param": "limit", "page_size": 100},
        ),
        notes="Cleaner than a report and conditional on your tenant: the "
              "client-credentials grant has to be switched on for the API "
              "client, and some tenants permit only the JWT bearer grant, "
              "which is not one of the six styles here. If the token request "
              "is refused, the custom report is the way in.",
    ),

    # --------------------------------------------- Google, by service account
    Connector(
        key="google_drive", label="Google Drive", category="Google",
        pulls="Files in a folder",
        auth_style="google_service_account", auth_help=GOOGLE_HELP,
        scopes=(
            Scope("folder", "Folder ID", "1AbCdEf...",
                  "From the folder's URL. Share the folder with the service "
                  "account's email or it returns nothing."),
        ),
        template=_http(
            "https://www.googleapis.com/drive/v3/files",
            query={"q": "'{folder}' in parents and trashed = false",
                   "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,webViewLink)",
                   "pageSize": "100"},
            items="files[*]", id_path="id", title="name",
            url_path="webViewLink", version="modifiedTime",
            pagination={"type": "cursor", "cursor_path": "nextPageToken",
                        "cursor_param": "pageToken"},
        ),
        notes="Lists one folder and stores each file's metadata. For "
              "subfolders and the documents themselves, use Google Drive "
              "(folder tree).",
    ),
    Connector(
        key="gmail", label="Gmail", category="Google",
        pulls="Messages matching a search",
        auth_style="google_service_account",
        auth_help=GOOGLE_HELP + " Reading a mailbox also needs domain-wide "
                  "delegation and a `subject` in auth_config — the assertion "
                  "says which user it is acting as.",
        scopes=(
            Scope("user", "Mailbox", "someone@acme.com"),
            Scope("q", "Search", "newer_than:30d", "Gmail search syntax."),
        ),
        template=_http(
            "https://gmail.googleapis.com/gmail/v1/users/{user}/messages",
            query={"q": "{q}", "maxResults": "100"},
            items="messages[*]", id_path="id",
            pagination={"type": "cursor", "cursor_path": "nextPageToken",
                        "cursor_param": "pageToken"},
        ),
        notes="Returns message ids. Bodies are a second request each and are "
              "not templated here.",
    ),
    Connector(
        key="google_calendar", label="Google Calendar", category="Google",
        pulls="Events from a calendar",
        auth_style="google_service_account", auth_help=GOOGLE_HELP,
        scopes=(Scope("calendar", "Calendar ID", "someone@acme.com"),),
        template=_http(
            "https://www.googleapis.com/calendar/v3/calendars/{calendar}/events",
            query={"maxResults": "250", "singleEvents": "true"},
            items="items[*]", id_path="id", title="summary",
            content="description", url_path="htmlLink", version="updated",
            pagination={"type": "cursor", "cursor_path": "nextPageToken",
                        "cursor_param": "pageToken"},
        ),
    ),

    # ------------------------------------------------- the same, walked whole
    #
    # Separate entries rather than a flag on the ones above, because they are
    # different in kind: a listing stores what a folder contains, and a tree
    # stores what the documents say. Someone choosing between them is choosing
    # between a file index and a corpus, and that is worth two names.
    Connector(
        key="google_drive_tree", label="Google Drive (folder tree)",
        category="Google",
        pulls="Every document under a folder, including subfolders",
        auth_style="google_service_account", auth_help=GOOGLE_HELP,
        scopes=(
            Scope("folder", "Folder ID", "1AbCdEf...",
                  "From the folder's URL. Share the folder with the service "
                  "account's email or the walk returns nothing."),
        ),
        template=_tree("google_drive", "{folder}"),
        notes="Docs, Sheets and Slides are exported as text; everything else "
              "is downloaded as-is and parsed. Bounded by the crawler's depth "
              "and item limits — a drive nobody has pruned is large.",
    ),
    Connector(
        key="sharepoint_tree", label="SharePoint (library tree)",
        category="Microsoft",
        pulls="Every document in a site's library, including folders",
        auth_style="client_credentials", auth_help=GRAPH_HELP,
        scopes=(Scope("site", "Site ID", "acme.sharepoint.com,<guid>,<guid>",
                      "From /sites/{hostname}:/sites/{name} in Graph."),),
        template=_tree("microsoft_graph", "sites/{site}/drive"),
    ),
    Connector(
        key="onedrive_tree", label="OneDrive (drive tree)", category="Microsoft",
        pulls="Every file in a user's drive, including folders",
        auth_style="client_credentials", auth_help=GRAPH_HELP,
        scopes=(Scope("user", "User", "someone@acme.com"),),
        template=_tree("microsoft_graph", "users/{user}/drive"),
    ),

    # ------------------------------------------ Microsoft, by app registration
    Connector(
        key="sharepoint", label="SharePoint", category="Microsoft",
        pulls="Documents in a site's library",
        auth_style="client_credentials", auth_help=GRAPH_HELP,
        scopes=(Scope("site", "Site ID", "acme.sharepoint.com,<guid>,<guid>",
                      "From /sites/{hostname}:/sites/{name} in Graph."),),
        template=_http(
            "https://graph.microsoft.com/v1.0/sites/{site}/drive/root/children",
            items="value[*]", id_path="id", title="name",
            url_path="webUrl", version="lastModifiedDateTime",
        ),
        notes="Lists the root of the library only. For the whole library and "
              "the documents themselves, use SharePoint (library tree).",
    ),
    Connector(
        key="onedrive", label="OneDrive", category="Microsoft",
        pulls="Files in a user's drive",
        auth_style="client_credentials", auth_help=GRAPH_HELP,
        scopes=(Scope("user", "User", "someone@acme.com"),),
        template=_http(
            "https://graph.microsoft.com/v1.0/users/{user}/drive/root/children",
            items="value[*]", id_path="id", title="name",
            url_path="webUrl", version="lastModifiedDateTime",
        ),
        notes="Root only. For the whole drive, use OneDrive (drive tree).",
    ),
    Connector(
        key="outlook", label="Outlook mail", category="Microsoft",
        pulls="Messages from a mailbox",
        auth_style="client_credentials", auth_help=GRAPH_HELP,
        scopes=(Scope("user", "Mailbox", "someone@acme.com"),),
        template=_http(
            "https://graph.microsoft.com/v1.0/users/{user}/messages",
            query={"$top": "50",
                   "$select": "subject,bodyPreview,webLink,lastModifiedDateTime"},
            items="value[*]", id_path="id", title="subject",
            content="bodyPreview", url_path="webLink",
            version="lastModifiedDateTime",
        ),
    ),
    Connector(
        key="teams", label="Microsoft Teams", category="Microsoft",
        pulls="Messages in a channel",
        auth_style="client_credentials", auth_help=GRAPH_HELP,
        scopes=(
            Scope("team", "Team ID", "<guid>"),
            Scope("channel", "Channel ID", "19:...@thread.tacv2"),
        ),
        template=_http(
            "https://graph.microsoft.com/v1.0/teams/{team}/channels/{channel}/messages",
            items="value[*]", id_path="id", content="body.content",
            url_path="webUrl", version="lastModifiedDateTime",
        ),
        notes="Teams also arrives as a webhook today — see Inbound, which "
              "needs none of this.",
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
            "requires": c.requires, "verified": c.verified,
            "exercised_against": c.exercised_against, "notes": c.notes,
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
