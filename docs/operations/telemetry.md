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

## Cost telemetry becomes enforcement

Token usage is already tracked per user, model and agent. Under multi-tenancy with user-supplied
provider keys that measurement has to become a **control**: budget consumed against cap, spend
projection for a queued backfill *before* it runs, and automatic tier-downgrade or refusal at the
limit.
