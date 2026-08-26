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
| `source_id`, `source_version` | which content produced it |
| `generator_id`, `generator_version` | which prompt / model / schema / parser / chunker |
| `produced_at` | transaction time |
| `status` | `current` · `stale` · `failed` |

An artifact is stale when either version moves. A sweep marks affected rows; W7 rebuilds by
priority. Without this, "we changed the summarization prompt" means either re-running the entire
corpus or living with permanent inconsistency — and at 50M rows the first option is not available.

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
