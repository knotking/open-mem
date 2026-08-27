# The Write API

One endpoint. Every producer uses it.

Earlier drafts had four write paths — an envelope endpoint for the gateway, an item endpoint for
clients, a batch endpoint for bulk, and an internal queue publish for crawler-discovered files.
Four paths meant four sets of admission control, four places to derive an ACL, four ways to be
disabled, and — as it turned out — an external crawler that could ingest records but not files,
because one of the four was an internal queue it could not reach.

**Everything writes through `POST /api/v1/write`.**

## Why it collapses cleanly

Two abstractions already in the design do the work:

**[`ContentRef`](workers.md)** unifies the payload. Content is `Inline`, `Stored` in the object
store, or `Pending` behind a provider reference. A gateway envelope, a client upload, a crawled
record and a file the crawler found are all one of those three.

**The producer** unifies the caller. Every writer is preconfigured and registered — that is what
makes a single endpoint safe rather than a free-for-all.

## Producers

Nothing writes anonymously. A producer is registered before it can write, and the registration
carries the policy that used to be scattered across four code paths.

```
Producer
  producer_id     whk_<ulid> | crw_<ulid> | key_<ulid> | upl_<ulid>
  type            webhook | crawler | client | upload | agent
  owner           user_id, org_id, project_id
  connection_id   optional — the ACL scope root
  defaults        project, memory_type, tags
  policy          enrich · priority · rate_limit · quota · budget
  status          draft | enabled | disabled

  inbound_auth    api_key | signature | url_secret | none
  api_key_id      ← when inbound_auth = api_key
  signing_secret  ← when inbound_auth = signature
```

**A user-created API key can be configured onto an inbound producer**, so one credential mechanism
serves both directions. Not every provider can present one, so the producer declares its method —
prefer `api_key` where supported, since it is revocable, attributable and capability-scoped, where
a URL secret is none of those. See [auth](../security/auth.md).

| Producer type | Preconfigured by | Identified as |
|---------------|-----------------|---------------|
| **Inbound channel or app** | the system, at connect time | `whk_<ulid>` |
| **Crawler** | the user, dry-run gated before enabling | `crw_<ulid>` |
| **Client / SDK / MCP** | key issuance | `key_<ulid>` |
| **Upload session** | presign request | `upl_<ulid>` |
| **External crawler / ETL** | a project-scoped key | `key_<ulid>` |

### What this unifies

Seven things that were specified separately now have one home:

| Concern | Now |
|---------|-----|
| **ACL inheritance** | Derived from the producer's connection scope — one rule, all paths |
| **Admission control** | One place: quota, budget, queue depth, payload size |
| **Enable / disable** | `producer.status`. A disabled producer accepts and drops, uniformly |
| **Rate limiting** | Per producer, which subsumes per-(provider, user) |
| **Freshness detection** | `producer.seconds_since_last_item` covers webhooks, crawlers *and* clients |
| **Quota attribution** | Per producer, rolling up to project and org |
| **Enrichment policy** | Producer defaults, overridable per write |

The freshness one is worth noting: the highest-value detector in
[telemetry](../operations/telemetry.md) was defined per *connection*. Per *producer* is strictly
better — it also catches a crawler whose selector broke and a client that stopped calling.

## The endpoint

```http
POST /api/v1/write
Authorization: Bearer <credential>
Idempotency-Key: 9f2c...

{
  "producer_id": "crw_01JQRS...",
  "items": [
    {
      "external_id": "0064xx0000ABCDE",
      "content": { "kind": "inline", "text": "..." },
      "metadata": { "tags": ["source:salesforce"] },
      "event_time": "2019-03-14T09:20:00Z",
      "case": { "external_id": "MRN-A12345", "case_type": "patient" }
    },
    {
      "external_id": "att-77812",
      "content": { "kind": "pending",
                   "provider": "google-drive",
                   "resource_id": "1AbC...",
                   "connection_id": "conn_01JQRS..." }
    },
    {
      "external_id": "img-4410",
      "content": { "kind": "stored",
                   "storage_ref": "gs://.../upl_01JQRS/img-4410",
                   "mime_type": "image/heic",
                   "size": 3841204,
                   "checksum": "sha256:..." }
    }
  ],
  "options": { "enrich": true, "priority": "live" }
}
```

```json
207 Multi-Status
{
  "accepted": 3, "failed": 0,
  "results": [
    { "index": 0, "status": "created", "data_id": "data_01J...", "state": "queued" },
    { "index": 1, "status": "created", "data_id": "data_01J...", "state": "fetch_pending" },
    { "index": 2, "status": "updated", "data_id": "data_01J...", "state": "queued" }
  ]
}
```

`items` is always an array — one item is an array of one. Always `207`. The SDK facade hides that
for the single-item case, but the *protocol* has one shape, one set of semantics and one admission
path, which is the entire point.

### `Pending` closes the gap

Item 1 above is the fix. A crawler that discovers a Drive file writes a `Pending` content
reference through the public API; the API creates the data item and enqueues the fetch. It does
**not** publish to an internal queue.

Which means an external crawler can do exactly what a managed one does — including files. Without
this, the claim that external producers are first-class was false for anything that is not a plain
record.

## What is not the write API

Two endpoints remain separate, for good reasons:

| Endpoint | Why separate |
|----------|-------------|
| `POST /api/v1/uploads` | Issues a presigned URL. It grants capability rather than writing data; the *completion* is an ordinary write with `Stored` content |
| `POST /webhooks/{whk_id}` | The public, provider-facing surface. It accepts whatever shape a provider sends, normalises it, and calls the write API. It is a **translator in front of** the write API, not a second one |

## Two kinds of duplicate

`external_id` dedupes **within** a source: re-crawling the same Salesforce record updates rather
than duplicates. It does nothing across sources.

The same PDF arrives from Drive and as an email attachment. Two producers, two `external_id`
values, both legitimate — and one document, embedded twice, entity-extracted twice, occupying two
places in every result set.

### Dedupe the derived work, not the provenance

The tempting fix is to reject the second write. That loses something real: **who sent it, when and
in what context is itself information.** The email attachment tells you a person shared it with a
colleague; the Drive copy tells you it lives in a folder. Collapsing them discards that.

So keep both items and **share the expensive derived layer**:

| Layer | Behaviour on a content-hash match |
|-------|----------------------------------|
| Data item | **Both kept** — provenance differs, and provenance is data |
| Content hash | Recorded on both; the match is what links them |
| Chunks, embeddings | **Computed once**, referenced by both |
| Extraction, entities, claims | **Computed once**, attributed to both sources |
| Retrieval | **Deduplicated at query time** — one result, both provenances shown |

That last row is what the user actually experiences: searching does not return the same document
twice, but opening it shows both places it came from.

The cost saving is not incidental either — the derived layer is where nearly all the expense sits,
so deduplicating it is most of the benefit of deduplicating at all.

## The write is synchronous; the enrichment is not

A common misreading worth stating plainly: **`POST /write` commits.** The item is durable and has
an id before the response returns. What is queued is the enrichment.

That is why ingest latency is a database write rather than a model call, and why the pipeline being
down delays enrichment without losing data.

## Readiness is a staircase, and clients need to see it

Three states, not one:

| State | Reached when | What works |
|-------|-------------|------------|
| `stored` | immediately | `GET /data/{id}` |
| `searchable` | after embedding | vector and hybrid retrieval find it |
| `enriched` | after the agent runs | viewpoint, entities, facets, graph |

The per-item `state` in the write response, and a `state` field on reads, exist so clients do not
each invent their own polling heuristic. "I just uploaded it and search cannot find it" is a
support ticket that is not a bug — but only if the state is visible.

These are the two SLIs that component metrics cannot show: **ingest → searchable** and
**ingest → enriched**.

## Admission control, in one place

Evaluated before anything is written:

- Producer exists, is enabled, and the credential is authorised for it
- Item count against the per-request cap
- Payload size against the configured maximum
- Storage quota for the target project
- **Enrichment queue depth** — `429` with `Retry-After` when the backlog is deep
- Token budget when `enrich: true`

Rejecting a write is cheap. Accepting one you cannot process is not.

## Requirements

`FR-ING-8`, `FR-EXT-1..7` and the `FR-CRAWL` series all resolve against this single path. See
[functional-requirements.md](../functional-requirements.md).
