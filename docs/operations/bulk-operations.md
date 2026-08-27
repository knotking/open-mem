# Bulk Operations

A batch endpoint sounds like an API convenience. It is actually a capacity control, because
**the write is the cheap part**.

## The amplification problem

A batch of 10,000 records does not create 10,000 rows. It creates:

```
10,000 rows written
   → 10,000 enrichment jobs queued
      → 10,000 LLM calls           ← the actual cost
      → 10,000+ embeddings
      → entity extraction, graph writes, index builds
```

A batch endpoint that accepts instantly and then floods the enrichment queue is **worse than no
batch endpoint** — the caller gets a `200`, the platform gets a multi-hour backlog, and every other
tenant's live ingestion queues behind it.

So bulk write needs admission control before it needs efficiency.

## Four decisions

### 1. Transaction semantics: per-item, not all-or-nothing

One malformed row must not fail 9,999 good ones. Bulk producers expect to retry individual
failures, not resubmit everything.

```http
POST /api/v1/write
Idempotency-Key: 9f2c...
{ "producer_id": "key_01J...", "items": [ {...}, {...} ],
  "options": { "enrich": false } }
```

There is no separate batch endpoint — bulk is the same verb with more items. See
[write-api.md](../ingestion/write-api.md).

```json
207 Multi-Status
{
  "accepted": 9987,
  "failed": 13,
  "results": [
    { "index": 0, "status": "created", "data_id": "data_01J..." },
    { "index": 1, "status": "updated", "data_id": "data_01J..." },
    { "index": 2, "status": "error",
      "code": "schema_validation_failed",
      "detail": "amount: expected number, got string" }
  ]
}
```

`207` rather than `200` — a client that treats any 2xx as total success will silently drop the
thirteen failures otherwise.

### 2. Idempotency at both levels

Two different duplicates, two different mechanisms:

| Duplicate | Cause | Mechanism |
|-----------|-------|-----------|
| The **whole batch** replayed | Client timed out, retried | `Idempotency-Key` header, cached response |
| **Individual items** re-sent | Overlapping runs, re-sync | `external_id` upsert |

Both are required. Batch-level alone does not help an ETL job whose windows overlap; item-level
alone means a network retry writes everything twice with fresh identifiers.

### 3. Bulk defaults to cheap

Borrowing the posture the capacity plan already takes for host document ingest: **bulk writes
should not trigger full enrichment by default.**

| Flag | Default | Effect |
|------|---------|--------|
| `enrich` | `false` | Store and index structurally; skip LLM analysis |
| `embed` | `true` | Embeddings are cheap and retrieval is useless without them |
| `priority` | `bulk` | Never starves live ingestion |

A caller that wants full enrichment on ten thousand items should have to ask for it, see the
estimate, and have it counted against budget — not get it by accident because the default was
convenient.

### 4. Admission control happens before acceptance

Checked at request time, before anything is written:

- Item count against the per-request cap (propose 1,000)
- Payload size against `QUOTA_MAX_BODY_BYTES`
- Storage quota for the project
- **Enrichment queue depth** — `429` with `Retry-After` when the backlog is already deep
- Token budget if `enrich: true` — refuse rather than overspend

Rejecting a batch is cheap. Accepting one you cannot process is not.

## Large imports are jobs, not requests

Above the per-request cap, the shape changes. A 500,000-row import is not a request.

**A bulk import is a crawl run whose discovery phase is "read the supplied payload."** Same run
entity, same states, same checkpointing, same progress reporting, same pause/resume/cancel, same
per-item error list. The only difference is where the items come from.

```http
POST /api/v1/imports              → 202 { "run_id": "run_01J..." }
GET  /api/v1/runs/run_01J...      → progress, counts, errors, spend
PATCH /api/v1/runs/run_01J...     → pause | resume | cancel
```

That reuse is worth taking. Bulk import and crawling have identical requirements — resumability,
partial success, progress, budget tracking, cancellation — and building them twice would produce
two subtly different failure models.

## Efficiency, once correctness is settled

| Layer | Naive | Wanted |
|-------|-------|--------|
| Database writes | 10,000 individual inserts | Chunked multi-row inserts (~500/statement) |
| Embeddings | 10,000 single-input calls | Batched calls at the provider's input limit |
| Queue publishes | 10,000 messages | Batched publish |
| Graph writes | Per-entity | Existing `entities/batch` endpoint |

The embedding one matters most — embedding APIs accept many inputs per call, and one-at-a-time
wastes both latency and, on metered providers, money.

## Bulk write is one of five

The same job machinery serves the others, which is the main argument for building it properly once:

| Operation | What it is | Notes |
|-----------|-----------|-------|
| **Bulk write** | Import many items | This document |
| **Bulk reprocess** | W7 — rebuild derived artifacts by selector | Already selector-based; needs the job wrapper |
| **Bulk delete** | W9 — erasure cascade | **The hard one** — see below |
| **Bulk update** | Retag, re-project, change ACL across many items | ACL changes must re-check derived artifacts |
| **Bulk export** | Portability (GDPR Art 20), workspace offboarding | Long-running, resumable, produces an archive |

### Bulk delete deserves specific attention

> Full treatment in [deletion.md](deletion.md).


An erasure request touching 50,000 items is not a `DELETE`. It is a cascading job across data
items, embeddings, entities, graph facts in two stores, object-store blobs, **and any compressed
summary that absorbed the content**.

It must be resumable — a cascade interrupted halfway leaves the corpus in a state where the source
is gone but the derived data is not, which is the worst possible outcome for the request that
triggered it. It must report what it touched, for the audit trail. And it must be verifiable
afterwards, because "we deleted it" is a claim someone may have to stand behind.

This is why the delete cascade cannot be bolted on later: it needs the same job infrastructure as
bulk import, and it needs derived artifacts to carry provenance from the day they are created.

## Requirements

See `FR-EXT-5` and the `FR-CRAWL` series in
[functional-requirements.md](../functional-requirements.md) — bulk import reuses the crawl run
contract, so `FR-CRAWL-7` (checkpointed and resumable), `FR-CRAWL-13` (pause/resume/cancel with
checkpoint preserved) and `FR-CRAWL-20` (heartbeat and reclaim) apply unchanged.
