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
| `artifacts` | Viewpoints, summaries, extractions — with `model_id`, `generator_version`, `served_by_model` |
| **`artifact_sources`** | `artifact_id`, `data_id`, `span_start`, `span_end` |
| `entities` · `entity_contributions` | An entity, and each item that contributed to it |
| `generators` | Immutable registry — prompt, model, schema, parser, chunker, handler digest |

**`artifact_sources` is a join table rather than an array column, and that is deliberate.** The
requirement is that erasure can ask *"which summaries absorbed this item?"* — an indexed reverse
lookup on `data_id`. An array column makes that a scan, and a scan is what gets skipped under time
pressure, which is how content survives inside a summary after the source is deleted.

The `span_start` / `span_end` columns are what let a citation open at the sentence, and what keeps
citations working after compression archives the originals.

`entity_contributions` exists for the same reason in a different shape: deleting an item removes
its **contribution** to an entity, not the entity.

### Governance and operations

| Table | Holds |
|-------|-------|
| `access_log` | **Append-only.** Who read what, when, under which principal and key |
| `runs` · `run_items` | The shared run entity — bulk write, selector delete, account delete, reprocess |
| `redaction_events` | rule id and version, action, **match count** — never the content |
| `engines` | Registered models with **encrypted** credentials, failing closed |
| `model_assignments` | Which model per purpose |
| `usage_records` | Tokens per user, model and agent — estimate against actual |
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
- **FR-SCH-12** Identifiers MUST be ULIDs, so creation time is recoverable from the id and ids sort
  chronologically. `event_time` MUST remain a column and MUST NOT be encoded in an identifier.
- **FR-SCH-9** `mime_type` MUST be server-detected and MUST outrank `source_type` for routing.
  `source_type` MUST be treated as a hint.
- **FR-SCH-10** The normalized projection MUST be stored separately from the original, tagged with
  the schema version that produced it.
- **FR-SCH-11** A normalization failure MUST store the record raw with a reason, and MUST remain
  retryable. It MUST NOT reject the write.
