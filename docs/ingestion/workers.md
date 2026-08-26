# Ingestion Workers

## The core problem

```
INVARIANT   integration credentials must never reach the enrichment pipeline
REALITY     the pipeline is what discovers content is missing
CONSTRAINT  webhook ack deadlines (~3s) forbid downloading inline
CONSTRAINT  queue messages ~1MB — bytes cannot travel in the envelope
```

These four rule out both naive designs: the gateway cannot download everything before acking,
and the pipeline cannot fetch authenticated resources itself.

## Worker taxonomy

Eight classes. Backfill and polling are **not** separate classes — they collapsed into the
[crawler](crawlers.md) once it became clear they are two schedules of the same machinery.

| ID | Class | Trigger | Creds | Bounded by | Retry costs |
|----|-------|---------|:-----:|-----------|-------------|
| **W1** | Enrich | content available | AI only | model capacity | tokens |
| **W2** | Fetch | content by reference | via proxy | per-provider rate limit | bandwidth |
| **W3** | Crawl | schedule or manual trigger | via proxy | per-provider + per-host limits | re-discovery only |
| **W4** | Scheduled | cron | via proxy | — | nothing |
| **W6** | Stream | persistent socket | via proxy | one conn / account | reconnect + gap fill |
| **W7** | Reprocess | config or schema change | AI only | model capacity | tokens |
| **W8** | Mutate | upstream revision | AI only | model capacity | tokens |
| **W9** | Retract | delete / erasure request | none | — | nothing |

### Why fetch and enrich must be separate pools

```
FETCH worker                        ENRICH worker
────────────                        ─────────────
I/O-bound, waiting on network       model-bound, waiting on inference
cheap per task, high concurrency    expensive per task, low concurrency
throttled by upstream 429s          throttled by model capacity
retry costs bandwidth               retry costs tokens
holds integration credentials       holds none
```

In one pool, a 200 MB download parks a GPU-capable slot on a socket, and a provider limiting you
to 5 req/s throttles all inference.

### Two that need special attention

**W6 (stream) cannot be retrofitted.** Slack Socket Mode, Discord gateway, Telegram long-polling
and WhatsApp bridges hold *one connection per account* — five replicas each holding the same
connection means ingesting everything five times. It needs sharding with leader election plus gap
backfill after a drop. That is a different deployment shape from "scale the pool".

**W7 (reprocess) is load-bearing.** It has surfaced as a prerequisite three separate times: agent
tuning, normalization schema evolution, and index regeneration. Without it, changing a prompt, a
schema or an index generator applies only to future data.

**W8 (mutate) is more urgent than it looks.** Crawlers re-discover constantly, making them the
largest source of mutations in the system. Without W8 every re-crawl either duplicates records or
leaves stale facts valid forever.

## The content contract

Three recorded defects share one root cause — agents branching on provenance fields that can lie:

- `source_type=DOCUMENT` with `mime_type=text/plain` misrouting to the PDF agent
- `is_downloaded=False` triggering a download that fails with "No downloadable URL"
- Attachment envelopes needing `is_downloaded=True` set by hand

### The fix

Replace provenance branching with a closed sum type. Enrichment must be **unable to tell** how
content arrived.

```
ContentRef =
  │ Inline (text | bytes)
  │ Stored (storage_ref, mime_type, size, checksum)
  │ Pending(provider, resource_id, hints)     ← only W2 ever sees this
```

Two rules make it hold:

1. **`is_downloaded` becomes derived, never assignable** — computed from whether `content_text`,
   `content_b64` or `storage_ref` is present. A field that must be manually kept in sync with
   another field will drift.
2. **Routing keys off sniffed MIME**, with `source_type` demoted to a hint. MIME must be detected
   server-side — a client-declared type is an injection vector that chooses which agent runs.

Inline payloads, fetch workers and uploads all converge on the same two-case type, so an uploaded
PDF and a Drive-fetched one are indistinguishable downstream.

## Classification cascade

Deterministic layers first; the LLM is the last resort and layers 1–6 resolve roughly 80% of
traffic.

| Order | Layer | Example |
|-------|-------|---------|
| 1 | Channel message detection | WhatsApp → `chat_message` |
| 2 | `source_type` field | `"pdf"` → `document_pdf` |
| 3 | Explicit `data_type` | supplied by caller — short-circuits everything |
| 4 | Payload heuristic | `latitude`/`longitude` → `sensor_gps` |
| 5 | MIME registry | `application/json` → `structured_json` |
| 6 | URL extension | `.csv` → `structured_csv` |
| 7 | LLM classifier (fallback) | small-tier model on ambiguous content |
| — | Catch-all | binary-blob agent |

## Failure policy diverges by class

Fetch failures are usually *about the connection*; enrich failures are usually *about capacity*.
Different remediation, different queues, different alerting.

### W2 fetch

| Condition | Handling |
|-----------|----------|
| `401` / `403` | **Terminal.** Mark connection `needs_reauth`, surface to user. Blind retry burns the connection and hides the cause. |
| `404` / `410` | Terminal. Resource deleted upstream; keep the record, mark unavailable. |
| `429` | Backoff **and** narrow that provider's token bucket. |
| `5xx` / timeout | Retry with backoff, then dead-letter. |

### W1 enrich

| Condition | Handling |
|-----------|----------|
| Primary model unavailable | Fall through the chain. Not a failure. |
| Chain exhausted | Retry with backoff — transient capacity, not bad data. |
| Schema violation | Retry N, then dead-letter *with raw output attached*. |
| Content corrupt | Terminal. Re-running will not help. |

## Fan-out

W2 emits `1..N` jobs. A resolved email thread re-queues each attachment as its **own** fetch job
rather than expanding in one pass — so one corrupt attachment in a 40-attachment thread does not
fail the thread. Requires a depth cap and a per-root job budget.

## Rate limiting

Token buckets keyed `(provider, user_id)`. One user syncing a large folder must not consume the
Slack budget of every other user. Bulk work runs at lower priority so historical import never
starves live ingestion.

## Idempotency

Providers redeliver. Key the fetch on `(provider, resource_id, version|etag|checksum)` so a
redelivery is a cache hit rather than a second download and a second data item.
