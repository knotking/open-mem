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
    result = subprocess.run(
        ["graphify", "extract", ".", "--output", str(out)],
        cwd=repo, capture_output=True, text=True, timeout=GRAPHIFY_TIMEOUT,
    )
    graph_path = out / "graph.json"
    if not graph_path.exists():
        raise AnalysisFailed(
            "graphify produced no graph.json: "
            f"{(result.stderr or result.stdout or '').strip()[:400]}"
        )
    try:
        data = json.loads(graph_path.read_text())
    except json.JSONDecodeError as exc:
        raise AnalysisFailed(f"graphify wrote an unreadable graph.json: {exc}") from exc

    report_path = out / "GRAPH_REPORT.md"
    report = report_path.read_text() if report_path.exists() else ""
    return data, report


def degree(data: dict) -> dict[str, int]:
    """How connected each node is, counting both directions.

    Degree is the selection signal because it is the one the graph actually
    supports. "Most important file" is not knowable from an AST; "the module
    the most other modules reach into" is, and it is the same file often enough
    to be worth reading first.
    """
    counts: dict[str, int] = {}
    for edge in data.get("edges") or []:
        for end in ("source", "target", "from", "to"):
            node = edge.get(end)
            if isinstance(node, str):
                counts[node] = counts.get(node, 0) + 1
    return counts


def node_paths(data: dict) -> dict[str, str]:
    """Node id -> the file it came from, where graphify recorded one."""
    paths: dict[str, str] = {}
    for node in data.get("nodes") or []:
        node_id = node.get("id") or node.get("name")
        location = node.get("file") or node.get("path") or (node.get("location") or {}).get("file")
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
        response = self._client.post(
            f"{self._base}/api/v1/write",
            json={"producer_id": self._producer, "items": items},
        )
        if response.status_code >= 400:
            raise AnalysisFailed(
                f"write failed ({response.status_code}): {response.text[:300]}"
            )
        return response.json()

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
        log.info("cloning %s@%s", repo_url, sha[:7])
        repo = clone(repo_url, sha, workdir)

        log.info("graphing")
        data, report = graph(repo)
        nodes, edges = len(data.get("nodes") or []), len(data.get("edges") or [])
        log.info("graph: %d nodes, %d edges", nodes, edges)

        selected, reasons = select_files(repo, data)
        deps = dependencies(repo)
        osv = advisories(deps)
        log.info("selected %d files, %d dependencies, OSV %s",
                 len(selected), len(deps), osv.get("status"))

        items = [
            item(f"{repo_url}@{sha}/graph.json", json.dumps(data)[:1_800_000],
                 memory_key=memory_key, repo=case_external,
                 data_type="code_graph", tags=["repo:graph"]),
            item(f"{repo_url}@{sha}/dependencies.json",
                 json.dumps({"dependencies": deps, "vulnerabilities": osv}, indent=2),
                 memory_key=memory_key, repo=case_external,
                 data_type="dependencies", tags=["repo:dependencies"]),
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

        log.info("writing %d records", len(items))
        # Chunked to stay inside the write endpoint's item and payload caps
        # rather than discovering them as a 413 after the expensive part.
        for start in range(0, len(items), 100):
            api.write(items[start:start + 100])

        log.info("deriving reports")
        reports = [api.derive(memory_id, g) for g in
                   ("repo_design", "repo_quality", "repo_bugs", "repo_deps")]

        print(json.dumps({
            "snapshot_id": snapshot_id, "status": "complete",
            "stats": {
                "nodes": nodes, "edges": edges,
                "files_selected": len(selected), "selection": reasons,
                "dependencies": len(deps),
                "osv_status": osv.get("status"),
                "osv_affected": osv.get("affected", 0),
                "records_written": len(items),
                "graphify_version": GRAPHIFY_VERSION,
            },
            "reports": [r.get("artifact_id") or r.get("error") for r in reports],
        }))
        return 0
    except AnalysisFailed as exc:
        log.error("analysis failed: %s", exc)
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
