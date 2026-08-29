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

from memdog import settings_store

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
        conftest._guard("postgresql://postgres:secret@10.100.0.3:5432/memdog")
    # The message has to name the host, or the reader cannot tell which
    # database they nearly dropped.
    assert "10.100.0.3" in str(exc.value)
    assert "docker compose up" in str(exc.value)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://memdog:memdog@localhost:54329/memdog",
        "postgresql://memdog:memdog@127.0.0.1:5432/memdog",
        "postgresql://memdog:memdog@postgres:5432/memdog",   # docker compose
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

    remote = "postgresql://postgres:secret@10.100.0.3:5432/memdog"
    for value in ("1", "true", "TRUE", "on", ""):
        monkeypatch.setenv("I_KNOW_THIS_DATABASE_IS_DISPOSABLE", value)
        with pytest.raises(pytest.UsageError):
            conftest._guard(remote)

    monkeypatch.setenv("I_KNOW_THIS_DATABASE_IS_DISPOSABLE", "yes")
    assert conftest._guard(remote) == remote
