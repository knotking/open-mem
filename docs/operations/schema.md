# Where Metadata Lives

**All of it is in Postgres.** The [blob store](blob-layout.md) holds bytes and nothing else.

This assembles the schema that the rest of the documentation has been specifying piecemeal —
`memory_members` in [memories.md](../memories.md), `access_log` in
[privacy-foundations.md](../security/privacy-foundations.md), `redaction_events` in
[write-customization.md](../ingestion/write-customization.md), and so on. Phase 1 *is* this
migration, so it is worth having in one place.

---

## The tables

### Tenancy and identity

| Table | Holds |
|-------|-------|
| `organizations` | The billing and policy boundary |
| `projects` | The isolation boundary every query is scoped to |
| `users` · `identities` | A person, and the several ways they authenticate |
| `memberships` | user × org, with role |
| `groups` · `group_members` | Principals for sharing |
| `api_keys` | **Capability-scoped**, with expiry and last-used |
| `producers` | `whk_` / `crw_` / `key_` / `upl_` — nothing writes anonymously |
| `connections` | An authorised link to a source, carrying **`scope: personal \| shared`** |

`connections.scope` is small and load-bearing: it decides the ACL at write time, and it decides
what an [account deletion](deletion.md) takes.

### Data

`data_items` is the core row, and most of its columns exist because they cannot be backfilled.

```
data_items
  data_id            ULID — the first 10 chars ARE the creation timestamp
  org_id             ─┐ cannot be backfilled — no way to reconstruct
  project_id         ─┤ which org owned a row after the fact
  producer_id        ─┘
  connection_id      nullable — null for uploads and direct writes
  external_id        caller's natural key; unique per (project, producer)

  access_level       private | org | shared | public
  shared_with        principals — jsonb

  content_text       ─┐  the three ContentRef cases,
  storage_ref        ─┤  exactly one non-null
  pending_ref        ─┘  jsonb: provider, resource_id, hints
  mime_type · size_bytes · checksum

  event_time         when the thing happened
  ingested_at        when we learned about it
  state              stored | searchable | enriched

  identifiers        text[] — MRN, docket, serial, VIN
  normalization_status · schema_version

  created_at · updated_at
  deleted_at         tombstone — invisible from this instant
  purged_at          cascade complete — the erasure certificate is issued against THIS
```

### The identifier already carries a timestamp

Every id in the system is a ULID, and a ULID is **not** a random string. It is a 48-bit millisecond
timestamp followed by 80 bits of randomness, Crockford-base32 encoded:

```
data_01JQRS3M4X 7Y8Z9A0B1C2D3E
     └────┬────┘└──────┬──────┘
   48-bit ms time   80-bit random
   (10 chars)       (16 chars)
```

Three properties fall out, and they are the reason for the choice:

| Property | What it buys |
|----------|-------------|
| **Lexicographically sortable by time** | `ORDER BY data_id` is chronological. A range scan over an id prefix is a time-range scan |
| **Creation time recoverable without a lookup** | Decode the first 10 characters. Useful in logs, in [blob keys](blob-layout.md), and during recovery when the database is what is unavailable |
| **Generated client- or server-side without coordination** | No sequence, no round trip, no collision risk at our volume |

**So there is no separate timestamp column to add** — `data_id` carries creation time, and
`ingested_at` stores it explicitly for querying. The two agree by construction because both are set
at insert.

> **`event_time` is a different thing and must stay a column.** It is when the event *happened*,
> which may precede the id by years and is **correctable** afterwards. It cannot be derived from an
> identifier, and it must never be encoded in one — a corrected `event_time` would otherwise mean a
> changed primary key.

That is the whole `event_time` / `ingested_at` distinction expressed in the identifier: **the id
tells you when we learned about it; only the column tells you when it happened.**

Two schema-level rules that are easy to lose:

- **`is_downloaded` is not a column.** It is derived — `content_text IS NOT NULL OR storage_ref IS
  NOT NULL`. A field kept in sync by hand is a field that drifts, and drifting was the original
  defect.
- **`event_time` and `ingested_at` are both required.** One column cannot carry both, and a
  timeline built on the wrong one renders perfectly while being wrong.

| Table | Holds |
|-------|-------|
| `data_versions` | Every revision, with a diff. Nothing is mutated in place |
| `chunks` | Text spans, with offsets back into the source |
| `embeddings` | `chunk_id`, the vector, **`model_id`**, `generator_version` |
| — | The lexical index is a `tsvector` **column on `chunks`**, not a table |

**`embeddings.model_id` is the single most important column in this schema.** Without it, a mixed
index cannot be identified, let alone repaired — you know ranking is wrong and cannot tell which
rows caused it.

### Four different "types", and where each one lives

The word *type* does four jobs in this system. Conflating two of them caused the original routing
defect — `source_type=DOCUMENT` with `mime_type=text/plain` reaching the PDF agent — so they are
separate columns with a stated precedence.

| Field | Question it answers | Where it lives | Authority |
|-------|--------------------|----------------|-----------|
| **`mime_type`** | What are these bytes? | `data_items` | **Authoritative** — server-sniffed |
| `source_type` | What did the producer call it? | `data_items` | **Hint only** — it can lie |
| **`data_type`** | What kind of thing is it? | `data_items` | Output of the classification cascade — decides which agent runs |
| **`target_type`** | What domain object is it? | `normalized_records` | Output of normalization — `Person`, `Message`, `Transaction` |

> **`mime_type` outranks `source_type`, and that ordering is the fix.** MIME is detected
> server-side from the bytes; a client-declared type is an injection vector that chooses which
> agent runs. `source_type` survives only as layer 2 of the
> [classification cascade](../ingestion/workers.md), below explicit caller intent and above
> payload heuristics.

The `kind` segment in the [blob path](blob-layout.md) — `raw` / `text` / `derived` — is a fifth
use of the word and is **not** a database field. It classifies the artifact, not the content.

### The normalized projection

```
normalized_records
  data_id           the item this projects
  target_type       Person | Message | Transaction | <user-defined>
  schema_version    which schema produced it — never mutated in place
  payload           jsonb — the canonical object
  identifiers       text[] — extracted here, mirrored onto data_items
  status            ok | failed
  failure_reason    why, when status is failed
```

**A projection is a derived view, not a replacement.** `data_items` keeps the original; this table
holds what normalization made of it. That separation is what allows a schema to change and the
corpus to be re-projected without re-running enrichment — and it is why a normalization failure
lands the record raw with a reason rather than rejecting the write.

Two consequences worth being explicit about:

- **`target_type` is what makes entity extraction structural rather than inferred.** If a Salesforce
  record projects to a canonical `Person`, no model needs to guess that a person is present. That
  is the argument for normalization being its own stage rather than living inside the agents.
- **`identifiers` is extracted during normalization and mirrored onto `data_items`**, because
  [correlation](../cases.md) joins on it and the join must not require a second table. The
  projection is the source; the column on `data_items` is the index.

### Memories and cases

| Table | Holds |
|-------|-------|
| `memory_types` | name, `ttl`, `on_expiry`, scope, lock state |
| `memories` | `mem_<ulid>`, mutable `type`, optional `memory_key` unique per (project, type) |
| `memory_members` | memory × data, `added_at`, **`added_by`** — explicit / routed / agent |
| `memory_links` | `part_of` / `derived_from` / `about` / `continues` / `supersedes` |
| `cases` · `case_members` | Declared subjects; membership records **asserted vs inferred** |

> **`memory_members` has no `expires_at`, and `data_items` has no `expires_at`.** Effective expiry
> is the *maximum* TTL across an item's memberships, and membership is mutable — so any stored
> answer is wrong the moment someone adds or removes a member. The sweeper computes it. This is a
> schema-level enforcement of a design rule, and adding the column back would silently break
> `orphan_delete`.

### Derived artifacts, and the provenance join

| Table | Holds |
|-------|-------|
| `artifacts` | Viewpoints, summaries, extractions — with `model_id`, `generator_version`, `served_by_model`. **Core envelope as columns** (`title`, `description`, `summary`, `keywords[]`, `language`); standard and extension fields as `jsonb` |
| **`artifact_sources`** | `artifact_id`, `data_id`, `span_start`, `span_end` |
| `entities` · `entity_contributions` | An entity, and each item that contributed to it |
| `generators` | Immutable registry — prompt, model, schema, parser, chunker, handler digest |

**The core envelope is columns, not jsonb.** `title` and `keywords` are read by every list view,
every search result and every citation, and `keywords` feeds the lexical index — all of which want
an index and a type. The overridable fields stay `jsonb` precisely because their shape is a tenant
decision. Where a field can change shape, it goes in `jsonb`; where every consumer depends on it,
it is a column.

**`artifact_sources` is a join table rather than an array column, and that is deliberate.** The
requirement is that erasure can ask *"which summaries absorbed this item?"* — an indexed reverse
lookup on `data_id`. An array column makes that a scan, and a scan is what gets skipped under time
pressure, which is how content survives inside a summary after the source is deleted.

The `span_start` / `span_end` columns are what let a citation open at the sentence, and what keeps
citations working after compression archives the originals.

`entity_contributions` exists for the same reason in a different shape: deleting an item removes
its **contribution** to an entity, not the entity.

### Where LLM output goes

Five distinct things come back from a model, and they land in four different places.

| Output | Lands in | Notes |
|--------|----------|-------|
| **Extraction** — the standard envelope | `artifacts` | Core envelope as columns, standard and extension fields as `jsonb` |
| **Embeddings** | `embeddings` | With `model_id`, which is what keeps the vector space comparable |
| **Classification** (layer 7 only) | `data_items.data_type` + `classified_by_layer` | Just a value. Recording *which layer resolved it* is what measures the "80% never reach a model" claim rather than asserting it |
| **Memory-level artifacts** — summaries, study guides, obligation extracts | `artifacts`, keyed on `memory_id` instead of `data_id` | The [`derive` generalisation](../use-cases-catalog.md) needs no new table |
| **Query answers** | `queries` · `query_sources` | **See below — this had nowhere to live** |

**The raw model response is not stored on success.** With schema-constrained output the parsed
artifact *is* the response, and `generator_version` plus `served_by_model` record what produced it.
On failure it is kept — the DLQ entry carries the raw output attached, which is the only case where
having it matters.

### Query answers, and why storing one is not obviously safe

```
queries
  query_id · user_id · project_id
  question           the text asked
  answer             nullable — see the policy below
  model_id · generator_version · served_by_model
  token_cost · latency_ms
  asked_at

query_sources
  query_id, data_id
  rank · score
  used               bool — retrieved and passed to the model, or retrieved and excluded
  excluded_reason    acl | threshold | not_yet_enriched
```

The console's *"what I asked, what was retrieved, and what it cost"* panel needs exactly this, and
`query_sources` is also what makes the [retrieval trace](../ui-sandbox.md) reconstructable after
the fact rather than only visible in the moment.

#### An answer is a derived artifact, and inherits the strictest source

This is the part that is easy to miss and expensive to retrofit.

> **An answer synthesised from three private documents contains their content.** It is not a new,
> unencumbered object because a model wrote it.

Two consequences, both of which the platform already has mechanisms for:

- **ACL** — a stored answer inherits the **strictest** ACL among its sources, exactly as every
  other derived artifact does. Otherwise a user's saved answer becomes a way to read, later and
  through a different surface, content whose permissions have since narrowed.
- **Erasure** — `query_sources` is the same shape as `artifact_sources` for the same reason.
  Delete a source document and every stored answer that quoted it must be invalidated, or this is
  [compression defeats erasure](../security/privacy-foundations.md) reappearing in a place nobody
  thought to look. **The answer is a summary; it just does not call itself one.**

#### Whether to store the answer text is a privacy decision, not a technical one

Storing every answer creates a **second corpus that is frequently more sensitive than the first** —
an answer distils precisely the parts of the corpus someone cared enough to ask about, stripped of
the surrounding context that made them innocuous.

So `answer` is nullable and governed by policy, defaulting to metadata-only:

| Setting | Stores | Suits |
|---------|--------|-------|
| `metadata` **(default)** | Question, sources, cost, timing — **not the answer text** | Everyone. The console panel works entirely from this |
| `full` | Answer text as well | Audit obligations, quality review, regulated review workflows |
| `none` | Nothing beyond the audit-log read record | Deployments where a question is itself sensitive |

The question text is stored even at `metadata`, because a query log without questions cannot answer
*"what was this key being used for?"* — but a deployment where the **question** is the sensitive
part (clinical, legal) has `none` for exactly that reason.

### Governance and operations

| Table | Holds |
|-------|-------|
| `access_log` | **Append-only.** Who read what, when, under which principal and key |
| `audit_events` | **Everything that is not a read** — writes, ACL changes, shares, config, keys, break-glass. Low volume, never purged. See [audit.md](../security/audit.md) |
| `queries` · `query_sources` | What was asked, what was retrieved and why it was excluded, what it cost |
| `runs` · `run_items` | The shared run entity — bulk write, selector delete, account delete, reprocess |
| `redaction_events` | rule id and version, action, **match count** — never the content |
| `engines` | Registered providers with **encrypted** credentials, failing closed |
| **`model_cards`** | Capabilities, hardware, licence, pricing, `verified_at` and `declared_by` per claim — the data the mapping is derived from |
| **`data_type_profiles`** | What a type *requires*: capabilities, context floor, **sensitivity class**, volume |
| `model_assignments` | Which model per `(purpose, data_type, scope)` |
| `usage_events` | Raw metered units, durably emitted — the source everything else derives from |
| `usage_records` | Rated events, stamped with **`rate_card_version`** so any period can be re-rated |
| `usage_rollups` | Hourly and daily aggregates per (account, dimension), with pointers to their event range |
| `storage_snapshots` | Daily levels in byte-days — **idempotent per (account, day)**, because storage is a level, not an event |
| `rate_cards` | Immutable price lists with `effective_from` |
| `invoices` · `invoice_lines` · `credit_notes` | Closed periods, **immutable**; a correction is a credit note, never an edit |
| `budgets` | Enforced at **user and project** scope |

`access_log` is separate from everything else because its volume, retention and access pattern all
differ: it is written on every read, never updated, queried rarely and by time range, and it must
**survive the deletion of what it describes** — you cannot evidence "we deleted it" if the evidence
was inside the deletion. Partition it by month.

---

## Why the indexes live in the record store too

`pgvector` and `tsvector` are Postgres, not separate systems. That is
[the central bet](technology.md), and its payoff is visible in the schema:

```sql
SELECT c.data_id, c.text, e.embedding <=> $1 AS distance
FROM   embeddings e
JOIN   chunks c   ON c.chunk_id = e.chunk_id
JOIN   data_items d ON d.data_id = c.data_id
WHERE  d.project_id = $2
  AND  (d.access_level = 'org' OR d.owner_id = $3 OR …)
  AND  e.model_id = $4
ORDER  BY distance
LIMIT  20;
```

The ACL predicate and the similarity search are **one query with one plan**. Filtering after
retrieval would return the wrong twenty rows and then hide some of them, which is a different and
worse thing than filtering before.

The `e.model_id = $4` predicate is the fallback-chain protection made physical: a mixed index still
returns comparable results, because the query only ever considers one vector space.

The cost is stated where it belongs — every workspace shares one instance, and filtered ANN over
tens of millions of rows is the named scaling risk.

## Indexes that are not optional

| Index | Why |
|-------|-----|
| `data_items (project_id, event_time DESC)` | Every scoped read and every timeline |
| `data_items (project_id, external_id)` unique | Upsert on the caller's natural key |
| `embeddings` HNSW on the vector, **partitioned or filtered by `model_id`** | Comparable vector spaces |
| `artifact_sources (data_id)` | The erasure reverse lookup |
| `memory_members (data_id)` | The reverse lookup that answers "why is this still here?" |
| `entity_contributions (data_id)` | Remove the contribution, not the entity |
| `normalized_records (data_id)` · `(target_type, project_id)` | The projection lookup, and facet queries by domain type |
| `query_sources (data_id)` | Which stored answers must be invalidated when a source is deleted |
| `access_log (org_id, at)` | The audit query, on a partitioned table |

Four of those seven exist for **deletion and diagnosis**, not for retrieval. That ratio is the
schema telling you what the hard operations actually are.

## Requirements

- **FR-SCH-1** All metadata MUST live in the record store. The blob store MUST hold bytes only.
- **FR-SCH-2** `is_downloaded` MUST be derived from the content columns, never stored.
- **FR-SCH-3** `event_time` and `ingested_at` MUST be separate columns, both populated.
- **FR-SCH-4** Every embedding row MUST carry `model_id`, and retrieval MUST filter on it.
- **FR-SCH-5** Effective expiry MUST NOT be stored on `data_items` or `memory_members`.
- **FR-SCH-6** Artifact provenance MUST be an indexed join table with span offsets, not an array.
- **FR-SCH-7** `access_log` MUST be append-only, partitioned, and MUST survive deletion of the data
  it describes.
- **FR-SCH-8** ACL predicates MUST be evaluated inside the retrieval query, not applied to results.
- **FR-SCH-13** A stored answer MUST record its retrieved sources, including those excluded and the
  reason for exclusion.
- **FR-SCH-14** A stored answer MUST inherit the strictest ACL among its sources.
- **FR-SCH-15** Deleting a source MUST invalidate every stored answer derived from it.
- **FR-SCH-16** Storing answer text MUST be a per-project policy, defaulting to metadata-only.
- **FR-SCH-12** Identifiers MUST be ULIDs, so creation time is recoverable from the id and ids sort
  chronologically. `event_time` MUST remain a column and MUST NOT be encoded in an identifier.
- **FR-SCH-9** `mime_type` MUST be server-detected and MUST outrank `source_type` for routing.
  `source_type` MUST be treated as a hint.
- **FR-SCH-10** The normalized projection MUST be stored separately from the original, tagged with
  the schema version that produced it.
- **FR-SCH-11** A normalization failure MUST store the record raw with a reason, and MUST remain
  retryable. It MUST NOT reject the write.
