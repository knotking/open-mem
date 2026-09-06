"""Repository snapshots -- a repo at one commit, analysed and reported on.

**A snapshot is the unit, and it is the whole design.** `owner/repo@sha` is
analysed whole or not at all: there is no incremental crawl, no watermark, and
no diff against the previous snapshot. That is a smaller feature than it sounds
and a much clearer one -- "has quality improved since March" needs a second
snapshot to compare against and a model of what comparable means, and building
half of it now would leave a corpus of reports that look comparable and are not.

Three things follow from the constraint, and they are why it is worth stating
first:

**Re-analysing a sha is free.** The unique key is `(project, url, sha)`, so a
second request for a commit already analysed returns the existing snapshot
rather than cloning it again. Artifacts add the second half of that: a report is
keyed by `generator_version`, so an unchanged prompt against an unchanged
snapshot re-uses the report instead of paying a model to reproduce it.

**A repo is not ingested as text.** It exceeds every ceiling in `docs/limit.md`
by orders of magnitude -- 200 archive members, two million indexed characters,
twenty-four extraction windows. The graph is the compression: `graphify` reduces
a repository to symbols and edges, and the analysers read that plus a bounded
set of files chosen for being load-bearing. Nothing here tries to hand a model a
repository, because the pipeline cannot carry one and a ceiling raised to admit
it would only move the failure later.

**The clone does not happen here.** Cloning and parsing is minutes of CPU, and
the API autoscales on request rate -- a long job in this process counts the same
as a 20 ms write and starves the pool without triggering a scale-up. This module
records the intent and enqueues it; `analysis/repo/` does the work as a Cloud
Run Job and writes results back through the ordinary public write path.
"""

from __future__ import annotations

import logging
import re

import asyncpg
import httpx

from .audit import record_audit
from .auth import DATA_READ, DATA_WRITE, Principal
from .cases import upsert_case
from .ids import new_id
from .memories import upsert_memory
from .telemetry import span

log = logging.getLogger(__name__)

REPO_TOPIC = "repo_analysis"

# The memory type a snapshot lands in. Registered per project on first use
# rather than added to `SHIPPED_TYPES`, because a project that never analyses a
# repository should not carry a container type for one.
#
# No TTL: an analysis somebody paid a model to produce should not evaporate on a
# default nobody chose. A project wanting expiry sets it on the type, which is
# the control that already exists.
SNAPSHOT_TYPE = "repo_snapshot"

# GitHub only, and matched exactly rather than parsed permissively. This string
# reaches `git clone` in the job, so the same reasoning as `youtube.VIDEO_ID`
# applies with more at stake: a permissive parse here is an argument-injection
# surface, and being generous would be a worse repository, never a worse
# permission. Owner and repo are GitHub's own character classes.
_OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
_REPO = r"[A-Za-z0-9_.-]{1,100}"
REPO_URL = re.compile(
    rf"^(?:https?://)?(?:www\.)?github\.com/({_OWNER})/({_REPO}?)(?:\.git)?/?$"
)

# A ref is a branch, tag or sha and goes into an API path, not a shell. Still
# bounded: `..` and control characters have no business in one.
REF = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")
SHA = re.compile(r"^[0-9a-f]{40}$")

GITHUB_API = "https://api.github.com"
RESOLVE_TIMEOUT_SECONDS = 20.0

STATUSES = ("pending", "running", "complete", "failed")


class RepoError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def parse_repo_url(raw: str) -> tuple[str, str]:
    """`owner`, `repo` -- or a refusal naming what was wrong.

    Refusing an unsupported host by name matters more than it looks. "Analyse
    this repo" against a GitLab URL is an ordinary thing to try, and the useful
    answer is that this only speaks GitHub -- not a clone that fails in a job
    ten seconds later with exit 128.
    """
    match = REPO_URL.match((raw or "").strip())
    if not match:
        raise RepoError(
            f"not a GitHub repository URL: {(raw or '')[:200]!r}. "
            "Expected github.com/<owner>/<repo>"
        )
    owner, repo = match.group(1), match.group(2)
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not repo or repo in (".", ".."):
        raise RepoError(f"not a repository name: {repo!r}")
    return owner, repo


def canonical_url(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}"


async def resolve_commit(owner: str, repo: str, ref: str | None = None) -> dict:
    """The sha a ref points at *now*, plus what the repository says about itself.

    Resolved here rather than in the job because it is the one question that
    decides whether there is anything to do: a sha already analysed needs no
    job, and finding that out after a clone means paying for the clone.

    A ref is resolved once and the sha is what is stored. `main` is a moving
    target and a report attributed to a branch is a report nobody can reproduce.
    """
    if ref is not None and not REF.match(ref):
        raise RepoError(f"not a valid git ref: {ref[:100]!r}")

    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "mem-dog-repo-analysis"}
    try:
        async with httpx.AsyncClient(timeout=RESOLVE_TIMEOUT_SECONDS) as client:
            meta = await client.get(f"{GITHUB_API}/repos/{owner}/{repo}", headers=headers)
            if meta.status_code == 404:
                # 404 is what GitHub answers for both "no such repository" and
                # "private, and you are nobody". Saying so is the honest
                # message: guessing at which it is would be wrong half the time
                # and the fix -- supply a token -- is the same either way.
                raise RepoError(
                    f"{owner}/{repo} is not a public GitHub repository. It does not "
                    "exist, or it is private -- this deployment analyses public "
                    "repositories only.",
                    status=404,
                )
            if meta.status_code == 403:
                raise RepoError(
                    "GitHub is rate limiting this deployment; try again shortly",
                    status=429,
                )
            meta.raise_for_status()
            info = meta.json()
            if info.get("private"):
                raise RepoError(
                    f"{owner}/{repo} is private; this deployment analyses public "
                    "repositories only",
                    status=403,
                )

            target = ref or info.get("default_branch") or "HEAD"
            commit = await client.get(
                f"{GITHUB_API}/repos/{owner}/{repo}/commits/{target}", headers=headers
            )
            if commit.status_code == 404:
                raise RepoError(
                    f"{target!r} does not resolve to a commit in {owner}/{repo}",
                    status=404,
                )
            commit.raise_for_status()
            head = commit.json()
    except RepoError:
        raise
    except httpx.HTTPError as exc:
        # Transient by assumption: GitHub being unreachable is not the caller's
        # mistake and a 502 here reads as one.
        raise RepoError(f"could not reach GitHub: {exc}", status=503) from exc

    sha = (head.get("sha") or "").lower()
    if not SHA.match(sha):
        raise RepoError(f"GitHub returned an unusable commit id for {target!r}", status=502)

    return {
        "sha": sha,
        "ref": ref or info.get("default_branch"),
        "default_branch": info.get("default_branch"),
        "size_kb": info.get("size") or 0,
        "language": info.get("language"),
        "license": ((info.get("license") or {}) or {}).get("spdx_id"),
        "stars": info.get("stargazers_count") or 0,
        "pushed_at": info.get("pushed_at"),
        "committed_at": (((head.get("commit") or {}).get("committer") or {}).get("date")),
        "message": ((head.get("commit") or {}).get("message") or "").split("\n")[0][:500],
    }


async def _ensure_type(conn, *, org_id: str, project_id: str) -> None:
    await conn.execute(
        """
        INSERT INTO memory_types (type_id, org_id, project_id, name, ttl_seconds, on_expiry)
        VALUES ($1, $2, $3, $4, NULL, 'keep_members')
        ON CONFLICT (project_id, name) DO NOTHING
        """,
        new_id("mty"), org_id, project_id, SNAPSHOT_TYPE,
    )


async def request_snapshot(
    pool: asyncpg.Pool,
    queue,
    principal: Principal,
    *,
    project_id: str,
    repo_url: str,
    ref: str | None = None,
    max_repo_mb: int = 512,
) -> dict:
    """Record the intent to analyse a commit, and enqueue the work.

    Returns the existing snapshot when this sha has been asked for before. That
    is the whole of "per snapshot only" as a runtime behaviour, and it is
    enforced by a unique constraint rather than by a check-then-insert, because
    two people pasting the same URL at once is the ordinary case and a race that
    produced two snapshots would double the bill.
    """
    principal.require(DATA_WRITE)
    owner, repo = parse_repo_url(repo_url)
    url = canonical_url(owner, repo)

    with span("repo.resolve", repo=f"{owner}/{repo}"):
        head = await resolve_commit(owner, repo, ref)

    # Refused before the clone rather than during it. GitHub reports size in KB
    # and it excludes history on a shallow clone, so this is an estimate -- but
    # an estimate that stops a four-gigabyte monorepo is worth more than an
    # exact number discovered by running out of disk in a job.
    size_mb = (head["size_kb"] or 0) / 1024
    if size_mb > max_repo_mb:
        raise RepoError(
            f"{owner}/{repo} is about {size_mb:.0f} MB; this deployment analyses "
            f"repositories up to {max_repo_mb} MB",
            status=413,
        )

    sha = head["sha"]
    async with pool.acquire() as conn, conn.transaction():
        existing = await conn.fetchrow(
            """
            SELECT snapshot_id, case_id, memory_id, status, commit_sha, created_at
            FROM repo_snapshots
            WHERE project_id = $1 AND repo_url = $2 AND commit_sha = $3
            """,
            project_id, url, sha,
        )
        if existing is not None:
            # **A failed snapshot is retried; a good one is re-used.**
            #
            # Returning the row whatever its status made a failure permanent for
            # that commit: the unique key meant asking again handed back the
            # same dead row, so a snapshot that failed because the deployment
            # had no job configured stayed failed after the job was configured.
            # Nothing the operator could do would clear it, and the console had
            # no retry -- the only escape was a different commit.
            #
            # Re-use is about not paying twice for work that succeeded. Work
            # that did not succeed was never paid for, so there is nothing to
            # protect, and the reason is cleared with it rather than left to
            # describe a run that is no longer the current one.
            if existing["status"] != "failed":
                return {**dict(existing), "repo_url": url, "ref": head["ref"],
                        "reused": True}
            await conn.execute(
                "UPDATE repo_snapshots SET status = 'pending', reason = NULL "
                "WHERE snapshot_id = $1", existing["snapshot_id"])
            await queue.publish(REPO_TOPIC, {
                "snapshot_id": existing["snapshot_id"],
                "org_id": principal.org_id,
                "project_id": project_id,
                "repo_url": url,
                "commit_sha": sha,
                "memory_id": existing["memory_id"],
                "case_id": existing["case_id"],
            })
            log.info("retrying failed snapshot %s for %s@%s",
                     existing["snapshot_id"], url, sha[:7])
            return {**dict(existing), "repo_url": url, "ref": head["ref"],
                    "status": "pending", "reason": None, "reused": False,
                    "retried": True}

        await _ensure_type(conn, org_id=principal.org_id, project_id=project_id)
        case_id = await upsert_case(
            conn, org_id=principal.org_id, project_id=project_id,
            case_type="repo", external_id=f"github.com/{owner}/{repo}",
            title=f"{owner}/{repo}",
        )
        memory_id = await upsert_memory(
            conn, org_id=principal.org_id, project_id=project_id,
            type_name=SNAPSHOT_TYPE, memory_key=f"{owner}/{repo}@{sha}",
            owner_id=principal.user_id, title=f"{owner}/{repo} @ {sha[:7]}",
        )
        snapshot_id = new_id("rsnap")
        await conn.execute(
            """
            INSERT INTO repo_snapshots (
                snapshot_id, org_id, project_id, case_id, memory_id,
                repo_url, commit_sha, ref, status, stats
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pending', $9::jsonb)
            """,
            snapshot_id, principal.org_id, project_id, case_id, memory_id,
            url, sha, head["ref"], _stats(head),
        )
        await record_audit(
            conn, principal, action="repo.snapshot.requested", project_id=project_id,
            target_type="repo_snapshot", target_id=snapshot_id,
            detail={"repo_url": url, "commit_sha": sha, "ref": head["ref"]},
        )

    await queue.publish(REPO_TOPIC, {
        "snapshot_id": snapshot_id,
        "org_id": principal.org_id,
        "project_id": project_id,
        "repo_url": url,
        "commit_sha": sha,
        "memory_id": memory_id,
        "case_id": case_id,
    })
    log.info("repo snapshot %s queued for %s@%s", snapshot_id, url, sha[:7])
    return {
        "snapshot_id": snapshot_id, "case_id": case_id, "memory_id": memory_id,
        "repo_url": url, "commit_sha": sha, "ref": head["ref"],
        "status": "pending", "reused": False,
    }


def _stats(head: dict) -> dict:
    """A dict, not a JSON string.

    The pool installs a jsonb codec (`db._init_connection`) whose encoder is
    `json.dumps`, so a value that is already serialised gets serialised again
    and lands as a jsonb *string* rather than an object. Nothing errors: the
    row stores `"{\"nodes\": 990}"`, `stats || …` then concatenates two
    strings into an array instead of merging them, and the console reads
    `stats.nodes` off a list and renders nothing.
    """
    return ({
        "size_kb": head.get("size_kb"),
        "primary_language": head.get("language"),
        "license": head.get("license"),
        "stars": head.get("stars"),
        "committed_at": head.get("committed_at"),
        "subject": head.get("message"),
    })


async def mark(
    pool: asyncpg.Pool, snapshot_id: str, status: str, *,
    reason: str | None = None, stats: dict | None = None,
) -> None:
    """Move a snapshot's status, and say why when it stopped.

    `reason` carries the same weight it does on a record that needed a model and
    did not get one: a snapshot that failed with nothing on the row is
    indistinguishable from one nobody ran, and the difference is the only thing
    a person looking at the screen wants to know.
    """
    if status not in STATUSES:
        raise RepoError(f"unknown status {status!r}")
    await pool.execute(
        """
        UPDATE repo_snapshots
        SET status = $2,
            reason = coalesce($3, reason),
            stats = CASE WHEN $4::jsonb IS NULL THEN stats ELSE stats || $4::jsonb END
        WHERE snapshot_id = $1
        """,
        snapshot_id, status, reason, stats or None,
    )


async def report_result(
    pool: asyncpg.Pool, principal: Principal, snapshot_id: str,
    *, status: str, reason: str | None = None, stats: dict | None = None,
) -> dict:
    """The job saying how it went.

    Without this the row stops at `running` and stays there: the job exits 0,
    the reports exist, and the console shows "cloning and parsing" forever. A
    finished analysis that reads as an unfinished one is the exact failure the
    status column exists to prevent, so the last thing the job does is say so.

    It is a normal authenticated call with `data:write` -- the job holds an API
    key and no database credential, and this is not an exception to that.
    """
    principal.require(DATA_WRITE)
    if status not in STATUSES:
        raise RepoError(f"unknown status {status!r}")
    row = await pool.fetchrow(
        "SELECT org_id FROM repo_snapshots WHERE snapshot_id = $1", snapshot_id)
    if row is None or row["org_id"] != principal.org_id:
        raise RepoError("unknown snapshot", status=404)
    await mark(pool, snapshot_id, status, reason=reason, stats=stats)
    return {"snapshot_id": snapshot_id, "status": status}


async def get(pool: asyncpg.Pool, principal: Principal, snapshot_id: str) -> dict:
    row = await pool.fetchrow(
        """
        SELECT s.*, c.external_id AS repo
        FROM repo_snapshots s JOIN cases c ON c.case_id = s.case_id
        WHERE s.snapshot_id = $1 AND s.org_id = $2
        """,
        snapshot_id, principal.org_id,
    )
    if row is None:
        raise RepoError("unknown snapshot", status=404)
    return dict(row)


class RepoAnalysisWorker:
    """Turns a queued snapshot into a running Cloud Run Job execution.

    The worker exists because the queue and the job runner speak different
    languages: the queue delivers a message at-least-once into this process, and
    Cloud Run Jobs are started by an API call with per-execution env overrides.
    This is the translation, and it is deliberately the *only* thing in the API
    that knows a job runner exists.

    **Not launching is a state, not a crash.** A deployment with no job
    configured -- every local one, and any test -- marks the snapshot failed with
    that as the reason rather than raising into the queue's retry loop. A
    retryable error would spin forever against configuration that will not
    change on its own, and the sentence on the row is what tells somebody which
    it was.
    """

    def __init__(self, pool: asyncpg.Pool, settings) -> None:
        self._pool = pool
        self._settings = settings

    def register(self, queue) -> None:
        queue.subscribe(REPO_TOPIC, self.handle)

    async def handle(self, message) -> None:
        body = message.body
        snapshot_id = body["snapshot_id"]
        job = getattr(self._settings, "repo_analysis_job", "")
        if not job:
            await mark(
                self._pool, snapshot_id, "failed",
                reason="no repo analysis job is configured for this deployment "
                       "(REPO_ANALYSIS_JOB); the snapshot was recorded but nothing ran",
            )
            log.warning("snapshot %s not started: REPO_ANALYSIS_JOB unset", snapshot_id)
            return

        overrides = {
            "SNAPSHOT_ID": snapshot_id,
            "REPO_URL": body["repo_url"],
            "COMMIT_SHA": body["commit_sha"],
            "MEMORY_ID": body["memory_id"],
            "MEMORY_KEY": f"{body['repo_url'].removeprefix('https://github.com/')}"
                          f"@{body['commit_sha']}",
            "CASE_EXTERNAL_ID": body["repo_url"].removeprefix("https://"),
            "PROJECT_ID": body["project_id"],
        }
        with span("repo.launch", snapshot_id=snapshot_id):
            try:
                await _run_job(job, overrides)
            except Exception as exc:  # noqa: BLE001 -- the row is where this belongs
                await mark(self._pool, snapshot_id, "failed",
                           reason=f"could not start the analysis job: {exc}"[:500])
                log.exception("snapshot %s failed to launch", snapshot_id)
                return
        await mark(self._pool, snapshot_id, "running")


async def _run_job(job: str, overrides: dict[str, str]) -> None:
    """Start one execution of a Cloud Run Job with these environment overrides.

    `job` is the fully qualified name --
    `projects/{p}/locations/{l}/jobs/{name}` -- so the deployment names it once
    and this does not assemble it from three settings that can disagree.
    """
    from google.auth import default as google_auth_default
    from google.auth.transport.requests import Request as AuthRequest

    credentials, _ = google_auth_default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(AuthRequest())
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"https://run.googleapis.com/v2/{job}:run",
            headers={"Authorization": f"Bearer {credentials.token}"},
            json={"overrides": {"containerOverrides": [{
                "env": [{"name": k, "value": v} for k, v in overrides.items()]
            }]}},
        )
        response.raise_for_status()


async def repos_for_project(
    pool: asyncpg.Pool, principal: Principal, project_id: str
) -> list[dict]:
    """Every analysed repository, with the state of its most recent snapshot.

    The latest snapshot's status is on the row rather than a click away, because
    "silence and success look identical" is the failure this list exists to
    avoid: a repository whose last run failed and one that has never been asked
    for anything are the same blank row otherwise.
    """
    principal.require(DATA_READ)
    rows = await pool.fetch(
        """
        SELECT c.case_id, c.external_id AS repo, c.title,
               count(s.snapshot_id) AS snapshots,
               max(s.created_at) AS last_analysed,
               (ARRAY_AGG(s.status ORDER BY s.created_at DESC))[1] AS last_status,
               (ARRAY_AGG(s.reason ORDER BY s.created_at DESC))[1] AS last_reason,
               (ARRAY_AGG(s.commit_sha ORDER BY s.created_at DESC))[1] AS last_sha,
               (ARRAY_AGG(s.snapshot_id ORDER BY s.created_at DESC))[1] AS last_snapshot_id
        FROM cases c
        LEFT JOIN repo_snapshots s ON s.case_id = c.case_id
        WHERE c.project_id = $1 AND c.org_id = $2
          AND c.case_type = 'repo' AND c.deleted_at IS NULL
        GROUP BY c.case_id, c.external_id, c.title
        ORDER BY max(s.created_at) DESC NULLS LAST
        LIMIT 200
        """,
        project_id, principal.org_id,
    )
    return [dict(r) for r in rows]


async def snapshots_for(
    pool: asyncpg.Pool, principal: Principal, case_id: str, *, limit: int = 50
) -> list[dict]:
    """Every snapshot of one repository, newest first.

    This is the only place snapshots of the same repository are seen together,
    and it is deliberately a list rather than a comparison: the case correlates
    them, nothing here claims they are comparable.
    """
    rows = await pool.fetch(
        """
        SELECT snapshot_id, commit_sha, ref, status, reason, stats, created_at
        FROM repo_snapshots
        WHERE case_id = $1 AND org_id = $2
        ORDER BY created_at DESC
        LIMIT $3
        """,
        case_id, principal.org_id, limit,
    )
    return [dict(r) for r in rows]


async def delete_repo(
    pool: asyncpg.Pool, queue, principal: Principal, case_id: str
) -> dict:
    """Stop tracking a repository, and take its snapshots with it.

    Analysing a repository leaves two kinds of trace. Each snapshot is a
    *memory* -- graph, digest and reports -- and the whole repository is a
    *case*, which is the durable "this is a thing we look at" and outlives any
    one commit. Removing one without the other leaves a screen that is wrong in
    a different direction each way round: orphaned snapshots nothing lists, or a
    repository row claiming a history that is gone.

    So both go, and the snapshots go **through `delete_memory`** rather than
    through a DELETE written here. That is the whole point -- the records get
    the same tombstone, the same blob reclamation and the same audit trail as
    anything else deleted in this system, and there is no second erasure path to
    keep honest.

    The case is tombstoned rather than dropped: `cases.deleted_at` is what every
    read already filters on, and a hard delete would take the audit rows that
    reference it.

    The `repo_snapshots` delete looks redundant and is not. Under the policies a
    snapshot memory normally carries the memory row is really removed, so
    `ON DELETE CASCADE` takes the snapshot with it. Under `archive` the memory
    is tombstoned instead, the cascade never fires, and the rows would survive a
    repository that no longer exists. The `on_expiry` policy is a per-project
    setting, so which of those happens is not this function's to assume.
    """
    from .memories import delete_memory

    principal.require(DATA_WRITE)
    case = await pool.fetchrow(
        """
        SELECT case_id, project_id, external_id, title
        FROM cases
        WHERE case_id = $1 AND org_id = $2 AND case_type = 'repo'
          AND deleted_at IS NULL
        """,
        case_id, principal.org_id,
    )
    if case is None:
        raise RepoError("no such repository", status=404)

    rows = await pool.fetch(
        "SELECT snapshot_id, memory_id FROM repo_snapshots "
        "WHERE case_id = $1 AND org_id = $2",
        case_id, principal.org_id,
    )

    # Memories first. If this fails halfway the case is still here and the
    # repository is still listed, which is a state somebody can retry from --
    # the reverse order leaves snapshots nothing lists and no way back to them.
    deleted, failed = [], []
    for row in rows:
        try:
            await delete_memory(pool, principal, queue, row["memory_id"])
            deleted.append(row["snapshot_id"])
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            log.warning("repo delete: snapshot %s: %s", row["snapshot_id"], exc)
            failed.append({"snapshot_id": row["snapshot_id"], "reason": str(exc)})

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "DELETE FROM repo_snapshots WHERE case_id = $1 AND org_id = $2",
            case_id, principal.org_id,
        )
        await conn.execute(
            "UPDATE cases SET deleted_at = now() WHERE case_id = $1 AND org_id = $2",
            case_id, principal.org_id,
        )
        await record_audit(
            conn, principal,
            action="repo.deleted",
            project_id=case["project_id"],
            target_type="case",
            target_id=case_id,
            detail={
                "repo": case["external_id"],
                "snapshots": len(rows),
                "snapshots_failed": len(failed),
            },
        )

    return {
        "case_id": case_id,
        "repo": case["external_id"],
        "snapshots_deleted": len(deleted),
        # Named, not counted. A partial delete that reports only a number is
        # indistinguishable from one that quietly did less.
        "failed": failed,
    }
