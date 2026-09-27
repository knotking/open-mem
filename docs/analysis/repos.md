# Analysing a repository

Paste a GitHub URL and get four saved reports about **one commit**: design, code
quality, functional bugs, dependencies. Each is an artifact from a named
generator, so it records the prompt and model that produced it, goes stale when
either changes, and is erased with its sources.

---

## The one constraint everything follows from

**A snapshot is `owner/repo@sha`, and it is comparable to nothing.**

That is a smaller feature than it sounds and a much clearer one. There is no
incremental crawl, no watermark, and no diff against a previous snapshot —
because "has quality improved since March" needs a second snapshot *and* a model
of what comparable means, and building half of that leaves a corpus of reports
that look comparable and are not.

Four things follow:

- **Re-analysing a sha costs nothing.** `(project, url, sha)` is UNIQUE, so a
  second request returns the first snapshot. Reports add the other half: an
  artifact is keyed by `generator_version`, so an unchanged prompt against an
  unchanged snapshot re-uses the report rather than paying to reproduce it.
- **A branch is resolved once and the sha is stored.** `main` moves; a report
  attributed to it is one nobody can reproduce.
- **Snapshots of one repository share a case and nothing else.** The case
  correlates them. Nothing claims they can be read against each other.
- **A snapshot expires like anything else**, because it is a memory.

## What actually happens

```
POST /api/v1/repos/analyze  {repo_url, ref?}
  │  resolve the ref to a commit sha (one GitHub call)
  │  upsert case  ·  create snapshot memory  ·  enqueue
  ▼
Cloud Run Job  analysis/repo/
  1  git fetch --depth 1 <sha>
  2  graphify extract . --code-only     → graph.json
  3  read manifests → query OSV
  4  select a bounded set of files
  5  write records back through the public write API
  ▼
derive × 4  →  repo_design · repo_quality · repo_bugs · repo_deps
```

Nothing in the middle is new machinery: step 5 is the ordinary write path and
the derives are the existing endpoint with new generator names.

## The graph is the compression

A repository exceeds every ceiling in [limits](../limit.md) by orders of
magnitude — 200 archive members, two million indexed characters, twenty-four
extraction windows. **Handing a model a repository is not something this
pipeline can do at any setting**, and raising a ceiling to admit one only moves
the failure later.

So the repository is reduced first, by `graphify`, with tree-sitter and no model
at all. What the analysers read is:

| Read | Why |
|---|---|
| A **digest** of the graph | Hubs, module-to-module edges, communities — counted, not described. A few kilobytes. |
| **Manifests** | Dependency findings are worthless without them. |
| **Entry points** | They say what the thing is. |
| **The most connected modules**, by graph degree | Where a defect costs most and where the design actually lives. |

Capped at 40 files. The full selection, with a reason per file, is stored on the
snapshot and rendered on the screen — because a report over twelve files and one
over forty are different claims, and **a thin report with no file count reads as
a clean bill of health**.

> **This was learned the hard way.** The first implementation stored the raw
> `graph.json` — a megabyte of node-link JSON — as a record the analysers read.
> It filled every extraction window and all four reports came back as an echo of
> their own input. It looks like a model failure and is a units failure: the
> graph was meant to be smaller than the code, and raw it is larger. The raw
> graph is now kept as provenance in a sibling memory, where it cannot crowd out
> the material the reports are actually built from.

## Vulnerabilities come from OSV, never from the model

Asked whether a version is affected, a model produces fluent, plausible, wrong
CVE numbers — and a wrong advisory is a security claim about somebody's
software.

So the job queries [OSV](https://osv.dev) for the exact declared versions, and
the dependency prompt forbids reporting any advisory absent from that result.
**An unreachable OSV is recorded as `unavailable`, never as an empty result**,
and the console says *unchecked*: "no advisories" and "nobody asked" must not
render the same.

What the model is left to judge is what the manifests plainly show — unpinned
ranges, two majors of one package, a direct dependency declared only
transitively, licence conflicts.

## Findings, and the shape that makes them possible

The bug report described the codebase instead of answering it, four runs
running, and no wording fixed it. The cause was never the prompt: the envelope's
one open field is `summary`, documented everywhere as *what the document
covers*, so a prompt asking for located defects was answered with a description
of the material.

A generator may now name the **shape** it needs. `data_type: code_review` puts a
`findings` array in the envelope:

```jsonc
{"file": "requests/sessions.py", "symbol": "SessionRedirectMixin",
 "severity": "medium", "statement": "…", "trigger": "…"}
```

`file` is required, because a finding that cannot say where it is cannot be
checked. **An empty array is a first-class answer** and the honest one for code
with no locatable defect — `[]` and a missing key are different claims, and
rendering them the same is how a report that never ran reads as a clean bill of
health.

## Running it

**The job is not on the API image and not in the job loop.** Every other Cloud
Run job runs `open-mem` and is redeployed with the API so it cannot drift. This
one carries `git`, `graphifyy` and 37 tree-sitter grammars — keeping that out of
the API image is the entire reason it exists — so it has its own Dockerfile,
tag, and deploy. See the [deploy skill](../../.claude/skills/deploy-gcp/SKILL.md).

```bash
cd analysis/repo && PRODUCER_ID=<prd_…> ./deploy.sh <tag>
```

`REPO_ANALYSIS_JOB` switches the feature on. **Empty is the correct default**:
the job clones arbitrary public repositories and spends four model calls per
snapshot. A snapshot requested without it is recorded and immediately marked
`failed` with that as its reason — never left `pending`, which reads as still
running.

The job holds **no database credential**. It writes back through the ordinary
public write API like any other producer, so everything it stores goes through
admission, sniffing, parsing and the ACL.

## What this deliberately does not do

Named so they are decisions rather than omissions.

- **No trend across snapshots.** See the constraint above.
- **The code graph is not merged into the entity graph.** open-mem's vocabulary
  is `person, organization, location, product, event, topic, other`; tens of
  thousands of code symbols forced into it produce exactly the graph that
  [templates](../ingestion/graph-templates.md) warns about — one that fills up
  and says nothing. The cost of that choice, stated plainly: you cannot ask
  "which meeting discussed this module" until it is built.
- **GitHub only, public only.** A private repo is refused by name, not by a
  clone that fails with exit 128.
- **No auto-analysis on push.** That is a webhook feature and it fights
  "per snapshot only".
- **It reports; it does not edit.**
