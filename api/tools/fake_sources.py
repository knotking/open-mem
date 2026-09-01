"""Four more source shapes, so the crawler strategies can be run rather than read.

`fake_salesforce.py` paid for itself immediately: running the shipped template
against it found that paging put `nextRecordsUrl` into a query parameter, which
re-requested page one until the page limit and reported a plausible item count.
That defect was invisible in the config and would have been invisible in
production -- a crawl that returns forty items looks like a crawl that worked.

The same argument applies to every *other* pagination shape, and there are four
more of them. Each source below exists because a real connector depends on that
mechanism and nothing has ever exercised it:

| Path        | Shape                              | Who pages this way        |
|-------------|------------------------------------|---------------------------|
| `/gh`       | `Link: <...>; rel="next"` header   | GitHub, and most of REST  |
| `/jira`     | `startAt` / `maxResults` / `total` | Jira, Confluence, Zendesk |
| `/graph`    | `@odata.nextLink`, **absolute**    | Microsoft Graph, Dynamics |
| `/pages`    | `page=N` with `has_more`           | Notion, Linear, Front     |
| `/feed.xml` | RSS with `pubDate`                 | every feed connector      |

The absolute-vs-path distinction between `/graph` and Salesforce is the point of
having both: `next_url` resolves relative to the current URL, so a path and an
absolute URL take different branches, and only one of them was ever written down.

**Every source honours a real incremental filter.** That is the other half of
why this file exists. 36 of 37 catalog entries have no incremental clause, so
every scheduled run re-reads the whole source; a simulator that ignored the
filter would let a template claim incremental and be wrong in the direction
nobody notices until the bill arrives.

Run it:

    python -m tools.fake_sources          # 127.0.0.1:8788
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

PAGE = 2

# How many requests each path has served since the last `reset()`.
#
# The crawler's own Budget counts *items*, not requests, so nothing in-process
# can tell "five records in three requests" from "five records in twenty".
# That distinction is the entire Salesforce defect -- a crawl that re-requested
# page one until the page limit reported a plausible count and no error -- so
# the simulator counts, and tests assert on it.
REQUESTS: dict[str, int] = {}


def reset() -> None:
    REQUESTS.clear()

# Fixed dates rather than now-relative. A simulator whose data moves with the
# clock makes a failing incremental test ambiguous -- you cannot tell a broken
# watermark from a record that aged past the window while the test ran.
DATES = ["2026-06-01T09:00:00Z", "2026-07-14T11:30:00Z",
         "2026-08-02T16:45:00Z", "2026-08-29T08:15:00Z", "2026-08-31T22:05:00Z"]

TITLES = [
    "Depot rollout blocked on the security review",
    "Clinical pilot: BAA and the sub-processor list",
    "Signalling data renewal — champion moves teams in November",
    "Maintenance logs must be searchable by tail number",
    "Procurement asked for the indemnity cap in writing",
]

BODIES = [
    "Eleven depots. Legal flagged the indemnity cap and procurement will not "
    "move until the security review is signed off.",
    "Two wards. Their privacy office is holding until the sub-processor list "
    "is final; the BAA is drafted but unsigned.",
    "Signed in August. Renewal lands next August and the champion is moving "
    "teams in November, which is the actual risk.",
    "Early-stage. They asked twice whether it can run inside their own "
    "tenancy, which reads as a procurement constraint rather than curiosity.",
    "Raised on the call and again in writing. Finance wants the cap at twelve "
    "months of fees; we have offered twenty-four.",
]

RECORDS = [
    {"n": i + 1, "updated": DATES[i], "title": TITLES[i], "body": BODIES[i]}
    for i in range(5)
]


def _after(watermark: str | None) -> list[dict]:
    """The incremental filter, applied for real.

    ISO-8601 in UTC compares correctly as a string, which is the only reason
    this is one line -- and the reason the formats below are all `Z`-suffixed
    rather than each source inventing its own offset spelling.
    """
    rows = RECORDS if not watermark else [r for r in RECORDS if r["updated"] > watermark]
    return sorted(rows, key=lambda r: r["updated"])


def _slice(rows: list[dict], offset: int) -> tuple[list[dict], bool]:
    page = rows[offset:offset + PAGE]
    return page, offset + PAGE >= len(rows)


# ----------------------------------------------------------------- shapes

def github(query: dict, base: str) -> tuple[dict | list, dict]:
    """`Link: <url>; rel="next"` -- a whole URL, in a header, comma-separated
    with the other relations so a naive `split(',')` finds `rel="last"` first."""
    rows = _after(query.get("since", [None])[0])
    offset = int(query.get("page", ["1"])[0]) - 1
    page, done = _slice(rows, offset * PAGE)
    body = [{"number": r["n"], "title": r["title"], "body": r["body"],
             "updated_at": r["updated"],
             "html_url": f"https://example.invalid/issues/{r['n']}"} for r in page]
    headers = {}
    if not done:
        nxt = f"{base}/gh/repos/acme/widgets/issues?page={offset + 2}"
        last = f"{base}/gh/repos/acme/widgets/issues?page={(len(rows) + PAGE - 1) // PAGE}"
        # Deliberately not last-first: a parser that takes the first `<...>` it
        # sees rather than the one tagged `rel="next"` walks to the end and
        # stops, silently skipping the middle of the source.
        headers["Link"] = f'<{last}>; rel="last", <{nxt}>; rel="next"'
    return body, headers


_JQL_AFTER = re.compile(r'updated\s*>\s*"([^"]+)"')


def jira(query: dict, base: str) -> tuple[dict, dict]:
    """`startAt` / `maxResults` / `total` -- the offset family.

    The trap here is `total`: a crawler that pages until an empty response makes
    one wasted request per run, and a crawler that trusts `total` without
    re-checking drops records when the source grows mid-crawl.
    """
    jql = query.get("jql", [""])[0]
    found = _JQL_AFTER.search(jql)
    rows = _after(found.group(1) if found else None)
    start = int(query.get("startAt", ["0"])[0])
    page, _done = _slice(rows, start)
    return {
        "startAt": start, "maxResults": PAGE, "total": len(rows),
        "issues": [{"key": f"ACME-{r['n']}",
                    "fields": {"summary": r["title"], "description": r["body"],
                               "updated": r["updated"]}} for r in page],
    }, {}


def graph(query: dict, base: str) -> tuple[dict, dict]:
    """`@odata.nextLink` -- the whole next URL, **absolute**.

    Salesforce returns its continuation as a *path*. Graph returns an absolute
    URL. Both are `next_url`, and they take different branches through the
    resolver, so testing one proves nothing about the other.
    """
    rows = _after(query.get("since", [None])[0])
    skip = int(query.get("$skip", ["0"])[0])
    page, done = _slice(rows, skip)
    body: dict = {"value": [
        {"id": f"graph-{r['n']}", "subject": r["title"], "bodyPreview": r["body"],
         "lastModifiedDateTime": r["updated"]} for r in page]}
    if not done:
        since = query.get("since", [None])[0]
        tail = f"&since={since}" if since else ""
        body["@odata.nextLink"] = f"{base}/graph/v1.0/messages?$skip={skip + PAGE}{tail}"
    return body, {}


def pages(query: dict, base: str) -> tuple[dict, dict]:
    """`page=N` with `has_more` -- the shape that needs `stop_when`.

    There is no cursor and no total, so the only thing that ends the crawl is
    the flag. A config that omits `stop_when` here runs to `max_pages` every
    time and never notices.
    """
    rows = _after(query.get("updated_since", [None])[0])
    number = max(1, int(query.get("page", ["1"])[0]))
    page, done = _slice(rows, (number - 1) * PAGE)
    return {"page": number, "has_more": not done,
            "results": [{"uid": f"pg-{r['n']}", "name": r["title"],
                         "text": r["body"], "changed": r["updated"]}
                        for r in page]}, {}


def feed(query: dict, base: str) -> tuple[str, dict]:
    """RSS, with one entry deliberately malformed.

    The feed parser is regex-based on purpose -- feeds in the wild are often not
    well-formed, and a strict XML parser turns a slightly broken feed into a
    crawler that reports zero items forever. So this feed is slightly broken, or
    it would not be testing that decision.
    """
    items = []
    for r in _after(query.get("since", [None])[0]):
        # A bare `&` in the title. Invalid XML, and about as common as any
        # single defect in feeds in the wild -- a strict parser rejects the
        # whole document over it, taking the four well-formed entries with it.
        title = r["title"] + " & renewals" if r["n"] == 3 else r["title"]
        items.append(
            f"<item><guid>feed-{r['n']}</guid><title>{title}</title>"
            f"<description><![CDATA[{r['body']}]]></description>"
            f"<pubDate>{r['updated']}</pubDate>"
            f"<link>https://example.invalid/p/{r['n']}</link></item>")
    return ('<?xml version="1.0"?><rss version="2.0"><channel>'
            "<title>Acme changes</title>" + "".join(items)
            + "</channel></rss>"), {"Content-Type": "application/rss+xml"}


def graphql(query: dict, base: str, body: dict | None = None) -> tuple[dict, dict]:
    """A POST with a JSON body, and a filter that lives inside it.

    Here to prove a request shape rather than a pagination shape. `HttpRequest`
    declared `method` and `body` for months and `discover_http` never read
    either, so four catalog entries -- Linear, Notion, Attio, Copper -- were
    issued as a bodyless GET. For Linear, which is GraphQL, that is not a
    degraded request: there is no query, so there is no meaning.

    The incremental filter is nested two levels down, because that is where
    these APIs put it and because a renderer that only walked the top level
    would leave `{{ watermark }}` in the payload as a literal.
    """
    if not body:
        return {"errors": [{"message": "a GraphQL request needs a query"}]}, {}
    after = (((body.get("variables") or {}).get("filter") or {})
             .get("updatedAt") or {}).get("gt")
    if after and "{{" in str(after):
        # The placeholder arrived unrendered. Answering it as though it were a
        # date would make a broken template look like an empty result set.
        return {"errors": [{"message": f"unrendered template in filter: {after}"}]}, {}
    rows = _after(after)
    return {"data": {"issues": {"nodes": [
        {"id": f"lin-{r['n']}", "title": r["title"], "description": r["body"],
         "updatedAt": r["updated"]} for r in rows]}}}, {}


ROUTES = {
    "/gh/repos/acme/widgets/issues": github,
    "/jira/rest/api/3/search": jira,
    "/graph/v1.0/messages": graph,
    "/pages/items": pages,
    "/feed.xml": feed,
}

POST_ROUTES = {"/graphql": graphql}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's contract
        url = urlparse(self.path)
        route = ROUTES.get(url.path)
        if route is None:
            self._send(404, {"error": f"no source at {url.path}"}, {})
            return

        # Every source except the feed requires a credential -- a template that
        # forgot one should fail here rather than against somebody's account.
        # The feed is public because feeds are, and because `discover_feed`
        # sends no Authorization header at all: requiring one would be testing
        # the simulator's opinion rather than the crawler's behaviour.
        if url.path != "/feed.xml" and not self.headers.get("authorization"):
            self._send(401, {"error": "unauthorized"}, {})
            return

        REQUESTS[url.path] = REQUESTS.get(url.path, 0) + 1
        base = f"http://{self.headers.get('host')}"
        body, headers = route(parse_qs(url.query), base)
        self._send(200, body, headers)

    def do_POST(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's contract
        url = urlparse(self.path)
        route = POST_ROUTES.get(url.path)
        if route is None:
            self._send(404, {"error": f"no source at {url.path}"}, {})
            return
        if not self.headers.get("authorization"):
            self._send(401, {"error": "unauthorized"}, {})
            return
        REQUESTS[url.path] = REQUESTS.get(url.path, 0) + 1
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = None
        payload, headers = route(parse_qs(url.query),
                                 f"http://{self.headers.get('host')}", body)
        self._send(200, payload, headers)

    def _send(self, status: int, payload, headers: dict) -> None:
        if isinstance(payload, str):
            raw = payload.encode()
            content_type = headers.pop("Content-Type", "text/plain")
        else:
            raw = json.dumps(payload).encode()
            content_type = "application/json"
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(raw)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args) -> None:  # noqa: D102 -- quiet by default
        pass


def serve(host: str = "127.0.0.1", port: int = 8788) -> HTTPServer:
    return HTTPServer((host, port), Handler)


if __name__ == "__main__":
    httpd = serve()
    host, port = httpd.server_address
    print(f"fake sources on http://{host}:{port} — send any Authorization header")
    for path in ROUTES:
        print(f"  {path}")
    print(f"  {len(RECORDS)} records, updated {DATES[0]} .. {DATES[-1]}")
    httpd.serve_forever()
