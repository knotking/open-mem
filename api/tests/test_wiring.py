"""Declared, and connected to nothing.

Six defects found in one week shared a shape: a column, a setting or a function
that existed, was correct, was documented, and was reached by no code path.
`sensitivity` was selected beside `requires` and dropped. `allowed_providers`
was described in the register as enforced and read nowhere. `normalize.project`
knew how to run a schema and was called by nothing. Six metrics were emitted
into an unregistered name. None of them errored; each looked exactly like a
feature that happened to be quiet.

That is not a run of bad luck, it is a property of a codebase whose design ran
ahead of its implementation — so this file checks the wiring rather than the
behaviour. Each guard is cheap, and each one has already caught something.

**The allow-lists are the point as much as the assertions.** Anything listed
below is a thing that exists and is deliberately not connected, with the reason
written down. A guard whose exemptions are undocumented becomes a guard people
add exemptions to.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from open_mem import settings_store

SRC = Path(settings_store.__file__).parent
MODULES = sorted(p for p in SRC.glob("*.py") if p.name != "__init__.py")
SOURCE = {p.name: p.read_text() for p in MODULES}


# Settings with no server-side consumer *by design*: a client reads them from
# `GET /settings/effective` and acts on them itself. Anything else in the
# register that nothing reads is decorative, and a decorative control is how
# people conclude the product is broken.
CLIENT_ONLY_SETTINGS = {
    "default_project": "a preference about how one person works; the console reads it",
    "notifications": "delivery preferences; there is no notifier in the service yet",
}

# Columns that exist and no code names. Each is a real gap; listing it here is
# what turns it from silent into tracked, and the reason is the part that has to
# stay true. Anything added to this list should be something a reader would
# agree is deliberately unbuilt — not something that was easier to exempt.
UNWIRED_COLUMNS = {
    # Inputs to the ranked model proposal, which is Phase 6 UX. The tables were
    # shipped early on purpose so the surface has something to read when it
    # arrives; until then nothing consults them.
    "context_floor": "an input to the model proposal surface, which is Phase 6",
    "verified_at": "provenance for a measured card; the catalog UI reads it",

    # Recorded-for-later fields on entities and crawls. Written by nothing yet,
    # which is the honest state rather than a plan.
    "first_seen_at": "entity lifecycle; nothing populates it",
    "merged_at": "entity merges record their time in the audit trail instead",
    "bytes_seen": "a crawl statistic nothing accumulates",
}


# Public functions the package legitimately never calls itself. Empty, and worth
# keeping empty: every entry is a claim that something unreachable is fine, and
# the last one here turned out to be stale the moment its caller was written.
UNCALLED_BY_DESIGN: dict[str, str] = {}


def _public_functions(module: Path):
    """Module-level `def`s that are neither private nor framework-registered.

    Decorated functions are excluded because a decorator *is* the call site:
    every FastAPI endpoint is referenced by nothing and reached on every
    request.
    """
    tree = ast.parse(SOURCE[module.name])
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_") or node.decorator_list:
            continue
        yield node.name


def test_every_setting_in_the_register_is_read_by_something():
    """A setting nothing consults is a switch wired to nothing.

    This caught `enrich_by_default`, which the register described as governing
    whether a write enqueues enrichment while the write path never asked.
    """
    unread = {}
    for key in settings_store.REGISTER:
        if key in CLIENT_ONLY_SETTINGS:
            continue
        # Both quote styles: some are read through `resolve()` and some are
        # matched in SQL against the settings table.
        pattern = re.compile(rf"""['"]{key}['"]""")
        if not any(
            pattern.search(body)
            for name, body in SOURCE.items() if name != "settings_store.py"
        ):
            unread[key] = settings_store.REGISTER[key].description[:60]
    assert not unread, (
        "these settings are offered and consulted by nothing, so setting them "
        f"does nothing: {unread}"
    )


def test_no_public_function_is_unreachable():
    """A function nothing references is either dead or a duplicate of something
    live, and the second is worse — it is the copy someone fixes by mistake.

    This caught four: a scheduling helper superseded by inline logic, an id
    utility, an accessor added with the meter and never used, and a webhook
    signature verifier that the request path stopped calling when signing moved
    per-provider — with the tests still pointed at the dead one.
    """
    orphans = {}
    for module in MODULES:
        for name in _public_functions(module):
            if name in UNCALLED_BY_DESIGN:
                continue
            word = re.compile(rf"\b{name}\b")
            elsewhere = sum(
                len(word.findall(body))
                for other, body in SOURCE.items() if other != module.name
            )
            here = len(word.findall(SOURCE[module.name])) - 1   # its own def
            if elsewhere == 0 and here == 0:
                orphans[name] = module.name
    assert not orphans, (
        "these are defined, exported and referenced by no code path in the "
        f"package: {orphans}"
    )


def test_every_column_the_schema_declares_is_mentioned_in_the_code():
    """A column nothing names is a column nothing can be reading.

    Weaker than the other two — it cannot see a column that is selected and then
    ignored, which is exactly what `sensitivity` was. It still catches the
    cheaper mistake of shipping a migration and never wiring it.
    """
    ddl = "\n".join(p.read_text() for p in (SRC / "migrations").glob("*.sql"))
    python = "\n".join(SOURCE.values())

    columns: set[str] = set()
    for table in re.findall(r"CREATE TABLE (\w+) \((.*?)\n\);", ddl, re.S):
        for line in table[1].splitlines():
            line = line.strip()
            if not line or line.startswith("--"):
                continue
            match = re.match(r"([a-z_]+)\s+(text|int|bigint|boolean|jsonb|date|"
                             r"timestamptz|bytea|bigserial|float8|vector|tsvector)", line)
            if match:
                columns.add(match.group(1))

    # A column a later migration dropped is not part of the schema, however
    # convincingly it still reads in the CREATE TABLE that first declared it.
    # Without this the guard describes the database as it was rather than as it
    # is, and every drop would need an exemption to stay quiet.
    dropped = set(re.findall(r"DROP COLUMN (?:IF EXISTS )?([a-z_]+)", ddl))
    columns -= dropped

    assert len(columns) > 50, "the DDL parse found almost nothing — it has drifted"
    # Names that are structural rather than read by hand: every table has them
    # and they are touched by `now()` defaults and ordering, not by name.
    structural = {"created_at", "updated_at", "at", "occurred_at", "sequence"}
    missing = {
        column for column in columns - structural - set(UNWIRED_COLUMNS)
        if not re.search(rf"\b{column}\b", python)
    }
    assert not missing, (
        f"these columns exist and no Python names them: {sorted(missing)}"
    )


def test_a_dropped_column_leaves_the_schema():
    """`allow_public_sharing` is the case: declared in the first migration,
    never read, and dropped once the setting that actually gates public sharing
    was shown to be the only mechanism. The guard has to see the drop, or every
    removal would need an exemption to stay quiet."""
    ddl = "\n".join(p.read_text() for p in (SRC / "migrations").glob("*.sql"))
    assert "allow_public_sharing" in ddl, "expected the drop migration to name it"
    assert re.search(r"DROP COLUMN (?:IF EXISTS )?allow_public_sharing", ddl)


def test_the_guards_would_fail_if_the_patterns_drifted():
    """A guard that silently matches nothing is worse than no guard.

    Each check above depends on a regex over source text, and a refactor that
    changed how settings are declared or functions written would quietly turn
    all of this green.
    """
    assert settings_store.REGISTER, "the settings register parsed as empty"
    assert any(_public_functions(m) for m in MODULES), "no public functions found"
    assert len(MODULES) > 30, f"only {len(MODULES)} modules found — SRC is wrong"


@pytest.mark.parametrize("key,reason", sorted(CLIENT_ONLY_SETTINGS.items()))
def test_client_only_settings_are_still_declared(key, reason):
    """The exemption list may not outlive what it exempts."""
    assert key in settings_store.REGISTER, (
        f"{key} is exempted from the wiring guard but no longer exists"
    )


@pytest.mark.parametrize("column,reason", sorted(UNWIRED_COLUMNS.items()))
def test_an_exempted_column_still_exists_and_is_still_unwired(column, reason):
    """An exemption has to expire.

    If a column is wired up later the exemption becomes a lie, and the guard
    stops noticing when it is torn out again. If it is dropped, the list is
    describing a schema that no longer exists.
    """
    ddl = "\n".join(p.read_text() for p in (SRC / "migrations").glob("*.sql"))
    python = "\n".join(SOURCE.values())

    assert re.search(rf"\b{column}\b", ddl), (
        f"{column} is exempted from the wiring guard but is no longer in any "
        "migration — remove it from UNWIRED_COLUMNS"
    )
    assert not re.search(rf"\b{column}\b", python), (
        f"{column} is wired up now ({reason} is out of date) — remove it from "
        "UNWIRED_COLUMNS so the guard watches it again"
    )


# --- the guard on the suite itself -------------------------------------------


def test_the_test_database_guard_refuses_a_remote_host():
    """Every `pool` fixture starts with `DROP SCHEMA public CASCADE`.

    Until this existed, the only thing choosing which database that ran against
    was `os.environ.setdefault` — so an exported `DATABASE_URL`, of the kind
    anyone running a deploy or opening a psql session has, silently became the
    thing the suite dropped. It cost a seeded corpus in development; against
    Cloud SQL it would have cost the corpus.
    """
    import conftest

    with pytest.raises(pytest.UsageError) as exc:
        conftest._guard("postgresql://postgres:secret@10.100.0.3:5432/open_mem")
    # The message has to name the host, or the reader cannot tell which
    # database they nearly dropped.
    assert "10.100.0.3" in str(exc.value)
    assert "docker compose up" in str(exc.value)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://open_mem:open_mem@localhost:54329/open_mem",
        "postgresql://open_mem:open_mem@127.0.0.1:5432/open_mem",
        "postgresql://open_mem:open_mem@postgres:5432/open_mem",   # docker compose
    ],
)
def test_the_guard_allows_a_local_database(url):
    import conftest

    assert conftest._guard(url) == url


def test_the_escape_hatch_cannot_be_tripped_by_accident(monkeypatch):
    """A guard people switch off without meaning to is not a guard.

    The variable is named so that setting it is a sentence about the database,
    and it is not one anybody exports for another purpose.
    """
    import conftest

    remote = "postgresql://postgres:secret@10.100.0.3:5432/open_mem"
    for value in ("1", "true", "TRUE", "on", ""):
        monkeypatch.setenv("I_KNOW_THIS_DATABASE_IS_DISPOSABLE", value)
        with pytest.raises(pytest.UsageError):
            conftest._guard(remote)

    monkeypatch.setenv("I_KNOW_THIS_DATABASE_IS_DISPOSABLE", "yes")
    assert conftest._guard(remote) == remote


# --------------------------------------------------------------- endpoints

REPO = SRC.parents[2]

# Endpoints nothing anywhere issues a request to. Four defects in one day had
# this exact shape -- `POST /data/{id}/enrich`, `POST /reprocess`,
# `GET /artifacts/stale` and `PUT /settings/{scope}/{key}` all existed, were
# tested at the function level, were documented, and were reachable by no
# client we ship. Each read as a missing feature and was a missing wire.
#
# The list is a ratchet: it may shrink and it may not grow without a person
# writing a reason. Wiring one and leaving it here fails too, so the reasons
# cannot go stale quietly.
UNCALLED_ENDPOINTS: dict[str, str] = {
    # Not ours to call. Someone else's browser opens this.
    "GET /s/{token}": "a share link, opened by whoever received it",

    # An admin acting on somebody else. The console does the self-service half
    # -- `/users/me/deletion` -- and has no member-administration surface.
    "POST /api/v1/users/{user_id}/deletion":
        "deleting another person's data; the console offers only /users/me/deletion",
    "DELETE /api/v1/organizations/members/{user_id}":
        "removing a member; there is no member-administration screen",

    # Connection administration.
    "PATCH /api/v1/connections/{connection_id}":
        "personal vs shared scope, which decides ACL inheritance; nothing sets it",

    # Cases, exempt again -- and the history is the point, so it is kept rather
    # than rewritten. This was originally exempt because cases were declared by
    # producers and the console only read them; the exemption was retired when
    # the Cases screen learned to declare one, on the grounds that a subject
    # nobody can create is a timeline nobody can start.
    #
    # The screen is now removed. Not because the feature is wrong -- the
    # asserted/inferred distinction underneath it is the part of this system
    # that keeps one patient's timeline from quietly including another's -- but
    # because nothing on this deployment has ever written a case, so what
    # shipped was a screen over an empty table, and a picker on Add data that
    # could only ever offer nothing. The machinery, its tests and the write
    # path all stand; only the console stopped claiming to be a way in.
    "PUT /api/v1/cases":
        "declaring a subject; the console has no cases screen while nothing writes one",
    "GET /api/v1/projects/{project_id}/cases":
        "listing subjects; same -- restore the screen and both come back",

    # Configuration surfaces that read but do not write.
    "POST /api/v1/agents/{data_type}/config/test":
        "test-before-save for a prompt override; the Prompts screen has no save",
    "GET /api/v1/schemas": "normalization schemas; no editor",
    "POST /api/v1/schemas": "normalization schemas; no editor",
    "POST /api/v1/models/assignments": "the Models screen is read-only",
    "POST /api/v1/models/cards": "the Models screen is read-only",
    "POST /api/v1/engines": "engine registration is a deployment step, not a screen",

    # The ephemeral-token exchange, for hosts embedding the platform. Our own
    # console holds a durable credential server-side and never needs it, which
    # is exactly why it has no caller here.
    "POST /api/v1/tokens/ephemeral": "for embedding hosts; the console holds its credential",

    # Upload sessions are wired into the console now, so they are no longer
    # exempt. They were exempt on the grounds that inlining was "right for a
    # paste and wrong for 500 MB" -- true, and the missing half meant a document
    # too big to inline could not be added at all. This test is what noticed the
    # exemption had gone stale, which is what it is for.

    # Graph and facts. The console reads entities and edges; asserting a fact
    # by hand, reading one entity's history and listing contradictions have no
    # surface.
    "POST /api/v1/facts": "facts are asserted by enrichment; no manual surface",
    "POST /api/v1/facts/{fact_id}/retract": "no manual retraction surface",
    "GET /api/v1/entities/{entity_id}/history": "bitemporal history has no screen",
    "GET /api/v1/graph/conflicts": "contradiction surfacing has no screen",

    # Observability that shipped without its reader.
    "GET /api/v1/crawlers/{crawler_id}/runs": "run history per crawler; the screen shows one run",
    "GET /api/v1/event-subscriptions/{subscription_id}/deliveries":
        "delivery attempts per subscription; the alerts screen does not read them",

    # Screens deliberately removed from the console, September 2026: the nav
    # was collapsed to one story -- make a place, add data, read it back -- and
    # Cases, Entities, Workflows and Producers were not on it. The endpoints
    # stayed because an API client may still use them; only the screens went.
    # Listed here rather than deleted so this test keeps saying so out loud: if
    # one of these is ever meant to be reachable again, it needs a screen, and
    # if it is meant to be gone it should be deleted rather than exempted.
    # The two case reads are no longer here: the Cases screen came back, this
    # time as the *subject* half of the story -- a patient, a matter, an asset
    # and its whole history -- rather than as a third description of how the
    # corpus is arranged, which is what got it removed. Exactly the movement
    # this list is a ratchet for.
    "GET /api/v1/entities/{entity_id}": "the Entities screen was removed",
    "GET /api/v1/entities/{entity_id}/graph": "the Entities screen was removed",
    "GET /api/v1/entities/{entity_id}/co-mentions": "the Entities screen was removed",
    "POST /api/v1/entities/merge": "the Entities screen was removed",
    "POST /api/v1/entities/merges/{merge_id}/undo": "the Entities screen was removed",
    "GET /api/v1/projects/{project_id}/workflows": "the Workflows screen was removed",
    "PUT /api/v1/workflows": "the Workflows screen was removed",
    "POST /api/v1/workflows/{definition_id}/instances": "the Workflows screen was removed",
    "GET /api/v1/projects/{project_id}/instances": "the Workflows screen was removed",
    "GET /api/v1/instances/{instance_id}": "the Workflows screen was removed",
    "GET /api/v1/instances/{instance_id}/history": "the Workflows screen was removed",
    "GET /api/v1/instances/{instance_id}/verify": "the Workflows screen was removed",
    "POST /api/v1/instances/{instance_id}/input": "the Workflows screen was removed",
    "GET /api/v1/projects/{project_id}/source-lag": "the Producers screen was removed",
    "PATCH /api/v1/producers/{producer_id}": "the Producers screen was removed",

}


def _routes() -> list[tuple[str, str]]:
    app = (SRC / "app.py").read_text()
    return [
        (m.group(1).upper(), m.group(2))
        for m in re.finditer(r'@app\.(get|post|put|patch|delete)\("([^"]+)"', app)
    ]


def _concrete(path: str) -> str:
    """Drop `${...}` interpolations, brace-matched.

    The same reduction `scripts/check-proxy-paths.mjs` performs, for the same
    reason: an interpolation that is its own path segment is a parameter, and
    anything else -- `${kind ? `?type=${kind}` : ""}` -- is a query fragment
    glued on the end.
    """
    out, i = "", 0
    while i < len(path):
        if path[i] == "$" and path[i + 1:i + 2] == "{":
            depth, j = 1, i + 2
            while j < len(path) and depth:
                depth += {"{": 1, "}": -1}.get(path[j], 0)
                j += 1
            out += "*" if out.endswith("/") else ""
            i = j
        else:
            out += path[i]
            i += 1
    return out


def _shape(path: str) -> str:
    """Literal segments, every parameter collapsed, query dropped."""
    path = path.split("?")[0].strip("/")
    return "/".join(
        "*" if seg.startswith("{") or seg == "*" else seg
        for seg in path.split("/") if seg
    )


def _caller_text() -> list[str] | None:
    """Everything that issues an HTTP request at our own API.

    The proxy's allow-list is excluded on purpose: it routes requests, it does
    not make them, and counting it would mean every path anyone remembered to
    allow looked called.
    """
    ui = REPO / "ui"
    if not ui.exists():
        return None
    texts = [p.read_text() for p in (ui / "components").glob("*.tsx")]
    texts += [p.read_text() for p in (ui / "app").rglob("*.ts*") if "proxy" not in str(p)]
    texts += [p.read_text() for p in (REPO / "api/tests").glob("*.py")]
    texts += [p.read_text() for p in (REPO / "api/deploy").glob("*.sh")]
    texts += [(SRC / "mcp.py").read_text(), (SRC / "__main__.py").read_text()]
    return texts


def _called_shapes() -> set[str] | None:
    texts = _caller_text()
    if texts is None:
        return None
    blob = "\n".join(texts)
    called = set()
    for m in re.finditer(
        r"""["'`](/?(?:api/proxy/)?(?:api/v1|webhooks|s|healthz)[^"'`\s]*)""", blob
    ):
        raw = m.group(1).lstrip("/")
        raw = raw[len("api/proxy/"):] if raw.startswith("api/proxy/") else raw
        called.add(_shape(_concrete(raw)))
    return called


def test_every_endpoint_is_called_by_something():
    """An endpoint no client reaches is a feature nobody can use.

    It is the most expensive version of this file's subject, because the code
    is *correct*: it has tests, it has documentation, and the only thing
    missing is the one line that would let a person get to it. The failure
    presents as "we never built that".
    """
    called = _called_shapes()
    if called is None:
        pytest.skip("the console is not present in this checkout")
    missing = {
        f"{method} {path}": ""
        for method, path in _routes()
        if _shape(path) not in called and f"{method} {path}" not in UNCALLED_ENDPOINTS
    }
    assert not missing, (
        "these endpoints exist and nothing calls them -- wire one, or add it to "
        f"UNCALLED_ENDPOINTS with a reason: {sorted(missing)}"
    )


@pytest.mark.parametrize("route,reason", sorted(UNCALLED_ENDPOINTS.items()))
def test_an_exempted_endpoint_still_exists_and_is_still_uncalled(route, reason):
    """The exemption list may shrink and may not rot.

    Wiring an endpoint and leaving it listed here would be a lie that nobody
    trips over, which is how an allow-list stops meaning anything.
    """
    method, path = route.split(" ", 1)
    assert (method, path) in _routes(), f"{route} no longer exists; drop the exemption"
    called = _called_shapes()
    if called is None:
        pytest.skip("the console is not present in this checkout")
    assert _shape(path) not in called, (
        f"{route} has a caller now -- remove it from UNCALLED_ENDPOINTS"
    )
    assert reason, "every exemption carries a reason"


def test_the_endpoint_guard_would_notice_a_wired_route():
    """The guard is only worth its exemption list if the extractor works.

    Both halves: a route that is obviously wired must read as called, and the
    interpolation reducer must survive the nested-template form the console
    actually writes.
    """
    called = _called_shapes()
    if called is None:
        pytest.skip("the console is not present in this checkout")
    assert ("POST", "/api/v1/write") in _routes()
    assert _shape("/api/v1/write") in called
    assert _shape("/api/v1/projects/{project_id}/staircase") in called
    assert _concrete("api/v1/projects/${projectId}/entities${kind ? `?type=${kind}` : ``}") \
        == "api/v1/projects/*/entities"
