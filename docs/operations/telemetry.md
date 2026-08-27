# Telemetry

## This system's characteristic failure is silence, not errors

Almost every serious failure mode produces **no error**. A revoked connection stops ingesting and
returns nothing. An embedding fallback writes vectors into the wrong space and ranks them anyway.
An unenriched item is still searchable, just worse. Stale facts answer temporal queries
confidently and incorrectly. A disabled webhook returns `200 OK` and drops the payload —
deliberately.

Telemetry therefore cannot be organised around error rates. It has to be organised around
**detecting absence**.

## The silent-failure catalogue

| Failure | Visible symptom | Detector |
|---------|-----------------|----------|
| **Connection expired / revoked** | none — ingestion just stops | `needs_reauth` count, and **per-connection time-since-last-item against its own baseline** |
| **Embedding in wrong space** | degraded ranking | distinct `model_id` count per index — must be exactly 1 |
| **Crawler discovering nothing** | runs succeed, find zero items | discovery count trending to zero against the config's baseline — a changed selector looks identical to "no new data" |
| Enrichment backlog | items searchable but shallow | queue lag; count of items with no viewpoint older than N minutes |
| Stale derived artifacts | old prompts still in effect | stale-artifact count by generator version |
| Stale facts after revision | confidently wrong temporal answers | facts with `invalid_at = null` whose source has a newer version |
| Disabled webhook | `200 OK`, payload dropped | explicit dropped-event counter, not an error rate |
| Fan-out truncation | partial thread ingested | emitted-vs-expected child job count |
| Quota throttling a tenant | slow, not broken | `429` rate by `org_id` |

### The single highest-value signal

**Time since last item, per connection, compared against that connection's own baseline.** A
workspace that normally delivers 200 messages a day and has delivered none for six hours is
broken — but nothing errored, no alert fired, and the user will not notice until they search for
something that should be there.

## Trace context breaks at the queue

`X-Request-Id` is accepted and echoed by the API, but propagation into the pipeline is optional.
Distributed tracing therefore stops precisely where debugging is hardest — the asynchronous half.

```
gateway ──▶ API ──▶ queue ──╳── fetch worker    (orphan span)
 trace ✓    trace ✓   context   enrich worker   (orphan span)
                      dropped
```

Carry `traceparent` in the message envelope and restore it in the worker. Otherwise the only spans
you have cover the fast synchronous path that rarely fails.

## Per-worker signals

| Worker | Signals that matter |
|--------|--------------------|
| W1 enrich | queue lag · model latency · **fallback depth reached** · token spend by tenant · schema-violation rate |
| W2 fetch | **per-provider 429 rate** · bytes fetched · fetch latency · `needs_reauth` count · fan-out ratio |
| W3 crawl | discovery rate · **dedupe hit rate** · frontier size · **run duration vs interval** · politeness compliance · spend per run |
| W3 runs | checkpoint progress · items remaining · projected completion · **spend so far** |
| W6 stream | connection state · reconnect count · **gap duration** after a drop |
| W7 reprocess | stale count by generator · rebuild throughput |

**Fallback depth** deserves particular attention. A system silently running entirely on its
third-choice model still works — and costs more, answers differently, and indicates the primary
has been down for some time. It is the difference between "healthy" and "healthy-looking".

## Two structural constraints

**Cardinality.** Labelling every metric by `project_id` at ~1,000 workspaces multiplies series
count past what a single metrics store comfortably holds. Label high-volume series by `org_id`,
sample at `project_id`, and keep per-project detail in logs and traces where cardinality is cheap.

**Traces stored as memories cost twice.** Tracing is persisted as a `tracing` memory type with a
3-day TTL. That makes observability searchable by the same engine as everything else — but it also
means trace volume scales with ingest volume, in the same database that is already the shared fate
of every tenant. Measure early; keep an escape hatch to ship traces externally.

## Four signals, not three

OpenTelemetry's three pillars are traces, metrics and logs. This system needs a fourth, and
conflating it with logs is how the compliance story fails.

| Signal | Shape | Retention | Answers |
|--------|-------|-----------|---------|
| **Metrics** | Aggregated, low cardinality, always on | Long, cheap | "Is it healthy? Is it getting worse?" |
| **Traces** | Per-request spans, sampled | Short | "Why was *this one* slow or wrong?" |
| **Logs** | Structured lines, correlated | Medium | "What exactly happened here?" |
| **Domain events** | Durable, business-meaningful, queryable | **Long, immutable** | "Who did what, when — and can we prove it?" |

Domain events are not verbose logs. They are the audit substrate: an erasure request, a break-glass
access, a legal hold, a key rotation. Different retention, different mutability guarantees,
different threat model. See the note above about tracing memories not being an audit log — this is
the thing they are not.

---

## Metric catalog

Named by domain. Every counter carries `org_id`; see the cardinality rules below for what else may
be attached.

### Ingestion

| Metric | Type | Notes |
|--------|------|-------|
| `ingest.requests` | counter | by `source_type`, `status` |
| `ingest.bytes` | counter | |
| `ingest.items` | counter | by `outcome: created \| updated` — upsert effectiveness |
| `ingest.rejected` | counter | by `reason: quota \| validation \| auth \| body_size` |
| **`ingest.dropped`** | counter | **Disabled webhooks return `200` and drop.** An error rate is correctly zero here, so this needs its own counter |

### Queue

| Metric | Type | Notes |
|--------|------|-------|
| `queue.depth` | gauge | by subject |
| **`queue.consumer_lag`** | gauge | **Autoscaling signal** — enrich workers block on inference, not CPU |
| `queue.publish` / `ack` / `nack` | counter | |
| `queue.redeliveries` | counter | Spikes indicate ack-deadline pressure |
| `queue.message_age` | histogram | Oldest unprocessed |
| `queue.dlq_depth` | gauge | Should be zero; anything else needs a human |

### Fetch

| Metric | Type | Notes |
|--------|------|-------|
| `fetch.requests` | counter | by `provider`, `status` |
| `fetch.duration` / `fetch.bytes` | histogram / counter | by provider |
| **`fetch.rate_limited`** | counter | Per provider — drives bucket narrowing |
| **`fetch.needs_reauth`** | gauge | Per provider. Non-zero means data has silently stopped |
| `fetch.fanout_ratio` | histogram | Children emitted per parent — catches truncation |
| `fetch.depth_reached` | histogram | Against the recursion cap |

### Enrichment

| Metric | Type | Notes |
|--------|------|-------|
| **`enrich.classification_layer`** | counter | **By layer 1–7.** This is how you *measure* the claim that ~80% never reach an LLM, rather than asserting it |
| `enrich.duration` | histogram | by `agent`, `tier` |
| `enrich.agent_invocations` | counter | by agent type |
| `enrich.schema_violations` | counter | by agent — rising means a model or prompt changed under you |
| **`enrich.unenriched_age`** | gauge | Items older than N minutes with no viewpoint |

### Inference

| Metric | Type | Notes |
|--------|------|-------|
| `inference.calls` | counter | by `serving_model`, `provider`, `purpose`, `status` |
| `inference.duration` | histogram | |
| `inference.tokens` | counter | by `direction: in \| out \| cached` |
| **`inference.fallback_depth`** | histogram | Running entirely on third choice still "works" |
| **`inference.free_to_paid`** | counter | A category change, not a degradation |
| `inference.capability_rejections` | counter | Assignment validation catching mismatches |
| `inference.capacity_wait` | histogram | Time queued for a model, distinct from queue lag |

### Embedding

| Metric | Type | Notes |
|--------|------|-------|
| **`embed.distinct_models_per_index`** | gauge | **Must be exactly 1.** Any other value means the vector-space corruption happened |
| `embed.batch_size` | histogram | Are we actually batching, or issuing one call per chunk? |
| `embed.pending_backlog` | gauge | Deferred rather than fallen back |

### Crawl

| Metric | Type | Notes |
|--------|------|-------|
| **`crawl.discovered`** | counter | Per config. **Trending to zero is the crawler equivalent of a dead connection** |
| `crawl.dedupe_hit_rate` | gauge | How much work was avoided |
| `crawl.frontier_size` | gauge | |
| **`crawl.duration_vs_interval`** | ratio | Above 1.0 and runs overlap forever |
| `crawl.robots_denied` | counter | |
| `crawl.runs` | counter | by terminal status |

### Retrieval

| Metric | Type | Notes |
|--------|------|-------|
| `search.requests` | counter | by `mode`, `reranker` |
| `search.duration` | histogram | Target p95 < 800 ms excluding generation |
| **`search.result_shortfall`** | histogram | Requested minus returned. **Systematic shortfall means ACL filtering is happening after ranking**, which silently degrades every answer |
| `search.zero_results` | counter | |
| `rag.citations_per_answer` | histogram | Zero citations on a non-empty corpus is a quality signal |

### Storage and database

| Metric | Type | Notes |
|--------|------|-------|
| **`db.pool_saturation`** | gauge | The serverless killer — instances × pool against the ceiling |
| `db.connections_active` | gauge | |
| `db.query_duration` | histogram | by query class |
| `db.statement_timeouts` | counter | |
| `storage.bytes` | gauge | by org; sampled by project |
| `vector.index_size` | gauge | |

### Derived-artifact health

| Metric | Type | Notes |
|--------|------|-------|
| `artifacts.stale` | gauge | by generator — what W7 has to rebuild |
| `artifacts.rebuild_rate` | counter | |
| **`facts.stale_valid`** | gauge | Facts with `invalid_at = null` whose source has a newer version. Every one is a confidently wrong temporal answer waiting to happen |

### Cases

| Metric | Type | Notes |
|--------|------|-------|
| `case.members` | histogram | Size distribution |
| `case.inferred_ratio` | gauge | How much membership is guessed rather than asserted |
| **`case.missing_event_time`** | gauge | Every one of these is a timeline entry in the wrong place |
| `case.stale_summaries` | gauge | |

### Tenancy, quota, security

| Metric | Type | Notes |
|--------|------|-------|
| `quota.rejections` | counter | by org, type |
| `budget.utilization` | gauge | by org and billing account |
| `auth.attempts` | counter | by method, status |
| `auth.lockouts` | counter | |
| `acl.denials` | counter | Should be low; a spike is misconfiguration, not attack |
| **`breakglass.invocations`** | counter | **Always alert.** Never routine |

### Connection health — the one that matters most

| Metric | Type | Notes |
|--------|------|-------|
| **`connection.seconds_since_last_item`** | gauge | Per connection, compared against **that connection's own baseline** |

Every other detector in this document is narrower than this one.

---

## Trace model

```
ingest.request                          (gateway)
├── gateway.normalize
├── gateway.resolve_identity
├── gateway.tag_integrations            → credential broker
├── api.persist
└── api.publish
        ╎ span link, not parent-child
        ╎
        └── enrich.job                  (worker, minutes later)
            ├── classify                 attr: layer_resolved
            ├── route                    attr: agent, tier
            ├── agent.invoke
            │   └── inference.call       attr: serving_model, provider,
            │                                  fallback_depth, tokens
            ├── embed
            ├── extract_entities
            └── graph.write
```

**The async continuation must be a span link, not a child span.** The parent request ended long
before the worker ran; modelling it as a child produces traces with impossible durations and breaks
every latency percentile that includes them. Link the worker span to the originating trace and
record both.

### Standard span attributes

`org_id` · `project_id` · `data_id` · `run_id` · `case_id` · `agent` · `tier` ·
`configured_model` · `serving_model` · `provider` · `fallback_depth`

### Sampling

Head-based sampling for volume, **plus tail-based retention of every error and every trace above a
latency threshold**. Errors are exactly what you sampled away when you needed them.

---

## Log discipline

Structured JSON, always carrying `trace_id`, `span_id`, `org_id`, `project_id`, `request_id`.

**What must never be logged:**

| Never | Why |
|-------|-----|
| Item content, or excerpts | It is user data — logging it copies regulated content into a store with different retention and access control |
| **Prompt and completion bodies** | Prompts *contain* user content. "Log the prompt for debugging" is a data-exfiltration path that looks like observability |
| Credentials, tokens, keys | Including in URLs and error bodies |
| Personal identifiers in free text | Structured fields can be redacted; prose cannot |

Where prompt debugging is genuinely needed, it belongs behind an explicit, time-boxed, audited
per-tenant flag — not a log level.

---

## Domain events

Durable, immutable, queryable. This is the audit substrate, and several entries here exist because
a regulator or a court may ask.

| Event | Why it matters |
|-------|---------------|
| `connection.authorized` / `revoked` / `needs_reauth` | Data provenance and the silent-stop story |
| `crawl.run.*` | started, completed, failed, budget_exhausted |
| `model.assignment.changed` | With the staleness impact that was shown and accepted |
| `schema.version.published` | Normalization changes are generator changes |
| **`erasure.requested` / `completed` / `partially_withheld`** | With what was withheld and under which hold |
| **`hold.applied` / `released`** | Scope and authorising identity |
| **`breakglass.invoked`** | Justification, accessor, case, timestamp |
| `case.created` / `member.asserted` | Membership provenance |
| `key.created` / `rotated` / `revoked` | |
| `quota.exceeded` / `budget.exhausted` | |
| `access.denied` | For ethical-wall and ACL forensics |

---

## Service level indicators

The user-facing ones, which are not the same as the component ones:

| SLI | Target |
|-----|--------|
| API availability | 99.5% monthly |
| Search p95 | < 800 ms, excluding generation |
| **Ingest → searchable** | p95 — when can I find it at all? |
| **Ingest → enriched** | p95 — when is it *good*? |
| Enrichment success rate | Excluding terminal content errors |
| Crawl freshness | Time since last successful run, per config |

The two ingest latencies matter most because they are what a user actually experiences, and neither
is visible from component metrics alone.

---

## Cardinality rules

Getting this wrong takes down the metrics store, which then takes down your ability to see anything.

| Dimension | Metrics | Traces / logs |
|-----------|:-------:|:-------------:|
| `org_id` | yes — bounded at target scale | yes |
| `project_id` | **sample only** | yes |
| `user_id` | **never** — unbounded | yes |
| `model`, `provider`, `agent`, `tier` | yes — bounded | yes |
| `data_id`, `case_id`, `run_id` | **never** | yes |
| `connection_id` | gauge only, for the freshness detector | yes |

---

## Alerts derive from silent failures

Each alert maps to an entry in the silent-failure catalogue above, which is the point — alerting on
CPU and error rate would catch almost none of them.

| Alert | Condition |
|-------|-----------|
| Connection gone quiet | `seconds_since_last_item` beyond that connection's baseline |
| Vector index contaminated | `embed.distinct_models_per_index` ≠ 1 |
| Crawler blind | `crawl.discovered` at zero against its own baseline |
| Enrichment falling behind | `queue.consumer_lag` or `enrich.unenriched_age` rising |
| Running on fallbacks | `inference.fallback_depth` elevated and sustained |
| Paying unexpectedly | `inference.free_to_paid` non-zero |
| Retrieval degraded | `search.result_shortfall` systematic |
| Stale facts accumulating | `facts.stale_valid` rising |
| Pool exhaustion imminent | `db.pool_saturation` above threshold |
| Break-glass used | Any invocation |
| Erasure withheld | Any `partially_withheld` event |

## Cost telemetry becomes enforcement

> Detailed usage-record design, the six ways token counts go wrong, and budget enforcement are in
> [token-accounting.md](token-accounting.md).

Token usage is already tracked per user, model and agent. Under multi-tenancy with user-supplied
provider keys that measurement has to become a **control**: budget consumed against cap, spend
projection for a queued backfill *before* it runs, and automatic tier-downgrade or refusal at the
limit.
