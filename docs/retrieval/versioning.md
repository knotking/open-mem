# Versioning & Staleness

Versioning exists in exactly two places today: data items get a new version per mutation with
diff tracking, and the temporal graph stamps facts with `valid_at` / `invalid_at`. Everything
*between* — every chunk, embedding, entity, claim, summary and facet — is produced once and never
reconsidered.

## The rule

A derived artifact is a function of **two** inputs:

```
artifact = f(source_version, generator_version)
```

Change either and the artifact is stale. Without recording both, "is this embedding still valid?"
is unanswerable — which is why config changes today silently apply only to future data.

**Generator version is compound**, and every component independently invalidates output: agent
prompt · model identity *and weights* · output schema · embedding model · normalization schema ·
chunking strategy · **parser** (different parsers extract different text from the same PDF).

## Four version surfaces

| Surface | Changes when | Status |
|---------|--------------|--------|
| **Source content** | upstream doc revised, message edited, ticket updated | partial — data items version; connector revisions don't upsert |
| **Generators** | prompt, model, schema, parser or chunker changes | **untracked** |
| **Schemas** | normalization target evolves | designed — versioned, never mutated in place |
| **Facts** | world changes, or we learn we were wrong | partial — valid-time only |

## Embeddings: the one that is actually broken

Embeddings are not merely stale-able — they are **incomparable across models**. A vector from one
embedding model and one from another live in different spaces with different dimensionality.
Cosine similarity between them is a number, and that number is meaningless.

**The documented fallback chain switches between a local embedder and a cloud one.** For
generation that is graceful degradation; for embeddings it silently corrupts ranking, with no
error raised and no way to identify affected rows after the fact.

```
embed request ──┬── normal ──▶ local embedder   (space A, dim 768)
                └── outage ──▶ cloud embedder   (space B, dim 3072)
                                     ↓
                            ONE vector index — mixed spaces
                                     ↓
                            similarity search returns nonsense
```

Three consequences:

1. Every embedding row must carry `model_id` and `dim`, and search must filter to a single space.
   Without the column, affected rows cannot even be identified retroactively.
2. **Embeddings must not silently fall back.** If the primary embedder is unavailable, defer with
   `embed_status = pending` — never substitute a different space.
3. Changing embedding model is a **corpus-wide migration**: build the new index alongside, then
   swap. Never mix.

## Knowledge graph: valid time exists, transaction time does not

| Question | Needs |
|----------|-------|
| "Who was CEO in 2024?" | valid time — **have it** |
| "What did we believe on March 1?" | transaction time — partial |
| "When did we learn we were wrong?" | both — **gap** |

**Nothing invalidates facts when a source is revised.** If a document is superseded, facts
extracted from the old version keep `invalid_at = null` — they remain true forever. Temporal
queries then return confidently wrong answers, which is worse than returning nothing. Source
revision (W8) must set `invalid_at` on facts derived from the superseded version.

A harder case sits behind it: **entity resolution decisions are themselves versioned claims.** If
the graph merges two people and later learns they are distinct, an un-merge is required — and
merges are lossy. Recording the merge as a retractable, evidence-bearing decision rather than a
destructive edit is the only way this stays recoverable.

## The staleness model

The concrete deliverable — what makes W7 targetable rather than a full-corpus rebuild:

| Field | Purpose |
|-------|---------|
| `source_id`, `source_version` | which content produced it — a **list** where several sources contributed |
| `generator_version` | what was **intended** — FK to the immutable generator registry |
| `served_by_model`, `fallback_depth` | what **actually ran**, because the chain may have substituted |
| `produced_at` | transaction time |
| `status` | `current` · `superseded` · `stale` · `failed` |

An artifact is stale when either version moves. A sweep marks affected rows; W7 rebuilds by
priority. Without this, "we changed the summarization prompt" means either re-running the entire
corpus or living with permanent inconsistency — and at 50M rows the first option is not available.

## The fingerprint is a change detector, not a record

`generator_version = sha256(canonical_json({prompt, model_id, schema, parser_version, ...}))` tells
you *that* two artifacts were produced differently. It cannot tell you *how*.

Knowing an artifact came from `a3f2…` and another from `b7c1…` does not let you debug a bad
summary, reproduce a result, roll back a regression, or answer "what instructions produced this
clinical summary?" — which is a real question in a regulated context.

### A generator registry

Immutable and append-only, keyed by the fingerprint:

```
generator_versions
  generator_version      PK — the fingerprint
  agent_id
  prompt_text            the actual prompt, not a reference to a mutable one
  model_id, provider     the CONFIGURED model
  output_schema
  parser_version, chunker_version, embedder_id
  processing_flags
  created_at, created_by
```

Every derived artifact holds a foreign key into it. The full configuration that produced any
artifact in the corpus is always reconstructible, and a prompt change is a new row rather than an
edit — a mutable prompt breaks the guarantee the fingerprint exists to provide.

---

## The bug: the fingerprint records intent, not what happened

This one matters more than it looks.

The fingerprint is computed from the **configured** model. But the fallback chain may have served a
**different** one — a busy local GPU falls through to a cloud provider, and the artifact is written
as though nothing happened.

```
generator_version = a3f2…    ← says "gemma, medium tier"
actually served   = a cloud model, two hops down the chain
recorded          = nothing
```

So two artifacts with **identical fingerprints** can have been produced by different models. That
breaks the core assumption of the staleness model: that equal fingerprints imply equivalent
provenance.

The usage record already captures `serving_model` — but on the *inference event*, not on the
*artifact*. The artifact is what survives, and it is what a rebuild decision reads.

### Fix

Record both on the artifact:

| Field | Meaning |
|-------|---------|
| `generator_version` | What was **intended** — the fingerprint, FK to the registry |
| `served_by_model` | What **actually ran** |
| `fallback_depth` | `0` means the primary served it |
| `under_fallback` | Derived — `served_by_model ≠ configured` |

This makes "artifacts produced under fallback" a **selector for reprocess**, which is a genuinely
useful cleanup: after an outage, rebuild exactly the artifacts that degraded, and nothing else.

---

## Derived artifacts need history, not just current state

Reprocess overwrites. That is the obvious implementation and it loses three things:

- **Rollback.** A prompt change that made output worse cannot be undone
- **Comparison.** Evaluating whether a change helped requires old and new side by side
- **The regulated question.** "What did the system say in March?" has no answer

Keep the **previous** version of each derived artifact by default, with retention configurable per
artifact type. Reprocess writes a new version and demotes the old rather than replacing it.

Storage is the objection, and it is real at fifty million rows — so make it a policy: summaries and
claims keep history, chunk embeddings do not. The expensive ones to regenerate are the cheap ones
to keep.

## Two more leaks

- **Compressed summaries.** A summary derived from N items goes stale when any one is revised or
  deleted — and deleting the original does not remove its content from the prose. Summaries must
  record their `(source_id, version)` list.
- **Query provenance.** A cited RAG answer cannot be reproduced or audited unless the index
  versions, model versions and retrieval parameters used are recorded with it.

## Deletion vs history

Hard deletion breaks version history; soft deletion fails erasure requests. The workable split is
to **hard-delete content and retain a metadata-only tombstone** — id, versions, timestamps,
reason — so lineage stays intact and no user content survives.

---

## Requirements

- **FR-VER-1** Every derived artifact MUST record the complete configuration fingerprint that
  produced it, as a reference to an **immutable** generator registry.
- **FR-VER-2** The generator registry MUST store the full configuration — prompt text, model,
  schema, parser and chunker versions — so any artifact's provenance is reconstructible. Registry
  entries MUST NOT be edited; a change is a new entry.
- **FR-VER-3** Every derived artifact MUST record the model that **actually served** it, not only
  the one configured, together with the fallback depth reached.
- **FR-VER-4** Artifacts produced under fallback MUST be identifiable as a selector for reprocess.
- **FR-VER-5** Derived artifacts MUST retain at least the previous version, with retention
  configurable per artifact type.
- **FR-VER-6** Reprocess MUST write a new version and supersede the old, not overwrite it.
