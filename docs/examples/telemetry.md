# Telemetry — Machine Data

The opposite end of the spectrum from a legal matter: enormous volume, low value per record, and
the wrong default settings will produce a very large bill for very little benefit.

## Setup

```
producer   whk_01J…  device gateway     inbound_auth: api_key   priority: bulk
producer   crw_01J…  historian pull     query, cursor on ts

case type  asset      identifiers[]  serial, VIN, asset tag
memory     asset-window  ttl 30d     on_expiry orphan_delete
memory     asset-events  ttl null    keep_members          ← anomalies only
```

Two memories per asset, deliberately: a **rolling 30-day window** that expires, and a **permanent
event log** that does not. The same reading can be in both — and if it is, the 30-day expiry will
not delete it, because expiry is `orphan_delete` and something else still holds it.

## Records

```json
{ "external_id": "dev-4471:1724692810", "event_time": "2026-08-26T14:20:10Z",
  "content": { "kind": "inline",
               "text": "{\\"temp_c\\":81.4,\\"rpm\\":1420,\\"vibration\\":0.62}" },
  "case":   { "external_id": "SN-4471", "case_type": "asset" },
  "memory": { "type": "asset-window", "key": "SN-4471" } }
```

Classification resolves at **layer 5** — MIME registry, `application/json` → `structured_json`.
No LLM call. That is the point.

After normalization:

```json
{ "canonical_type": "Activity",
  "identifiers": ["SN-4471"],
  "facets": { "temp_c": 81.4, "rpm": 1420, "vibration": 0.62 },
  "derived": { "embedding": null, "viewpoint": null } }
```

**Nothing was embedded and nothing was summarised**, because the producer's policy says so.

## The economics

At 10,000 devices reporting every 30 seconds:

| | Per day | If enriched with an LLM |
|---|---|---|
| Records | ~29 million | — |
| Embeddings | 0 (policy) | ~29 million |
| Model calls | **0** | ~29 million |
| Cost | storage only | catastrophic |

The controls that make this survivable are all already in the design and all default to the safe
side:

- **Deterministic classification** — layer 5, never reaching the LLM fallback
- **Per-agent processing flags** — `embed: false`, `summarize: false` for this data type
- **`enrich: false` default on bulk writes** — full enrichment has to be *asked for* and budgeted
- **`priority: bulk`** — never starves live ingestion
- **Cost-weighted quota** — a request counter would not notice this at all

## When it does need a model

An anomaly detector — deterministic, not a model — flags a reading and writes it into
`asset-events`, the permanent memory. *That* one gets enriched:

```json
{ "external_id": "dev-4471:1724699999", "event_time": "…",
  "content": { "kind": "inline", "text": "{\\"temp_c\\":118.9,…}" },
  "memory": [ { "type": "asset-window", "key": "SN-4471" },
              { "type": "asset-events", "key": "SN-4471" } ],
  "options": { "enrich": true } }
```

Two memberships, two lifecycles: the window expires in 30 days, the event log keeps it forever, and
the reading survives because **effective expiry is the maximum across memberships**.

## The ask

*"What was happening to unit 4471 before the failure?"*

Retrieval scoped to the asset case, filtered by a time range on `event_time`, ranked by proximity
to the failure — returning structured facets rather than prose, because that is what was stored.

## What the design contributes

| Mechanism | Why it matters here |
|-----------|--------------------|
| **Deterministic classification** | 29 million records a day that never touch a model |
| **Per-type processing flags** | Embedding everything here would be the single largest line item in the system |
| **Many-to-many membership** | One reading, a rolling window and a permanent log |
| **`orphan_delete`** | The window expiring must not delete what the event log still holds |
| **Cost-weighted quota** | Request counters do not see the difference between this and a chat query |
| **Normalized facets** | `temp_c > 100` is a filter, not a semantic search |
