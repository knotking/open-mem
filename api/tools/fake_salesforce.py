"""A Salesforce-shaped API, so a connector can be verified without an org.

Thirty-seven connector templates ship and **none has ever been verified**,
because verifying one needs a credential to a real tenant and the repository's
own note is blunt about what that means: *"adding entries is cheap and proves
nothing"*. This closes the gap for one connector by removing the tenant rather
than the verification.

It is deliberately not a mock. It speaks the parts of the Salesforce REST API
the template actually depends on, and it is those parts that break:

- **`client_credentials` at a token endpoint**, returning a bearer and an
  `instance_url`, because the template's auth style is the one Salesforce
  requires a connected app to opt into.
- **`/services/data/v61.0/query?q=<SOQL>`**, returning `{totalSize, done,
  nextRecordsUrl, records[]}` — the exact envelope `records[*]`,
  `done == \\`true\\`` and `nextRecordsUrl` are written against.
- **Cursor paging that is a *path*, not a token.** `nextRecordsUrl` comes back
  as `/services/data/v61.0/query/01g...-2000`, and a crawler that treats it as
  an opaque query parameter fetches page one forever. That is the single most
  likely way this template is wrong, and it cannot be found by reading it.
- **`WHERE LastModifiedDate > …`**, honoured for real, so an incremental clause
  can be shown to shrink the second run rather than asserted to.

Run it:

    python -m tools.fake_salesforce            # 127.0.0.1:8787

Then point a crawler at `http://127.0.0.1:8787` as the instance URL. The data
is a small, coherent CRM — accounts with opportunities that reference them, and
notes that mention the opportunity by name — so that what a crawl produces is
worth asking questions of rather than being lorem with an id.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

PAGE_SIZE = 2
API = "/services/data/v61.0"


def _iso(days_ago: float) -> str:
    """Salesforce's own format: milliseconds and a `+0000` offset, no colon.

    Worth copying exactly. A consumer that parses `+00:00` and not `+0000` fails
    on the real thing and passes against a simulator that formats it the easy
    way, which is the failure a simulator is supposed to prevent.
    """
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return when.strftime("%Y-%m-%dT%H:%M:%S.") + f"{when.microsecond // 1000:03d}+0000"


# A small CRM that hangs together: the opportunities name their account, and the
# notes name the opportunity. A crawl of this is worth asking a question of.
ACCOUNTS = [
    {"Id": "001AA000001", "Name": "Northwind Traders", "Industry": "Logistics",
     "AnnualRevenue": 48_000_000, "LastModifiedDate": _iso(40)},
    {"Id": "001AA000002", "Name": "Contoso Health", "Industry": "Healthcare",
     "AnnualRevenue": 210_000_000, "LastModifiedDate": _iso(18)},
    {"Id": "001AA000003", "Name": "Fabrikam Rail", "Industry": "Transport",
     "AnnualRevenue": 96_000_000, "LastModifiedDate": _iso(2)},
    {"Id": "001AA000004", "Name": "Tailspin Aviation", "Industry": "Aerospace",
     "AnnualRevenue": 12_000_000, "LastModifiedDate": _iso(0.2)},
]

OPPORTUNITIES = [
    {"Id": "006AA000001", "Name": "Northwind — depot rollout", "AccountId": "001AA000001",
     "StageName": "Negotiation", "Amount": 240_000, "CloseDate": "2026-10-31",
     "Description": "Eleven depots. Procurement asked for the security review "
                    "before signature and legal flagged the indemnity cap.",
     "LastModifiedDate": _iso(35)},
    {"Id": "006AA000002", "Name": "Contoso — clinical pilot", "AccountId": "001AA000002",
     "StageName": "Proposal", "Amount": 85_000, "CloseDate": "2026-09-30",
     "Description": "Pilot across two wards. Blocked on a BAA; their privacy "
                    "office will not move until the sub-processor list is final.",
     "LastModifiedDate": _iso(12)},
    {"Id": "006AA000003", "Name": "Fabrikam — signalling data", "AccountId": "001AA000003",
     "StageName": "Closed Won", "Amount": 410_000, "CloseDate": "2026-08-14",
     "Description": "Signed. Renewal lands next August and the champion is "
                    "moving teams in November, which is the risk on renewal.",
     "LastModifiedDate": _iso(1.5)},
    {"Id": "006AA000004", "Name": "Tailspin — maintenance logs", "AccountId": "001AA000004",
     "StageName": "Discovery", "Amount": 60_000, "CloseDate": "2026-12-19",
     "Description": "Early. They want the logs searchable by tail number and "
                    "asked twice whether it can run in their own tenancy.",
     "LastModifiedDate": _iso(0.1)},
]

CONTACTS = [
    {"Id": "003AA000001", "Name": "Dana Okonjo", "AccountId": "001AA000001",
     "Title": "Head of Operations", "Email": "dana@northwind.example",
     "LastModifiedDate": _iso(38)},
    {"Id": "003AA000002", "Name": "Priya Raman", "AccountId": "001AA000002",
     "Title": "CISO", "Email": "priya@contoso.example", "LastModifiedDate": _iso(11)},
    {"Id": "003AA000003", "Name": "Marek Novak", "AccountId": "001AA000003",
     "Title": "Signalling Lead", "Email": "marek@fabrikam.example",
     "LastModifiedDate": _iso(0.5)},
]

TABLES = {"Account": ACCOUNTS, "Opportunity": OPPORTUNITIES, "Contact": CONTACTS}

_FROM = re.compile(r"\bFROM\s+(\w+)", re.I)
_AFTER = re.compile(r"LastModifiedDate\s*>\s*([0-9T:.+\-Z]+)", re.I)


# Salesforce's continuation URL carries an opaque **query locator**, and the
# query itself stays on the server -- the client cannot reconstruct it. Modelled
# here rather than simplified away, because the first version of this file
# simplified it away: the continuation had no query, the handler fell back to a
# default, and page two of an Opportunity crawl came back full of Accounts. A
# crawler would have stored those under the wrong scope and nothing downstream
# would have noticed.
_LOCATORS: dict[str, str] = {}


def _locator(soql: str) -> str:
    key = "01g" + hashlib.sha1(soql.encode()).hexdigest()[:12]
    _LOCATORS[key] = soql
    return key


def run_query(soql: str, offset: int = 0) -> dict:
    """The parts of SOQL the template uses, and nothing else.

    A simulator that implemented more of the language would be a worse test: it
    would stop failing when the template asks for something Salesforce would
    reject, and the point is to fail the way the real thing does.
    """
    table = _FROM.search(soql)
    if table is None or table.group(1) not in TABLES:
        return {"error": "MALFORMED_QUERY", "message": f"unknown object in {soql!r}"}
    rows = list(TABLES[table.group(1)])

    after = _AFTER.search(soql)
    if after:
        # Lexicographic on ISO-8601 is a real date comparison, which is why the
        # format matters more than it looks.
        cutoff = after.group(1).strip().strip("'")
        rows = [r for r in rows if r["LastModifiedDate"] > cutoff]
    rows.sort(key=lambda r: r["LastModifiedDate"])

    page = rows[offset:offset + PAGE_SIZE]
    done = offset + PAGE_SIZE >= len(rows)
    body = {"totalSize": len(rows), "done": done, "records": [
        {"attributes": {"type": table.group(1), "url": f"{API}/sobjects/"
                        f"{table.group(1)}/{r['Id']}"}, **r} for r in page]}
    if not done:
        # A path, not a token. A crawler that treats this as an opaque cursor
        # parameter re-fetches page one forever, and nothing about the template
        # reveals that -- it is the whole reason this simulator exists.
        body["nextRecordsUrl"] = f"{API}/query/{_locator(soql)}-{offset + PAGE_SIZE}"
    return body


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's contract
        if urlparse(self.path).path != "/services/oauth2/token":
            self._send(404, {"error": "not_found"})
            return
        length = int(self.headers.get("content-length") or 0)
        form = parse_qs(self.rfile.read(length).decode())
        if form.get("grant_type", [""])[0] != "client_credentials":
            # Salesforce's own error, because a connector that mishandles it
            # should fail here rather than against somebody's tenant.
            self._send(400, {"error": "unsupported_grant_type",
                             "error_description": "grant type not supported"})
            return
        self._send(200, {
            "access_token": "00D000000000000!fake.token.for.local.verification",
            "instance_url": f"http://{self.headers.get('host')}",
            "token_type": "Bearer", "issued_at": "1787000000000",
        })

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if not self.headers.get("authorization", "").startswith("Bearer "):
            # 401 with Salesforce's shape: a list, not an object.
            self._send(401, [{"message": "Session expired or invalid",
                              "errorCode": "INVALID_SESSION_ID"}])
            return

        if url.path == f"{API}/query":
            soql = parse_qs(url.query).get("q", [""])[0]
            self._send(200, run_query(soql))
            return

        # The continuation. Both halves come from the path Salesforce generated:
        # the locator names the query, the suffix is how far in we are. A client
        # has to follow the URL rather than reconstruct it.
        page = re.fullmatch(rf"{re.escape(API)}/query/(01g[0-9a-f]+)-(\d+)", url.path)
        if page:
            soql = _LOCATORS.get(page.group(1))
            if soql is None:
                self._send(400, [{"message": "invalid query locator",
                                  "errorCode": "INVALID_QUERY_LOCATOR"}])
                return
            self._send(200, run_query(soql, offset=int(page.group(2))))
            return

        self._send(404, [{"message": f"no route for {url.path}",
                          "errorCode": "NOT_FOUND"}])

    def log_message(self, *args) -> None:  # noqa: D102 -- quiet by default
        pass


def serve(host: str = "127.0.0.1", port: int = 8787) -> HTTPServer:
    server = HTTPServer((host, port), Handler)
    return server


if __name__ == "__main__":
    httpd = serve()
    host, port = httpd.server_address
    print(f"fake Salesforce on http://{host}:{port}")
    print(f"  token   POST http://{host}:{port}/services/oauth2/token")
    print(f"  query   GET  http://{host}:{port}{API}/query?q=SELECT+Id,Name,"
          "LastModifiedDate+FROM+Account")
    print(f"  objects {', '.join(TABLES)} — {sum(len(v) for v in TABLES.values())} records")
    httpd.serve_forever()
