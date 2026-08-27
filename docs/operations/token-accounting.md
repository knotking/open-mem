# Token Accounting

`FR-OBS-4` requires tracking LLM token usage per user, per model and per agent. That is the right
instinct and not enough to bill on, budget against, or explain a surprise.

Six things are missing, and each of them biases the number in the same direction: **under-counting**.

## What a usage record has to carry

One record per inference call — not per request, per *call*, since one ingest can trigger several.

```
inference_events
  ts, event_id
  org_id, project_id, user_id
  case_id | run_id | job_id            ← attribution to crawl runs, imports, reprocess
  agent_id, tier, purpose              classify | summarize | embed | rerank | chat | map
  configured_model
  serving_model, serving_provider      ← what actually ran
  fallback_depth
  tokens_in, tokens_out, tokens_cached
  latency_ms
  status                               ok | failed | timeout | refused
  billing_account                      user_key | org_key | platform
  cost_estimate                        nullable; local inference is 0
```

## The six gaps

### 1. Input and output are not the same token

Providers price them differently — often several times apart. A single `tokens` counter cannot
produce a cost estimate, only a usage figure that looks like one.

### 2. Failed and retried calls still cost

A call that times out after generating three thousand tokens consumed three thousand tokens. A
retry doubles it. The fallback chain can triple it.

Counting only successful calls under-reports spend **systematically**, and in exactly the direction
that produces surprise bills. Record every call, with status; report success and total separately.

### 3. Cached tokens are a different rate

Prompt caching changes the arithmetic substantially where a provider offers it, and an agent
re-running the same system prompt across ten thousand items is precisely the workload that benefits.
Track cached input separately or the estimate is wrong in both directions — too high before caching
is enabled, and unimprovable after.

### 4. Embedding calls consume tokens too

Embeddings are priced per input token and are the *highest-volume* model call in the system — every
chunk of every item. Omitting them from accounting misses the single largest line for bulk
ingestion.

### 5. Fallback silently changes who pays

This is the one worth staring at.

```
primary:  local model      → $0
fallback: cloud provider   → real money, per token
```

The chain exists so that a provider outage degrades quality rather than availability. But it also
means **a $0 operation can become a paid one with no signal at all**. A local GPU busy for twenty
minutes during a fifty-thousand-item backfill is a bill nobody authorised and nobody was told about.

Cost attribution must follow `serving_provider`, never `configured_model`. And a fallback that
crosses from free to paid deserves its own counter, because it is a category change rather than a
degradation.

The same structural defect appears three times now: fallback that is safe for availability and
unsafe for something else — [correctness](../retrieval/versioning.md) with embeddings,
[legality](../security/compliance.md) with regulated content, and cost here.

### 6. The new primitives need attribution

Crawl runs, bulk imports, reprocess jobs and cases each need a spend total, and each has a
user-facing reason:

| Primitive | Why it needs a number |
|-----------|----------------------|
| **Crawl run** | The dry-run promised an estimate. Without actuals it is decoration |
| **Bulk import** | Admission control checked a budget; something has to decrement it |
| **Reprocess job** | The staleness preview quoted a rebuild cost |
| **Case** | Per-case cost is a real question in embedded and clinical deployments |

## Estimate versus actual

Every estimate the system shows — dry-run projections, batch admission, staleness rebuild previews —
must be **recorded alongside the eventual actual**.

Two reasons. Estimates that are never checked drift until nobody trusts them, at which point the
dry-run gate becomes a formality people click through. And the ratio is itself a useful signal: a
run that costs three times its estimate usually means the corpus is not what the config assumed.

## Budget enforcement

Three stages, and the middle one is where most systems stop:

| Stage | Mechanism |
|-------|-----------|
| **Estimate** | Before accepting a run, import or reprocess — refuse rather than overspend |
| **Track** | Running total against cap, visible while work is in flight |
| **Enforce** | Soft warn → hard stop → refuse new work |

Mid-operation exhaustion is already specified for crawls: the run stops as `partial` with a reason
and **does not advance its watermark**, so the remaining work is re-covered on the next tick rather
than lost. Bulk imports and reprocess jobs behave the same way.

### Whose budget

Three economies share one code path, and conflating them produces bad decisions:

| Billing account | Whose money | Enforcement posture |
|-----------------|-------------|--------------------|
| `user_key` | The user's own provider key | Their spend — show it, warn, do not silently cap |
| `org_key` | Org-provided credentials | Org policy applies; admin sets the ceiling |
| `platform` | Ours, in a hosted deployment | Quota with margin; refuse at the limit |

## Read-side cost is unmetered

Write quotas are designed in detail. Retrieval has none — and retrieval is where a single request
can be a thousand times more expensive than another.

| Request | Relative cost |
|---------|--------------|
| Vector search, top-10 | 1× |
| Hybrid with RRF | ~2× |
| `full` mode — all signals, RRF merged | ~5× |
| …plus a cross-encoder reranker | **~100×** — an inference call per candidate |
| …plus RAG generation | **~1000×** — generation dominates everything above it |

### Rate-limiting by request count is the wrong primitive

A hundred vector searches and a hundred `full`+cross-encoder+chat requests are the same number to a
counter and three orders of magnitude apart in cost. **Quota must be cost-weighted**, priced on the
work a request actually authorises rather than on the fact that it arrived.

Controls, in order of how much they matter:

| Control | Effect |
|---------|--------|
| **Cost-weighted quota** | The only one that survives contact with `full` mode |
| Budget check **before** the expensive stage | Rerank and generation are gated, not the retrieval that precedes them |
| Reranker availability by tier | Cross-encoder is not a default anyone can loop |
| Max candidates into rerank | Bounds the worst case rather than trusting the caller |
| Per-key concurrency | One client cannot occupy the model tier |
| Query timeout | A runaway hybrid query is cancelled, not waited on |

This is both a **cost** vector and a **denial-of-service** vector, and the second is the one that
arrives without malice — a client with a retry loop and an expensive default configuration will do
it by accident.

## Volume

One row per inference call at target ingest rates is millions of rows, so this cannot live in the
same store as user content without becoming a meaningful share of it.

- **Raw events** — short TTL, sized to cover dispute and debugging windows
- **Rollups** — hourly aggregates by (org, model, purpose, status), retained long
- Reporting reads rollups; investigation reads raw

Same shape as the [tracing-memories concern](telemetry.md): observability whose volume scales with
ingest volume needs its own retention story, decided deliberately.

## Requirements

Extends `FR-OBS-4`:

- **FR-TOK-1** Every inference call MUST produce a usage record, including calls that fail, time
  out or are retried.
- **FR-TOK-2** Records MUST separate input, output and cached tokens.
- **FR-TOK-3** Embedding calls MUST be accounted alongside generation calls.
- **FR-TOK-4** Cost MUST be attributed to the **serving** provider, never the configured one.
- **FR-TOK-5** A fallback that moves a call from a free provider to a paid one MUST be counted and
  surfaced separately from ordinary fallback.
- **FR-TOK-6** Usage MUST be attributable to a crawl run, bulk import, reprocess job or case where
  one applies.
- **FR-TOK-7** Every estimate the system presents MUST be recorded and reconciled against actuals.
- **FR-TOK-8** Budgets MUST distinguish user-provided, org-provided and platform credentials.
- **FR-TOK-9** Exhausting a budget mid-operation MUST stop the operation as partial, with reason,
  without losing recoverable progress.
- **FR-TOK-10** Raw usage events and long-retention rollups MUST have separate retention.
