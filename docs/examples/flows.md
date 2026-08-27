# The Two Canonical Flows

Everything reduces to one of these. The difference is only *who* decides the association.

## Flow 1 — explicit: a client creates a memory and writes into it

```
CLIENT                    API                   QUEUE            WORKERS           STORE
  │                        │                      │                 │                │
  │ 1  POST /memories      │                      │                 │                │
  │───{type, key}─────────▶│                      │                 │                │
  │                        │─ upsert by (project, type, key) ───────────────────────▶│
  │◀──── mem_01J… ─────────│                      │                 │                │
  │                        │                      │                 │                │
  │ 2  POST /write         │                      │                 │                │
  │───{producer_id,        │                      │                 │                │
  │    items:[{content,    │                      │                 │                │
  │      memory:{key},     │                      │                 │                │
  │      event_time}]}────▶│                      │                 │                │
  │                        │ producer → enabled?  │                 │                │
  │                        │ admission: quota,    │                 │                │
  │                        │   budget, depth      │                 │                │
  │                        │ ACL ← producer scope │                 │                │
  │                        │ AUDIT write ─────────────────────────────────────────▶ │
  │                        │─ commit + membership ──────────────────────────────────▶│
  │◀── 207 {data_id,       │                      │                 │                │
  │      state:"queued",   │                      │                 │                │
  │      memories:[mem_…]} │                      │                 │                │
  │                    ◀── the write returns here. nothing has been enriched yet ── │
  │                        │─ publish ───────────▶│                 │                │
  │                        │                      │─ ingest.enrich ▶│                │
  │                        │                      │                 │ classify       │
  │                        │                      │                 │ normalize      │
  │                        │                      │                 │ route → agent  │
  │                        │                      │                 │ embed          │
  │                        │                      │                 │ extract        │
  │                        │                      │                 │───────────────▶│
  │                        │                      │                 │                │
  │ 3  POST /retrieve      │                      │                 │                │
  │───{match, filter:{     │                      │                 │                │
  │     memory_id}}───────▶│                      │                 │                │
  │                        │ ACL applied IN the query ─────────────────────────────▶│
  │                        │ AUDIT read ──────────────────────────────────────────▶ │
  │◀── results + citations │                      │                 │                │
  │                        │                      │                 │                │
  │ 4  GET /data/{id}/memories                    │                 │                │
  │───────────────────────▶│                      │                 │                │
  │◀── {effective_expiry, reason, memberships[]}  │                 │                │
```

**Three things worth noticing.**

The write returns after step 2's commit — **before any model runs**. Ingest latency is a database
write, which is why a dead pipeline delays enrichment rather than losing data.

The response carries `memories[]`, including memberships the caller did not ask for. A routing rule
or the project default may have added one, and the caller should not have to query to find out
where its own write went.

Step 4 answers *"why is this still here?"* and *"why did this vanish?"* — both are membership
questions, and `effective_expiry` comes with its **reason** rather than a timestamp the caller has
to interpret.

## Flow 2 — preconfigured: a producer routes without the caller deciding

This is the ingestion pipeline. Nobody creates a memory; the producer's configuration does.

```
  ONE-TIME SETUP                                    THEN, PER EVENT
  ──────────────                                    ───────────────
  POST /producers                                   provider ──▶ POST /webhooks/whk_…
  { type: "webhook",                                              │
    connection_id: "conn_…",        ┌──────────────────────────────┘
    defaults: {                     ▼
      project_id, memory_type:   GATEWAY  normalise → UniversalEnvelope
        "conversation",              │    resolve user, tag connections
      routing: {                     ▼
        key: "$.thread_ts",       POST /api/v1/write   { producer_id, items[] }
        case: "$.customer_id" },      │
      tags: ["source:slack"] },       ▼
    inbound_auth: "signature",     RESOLVE from producer config:
    policy: { enrich: true,          memory  ← upsert (conversation, $.thread_ts)
              priority: "live" } }   case    ← upsert (account, $.customer_id)
         │                           ACL     ← connection scope (personal | shared)
         ▼                           project ← producer default
  { producer_id: "whk_01J…" }        tags    ← producer defaults + item
                                        │
                                        ▼
                                  commit · audit · publish   ──▶  enrich, unchanged
```

**The configuration is the association.** A thousand Slack messages arrive; each upserts into the
conversation memory for its thread and the case for its customer, with the ACL its connection
scope dictates. No caller tracks session state, and no code path special-cases Slack.

Change the producer's `memory_type` and subsequent writes route differently — existing memberships
are untouched, because [membership is mutable](../memories.md) and a config change is not a
retroactive claim about data already written.

## What a stored record looks like

After both flows, the same shape:

```json
{
  "data_id": "data_01JQRS5X7Y8Z...",
  "org_id": "org_01JQ...", "project_id": "proj_01JQ...",
  "producer_id": "whk_01JQ...",
  "external_id": "1724692810.004200",
  "access_level": "shared",
  "shared_with": ["group:grp_01JQ..."],
  "language": "en",
  "event_time":   "2026-08-26T14:20:10Z",
  "ingested_at":  "2026-08-26T14:20:11Z",
  "content": { "kind": "inline", "mime_type": "text/plain" },
  "state": "enriched",
  "memories": [
    { "memory_id": "mem_01JQ...", "type": "conversation", "added_by": "routed" },
    { "memory_id": "mem_01JQ...", "type": "timeline",     "added_by": "routed" }
  ],
  "case_id": "cas_01JQ...",
  "derived": {
    "viewpoint":  { "generator_version": "a3f2...", "served_by_model": "gemma4:12b" },
    "embedding":  { "model_id": "embeddinggemma", "dim": 768 },
    "entities":   ["ent_01JQ...", "ent_01JQ..."],
    "facets":     { "sentiment": "negative", "intent": "escalation" }
  }
}
```

Six fields there exist because of a specific finding: `language` (the lexical index needs it before
indexing), `event_time` separate from `ingested_at` (or backfilled timelines render wrong),
`added_by` on each membership (routed and explicit are different claims), `generator_version` and
`served_by_model` (the fingerprint records intent; the chain may have served something else), and
`model_id` on the embedding (or a contaminated index cannot be identified).
