# Deletion

The most destructive operation in the system, and the one where "it seemed to work" is least
trustworthy — because what remains after a bad delete is invisible.

## Two different operations wearing one word

| | **Cleanup** | **Erasure** |
|---|---|---|
| Intent | "I don't want this any more" | "This person has a legal right to have it gone" |
| Initiated by | user or admin | subject request, or a compliance process |
| Grace period | **yes** — recoverable window | **no** — immediate |
| Legal hold | respected, deletion deferred | respected, returns **partial completion** |
| Audit weight | normal | **the record is the deliverable** |
| Verification | optional | **required** |

Conflating them produces one of two failures: a GDPR erasure that sits in a grace bin for thirty
days is not an erasure, and a user who fat-fingers "delete project" and cannot undo it has been
badly served. Different intents, different behaviour, one API with a `mode`.

## Scope

| Scope | Endpoint |
|-------|----------|
| One item | `DELETE /api/v1/data/{id}` |
| A selection | `POST /api/v1/deletions` with a selector |
| Everything in a project | `DELETE /api/v1/projects/{id}?purge=true` |
| Everything for a subject | `POST /api/v1/deletions` with `subject` |
| Org offboarding | `DELETE /api/v1/organizations/{id}?purge=true` |

Beyond a single item, **deletion is a job, not a request** — cascading across embeddings, chunks,
entities, graph facts, summaries and blobs takes time and must survive a worker restart. It reuses
the run entity from [bulk operations](bulk-operations.md): checkpointed, resumable, pausable, with
per-item errors.

### The selector

```
selector:
  data_ids     [...]
  producer_id  crw_… | whk_… | key_…      everything a source ever wrote
  project_id
  case_id
  tags
  source
  access_level
  time_range   { field: event_time | ingested_at, from, to }
```

**`time_range` must name its clock.** "Delete everything from 2019" means something entirely
different by ingestion time than by event time once a backfill has happened — the same request
either deletes three years of history or deletes nothing. Requiring the field makes the ambiguity
impossible rather than merely documented.

`producer_id` is the one people reach for after a mistake: a crawler misconfigured and ingested the
wrong site, and the fix is "remove everything that producer wrote."

## Dry-run is mandatory for scoped deletes

Same pattern as [crawler configs](../ingestion/crawlers.md), for the same reason:

```json
POST /api/v1/deletions   { "selector": {...}, "dry_run": true }

{ "would_delete": { "items": 12403, "embeddings": 91220,
                    "summaries_affected": 340, "blobs_bytes": "8.2 GB" },
  "withheld": { "legal_hold": 22, "reason": "matter M-2291" },
  "shared_entities_retained": 1841,
  "sample": [ … ] }
```

A destructive operation whose blast radius is only visible afterwards is not a safe operation. For
project- and org-scoped purges, dry-run plus explicit confirmation is **required**, not advisory.

## What the cascade actually touches

| Artifact | Behaviour |
|----------|-----------|
| Data item | Hard-deleted; a **metadata-only tombstone** remains — id, versions, timestamps, reason |
| Chunks, embeddings | Deleted |
| Blobs | Deleted from the object store |
| **Entities** | **Reference-counted.** An entity mentioned by fifty documents is not deleted because one is — only its contribution is removed |
| **Memory membership** | Removed. An item held by another memory survives — see [memories](../memories.md) |
| **Graph facts** | Facts sourced solely from the item are deleted; facts with other sources have that source removed |
| **Summaries** | **Marked stale and rebuilt**, not deleted — see below |
| Derived indexes | Deleted with their source |
| **Audit records** | **Survive.** They record that the deletion happened; deleting them defeats the purpose |

### Entities and summaries are where naive deletes go wrong

**Deleting an entity because one of its sources went away destroys knowledge that fifty other
documents still support.** Reference counting is the difference between removing a contribution and
removing a fact.

**Summaries are the harder case.** A summary spanning forty items, one of which is erased, still
contains the erased content in prose. Deleting the summary loses value; leaving it is a compliance
failure. The right answer reuses machinery that already exists: **mark it stale and let reprocess
rebuild it from the surviving members.** That is exactly why derived artifacts must record their
source set as a *list* from the first row — see
[privacy foundations](../security/privacy-foundations.md).

Without that list, a summary is unerasable, because nothing records that the paragraph someone
wants removed came from the document they are asking about.

## Legal hold returns partial completion

An erasure touching a case under hold does **neither** silent thing:

```json
{ "status": "partial",
  "deleted": 11890,
  "withheld": [ { "case_id": "cas_…", "items": 513,
                  "hold": "hold_…", "authority": "Matter M-2291" } ],
  "requeued_on_release": true }
```

Silently deleting held data destroys evidence someone is legally obliged to preserve. Silently
ignoring the request is a compliance failure dressed as success. The only defensible behaviour is
to do what is possible and say precisely what was not — and to re-queue automatically when the hold
lifts.

## Verification

After an erasure the job runs a verification pass: no derived artifact references the deleted
source, no blob remains, no embedding row survives, no graph fact retains it as its only source.

"We deleted it" is a claim someone may have to stand behind. Verification turns it into a checkable
one, and the result belongs in the audit record.

## Permissions

| Scope | Required |
|-------|----------|
| Own item | owner |
| Selection within a project | `member` for own data, `admin` for others' |
| Project purge | `admin` |
| Org purge | `owner`, plus typed confirmation |
| Subject erasure | `admin`, or an authenticated compliance process |

Every deletion is audited with actor, scope, mode, counts and withholdings — and audit is the one
thing a delete never touches.

## API

```
DELETE /api/v1/data/{id}                     single; idempotent, returns already_gone
POST   /api/v1/deletions                     job — selector, mode, dry_run
GET    /api/v1/deletions/{job_id}            progress, counts, withheld, errors
POST   /api/v1/deletions/{job_id}/confirm    required for project and org scope
POST   /api/v1/deletions/{job_id}/cancel     cleanup mode only, within the grace window
DELETE /api/v1/projects/{id}?purge=true      convenience over the job API
DELETE /api/v1/organizations/{id}?purge=true offboarding
```

The UI is a client of exactly these — dry-run preview, a confirmation step naming what will go and
what is held, progress while it runs, and the result with what was withheld and why.

## Requirements

- **FR-DEL-1** Deletion MUST distinguish **cleanup** (grace period, cancellable) from **erasure**
  (immediate, verified).
- **FR-DEL-2** Deletion beyond a single item MUST be a checkpointed, resumable job.
- **FR-DEL-3** A selector `time_range` MUST name which clock it applies to.
- **FR-DEL-4** Scoped deletion MUST support dry-run, and project- and org-scoped purges MUST
  require it plus explicit confirmation.
- **FR-DEL-5** The cascade MUST reach chunks, embeddings, blobs, derived indexes, entity
  contributions and graph facts.
- **FR-DEL-6** Entities MUST be reference-counted; an entity supported by other sources MUST NOT be
  removed.
- **FR-DEL-7** Summaries containing deleted content MUST be marked stale and rebuilt, not left
  intact and not silently discarded.
- **FR-DEL-8** Deletion MUST leave a metadata-only tombstone and MUST NOT delete audit records.
- **FR-DEL-9** Erasure touching held data MUST return partial completion naming what was withheld,
  and MUST re-queue on release.
- **FR-DEL-10** Erasure MUST run a verification pass, and the result MUST be recorded in the audit
  trail.
