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
| **An account's data** | `DELETE /api/v1/users/{id}/data` — see below |
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

## Deleting an account's data

Distinct from a project purge and from org offboarding, because the boundary is a **person**, not
a container — and a person's data is scattered across containers that must survive them. This is
the GDPR Art 17 case and the employee-offboarding case, and they are the same operation.

Three things make it different from any other scoped delete.

### Revoke first, then delete — the ordering is not cosmetic

An account with live connections is **still ingesting** while its data is being deleted. Delete
first and the cascade races the pipeline: items arrive behind the checkpoint, the run completes
successfully, and the account is left partially deleted and quietly re-populating.

```
1. Revoke   API keys, sessions, OAuth connections, webhook producers   ← the account stops producing
2. Verify   no producer belonging to this account is still admitted
3. Delete   the cascade, as a resumable run
4. Verify   re-query by account scope, expect zero
```

Step 1 must be **synchronous and complete** before step 3 begins. It is also the step that makes
the operation safe to interrupt: a revoked account that is half-deleted is inert, whereas a live
account that is half-deleted is a leak that regenerates.

### Connection scope already decides what gets taken

The hard question in a team context is *"a departing employee wrote 4,000 messages in a shared
channel — whose are they?"* Deleting them erases the organisation's own record; keeping them
ignores an erasure request.

**The mechanism that answers this already exists.** A connection carries a scope, and it is the
same scope that decided the ACL at write time:

| Connection scope | On account deletion |
|------------------|---------------------|
| `personal` — their own mail, their own files | **Deleted.** It was only ever theirs |
| `shared` — a team Slack, a shared drive | **Retained by the project**, with the account's link removed |

This is the [entity problem](#entities-and-summaries-are-where-naive-deletes-go-wrong) in another
costume: remove the **contribution**, not the thing. The record stays with the org that owns it;
what goes is the identity attached to it, along with anything derived that identified them.

> **This must be shown, not assumed.** The dry-run states it plainly — *"3,180 items deleted,
> 4,002 retained by shared projects with your attribution removed"* — because a person exercising
> an erasure right is entitled to know which of those two things happened to their data, and an
> organisation is entitled to know before it agrees.

### What deliberately survives

Two categories, and both are refusals the caller must see rather than silent omissions:

| Survives | Why |
|----------|-----|
| **The audit record of the deletion itself** | You cannot evidence *"we deleted it"* if the evidence is inside what you deleted. The record retains the account id, the scope, the counts and the timestamp — never the content |
| **Anything under [legal hold](#legal-hold-returns-partial-completion)** | Hold beats erasure, and the response is a partial completion **with the reason stated**, not a quiet skip |

The first is the one that surprises people: an account deletion is not complete erasure of every
row mentioning the account, and claiming otherwise would make the deletion unprovable.

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

## Deletion is asynchronous — but its *effect* is not

Every deletion, including a single item, runs as an async pipeline. The synchronous part is
deliberately tiny.

```
SYNCHRONOUS  ── one transaction, returns immediately
  1. tombstone      deleted_at = now() on the root row
  2. audit          who, what, scope, why — written in the same transaction
  3. enqueue        a deletion run

ASYNCHRONOUS ── W9, resumable, idempotent
  4. chunks · embeddings · lexical rows
  5. artifact_sources · entity_contributions   ← contributions, not the entities
  6. normalized_records · versions
  7. blobs                                     ← the network call that can fail
  8. graph facts, where a graph exists
  9. purged_at = now(), and ONLY NOW remove the root row
```

### Why the effect must still be immediate

An erasure request cannot wait in a queue to take effect. But a cascade across Postgres, pgvector,
an object store and possibly a graph store **cannot** complete inside a request — it is several
network calls to systems with independent failure modes, and a synchronous version either times out
under load or swallows failures to avoid doing so.

The tombstone resolves it: **visibility is transactional, reclamation is eventual.**

| | When | Guaranteed by |
|---|------|--------------|
| **Invisible to every read** | Immediately, at commit | The `deleted_at` predicate, applied in the query alongside the ACL predicate |
| **Physically reclaimed** | Eventually, minutes | The W9 run, resumable and monitored |

Because the filter lives *inside* the retrieval query — [the same place the ACL predicate lives](../operations/schema.md) — there is no window where a deleted item is returnable. It stops
being visible at the same instant the caller gets their response.

### A tombstone is not an erasure, and the difference must be recorded

This is the failure the design exists to prevent: a record that **looks** deleted, is invisible to
every query, and whose bytes are still in the object store because the cascade failed at step 7 and
nobody looked.

So **"deleted" has two timestamps, and both are stored:**

```
deleted_at    the tombstone — when it became invisible
purged_at     when the last derived artifact and blob were actually gone
```

> **An erasure certificate is issued at `purged_at`, never at `deleted_at`.** Reporting the first as
> though it were the second is how an organisation certifies an erasure that did not happen — in
> good faith, from a screen that said "deleted".

`deletion.oldest_pending_purge` is the metric, and it is the usual shape: nothing errors, the UI is
correct, and the only signal that anything is wrong is a number nobody is watching.

### The root row is deleted last, because it is the map

Step 9 is not stylistic ordering. **`data_items` is what tells the cascade which chunks, which
blobs, which contributions belong to this item.** Remove it first and a cascade that fails halfway
has lost the ability to find what it did not finish — the remaining artifacts become orphans with
no path back to a request that would clean them up.

Delete the dependents, then the map. The same logic applies at every level of the cascade.

### Cleanup and erasure are the same pipeline with a different delay

The [two operations wearing one word](#two-different-operations-wearing-one-word) need no separate
machinery:

| | Tombstone | Cascade enqueued | Cancellable |
|---|---|---|---|
| **Cleanup** | Immediately | **After the grace window** | Yes, until the cascade starts |
| **Erasure** | Immediately | Immediately | **No** |

A grace period is a delay before enqueueing, not a different code path — which is what keeps
"restore from the bin" and "irrevocably gone" from drifting apart in behaviour.

### What the async pipeline inherits for free

It runs on the [run entity](bulk-operations.md), so it already has what it needs:

- **Resumable** — a cascade interrupted at step 7 restarts at step 7, not step 1
- **Idempotent** — every step is `DELETE … WHERE data_id = $1`, safe to re-run
- **Per-item errors** — a single-item delete is a run of one, so the reporting is uniform
- **Backpressure** — a 50,000-item erasure does not starve ingestion; it is a queue with a priority

And the [orphan sweep](blob-layout.md) is the backstop beneath all of it: if a blob delete fails
permanently and the run gives up, the object still carries its `data_id` in metadata, and the sweep
finds it with no row behind it.

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
| **Own account data** | the account holder, or `admin` |
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
DELETE /api/v1/users/{id}/data               revoke, then cascade, as a run
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
- **FR-DEL-14** All deletion MUST be asynchronous. The synchronous portion MUST be limited to the
  tombstone, the audit record and enqueueing the run, in one transaction.
- **FR-DEL-15** A tombstoned item MUST be invisible to every read immediately, enforced by a
  predicate inside the retrieval query.
- **FR-DEL-16** `deleted_at` and `purged_at` MUST both be recorded, and an erasure certificate MUST
  be issued against `purged_at`.
- **FR-DEL-17** The root row MUST be deleted last, after every dependent artifact.
- **FR-DEL-18** Cleanup and erasure MUST use the same pipeline, differing only in the delay before
  the cascade is enqueued.
- **FR-DEL-19** Un-purged tombstones MUST be reported as a metric.
- **FR-DEL-10** Account data deletion MUST revoke keys, sessions, connections and producers
  **before** the cascade begins, and MUST verify the account is no longer producing.
- **FR-DEL-11** Account data deletion MUST delete data from `personal`-scoped connections and
  MUST retain data from `shared`-scoped connections, removing the account's attribution instead.
- **FR-DEL-12** The dry-run MUST state both counts separately — deleted, and retained with
  attribution removed.
- **FR-DEL-13** The audit record of a deletion MUST survive that deletion, carrying scope, counts
  and timestamp but never content.
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
