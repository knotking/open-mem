# Compaction

Fold a memory's members into a derived artifact so the working set stops
growing — **without losing anything**.

## The one thing that decides the design

mem0 reconciles by overwriting: when a new memory contradicts an old one, the
old one is replaced and gone. memdog cannot do that, and not as a matter of
taste. The [temporal graph](graph.md) shipped on the opposite premise — a claim
is **closed**, never replaced, so `as_of` can still answer what was believed in
March. A compaction that destroyed its inputs would make `as_of` lie about
everything it touched.

[memories.md](memories.md) already stated the rule:

> Archived originals remain searchable with `include_archived=true`, so
> compression is a **retrieval default rather than a deletion**.

That sentence *is* the feature. Compaction changes what retrieval returns by
default. It does not change what exists.

### And it is about volume, not truth

| Question | Mechanism |
|---|---|
| *Is this still true?* | `entity_facts` — supersession, with a validity window and evidence |
| *Do we still need all of it in the working set?* | Compaction |

A compaction that decided facts were obsolete would be a second, weaker
supersession with neither of those. It reads members and writes a summary; it
does not adjudicate.

## Algorithms

Served by `GET /api/v1/compaction/algorithms`, so a console never holds a second
copy that can drift from what the server will run.

| | Needs a model | What it does |
|---|---|---|
| **`dedupe`** | No | Archives members byte-identical to a newer one, keeping the most recent |
| **`summarize`** | Yes | Folds members into one artifact recording every source it drew on, then archives them |

`dedupe` is first deliberately. Most of what a corpus accumulates is the same
record written twice — a re-crawl, a re-import — and noticing that needs no
model. Deterministic before probabilistic, as everywhere else here.

`summarize` **refuses rather than degrading** when no extractor is configured,
and says `dedupe` needs none. A summary produced by a fallback heuristic would
be a worse summary presented as the same thing.

## A job is a memory, an algorithm and a schedule

```jsonc
POST /api/v1/compaction/jobs
{ "project_id": "prj_…",
  "name": "nightly de-duplication",
  "memory_id": "mem_…",
  "algorithm": "dedupe",
  "schedule": { "type": "interval", "every_seconds": 86400 } }
```

A memory rather than a filter, because that is the container people already
think in and the axis the corpus is organised on.

### It is created stopped, and previewing is a gate

```bash
POST /api/v1/compaction/jobs/{id}/preview   # writes nothing
POST /api/v1/compaction/jobs/{id}/enabled   # 409 until this version is previewed
```

Same code path with its writes withheld, so what a preview reports is what would
actually happen — the crawler's dry-run discipline, and it matters more here
because the live version moves records out of the working set. **A compaction
nobody has looked at is one that empties a memory quietly.**

Editing what a job would do — its memory, algorithm or options — bumps
`config_version`, drops the preview and stops the job. Renaming does none of
those.

## What a run records

`GET /api/v1/compaction/jobs/{id}/runs` — the questions somebody deciding
whether to keep a job would actually ask:

| | |
|---|---|
| `considered` | members it looked at |
| `archived` | members folded out of the working set |
| `artifacts` | summaries written |
| `bytes_before` / `bytes_after` | how much smaller the working set is |
| `model_calls` | what it cost — `0` for `dedupe` |

## Two guarantees worth stating

**The summary takes the ACL of its most restrictive source.** A summary spanning
a private record and two org ones is private. `acl.strictest` applies to every
other derived artifact, and a compaction artifact is not special — otherwise
compaction becomes a way to widen visibility by summarising.

**The summary records where each part came from, with offsets.**
`artifact_sources` carries `span_start` and `span_end`, which is what lets a
citation open its source at the sentence. Without them a summary can name what
it read and not point into it, and every citation in a compacted memory silently
degrades to a document-level reference — which reads as working.

That join is also what makes erasure possible: it exists to answer *which
artifacts absorbed this?*, so erasing a compacted member still reaches the
summary that absorbed it.

## Endpoints

| Method | Path |
|---|---|
| `GET` | `/api/v1/compaction/algorithms` |
| `POST` · `PATCH` · `DELETE` | `/api/v1/compaction/jobs[/{id}]` |
| `GET` | `/api/v1/projects/{id}/compaction/jobs` |
| `POST` | `/api/v1/compaction/jobs/{id}/preview` — writes nothing |
| `POST` | `/api/v1/compaction/jobs/{id}/run` — archives, never deletes |
| `POST` | `/api/v1/compaction/jobs/{id}/enabled` |
| `GET` | `/api/v1/compaction/jobs/{id}/runs` |

Scheduled jobs ride the same minute sweep the alerts use rather than adding a
fourth Cloud Run job: they are due at most daily, so a per-minute pass costs one
indexed lookup that usually returns nothing.

## Not built

**TTL is still not enforced.** `memory_types.ttl_seconds` and `on_expiry` are
stored and `effective_expiry()` is computed, but nothing sweeps — a
`conversation` memory with a one-hour TTL is still there next year, and
`orphan_delete` and `archive` have never run. Compaction is explicit and
scheduled; **expiry is a separate mechanism that does not exist yet**, and the
plan for it is in `.claude/plans/compaction.md`.
