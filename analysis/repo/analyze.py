"""Clone a repository at one commit, graph it, and write the result back.

This runs as a Cloud Run Job and talks to mem-dog only through the public write
API, exactly as any other producer does. It has no database credentials and no
privileged path: everything it stores goes through admission, sniffing, parsing
and the ACL like a write from anywhere else. That is the property worth
protecting -- an analysis job with a back door into the record store would be a
second write path, and the first one is load-bearing precisely because there is
only one.

**The graph is the compression, and that is the whole reason graphify is here.**
A repository exceeds every ceiling in `docs/limit.md` by orders of magnitude:
200 archive members, two million indexed characters, twenty-four extraction
windows. Handing a model a repository is not a thing this pipeline can do at any
setting. So the repository is reduced first -- to symbols and edges, parsed
deterministically with tree-sitter and no model at all -- and the analysers read
that plus a small, deliberately chosen set of files.

**What gets selected is a quality decision, and it is visible.** Manifests,
because dependency findings are worthless without them. Entry points, because
they say what the thing is. The most-connected modules by graph degree, because
that is where a defect costs the most and where the design actually lives. The
selection is written into the snapshot's stats so a thin report can be read as
"it only saw twelve files" rather than as "there is nothing wrong here".
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("repo-analysis")

API_URL = os.environ.get("MEMDOG_API_URL", "").rstrip("/")
API_KEY = os.environ.get("MEMDOG_API_KEY", "")
PRODUCER_ID = os.environ.get("MEMDOG_PRODUCER_ID", "")

CLONE_ROOT = os.environ.get("CLONE_ROOT", "/work/clones")
GRAPHIFY_VERSION = os.environ.get("GRAPHIFY_VERSION", "unknown")

# How many files the analysers may read, beyond the manifests. Bounded because
# each one is characters against the extraction window, and twenty-four windows
# is the whole budget for a report. Raising it does not buy more understanding
# past the point where the window fills -- it buys a truncated read of more
# files, which is worse than a complete read of fewer.
MAX_SELECTED_FILES = int(os.environ.get("MAX_SELECTED_FILES", "40"))
MAX_FILE_BYTES = int(os.environ.get("MAX_FILE_BYTES", "120000"))

CLONE_TIMEOUT = int(os.environ.get("CLONE_TIMEOUT_SECONDS", "600"))
GRAPHIFY_TIMEOUT = int(os.environ.get("GRAPHIFY_TIMEOUT_SECONDS", "1800"))

SHA = re.compile(r"^[0-9a-f]{40}$")
REPO_URL = re.compile(r"^https://github\.com/[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}$")

# Dependency manifests, by ecosystem. The name is the whole detection: a
# lockfile is a lockfile wherever it sits, and guessing an ecosystem from
# directory layout is how a monorepo gets read as one project.
MANIFESTS = {
    "package.json": "npm", "package-lock.json": "npm", "yarn.lock": "npm",
    "pnpm-lock.yaml": "npm",
    "requirements.txt": "PyPI", "pyproject.toml": "PyPI", "poetry.lock": "PyPI",
    "Pipfile.lock": "PyPI", "uv.lock": "PyPI",
    "go.mod": "Go", "go.sum": "Go",
    "Cargo.toml": "crates.io", "Cargo.lock": "crates.io",
    "Gemfile": "RubyGems", "Gemfile.lock": "RubyGems",
    "pom.xml": "Maven", "build.gradle": "Maven", "build.gradle.kts": "Maven",
    "composer.json": "Packagist", "composer.lock": "Packagist",
}

ENTRY_POINT_NAMES = {
    "main.py", "__main__.py", "app.py", "server.py", "cli.py", "manage.py",
    "index.js", "index.ts", "main.js", "main.ts", "server.js", "server.ts",
    "main.go", "main.rs", "lib.rs", "Main.java", "Program.cs",
}

README_NAMES = {"README.md", "README.rst", "README.txt", "README"}


class AnalysisFailed(Exception):
    """Terminal. The snapshot is marked failed with this as its reason."""


# --------------------------------------------------------------------- clone


def clone(repo_url: str, sha: str, into: Path) -> Path:
    """Shallow-clone one commit.

    `--depth 1` against a sha rather than a branch: the sha was resolved when
    the snapshot was requested, and re-resolving a branch here would analyse a
    different commit than the one the row claims. That mismatch is invisible
    afterwards, which is what makes it worth a fetch-by-sha.

    Nothing here is shelled through a string. `repo_url` was pattern-matched at
    the API and is checked again below, but the argument list is what actually
    makes injection impossible.
    """
    if not REPO_URL.match(repo_url):
        raise AnalysisFailed(f"refusing to clone an unexpected URL: {repo_url[:200]!r}")
    if not SHA.match(sha):
        raise AnalysisFailed(f"not a commit sha: {sha[:80]!r}")

    into.mkdir(parents=True, exist_ok=True)
    def run(args: list[str]) -> None:
        result = subprocess.run(
            args, cwd=into, capture_output=True, text=True, timeout=CLONE_TIMEOUT
        )
        if result.returncode != 0:
            raise AnalysisFailed(
                f"{' '.join(args[:2])} failed: {(result.stderr or '').strip()[:400]}"
            )

    run(["git", "init", "--quiet"])
    run(["git", "remote", "add", "origin", repo_url])
    # Fetching the single commit is what keeps a large repository's history out
    # of an instance whose filesystem is memory.
    run(["git", "fetch", "--depth", "1", "--quiet", "origin", sha])
    run(["git", "checkout", "--quiet", "FETCH_HEAD"])
    shutil.rmtree(into / ".git", ignore_errors=True)
    return into


# ------------------------------------------------------------------ graphify


def graph(repo: Path) -> tuple[dict, str]:
    """Run graphify over the clone and read what it produced.

    Deterministic and local: tree-sitter ASTs, no model, nothing leaving the
    instance. That is why the graph can be stored as a fact about the snapshot
    while every report derived from it is stamped with a `generator_version` --
    the parse is reproducible and the reading of it is not.
    """
    out = repo / "graphify-out"
    # `--code-only` is not a limitation accepted reluctantly; it is the mode
    # this design already claimed to be in. Without it graphify offers to read
    # the repository's docs, papers and images through a model and **refuses to
    # run at all without an API key** -- which is how this first failed, on a
    # repository with fifty markdown files.
    #
    # Three reasons it is the right mode rather than a workaround. The parse
    # stays deterministic, so two analyses of one commit agree, which a
    # per-snapshot design cannot give up. It stays free, where the alternative
    # is a model call per document before the four reports have even started.
    # And the repository's prose is not what the graph is for: the reports read
    # the README and the manifests directly as selected files, so sending them
    # through an extractor here would pay twice for the same text.
    # No `--output`. The flag names a *parent* -- `--output DIR` writes to
    # `DIR/graphify-out/` -- so passing the graphify-out directory itself nests
    # it one level deeper and the graph lands somewhere nothing looks. The
    # default is the scanned path, which is where `out` already points.
    result = subprocess.run(
        ["graphify", "extract", ".", "--code-only"],
        cwd=repo, capture_output=True, text=True, timeout=GRAPHIFY_TIMEOUT,
    )
    graph_path = out / "graph.json"
    if not graph_path.exists():
        said = (result.stderr or result.stdout or "").strip()
        # **An empty graph is a result, not a crash.** graphify refuses to write
        # `graph.json` when extraction produced no nodes, and that is the
        # correct outcome for a repository this can legitimately find no code
        # in: a docs-only repo, a corpus of notebooks or data files, a project
        # written in a language tree-sitter has no grammar for, or one whose
        # sources all sit behind `.gitignore`.
        #
        # Failing the whole snapshot there was wrong twice over. It threw away
        # the manifests and the README, which need no graph and are most of the
        # dependency and design material. And it reported a tool error where the
        # honest answer is "there is no code here to graph" -- which reads as
        # broken rather than as a finding about the repository.
        #
        # So the run continues with an empty graph, and the reason travels with
        # it onto the snapshot. What the analysers lose is the connected-module
        # selection; what they keep is everything chosen by name.
        if "graph is empty" in said or "produced no nodes" in said:
            log.warning("no code graph for this repository: %s", said[:200])
            return {"nodes": [], "links": [], "graph": {}, "_empty_reason": said[:400]}, ""
        raise AnalysisFailed(f"graphify produced no graph.json: {said[:400]}")
    try:
        data = json.loads(graph_path.read_text())
    except json.JSONDecodeError as exc:
        raise AnalysisFailed(f"graphify wrote an unreadable graph.json: {exc}") from exc

    # `extract` writes the graph and stops; the human-readable report comes from
    # a second pass. `--no-label` keeps the community names as placeholders,
    # which is what keeps this free -- naming them is the one part of clustering
    # that wants a model, and a report whose sections are called "Community 3"
    # is still worth having next to a graph.
    #
    # Best effort: the graph is the thing the analysers actually read, so a
    # failure here costs a nicety and must not lose the run.
    try:
        subprocess.run(
            ["graphify", "cluster-only", ".", "--no-label", "--no-viz"],
            cwd=repo, capture_output=True, text=True, timeout=GRAPHIFY_TIMEOUT,
        )
    except subprocess.SubprocessError as exc:
        log.warning("cluster-only failed, continuing without a graph report: %s", exc)

    report_path = out / "GRAPH_REPORT.md"
    report = report_path.read_text() if report_path.exists() else ""
    return data, report


def edges(data: dict) -> list[dict]:
    """The graph's edges.

    **`links`, not `edges`.** graphify emits NetworkX node-link JSON, where the
    edge list is called `links`. Reading `edges` returns nothing and every
    degree is zero -- which does not fail, it just quietly selects no files, and
    the reports come back thin over manifests alone with nothing saying why.
    `edges` is accepted too so a future format change degrades rather than
    silently empties.
    """
    return list(data.get("links") or data.get("edges") or [])


def degree(data: dict) -> dict[str, int]:
    """How connected each node is, counting both directions.

    Degree is the selection signal because it is the one the graph actually
    supports. "Most important file" is not knowable from an AST; "the module
    the most other modules reach into" is, and it is the same file often enough
    to be worth reading first.
    """
    counts: dict[str, int] = {}
    for edge in edges(data):
        for end in ("source", "target"):
            node = edge.get(end)
            if isinstance(node, str):
                counts[node] = counts.get(node, 0) + 1
    return counts


def digest(data: dict, *, top: int = 60) -> str:
    """The graph as something a model can actually read.

    **This is the compression the whole design rests on, and storing the raw
    graph instead of this is what broke the first live run.** `graph.json` for a
    mid-size repository is well over a megabyte of node-link JSON; handed to an
    extractor it fills every window with punctuation and the report comes back
    as an echo of its own input. It looks like a model failure and is a units
    failure -- the graph was supposed to be smaller than the code, and raw it is
    larger.

    So the raw graph is kept as provenance and *this* is what the analysers
    read: the module dependency structure, the hubs, and the shape of the
    communities, in a few kilobytes of prose-ish text. Everything here is
    counted from the graph rather than described, because a digest that
    editorialises is already an analysis, and the analysis is the model's job.
    """
    nodes = data.get("nodes") or []
    links = edges(data)
    if not nodes:
        return (
            "# Code graph digest\n\n"
            "**No code graph was produced for this repository.** Extraction ran "
            "and found nothing to parse -- the repository holds no source in a "
            "language this can read, or its sources are excluded by "
            "`.gitignore`.\n\n"
            "That is a fact about the repository, not a failure of the run. The "
            "reports below are built from the files named directly -- the "
            "README, the manifests, the entry points -- and from nothing else, "
            "so they describe what those files say and cannot speak to code "
            "they never saw.\n\n"
            f"Tool output: {(data.get('_empty_reason') or 'none').strip()[:300]}\n"
        )
    paths = node_paths(data)
    counts = degree(data)

    by_file: dict[str, int] = {}
    for node in nodes:
        f = node.get("source_file")
        if isinstance(f, str):
            by_file[f] = by_file.get(f, 0) + 1

    # Module-to-module edges, which is the level design questions are asked at.
    # Symbol-to-symbol is too fine to see structure in and too many to list.
    between: dict[tuple[str, str], int] = {}
    relations: dict[str, int] = {}
    for link in links:
        rel = link.get("relation") or "?"
        relations[rel] = relations.get(rel, 0) + 1
        a, b = paths.get(link.get("source", "")), paths.get(link.get("target", ""))
        if a and b and a != b:
            between[(a, b)] = between.get((a, b), 0) + 1

    communities: dict[object, list[str]] = {}
    for node in nodes:
        communities.setdefault(node.get("community"), []).append(
            str(node.get("label") or node.get("id")))

    hubs = sorted(
        ((counts.get(n.get("id", ""), 0), n) for n in nodes),
        key=lambda pair: -pair[0])[:top]

    lines = [
        "# Code graph digest",
        "",
        f"{len(nodes)} symbols across {len(by_file)} files, {len(links)} edges.",
        "Parsed from source with tree-sitter; no model was involved in building this.",
        "",
        "## Edge kinds",
        *(f"- {rel}: {n}" for rel, n in sorted(relations.items(), key=lambda kv: -kv[1])),
        "",
        f"## Most connected symbols (top {len(hubs)})",
        "Where a defect costs the most and where the design tends to live.",
    ]
    for count, node in hubs:
        lines.append(
            f"- {node.get('label')} ({node.get('source_file')}"
            f":{node.get('source_location')}) — {count} edges")

    lines += ["", "## Files with the most symbols"]
    for f, n in sorted(by_file.items(), key=lambda kv: -kv[1])[:40]:
        lines.append(f"- {f}: {n}")

    lines += ["", "## Dependencies between files", "Counted both directions; a>b means a references b."]
    for (a, b), n in sorted(between.items(), key=lambda kv: -kv[1])[:80]:
        lines.append(f"- {a} > {b}: {n}")

    lines += ["", f"## Communities ({len(communities)})",
              "Clusters the graph fell into. Names are placeholders — they were not "
              "labelled by a model."]
    for name, members in sorted(communities.items(), key=lambda kv: -len(kv[1]))[:20]:
        lines.append(f"- Community {name}: {len(members)} symbols — "
                     f"{', '.join(members[:12])}{' …' if len(members) > 12 else ''}")
    return "\n".join(lines)


def node_paths(data: dict) -> dict[str, str]:
    """Node id -> the file it came from.

    The key is `source_file`; the others are accepted only as fallbacks. Getting
    this wrong has the same shape as getting `links` wrong -- an empty mapping
    means no file is ever ranked, and nothing reports that it happened.
    """
    paths: dict[str, str] = {}
    for node in data.get("nodes") or []:
        node_id = node.get("id") or node.get("label")
        location = (node.get("source_file") or node.get("file") or node.get("path")
                    or (node.get("location") or {}).get("file"))
        if isinstance(node_id, str) and isinstance(location, str):
            paths[node_id] = location
    return paths


# ------------------------------------------------------------------ selection


def select_files(repo: Path, data: dict) -> tuple[list[Path], list[str]]:
    """The bounded set the analysers read, and why each file is in it.

    Returned with its reasons so the snapshot can say what it looked at. A
    report over twelve files and a report over forty are different claims, and
    the difference has to be legible from the outside or a thin report reads as
    a clean bill of health.
    """
    chosen: dict[Path, str] = {}

    def take(path: Path, reason: str) -> None:
        if path.is_file() and path not in chosen and path.stat().st_size <= MAX_FILE_BYTES:
            chosen[path] = reason

    for name in README_NAMES:
        take(repo / name, "readme")
    # Manifests are not part of the budget: dependency findings are worthless
    # without them and they are small.
    for path in sorted(repo.rglob("*")):
        if path.name in MANIFESTS and "node_modules" not in path.parts:
            take(path, "manifest")

    counts = degree(data)
    paths = node_paths(data)
    ranked: dict[str, int] = {}
    for node_id, count in counts.items():
        location = paths.get(node_id)
        if location:
            ranked[location] = max(ranked.get(location, 0), count)

    for location, _ in sorted(ranked.items(), key=lambda kv: -kv[1]):
        if len([p for p, r in chosen.items() if r == "connected"]) >= MAX_SELECTED_FILES:
            break
        candidate = (repo / location).resolve()
        # A path from the graph is data, and a `..` in it would read outside the
        # clone. Refused rather than sanitised.
        if repo.resolve() in candidate.parents or candidate.parent == repo.resolve():
            take(candidate, "connected")

    for path in sorted(repo.rglob("*")):
        if path.name in ENTRY_POINT_NAMES and "node_modules" not in path.parts:
            take(path, "entry_point")

    return list(chosen), [f"{p.relative_to(repo)}:{r}" for p, r in chosen.items()]


# ----------------------------------------------------------------------- osv


def dependencies(repo: Path) -> list[dict]:
    """Package names and versions, read from the manifests without resolving.

    Deliberately shallow: this reads what the manifests declare, it does not run
    a package manager. A full resolution would be more accurate and would mean
    executing arbitrary install hooks from an untrusted repository inside the
    job, which is a much larger decision than dependency reporting justifies.
    """
    found: list[dict] = []
    for path in sorted(repo.rglob("*")):
        if path.name not in MANIFESTS or "node_modules" in path.parts:
            continue
        ecosystem = MANIFESTS[path.name]
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if path.name == "package.json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            for section in ("dependencies", "devDependencies"):
                for name, spec in (data.get(section) or {}).items():
                    found.append({"ecosystem": ecosystem, "name": name,
                                  "version": str(spec).lstrip("^~>=< "),
                                  "declared": str(spec), "manifest": path.name})
        elif path.name == "requirements.txt":
            for line in text.splitlines():
                line = line.split("#")[0].strip()
                match = re.match(r"^([A-Za-z0-9._-]+)\s*==\s*([A-Za-z0-9._+-]+)$", line)
                if match:
                    found.append({"ecosystem": ecosystem, "name": match.group(1),
                                  "version": match.group(2), "declared": line,
                                  "manifest": path.name})
        elif path.name == "go.mod":
            for line in text.splitlines():
                match = re.match(r"^\s*([\w./-]+)\s+v([\w.\-+]+)", line.strip())
                if match and not line.strip().startswith(("module", "go ")):
                    found.append({"ecosystem": ecosystem, "name": match.group(1),
                                  "version": match.group(2), "declared": line.strip(),
                                  "manifest": path.name})
    return found


def advisories(deps: list[dict]) -> dict:
    """Known advisories for these exact versions, from OSV.

    **This is the only source of vulnerability facts in the feature.** A model
    asked whether a version is affected produces fluent, plausible, wrong CVE
    numbers, and a wrong advisory is a security claim about somebody's software.
    So the facts are queried here and the generator is instructed to report
    nothing about vulnerabilities that is not in this result.

    A failure to reach OSV is recorded as such and is *not* silently an empty
    result: "no advisories" and "nobody asked" must not look the same, or an
    unreachable service reads as a clean report.
    """
    queried = [d for d in deps if d.get("version")]
    if not queried:
        return {"source": "osv.dev", "status": "no_dependencies_found", "results": []}

    payload = {"queries": [
        {"package": {"name": d["name"], "ecosystem": d["ecosystem"]},
         "version": d["version"]}
        for d in queried[:1000]
    ]}
    try:
        response = httpx.post("https://api.osv.dev/v1/querybatch",
                              json=payload, timeout=60.0)
        response.raise_for_status()
        batch = response.json().get("results") or []
    except Exception as exc:  # noqa: BLE001 -- any failure is the same outcome here
        log.warning("OSV unreachable: %s", exc)
        return {"source": "osv.dev", "status": "unavailable",
                "reason": str(exc)[:300], "results": []}

    results = []
    for dep, entry in zip(queried, batch):
        vulns = entry.get("vulns") or []
        if vulns:
            results.append({
                "package": dep["name"], "ecosystem": dep["ecosystem"],
                "version": dep["version"], "manifest": dep["manifest"],
                "advisories": [v.get("id") for v in vulns if v.get("id")],
            })
    return {"source": "osv.dev", "status": "ok",
            "queried": len(queried), "affected": len(results), "results": results}


# ------------------------------------------------------------------- writing


class Api:
    """The public API, as any producer sees it."""

    def __init__(self, base: str, key: str, producer_id: str) -> None:
        if not base or not key or not producer_id:
            raise AnalysisFailed(
                "MEMDOG_API_URL, MEMDOG_API_KEY and MEMDOG_PRODUCER_ID are all required"
            )
        self._base, self._producer = base, producer_id
        self._client = httpx.Client(
            timeout=120.0, headers={"Authorization": f"Bearer {key}"}
        )

    def write(self, items: list[dict]) -> dict:
        """Written *and embedded*, because a snapshot nobody can ask about is
        half a feature.

        Enrichment resolves to OFF when a write does not ask, which is the right
        default -- recording is cheap and synchronous, and anything that spends
        money is opt-in. This write asks, and it is the one place where asking
        is clearly correct: somebody requested an analysis of this repository,
        the four reports are already four model calls, and a corpus of
        `stored` records is invisible to both search and chat. A repository
        memory that looks full and answers nothing is the worse outcome.

        `summarize` is deliberately off. A per-record summary of eighty source
        files is eighty model calls for something no one reads -- the reports
        are the summary. Embedding is what makes the code answerable.
        """
        response = self._client.post(
            f"{self._base}/api/v1/write",
            json={"producer_id": self._producer, "items": items,
                  "options": {"enrich": True,
                              "enrichment": {"embed": True, "summarize": False}}},
        )
        if response.status_code >= 400:
            raise AnalysisFailed(
                f"write failed ({response.status_code}): {response.text[:300]}"
            )
        return response.json()

    def finish(self, snapshot_id: str, status: str, *,
               reason: str | None = None, stats: dict | None = None) -> None:
        """Say how it went. Best effort, and loudly logged when it fails --
        the analysis is already done and stored by this point, so failing here
        must not undo it, but a snapshot left at `running` reads as one still
        in progress and somebody has to be able to find out why."""
        try:
            response = self._client.patch(
                f"{self._base}/api/v1/repos/snapshots/{snapshot_id}",
                json={"status": status, "reason": reason, "stats": stats},
            )
            if response.status_code >= 400:
                log.error("could not report %s for %s: %s",
                          status, snapshot_id, response.text[:300])
        except Exception as exc:  # noqa: BLE001
            log.error("could not report %s for %s: %s", status, snapshot_id, exc)

    def stage(self, snapshot_id: str, name: str, **stats) -> None:
        """Say which stage is running, while it is running.

        The job used to report once, at the end, so a snapshot sat at `running`
        for minutes with nothing to distinguish "cloning a large repository"
        from "wedged". A clone, a full AST pass and four model calls are minutes
        of work and the console had one word for all of it.

        Best effort and never fatal: this is narration. Losing it costs the
        progress bar a step, and failing the analysis because the narration
        failed would be the tail wagging the dog.
        """
        try:
            self._client.patch(
                f"{self._base}/api/v1/repos/snapshots/{snapshot_id}",
                json={"status": "running", "stats": {"stage": name, **stats}},
                timeout=20.0,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("could not report stage %s: %s", name, exc)

    def derive(self, memory_id: str, generator: str) -> dict:
        response = self._client.post(
            f"{self._base}/api/v1/memories/{memory_id}/derive",
            json={"generator": generator},
        )
        if response.status_code >= 400:
            # One failed report must not lose the other three, nor the graph
            # that is already stored. Recorded and carried on.
            log.warning("derive %s failed (%s): %s",
                        generator, response.status_code, response.text[:200])
            return {"generator": generator, "error": response.text[:300]}
        return response.json()


def item(external_id: str, text: str, *, memory_key: str, repo: str,
         data_type: str | None = None, tags: list[str] | None = None) -> dict:
    return {
        "external_id": external_id,
        "content": {"kind": "inline", "text": text},
        "data_type": data_type,
        "source_type": "repo_analysis",
        "tags": ["source:repo", *(tags or [])],
        "memory": {"key": memory_key, "type": "repo_snapshot"},
        "case": {"external_id": repo, "case_type": "repo"},
    }


# -------------------------------------------------------------------- driver


def main() -> int:
    snapshot_id = os.environ.get("SNAPSHOT_ID", "")
    repo_url = os.environ.get("REPO_URL", "")
    sha = os.environ.get("COMMIT_SHA", "")
    memory_key = os.environ.get("MEMORY_KEY", "")
    memory_id = os.environ.get("MEMORY_ID", "")
    case_external = os.environ.get("CASE_EXTERNAL_ID", "")

    if not all([snapshot_id, repo_url, sha, memory_id]):
        log.error("SNAPSHOT_ID, REPO_URL, COMMIT_SHA and MEMORY_ID are required")
        return 2

    api = Api(API_URL, API_KEY, PRODUCER_ID)
    workdir = Path(tempfile.mkdtemp(prefix="repo-", dir=_clone_root()))
    try:
        api.stage(snapshot_id, "cloning")
        log.info("cloning %s@%s", repo_url, sha[:7])
        repo = clone(repo_url, sha, workdir)

        api.stage(snapshot_id, "graphing")
        log.info("graphing")
        data, report = graph(repo)
        nodes, edge_count = len(data.get("nodes") or []), len(edges(data))
        log.info("graph: %d nodes, %d edges", nodes, edge_count)

        api.stage(snapshot_id, "dependencies", nodes=nodes, edges=edge_count)
        selected, reasons = select_files(repo, data)
        deps = dependencies(repo)
        osv = advisories(deps)
        log.info("selected %d files, %d dependencies, OSV %s",
                 len(selected), len(deps), osv.get("status"))

        # The digest goes in the snapshot memory; the raw graph goes in a
        # sibling. Everything in the snapshot memory is read by all four
        # reports, so a member that cannot be read usefully does not merely
        # waste a window -- it *takes* windows from the material that can, and
        # a megabyte of node-link JSON takes all of them.
        raw_key = f"{memory_key}/graph"
        items = [
            item(f"{repo_url}@{sha}/graph-digest.md", digest(data),
                 memory_key=memory_key, repo=case_external,
                 tags=["repo:graph-digest"]),
            item(f"{repo_url}@{sha}/dependencies.json",
                 json.dumps({"dependencies": deps, "vulnerabilities": osv}, indent=2),
                 memory_key=memory_key, repo=case_external,
                 data_type="dependencies", tags=["repo:dependencies"]),
            # Provenance. Kept whole and kept out of the reports' way, so the
            # graph can still be traversed, diffed or re-read later.
            item(f"{repo_url}@{sha}/graph.json", json.dumps(data)[:1_800_000],
                 memory_key=raw_key, repo=case_external,
                 data_type="code_graph", tags=["repo:graph"]),
        ]
        if report:
            items.append(item(f"{repo_url}@{sha}/GRAPH_REPORT.md", report,
                              memory_key=memory_key, repo=case_external,
                              tags=["repo:graph-report"]))
        for path in selected:
            relative = path.relative_to(repo)
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            items.append(item(f"{repo_url}@{sha}/{relative}", text,
                              memory_key=memory_key, repo=case_external,
                              tags=["repo:file"]))

        api.stage(snapshot_id, "writing", files_selected=len(selected))
        log.info("writing %d records", len(items))
        # Chunked to stay inside the write endpoint's item and payload caps
        # rather than discovering them as a 413 after the expensive part.
        for start in range(0, len(items), 100):
            api.write(items[start:start + 100])

        api.stage(snapshot_id, "deriving", records_written=len(items))
        log.info("deriving reports")
        reports = [api.derive(memory_id, g) for g in
                   ("repo_design", "repo_quality", "repo_bugs", "repo_deps")]

        stats = {
                "nodes": nodes, "edges": edge_count,
                "files_selected": len(selected), "selection": reasons,
                "dependencies": len(deps),
                "osv_status": osv.get("status"),
                "osv_affected": osv.get("affected", 0),
                "records_written": len(items),
                "graphify_version": GRAPHIFY_VERSION,
                # Present only when there was no code to graph. The console
                # reads it to say so, because a snapshot with 0 nodes and four
                # reports is otherwise indistinguishable from one where the
                # parse silently did nothing.
                **({"no_code_graph": data["_empty_reason"]}
                   if data.get("_empty_reason") else {}),
        }
        api.finish(snapshot_id, "complete", stats=stats)
        print(json.dumps({"snapshot_id": snapshot_id, "status": "complete",
                          "stats": stats,
                          "reports": [r.get("artifact_id") or r.get("error")
                                      for r in reports]}))
        return 0
    except AnalysisFailed as exc:
        log.error("analysis failed: %s", exc)
        api.finish(snapshot_id, "failed", reason=str(exc)[:500])
        print(json.dumps({"snapshot_id": snapshot_id, "status": "failed",
                          "reason": str(exc)[:500]}))
        return 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _clone_root() -> str:
    root = Path(CLONE_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


if __name__ == "__main__":
    sys.exit(main())
