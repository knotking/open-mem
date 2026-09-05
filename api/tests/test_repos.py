"""Repository snapshots.

The tests worth having here are the ones that guard properties the feature
would otherwise lose quietly:

**A URL reaches `git clone`.** So the parser is tested by what it refuses, not
only by what it accepts. A permissive parse is an argument-injection surface and
would look exactly like a working one.

**A snapshot is analysed once.** The unique key is the whole of "per snapshot
only", and a second request for one commit must cost nothing.

**A dependency report must not invent an advisory.** The prompt carries that
constraint, so the test asserts the constraint is in the prompt -- the only part
of it that can be checked without a model.
"""

from __future__ import annotations

import pytest

from memdog.repos import (
    RepoError,
    canonical_url,
    parse_repo_url,
    repos_for_project,
    snapshots_for,
)

pytestmark = pytest.mark.asyncio


# ------------------------------------------------------------------ parsing

@pytest.mark.parametrize("raw,owner,repo", [
    ("https://github.com/Graphify-Labs/graphify", "Graphify-Labs", "graphify"),
    ("http://github.com/torvalds/linux", "torvalds", "linux"),
    ("github.com/psf/requests", "psf", "requests"),
    ("https://www.github.com/psf/requests/", "psf", "requests"),
    ("https://github.com/psf/requests.git", "psf", "requests"),
    ("https://github.com/a/b.c.d", "a", "b.c.d"),
])
async def test_the_shapes_a_repository_url_really_comes_in(raw, owner, repo):
    assert parse_repo_url(raw) == (owner, repo)


@pytest.mark.parametrize("raw", [
    "https://gitlab.com/foo/bar",
    "https://bitbucket.org/foo/bar",
    # The host has to be github.com itself, not something ending in it.
    "https://github.com.evil.example/foo/bar",
    "https://evilgithub.com/foo/bar",
    # A third path segment is not a repository -- it is a file, a tree or an
    # issue, and cloning the repository somebody half-pasted is a guess.
    "https://github.com/foo/bar/blob/main/README.md",
    "https://github.com/foo",
    "",
    "not a url at all",
])
async def test_what_is_not_a_repository_url_is_refused_by_name(raw):
    with pytest.raises(RepoError) as caught:
        parse_repo_url(raw)
    # The message names the expected shape; a refusal that does not is a refusal
    # somebody has to guess their way out of.
    assert "github.com/<owner>/<repo>" in str(caught.value)


@pytest.mark.parametrize("raw", [
    "https://github.com/foo/bar; rm -rf /",
    "https://github.com/foo/bar && curl evil.example",
    "https://github.com/foo/bar$(whoami)",
    "https://github.com/foo/bar\nrm -rf /",
    "https://github.com/../../etc/passwd",
    "https://github.com/foo/--upload-pack=touch pwned",
])
async def test_a_url_carrying_a_shell_never_gets_through(raw):
    """The clone builds an argv rather than a command line, so this is the
    second line of defence and not the first. It is tested anyway: the argv is
    in another process, in another repository, and this is the check that
    travels with the parser."""
    with pytest.raises(RepoError):
        parse_repo_url(raw)


async def test_the_canonical_url_is_what_gets_stored():
    """Three spellings of one repository must not become three cases."""
    spellings = ["https://github.com/psf/requests",
                 "github.com/psf/requests",
                 "https://www.github.com/psf/requests.git"]
    assert {canonical_url(*parse_repo_url(s)) for s in spellings} == {
        "https://github.com/psf/requests"}


# --------------------------------------------------------------- the schema

async def _snapshot(pool, tenant, *, sha: str, url: str = "https://github.com/psf/requests",
                    status: str = "complete"):
    """Insert a snapshot directly.

    Deliberately not through `request_snapshot`: that resolves a ref against
    GitHub, and a test suite that reaches the network is a test suite that fails
    when somebody else's service is down.
    """
    from memdog.ids import new_id

    case_id = await pool.fetchval(
        """
        INSERT INTO cases (case_id, org_id, project_id, case_type, external_id, title)
        VALUES ($1, $2, $3, 'repo', $4, $5)
        ON CONFLICT (project_id, case_type, external_id) DO UPDATE SET title = EXCLUDED.title
        RETURNING case_id
        """,
        new_id("cas"), tenant.org_id, tenant.project_id,
        url.removeprefix("https://"), "psf/requests",
    )
    await pool.execute(
        """
        INSERT INTO memory_types (type_id, org_id, project_id, name, ttl_seconds, on_expiry)
        VALUES ($1, $2, $3, 'repo_snapshot', NULL, 'keep_members')
        ON CONFLICT (project_id, name) DO NOTHING
        """,
        new_id("mty"), tenant.org_id, tenant.project_id,
    )
    memory_id = await pool.fetchval(
        """
        INSERT INTO memories (memory_id, org_id, project_id, type, memory_key, title)
        VALUES ($1, $2, $3, 'repo_snapshot', $4, $5)
        ON CONFLICT (project_id, type, memory_key) DO UPDATE SET updated_at = now()
        RETURNING memory_id
        """,
        new_id("mem"), tenant.org_id, tenant.project_id, f"psf/requests@{sha}", "snap",
    )
    snapshot_id = new_id("rsnap")
    await pool.execute(
        """
        INSERT INTO repo_snapshots (snapshot_id, org_id, project_id, case_id, memory_id,
                                    repo_url, commit_sha, ref, status)
        VALUES ($1, $2, $3, $4, $5, $6, $7, 'main', $8)
        """,
        snapshot_id, tenant.org_id, tenant.project_id, case_id, memory_id,
        url, sha, status,
    )
    return snapshot_id, case_id, memory_id


async def test_one_commit_is_analysed_once(pool, tenant):
    """The constraint that *is* the per-snapshot rule.

    Two people pasting the same URL a second apart is the ordinary case, and a
    check-then-insert would let both through and pay twice.
    """
    import asyncpg

    sha = "a" * 40
    await _snapshot(pool, tenant, sha=sha)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _snapshot(pool, tenant, sha=sha)


async def test_a_second_commit_is_a_second_snapshot_under_one_repository(pool, tenant,
                                                                        principal_for):
    """Snapshots of one repository share a case and nothing else."""
    _, case_a, _ = await _snapshot(pool, tenant, sha="a" * 40)
    _, case_b, _ = await _snapshot(pool, tenant, sha="b" * 40)
    assert case_a == case_b

    principal = await principal_for(tenant.api_key)
    listed = await snapshots_for(pool, principal, case_a)
    assert {s["commit_sha"] for s in listed} == {"a" * 40, "b" * 40}
    # Newest first: a list read top-down should start with the most recent.
    assert listed == sorted(listed, key=lambda s: s["created_at"], reverse=True)


async def test_a_short_sha_cannot_smuggle_a_second_analysis_of_one_commit(pool, tenant):
    """A seven-character sha would pass the unique key and name the same commit,
    which is exactly what the constraint exists to stop."""
    import asyncpg

    with pytest.raises(asyncpg.PostgresError):
        await _snapshot(pool, tenant, sha="abc1234")


async def test_an_unknown_status_is_refused_by_the_schema(pool, tenant):
    import asyncpg

    with pytest.raises(asyncpg.PostgresError):
        await _snapshot(pool, tenant, sha="c" * 40, status="probably_fine")


# ----------------------------------------------------------------- tenancy

async def test_another_organisation_cannot_see_a_snapshot(pool, tenant, other_tenant,
                                                          principal_for):
    _, case_id, _ = await _snapshot(pool, tenant, sha="d" * 40)
    intruder = await principal_for(other_tenant.api_key)
    assert await snapshots_for(pool, intruder, case_id) == []
    assert await repos_for_project(pool, intruder, tenant.project_id) == []


async def test_the_repository_list_carries_the_latest_run_state(pool, tenant, principal_for):
    """A repository whose last run failed and one never analysed must not be the
    same blank row -- that is the whole reason the status is on the list."""
    await _snapshot(pool, tenant, sha="e" * 40, status="complete")
    await _snapshot(pool, tenant, sha="f" * 40, status="failed")

    principal = await principal_for(tenant.api_key)
    repos = await repos_for_project(pool, principal, tenant.project_id)
    assert len(repos) == 1
    assert repos[0]["snapshots"] == 2
    assert repos[0]["last_status"] == "failed"
    assert repos[0]["last_sha"] == "f" * 40


async def test_deleting_the_memory_takes_the_snapshot_with_it(pool, tenant):
    """A snapshot row without its records is a claim with nothing behind it."""
    snapshot_id, _, memory_id = await _snapshot(pool, tenant, sha="1" * 40)
    await pool.execute("DELETE FROM memories WHERE memory_id = $1", memory_id)
    assert await pool.fetchval(
        "SELECT count(*) FROM repo_snapshots WHERE snapshot_id = $1", snapshot_id) == 0


# ---------------------------------------------------------------- reporting

async def test_the_four_reports_are_registered_generators():
    from memdog.derive import GENERATORS, validate

    for name in ("repo_design", "repo_quality", "repo_bugs", "repo_deps"):
        assert validate(name)["label"]
        # None may archive what it read. A report that ate the repository it
        # described would be a report nobody could check.
        assert GENERATORS[name]["archivable"] is False


async def test_the_dependency_report_may_not_invent_an_advisory():
    """The one constraint in this feature whose failure is a security claim.

    A model asked whether a version is vulnerable produces fluent, plausible,
    wrong CVE numbers. The facts come from OSV in the job; the prompt is what
    stops the model adding to them, so the prompt is what is asserted here.
    """
    from memdog.derive import GENERATORS

    prompt = GENERATORS["repo_deps"]["prompt"].lower()
    assert "only if it appears in the supplied vulnerability data" in prompt
    assert "never invent an advisory identifier" in prompt
    # Absent data must produce a stated absence, not silence that reads as clean.
    assert "no vulnerability data was supplied" in prompt


async def test_the_bug_report_omits_what_it_cannot_locate():
    """An unlocatable finding reads exactly like a located one and cannot be
    checked, so the prompt requires omission rather than hedging."""
    from memdog.derive import GENERATORS

    prompt = GENERATORS["repo_bugs"]["prompt"].lower()
    assert "omit it entirely" in prompt
    # And it must not let a partial read stand as a clean bill of health.
    assert "absence of a bug is not evidence of correctness" in prompt


# ---------------------------------------------------------- not configured

async def test_without_a_job_the_snapshot_fails_rather_than_waiting(pool, tenant):
    """Pending forever reads as still running.

    Every local deployment has no job configured, so this is the common path
    rather than an edge case -- and the sentence on the row is the only thing
    that distinguishes it from a run that is genuinely in progress.
    """
    from memdog.config import Settings
    from memdog.repos import RepoAnalysisWorker

    snapshot_id, case_id, memory_id = await _snapshot(
        pool, tenant, sha="2" * 40, status="pending")

    worker = RepoAnalysisWorker(pool, Settings(repo_analysis_job=""))

    class _Message:
        body = {"snapshot_id": snapshot_id, "repo_url": "https://github.com/psf/requests",
                "commit_sha": "2" * 40, "memory_id": memory_id, "case_id": case_id,
                "project_id": tenant.project_id}

    await worker.handle(_Message())
    row = await pool.fetchrow(
        "SELECT status, reason FROM repo_snapshots WHERE snapshot_id = $1", snapshot_id)
    assert row["status"] == "failed"
    assert "REPO_ANALYSIS_JOB" in row["reason"]


# ------------------------------------------------------- the review envelope

async def test_the_repo_reports_ask_for_findings_not_a_summary():
    """The bug report described the codebase instead of answering, four runs
    running, and no wording fixed it.

    The cause was the *shape*: the envelope's one open field is `summary`,
    documented everywhere as what the document covers, so a prompt asking for
    located defects was answered with a description of the material. Three of
    the four reports survived that because their answers are naturally
    summary-shaped. This asserts the shape, because the wording never was the
    problem.
    """
    from memdog.derive import GENERATORS
    from memdog.extraction import REVIEWED, envelope_schema

    for name in ("repo_design", "repo_quality", "repo_bugs", "repo_deps"):
        assert GENERATORS[name]["data_type"] == "code_review"
    assert "code_review" in REVIEWED

    schema = envelope_schema(findings="code_review" in REVIEWED)
    finding = schema["properties"]["findings"]["items"]
    # A finding that cannot say where it is cannot be checked, so the schema
    # refuses to let one exist.
    assert "file" in finding["required"]
    assert "statement" in finding["required"]

    # And the ordinary document envelope must not grow a findings field: a core
    # field that is null for every other data type is not a core field.
    assert "findings" not in envelope_schema()["properties"]


async def test_a_review_that_found_nothing_is_stored_as_nothing():
    """`[]` and a missing key are different answers.

    "Reviewed and found no locatable defect" is a result somebody can act on;
    "never reviewed" is not, and rendering them the same is how a report that
    did not run reads as a clean bill of health.
    """
    from memdog.extraction import Envelope

    envelope = Envelope(title="t")
    parsed = {"findings": []}
    if isinstance(parsed.get("findings"), list):
        envelope.fields["findings"] = parsed["findings"]
    assert envelope.fields["findings"] == []
    assert "findings" in envelope.fields


async def test_the_gemini_findings_schema_has_no_nullable_unions():
    """Gemini rejects `{"type": ["string", "null"]}` with a 400 on the whole
    request, not a complaint about the property.

    That is why this is worth a test rather than a comment: the failure is not
    local to the field. One nullable union anywhere fails every extraction
    carrying the schema, three of those trip the breaker, and every artifact
    afterwards records `circuit open` -- which names the symptom and hides the
    cause. Ordinary enrichment kept working throughout, because its schema has
    no such field, so the model looked healthy while the review path was dead.
    """
    from memdog.extraction import _gemini_schema, envelope_schema

    findings = _gemini_schema(findings=True)["properties"]["findings"]
    nullable = [name for name, spec in findings["items"]["properties"].items()
                if isinstance(spec.get("type"), list)]
    assert nullable == [], f"Gemini will 400 on: {nullable}"
    # Optional is still expressed, just by absence from `required`.
    assert "symbol" not in findings["items"]["required"]
    assert "file" in findings["items"]["required"]

    # The permissive dialect keeps its unions: the fix is per-engine, not a
    # narrowing of the contract everywhere.
    other = envelope_schema(findings=True)["properties"]["findings"]
    assert isinstance(other["items"]["properties"]["symbol"]["type"], list)


async def test_a_repository_with_no_code_is_a_result_not_a_failure():
    """graphify refuses to write `graph.json` when extraction finds no nodes.

    That is the correct outcome for a repository this can legitimately find no
    code in — docs only, notebooks, data files, a language with no grammar, or
    sources behind `.gitignore`. Failing the snapshot there threw away the
    manifests and the README, which need no graph and are most of the
    dependency and design material, and reported a tool error where the honest
    answer is a finding about the repository.
    """
    import importlib.util
    import pathlib

    spec = importlib.util.spec_from_file_location(
        "analyze", pathlib.Path(__file__).parent.parent.parent / "analysis/repo/analyze.py")
    analyze = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(analyze)

    empty = {"nodes": [], "links": [], "graph": {},
             "_empty_reason": "graph is empty — extraction produced no nodes"}
    digest = analyze.digest(empty)
    # It says what happened rather than rendering a table of zeroes.
    assert "No code graph was produced" in digest
    assert "not a failure of the run" in digest
    # And it warns the reader what the reports below can and cannot cover.
    assert "cannot speak to code they never saw" in digest

    # The graph helpers must not raise on it either.
    assert analyze.edges(empty) == []
    assert analyze.degree(empty) == {}
    assert analyze.node_paths(empty) == {}


async def test_an_empty_transcript_from_an_overridden_model_is_retried():
    """A purpose-built transcriber returning nothing is ambiguous.

    Found live: a 46 KB webm of somebody saying "hello, hello, hello" came back
    empty from `gemini-3.5-transcribe` and was stored as "no interpretable
    content found in the media" — a true-sounding sentence about something
    untrue. The same bytes through the general model transcribed correctly.

    It is the quiet mirror of a failure already recorded for video, which fails
    *loudly* against the same model and was routed away. Quiet is worse: nothing
    errors, and the row carries a confident explanation nobody can tell from the
    truth. So an empty answer from an overridden model is retried against the
    general one, and only silence from *that* is recorded as silence.
    """
    import inspect

    from memdog.multimodal import GeminiMultimodal, NullMultimodal

    # The retry needs to name a model for one call without changing assignment.
    for engine in (GeminiMultimodal, NullMultimodal):
        params = inspect.signature(engine.interpret).parameters
        assert "model" in params, f"{engine.__name__} cannot be asked for a specific model"
        assert params["model"].default is None

    source = inspect.getsource(__import__("memdog.workers", fromlist=["x"]))
    # Retried against the engine's own base model, not a hardcoded name.
    assert 'result.model_id != base' in source
    # And the second emptiness is still recorded, so a silent recording stays
    # an honest empty rather than being retried forever.
    assert source.count('"no interpretable content found in the media"') == 1


async def test_a_failed_snapshot_is_retried_rather_than_handed_back(pool, tenant):
    """Re-use protects work that succeeded, not work that did not.

    Returning the existing row whatever its status made a failure permanent for
    that commit: the unique key meant asking again handed back the same dead
    row, so a snapshot that failed because the deployment had no job configured
    stayed failed after the job was configured. Nothing an operator could do
    would clear it and the console offered no retry — the only escape was
    analysing a different commit.
    """
    import inspect

    from memdog import repos

    source = inspect.getsource(repos.request_snapshot)
    # Re-use is conditional on having succeeded.
    assert 'existing["status"] != "failed"' in source
    # A retry re-enqueues rather than merely relabelling the row, or it would
    # sit `pending` for ever with nothing to pick it up.
    retry = source[source.index('existing["status"] != "failed"'):]
    assert "queue.publish(REPO_TOPIC" in retry
    # And the stale reason goes, rather than describing a run that is no longer
    # the current one.
    assert "reason = NULL" in retry


async def test_a_completed_snapshot_is_still_reused(pool, tenant, principal_for):
    """The other half: a successful analysis must not be paid for twice."""
    import inspect

    from memdog import repos

    source = inspect.getsource(repos.request_snapshot)
    assert '"reused": True' in source
    # The cheap path returns before anything is enqueued.
    reuse = source.index('"reused": True')
    publish = source.index("queue.publish(REPO_TOPIC")
    assert reuse < publish, "a re-used snapshot must not enqueue a job"
