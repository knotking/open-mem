# Repo analysis

**Point a project at a GitHub repository at one commit, build a code graph with
[Graphify](https://github.com/Graphify-Labs/graphify), and derive four saved
reports from it — design, code quality, functional bugs, dependency issues —
each pinned to that snapshot and to nothing else.**

Status: plan. No code written.

---

## 1. What "per snapshot only" decides

The user said it as a scope limit; it is actually the load-bearing constraint,
and naming what it rules out is most of the design:

- **No incremental crawl.** No watermark, no etag, no "what changed since". A
  snapshot is `owner/repo@<40-char sha>` and it is analysed whole or not at all.
- **No cross-snapshot diffing.** "Did quality improve since last month" is a
  different feature with a different data model. Not built, not half-built.
- **Re-analysing the same sha is idempotent.** Same sha + same
  `generator_version` returns the existing artifact rather than spending again.
  This falls out of the generators registry for free (§3) and is the main
  reason to reuse it rather than invent a reports table.
- **A snapshot expires like anything else.** It is a memory, so retention
  already applies. A repo analysed once for a due-diligence question should not
  sit in the corpus forever by default.

---

## 2. What already exists, and must be reused

Very little of this is new mechanism. Building it as new mechanism is the main
way it goes wrong.

| Need | Existing thing | Where |
|---|---|---|
| "What is this about" across snapshots | **Case**, `case_type="repo"` | `cases.py` |
| One snapshot, with a retention policy | **Memory** | `memories.py` |
| The saved report | **Artifact** + **generator** | `derive.py`, `0001_spine.sql:232` |
| Report invalidated when its prompt changes | `generator_version` hashes the prompt | `derive.py:134` |
| Re-run a stale report | `/reprocess` | `reprocess.py` |
| Getting bytes in without the API touching them | `POST /uploads`, `Stored` ref | `uploads.py` |
| Long job that must not run in the API | Cloud Run **Job** | `agents/openclaw/` |
| Erasure cascading into reports | `artifact_sources` join table | `0001_spine.sql:254` |

**The shape is already `derive`.** "Take a memory's members, run a named
generator with a hashed prompt, write an artifact carrying the strictest ACL of
its sources" is exactly a repo report. Four new entries in `derive.GENERATORS`
is most of the analysis feature.

---

## 3. Decisions that need making

### 3.1 Where Graphify runs — **a Cloud Run Job, not the API**

Graphify is a Python CLI (`uv tool install graphifyy`) carrying 37 tree-sitter
grammars, and it needs the repo on local disk, which means `git` in the image.

It must not run in the API process. `docs/ingestion/uploads.md` already states
why for a smaller case: the API is the sole writer of record on min 2 / max 10
replicas and **autoscales on request rate**, so a six-minute CPU-bound parse
counts the same as a 20 ms write and starves the pool without triggering a
scale-up. A repo clone plus a full AST pass is worse than an upload.

**Recommendation: a Job, `analysis/repo/`, modelled on `agents/openclaw/`.**
That pattern landed on `main` two commits ago and is the precedent to copy —
including the reason OpenClaw is a Job rather than a service. The job clones,
runs `graphify extract`, and writes results back through the ordinary public
write API with a scoped key. Nothing about the API changes to accommodate it.

Rejected: adding `graphifyy` to `api/pyproject.toml`. It drags tree-sitter
grammars and a `git` binary into an image that `pyproject.toml` has already
refused ffmpeg for, on the same grounds.

### 3.2 Does the code graph merge into mem-dog's entity graph?

This is the real fork, and it is worth stating plainly because it is easy to
answer wrongly by reflex.

mem-dog's entity vocabulary is `person, organization, location, product, event,
topic, other` (`predicates.py:38`). Nothing code-shaped. Graphify emits modules,
functions, classes, packages joined by `calls`, `imports`, `inherits`,
`references`.

**Option A — extend the vocabulary.** New entity types and predicates, so a
design document can be traversed to the function it describes. This is the
sanctioned path: `predicates.py` says templates "may propose additions" to the
registry and that "a new node type should require deciding which edges may
touch it."

**Option B — the code graph is a stored record, not entity-graph content.**
`graph.json` is written as an ordinary record with the snapshot; the analysers
read it; the entity graph is untouched.

**Recommendation: B for v1, A named as the follow-on.** Three reasons:

1. `graph_templates.py` opens by warning about exactly this failure — a graph
   that "fills up and says nothing" when the wrong vocabulary is forced on
   content. A mid-size repo is tens of thousands of symbols; merged in, they
   swamp a graph built for people and organisations, and every `related_to`
   between a function and a person is noise nobody can filter back out.
2. The user asked for **reports**, not graph browsing. B delivers the requested
   scope; A is a graph feature wearing a repo-analysis hat.
3. B is reversible. A is a vocabulary change, and vocabulary is the one thing
   here that is expensive to walk back once edges exist.

The cost of B, stated honestly: you cannot ask "which meeting discussed this
module" until A is built.

### 3.3 Dependency issues need a fact source, not a model

"Package dependency issues" splits into two kinds and only one is a judgement:

- **Facts** — is this version affected by a known advisory. A model asked this
  will produce fluent, plausible, wrong CVE numbers. This is the hallucinated-edge
  failure the graph code is careful about, in a domain where being wrong is a
  security claim about somebody's software.
- **Judgement** — unpinned ranges, abandoned packages, license incompatibility,
  two majors of the same package in one lockfile, a direct dependency on
  something only used transitively.

**Recommendation: query [OSV.dev](https://osv.dev) in the job for the facts, and
let the generator reason only over judgement plus the OSV result.** The prompt
must forbid asserting a vulnerability not present in the supplied OSV data.

If OSV is out of scope for v1, then the dependency generator must be **renamed**
to what it actually does — dependency *hygiene*, not vulnerabilities — rather
than quietly producing CVE-shaped guesses.

### 3.4 A repo exceeds every ingestion ceiling

Per `docs/limit.md`, which this feature runs straight into:

| Ceiling | Value | Effect on a repo |
|---|---|---|
| `MAX_ARCHIVE_MEMBERS` | 200 | A tarball through the parse path expands 200 files. Useless. |
| `MAX_TEXT_CHARS` | 2M | A mid-size repo far exceeds this as one blob. |
| `MAX_EXTRACT_WINDOWS` × window | 24 × 40k ≈ 960k chars | The analysis model sees ~960k chars, not the repo. |
| `derive(max_members=...)` | 200 | Only 200 records feed one report. |

**So the graph is not a nice-to-have, it is the compression.** The analysers
read `graph.json` plus a bounded, deliberately chosen set of files — manifests,
entry points, the largest/most-connected modules — never the repo. Any design
that tries to feed the repo to a model is already over budget before it starts.

**Do not raise these ceilings for this feature.** `large-documents.md:88` argues
the general case: a limit large enough to admit the thing admits it into a
pipeline that cannot carry it.

### 3.5 Private repos

The clone needs a token. It belongs in the existing connection/credential path
(`connections.py`, envelope-encrypted under `MEMDOG_MASTER_KEY`), passed to the
job, never written into a record and never into the report. **v1 may be
public-repos-only** if that keeps it smaller — but it should refuse a private
URL with that sentence, not fail at `git clone` with a 128.

---

## 4. The flow

```
POST /api/v1/repos/analyze {repo_url, ref?}
  │  resolve ref -> commit sha (GitHub API, one call)
  │  upsert case      repo / github.com/owner/repo
  │  create memory    snapshot owner/repo@sha        <- idempotent on sha
  │  enqueue job
  ▼
Cloud Run Job  analysis/repo/
  1  git clone --depth 1 <url> && git checkout <sha>
  2  graphify extract .                -> graphify-out/{graph.json,GRAPH_REPORT.md}
  3  read manifests; query OSV for each dependency
  4  write back through the public API:
       - graph.json           (record, Stored ref via /uploads)
       - GRAPH_REPORT.md      (record)
       - manifests + OSV result (record)
       - selected source files (bounded set, §3.4)
     all joined to the snapshot memory and the repo case
  ▼
POST /api/v1/memories/{snapshot}/derive  × 4
     repo-design · repo-quality · repo-bugs · repo-deps
  ▼
artifacts, ACL = strictest of sources, generator_version = hash(prompt, model, schema)
```

Nothing in the middle column is new machinery. Step 4 is the ordinary write
path; the derive calls are the existing endpoint with new generator names.

---

## 5. Implementation steps

### Phase 1 — the snapshot exists and is stored

1. [x] **`api/src/memdog/repos.py`** *(new)* — resolve a GitHub URL and ref to a
   commit sha; validate the URL shape the way `youtube.py:47` validates a video
   id (an exact pattern, not a permissive parse, because this string reaches a
   shell); upsert the case; create the snapshot memory; return existing on a
   repeat sha.
2. [x] **`api/src/memdog/migrations/0051_repo_snapshots.sql`** *(new)* — see §6.
   Additive only.
3. [x] **`api/src/memdog/app.py`** — `POST /api/v1/repos/analyze`,
   `GET /api/v1/repos/{case_id}/snapshots`, `GET /api/v1/repos/snapshots/{id}`.
   Requires `DATA_WRITE`; quota checked before the job is enqueued, never after
   (an enqueued job cannot be un-spent).
4. [x] **`analysis/repo/`** *(new)* — `Dockerfile`, `job.sh`, `analyze.py`, modelled
   on `agents/openclaw/`. Clone, graphify, OSV, write back. Bounded by a wall
   clock and a repo-size guard that refuses before cloning where it can.

### Phase 2 — the four reports

5. [x] **`api/src/memdog/derive.py`** — four entries in `GENERATORS`:

   | Generator | Reads | Answers |
   |---|---|---|
   | `repo-design` | graph.json communities, entry points, module edges | layering, coupling, where the seams are and where they leak |
   | `repo-quality` | graph metrics + selected files | duplication, god modules, dead nodes, test coverage shape |
   | `repo-bugs` | selected files + call paths | concrete defects, each citing file and line |
   | `repo-deps` | manifests + OSV result | advisories (from OSV only), unpinned ranges, abandonment, licence conflicts |

   Each is `archivable: False` — a report must never archive the code it read.

6. [x] **Prompts** — extractive and citation-bound, in the house style of
   `multimodal.PROMPTS`: a finding that cannot name a file and a symbol is not a
   finding. `repo-bugs` in particular must be told that a suspected bug it
   cannot locate is to be omitted, not described.

### Phase 3 — the console

7. [x] **`ui/components/`** — invoke the `console-ui` skill first, per `CLAUDE.md`.
   A repo screen listing snapshots by sha and date; a snapshot screen showing
   the four reports, what was skipped and why, and what it cost. Per the skill:
   the test is whether someone can run an analysis *and see whether it worked*
   on one screen.

### Phase 4 — docs

8. [ ] `docs/analysis/repos.md` — what it does, the four reports, and what it
   deliberately does not do (no cross-snapshot trend, no merged code graph).
9. [ ] `docs/limit.md` — a row for repo snapshots; this feature is a live example of
   §3.4 and belongs there.
10. [ ] `docs/README.md` — index entry, both tables.
11. [ ] `CHANGELOG.md` — via the `changelog` skill, after each commit.

---

## 6. Data model

One migration, additive:

```sql
-- 0051_repo_snapshots.sql
CREATE TABLE repo_snapshots (
    snapshot_id   text PRIMARY KEY,
    org_id        text NOT NULL REFERENCES organizations ON DELETE CASCADE,
    project_id    text NOT NULL REFERENCES projects ON DELETE CASCADE,
    case_id       text NOT NULL REFERENCES cases ON DELETE CASCADE,
    memory_id     text NOT NULL REFERENCES memories ON DELETE CASCADE,
    repo_url      text NOT NULL,
    commit_sha    text NOT NULL,
    ref           text,
    status        text NOT NULL,   -- pending running complete failed
    reason        text,            -- why it failed, or what it skipped
    stats         jsonb NOT NULL DEFAULT '{}',  -- files, symbols, edges, languages, bytes
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, repo_url, commit_sha)   -- idempotency, per snapshot
);
```

The `UNIQUE` is the "per snapshot only" rule expressed where it cannot be
forgotten. Reports are artifacts and need no table.

---

## 7. Tests

- `test_repos.py` — URL/ref parsing, including the rejections: non-GitHub host,
  a ref that resolves to nothing, a path that is not `owner/repo`.
- Idempotency: two analyses of one sha produce one snapshot and re-use artifacts.
- A second sha of the same repo produces a second snapshot under the same case,
  and **does not** touch the first's artifacts.
- ACL: a snapshot's reports carry the strictest ACL of their sources; a member
  who cannot see the source records cannot see the report.
- Erasure: deleting the snapshot cascades through `artifact_sources`.
- `repo-deps` asserts no advisory absent from the supplied OSV fixture — the
  regression test for §3.3, and the one most worth having.
- Job-side: a fixture repo, asserting graph.json shape and that the bounded file
  set is bounded.

---

## 8. Out of scope for v1

Named so they are decisions rather than omissions: cross-snapshot trends;
merging the code graph into the entity graph (§3.2 option A); non-GitHub hosts;
auto-analysis on push (a webhook feature, and it fights "per snapshot only");
fixes or patches — this reports, it does not edit.

---

## 9. Open questions

1. **Private repos in v1?** Changes credential work and the console. Public-only
   is a smaller v1 and a clear refusal message.
2. **OSV in v1** (§3.3), or ship dependency *hygiene* and say so?
3. **Who runs it** — a person on demand only, or may a standing query trigger it?
   The latter makes cost unbounded and I would not do it in v1.
4. **What bounds the file set** the analysers read (§3.4)? Proposal: manifests,
   plus the 100 most-connected nodes by graph degree, plus entry points — but
   this is a quality knob and worth your view.


---

## 10. Implementation log

Phases 1-3 implemented. Phase 4 (docs) deliberately left for `/release`, and
tests for `/test` -- `/implement` does neither.

**Assumptions taken on §9, since implementing needed answers.** Each is
reversible and each is visible in the code:

1. **Public repositories only.** A private repo is refused by name at
   `resolve_commit`, with the sentence that says so, rather than failing at
   `git clone` with exit 128.
2. **OSV is in.** `analyze.advisories()` queries `api.osv.dev/v1/querybatch`
   and the `repo_deps` prompt forbids reporting any advisory absent from that
   result. An unreachable OSV is recorded as `unavailable`, never as an empty
   result, and the console says "unchecked" rather than showing nothing.
3. **On demand only.** Nothing triggers an analysis but a person.
4. **File selection** is manifests + readme + entry points + the most-connected
   modules by graph degree, capped at `MAX_SELECTED_FILES` (40). The full list
   with a reason per file is stored on the snapshot and rendered on the screen.

**Three things built that the plan did not name**, each because the plan was
incomplete rather than because scope grew:

- **`GET /api/v1/projects/{id}/repos`** (`repos.repos_for_project`). The plan's
  endpoints could show a repository's snapshots but nothing could list the
  repositories, so the screen had no entry point. It carries the latest
  snapshot's status on the row, because a repo whose last run failed and one
  never analysed are otherwise the same blank row.
- **`repos.RepoAnalysisWorker` and `REPO_ANALYSIS_JOB`.** The plan said "enqueue
  job" without saying what consumes the message. Without a consumer a snapshot
  would sit `pending` forever, which reads as still running -- so the worker is
  registered whether or not a job is configured, and marks the snapshot failed
  with that as the reason when it is not.
- **`quota.estimate_repo_analysis`.** The plan said to charge before enqueueing
  but named no number. Four generations at `GENERATION_ESTIMATE`.

**Verified:** `memdog.app` imports and the three routes register in the order
that keeps `repos/snapshots/{id}` from being read as a case id; `npm run verify`
clean (proxy allow-list covers all 128 call sites, typecheck, tests, build).
Nothing has been run against a live repository -- no job image is built and
`REPO_ANALYSIS_JOB` is unset, so a snapshot requested today is recorded and
marked failed with that reason.
