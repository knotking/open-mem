# Memories

A memory is a **typed container with a lifecycle**. Data items are mapped into memories; the type
decides how long they live, whether they compress, and what happens when they expire.

This is the axis on which the product differs most from adjacent memory layers — those offer three
or four fixed scopes. Here the set is configurable, which only works if the lifecycle semantics are
specified rather than implied.

> Not to be confused with a [case](cases.md). A memory is a **lifecycle** container — it answers
> "how long does this matter?". A case is a **subject** — it answers "what is this about?". A
> conversation expires; a patient does not.

## A type is a name, a TTL, and what happens at the end

Deliberately three fields. Not a taxonomy.

```
MemoryType
  name        conversation | session | factual | <anything>
  ttl         duration, or null for never
  on_expiry   orphan_delete | keep_members | archive
```

Earlier drafts shipped ten types across four categories with semantics baked into each. That is a
lot of product opinion to impose, and most of it is expressible as *a TTL and an expiry policy* —
so the categories are gone and the set is open.

The system ships a few sensible ones and organisations add their own:

| Shipped | TTL | `on_expiry` |
|---------|-----|-------------|
| `default` | never | — |
| `conversation` | 1 hour | `orphan_delete` |
| `session` | 24 hours | `archive` |
| `tracing` | 3 days | `orphan_delete` |

Everything else is a type someone defines — commonly `factual`, `episodic`, `semantic`,
`organizational`, and **`procedural`**.

`procedural` is worth naming because the taxonomy borrows episodic and semantic from cognitive
science and the third member is the one an agent system most needs: *how we do X here* — runbooks,
workflows, learned procedures. It fits neither `semantic` (concepts) nor `factual` (assertions). Precedence for definitions is the usual
project → org → shipped, with admin locks.

**`default` exists so nothing is orphaned.** An item written with no memory and no matching routing
rule lands there. Without it, unattached data is invisible from the memory side entirely — which
would leave a hole in exactly the view memories are for.

### Type is mutable, so it is not in the identifier

```
mem_<ulid>            not  mem_<type>_<ulid>
```

If a memory can change type, an identifier encoding the type becomes a lie the moment it does. The
type is a field.

## Memories move between types

This is what makes three fields enough. You do not need ten types if a memory can be re-typed.

A conversation that turns out to contain durable facts is **promoted** rather than expiring. A
working set that has gone cold is **demoted** rather than being deleted by hand.

```http
PATCH /api/v1/memories/{id}   { "type": "factual" }
```

The TTL is recomputed from the new type, which is where the care is needed:

| Direction | Effect | Handling |
|-----------|--------|----------|
| To a longer or null TTL | Expiry is cancelled or pushed out | Safe — apply immediately |
| **To a shorter TTL** | May be **already expired** under the new type | **Preview before applying** — say what will be deleted, as with any destructive operation |

Bulk re-typing takes a selector and runs as a job, on the same run entity as bulk import and
deletion. Same dry-run, same per-item results.

Automatic promotion — rules that re-type a memory when it accumulates enough durable content — is
a later addition. It is the same shape as crawler routing: an agent may **propose** a promotion; a
rule or a person applies it.

## Membership is many-to-many

A single message legitimately belongs to a `conversation` memory (one hour), a `timeline` memory
(seven days), and may contribute to a `user` memory that never expires.

```
memory_members
  memory_id, data_id
  added_at
  added_by     explicit | routed | agent
```

`added_by` matters for the same reason it does on case membership: "this item is in this memory
because the caller said so" and "because a rule put it there" are different claims, and only one of
them should be silently re-evaluated when the rule changes.

## Memories are unique, and memories correlate

Two properties that depend on each other: **you cannot reliably correlate to something that
duplicates.**

### Uniqueness — a natural key

A memory carries an optional `memory_key`, unique within `(project, type)`:

```
memory
  memory_id    mem_<ulid>            surrogate, stable forever
  type         mutable                type is a field, not part of the id
  memory_key   natural key           unique per (project, type)
```

Writes **upsert** on it. Messages sharing a thread id land in the same `conversation` memory on
every write, from every producer, forever — rather than accumulating a new memory per batch,
per restart, or per client that forgot it had one.

Same pattern as `external_id` on data items and cases. Third use, one idea: **a caller-supplied
natural key, an internal surrogate, and upsert between them.**

Without it, routing creates a new memory whenever the router restarts, correlation points at
whichever duplicate happened to be current, and TTL expires a fragment of a conversation while the
rest lives on.

### Correlation — memories form a graph

Memories are not isolated containers. A conversation belongs to a user. A session is part of a
longer timeline. A summary is derived from the conversations it compressed. A support thread is
*about* a case.

```
memory_links
  from_memory_id, to_memory_id
  relation      part_of | derived_from | about | continues | supersedes
  created_by    explicit | routed | agent
  confidence    for derived links
```

| Relation | Example |
|----------|---------|
| `part_of` | A session inside a timeline |
| `derived_from` | A compressed summary and the conversations it replaced |
| `about` | A conversation and the [case](cases.md) it concerns |
| `continues` | Today's session resuming yesterday's |
| `supersedes` | A corrected memory replacing an earlier one |

### Declared correlation and derived correlation are different claims

The same distinction that governs [case membership](cases.md), for the same reason:

| | **Declared** | **Derived** |
|---|---|---|
| Source | A caller or a routing rule said so | Shared members, shared entities, temporal proximity |
| Authority | Authoritative | **Suggestive** |
| Use | Traversal, expiry policy, access decisions | Ranking, "related to this", discovery |
| Reversal | Explicit unlink | Recomputed whenever the signal changes |

**Derived correlation must never drive an access or lifecycle decision.** Two memories sharing
eleven data items are probably related; that is a good reason to surface one while reading the
other, and a bad reason to extend one's TTL because the other was touched.

### What correlation unlocks

- **Traversal at retrieval** — answering from a conversation and the case it is about, in one query
- **Compression lineage** — a summary that knows what it replaced, which is what makes the
  originals recoverable and the erasure cascade possible
- **Expiry that respects structure** — a session `part_of` a live timeline is not silently orphaned
- **"What else is like this"** — derived correlation as a retrieval signal rather than a link

## How data gets mapped at write time

Three ways, in precedence order:

| Mechanism | Example |
|-----------|---------|
| **Explicit** — the write names a memory | A client that manages its own sessions |
| **Producer default** — the producer declares a target type | A chat channel producer writes into `conversation` |
| **Routing rule** — key-based grouping, auto-creating the memory | Messages sharing a thread id collect into one conversation memory |

The routing rule is what makes conversation memory work without every caller tracking session
state. It is also the one that needs a cap: an unbounded key space creates unbounded memories.

## Membership is mutable — memories are formed, not just routed

Routing at write time is the convenience. The primitive is that **any item can be added to or
removed from any memory at any time**, so memories are formed after the fact as readily as during
ingestion.

```http
POST   /api/v1/memories/{id}/members      add — item ids, or a selector
DELETE /api/v1/memories/{id}/members/{data_id}
POST   /api/v1/memories/{id}/members:bulk selector-based add or remove, as a job
```

Adding by **selector** is what makes "create a memory dynamically" a real operation rather than a
loop: create a memory, point it at everything tagged `incident-4471` from last Tuesday, and it is
populated.

### Remove is not delete

Removing a member **unmaps** it. The data item is untouched — it keeps its other memberships, its
embeddings, its entities and its place in retrieval.

Deleting is a [different operation](operations/deletion.md) with a different endpoint and a
cascade. Conflating them is how someone tidies a memory and loses data, so they are not the same
verb and the API does not let them be confused.

### Nothing becomes orphaned

Removing an item's **last** membership moves it to the project's `default` memory rather than
leaving it unattached. The invariant holds: every item is in at least one memory, so the memory
view is always complete and "which memories hold this?" always has an answer.

### Static and dynamic membership

Two kinds of memory, and the second is where "dynamically" earns its name:

| | **Static** | **Dynamic** |
|---|---|---|
| Defined by | An explicit member list | A **selector**, re-evaluated |
| Changes when | Someone adds or removes | The underlying data changes |
| Suits | A curated set, an incident, a reading list | "Everything tagged urgent", "this month's invoices" |
| Membership provenance | `explicit` | `routed` |

A dynamic memory is a saved selector with a lifecycle attached. Its members change without anyone
touching it — which is powerful and needs two guards:

- **TTL applies to the container, not to computed membership.** A dynamic memory with a one-hour
  TTL expires the *memory*; `orphan_delete` then only reaches items nothing else holds
- **Selector evaluation is bounded**, exactly as crawler scope is. An unconstrained selector on a
  large corpus is an expensive query someone will schedule

### Membership changes are a staleness trigger

A memory-level artifact — a summary, a compression, a memory-scoped embedding — is derived from its
member set. **Adding or removing a member marks those artifacts stale**, exactly as it does for
[case artifacts](cases.md).

Which is the same machinery again: the artifact records its member set as a list, the list changed,
reprocess rebuilds. Nothing new to build.

## Expiry is deletion, and needs reference counting

**This is the part that goes wrong if it is treated as a cleanup job.**

A `conversation` memory expires after an hour. Its members include a message that is *also* in a
`factual` memory that never expires. Deleting the conversation's members deletes data the factual
memory still depends on — and nothing errors, because from the conversation's point of view the
operation succeeded.

Exactly the shape of the entity problem in [deletion](operations/deletion.md): remove the
*contribution*, not the thing.

So `on_expiry` is a policy per type:

| Policy | Behaviour | Suits |
|--------|-----------|-------|
| **`orphan_delete`** | Remove membership; delete the data item **only if no other memory holds it** | `conversation`, `session` — ephemeral context |
| `keep_members` | Remove the memory, leave the data | `timeline` — the container was a view, not an owner |
| `archive` | Compress into a summary, archive originals | `session` at threshold, per the compression policy |

**Default to `orphan_delete`, never to unconditional delete.** The failure mode of the conditional
version is retaining slightly more than necessary. The failure mode of the unconditional version is
silent data loss from a container the user did not think of as owning anything.

Expiry runs through the same cascade as any other deletion — chunks, embeddings, blobs, entity
contributions, summaries marked stale — because an expiring memory that leaves orphaned embeddings
behind is a slow leak that only shows up as a storage bill.

## Effective TTL is computed, not stored

Many-to-many membership plus mutable membership has a consequence worth stating explicitly:

> **An item's effective TTL is the maximum across all the memories that hold it — and it changes as
> membership changes.**

An item in a one-hour `conversation` that is then added to a permanent `factual` memory has just
become permanent. Remove it from the factual memory and it becomes deletable again, subject to
whatever else still holds it.

That is correct behaviour. The problem is that it makes "when does this expire?" **not a property
of the item**. There is no `expires_at` column that is true, because the answer is derived from a
set that changes.

### So expose the computation, do not store the answer

| Anti-pattern | Why it fails |
|--------------|-------------|
| An `expires_at` column on the item | Wrong the moment any membership changes, and nothing recomputes it reliably |
| Earliest membership TTL | Deletes data a permanent memory still depends on — the `orphan_delete` bug in another form |
| Nothing at all | "Why did this vanish?" and "why is this still here?" become unanswerable |

The reverse lookup carries it:

```http
GET /api/v1/data/{id}/memories
```

```json
{ "effective_expiry": null,
  "reason": "held by a memory with no TTL",
  "memberships": [
    { "memory_id": "mem_01J…", "type": "conversation", "added_by": "routed",
      "expires_at": "2026-08-27T11:04:00Z" },
    { "memory_id": "mem_01J…", "type": "factual", "added_by": "explicit",
      "expires_at": null }
  ] }
```

`effective_expiry` with the **reason** is the whole point. "Held by a memory with no TTL" answers
the question in one read; a list of timestamps the caller has to reduce does not.

The expiry sweeper computes the same thing rather than reading a column — which is what makes
`orphan_delete` correct by construction instead of by remembering to check.

### Removing a membership can make something deletable

Worth surfacing in the UI, because it is the one non-obvious destructive side effect in the whole
memory model: unmapping an item from the memory that was keeping it alive schedules its deletion.

The reverse-lookup view should say so before the removal, not after.

## Compression, and what it does to membership

Compression summarises a memory's members into one artifact and archives the originals. Two
consequences already designed elsewhere, restated because they meet here:

- The summary records its **member set as a list**, which is what makes a later erasure of one
  member possible ([privacy foundations](security/privacy-foundations.md))
- Archived originals remain searchable with `include_archived=true`, so compression is a retrieval
  default rather than a deletion

## Seeing the mapping

Both directions are needed, and only one of them is obvious:

| Query | Answers |
|-------|---------|
| `GET /api/v1/memories/{id}/members` | What is in this memory — with each item's `added_by` and the memory's remaining TTL |
| **`GET /api/v1/data/{id}/memories`** | **Which memories hold this item** — the one that explains why something did or did not expire |

The second is the diagnostic that matters. "Why is this still here?" and "why did this vanish?" are
both answered by the membership list, and neither is answerable from the memory side alone.

The write response should carry it too: an item written into a routed conversation memory should
say so, rather than leaving the caller to discover the mapping by querying.

## API

```
CRUD  /api/v1/memory-types                 typed config, per scope, with lock state
CRUD  /api/v1/memories
GET   /api/v1/memories/{id}/members
POST  /api/v1/memories/{id}/members        explicit mapping
GET   /api/v1/data/{id}/memories           reverse lookup
POST  /api/v1/memories/{id}/compress
PATCH /api/v1/memories/{id}                ttl_hours, no_expiry
```

Everything is available in the UI at the same granularity — memory-type editor with TTL and expiry
policy, a memory browser showing members and time remaining, and the reverse lookup on any item.

## Requirements

- **FR-MEMT-1** Memory types MUST be configurable, not hardcoded, with precedence
  project → org → shipped default and admin locks.
- **FR-MEMT-2** The system MUST ship a small working set including a `default` type, so that an
  item written with no memory is never orphaned and no configuration is required to store one.
- **FR-MEMT-3** A memory type MUST be definable as a name, a TTL and an expiry policy. Richer
  attributes MAY be added later but MUST NOT be required.
- **FR-MEMT-4** Membership MUST be many-to-many, and MUST record whether it was explicit, routed or
  agent-assigned.
- **FR-MEMT-5** Expiry MUST NOT delete a data item that another memory still holds. The default
  policy MUST be `orphan_delete`, never unconditional deletion.
- **FR-MEMT-6** Expiry MUST run the full deletion cascade, so no orphaned embeddings, chunks or
  blobs survive it.
- **FR-MEMT-7** The API MUST expose both directions of the mapping — the members of a memory, and
  the memories holding an item.
- **FR-MEMT-8** A write response MUST report which memories the item was mapped into.
- **FR-MEMT-9** Routing rules MUST be bounded, so an unbounded key space cannot create unbounded
  memories.
- **FR-MEMT-10** A memory MUST support an optional natural key, unique within (project, type), and
  writes MUST upsert on it.
- **FR-MEMT-11** Memories MUST support typed relationships to other memories and to cases.
- **FR-MEMT-12** Correlation MUST record whether it was declared or derived, and **derived
  correlation MUST NOT drive access or lifecycle decisions**.
- **FR-MEMT-10** A memory MUST support an optional natural key, unique within (project, type), and
  writes MUST upsert on it.
- **FR-MEMT-11** Memories MUST support typed relationships to other memories and to cases.
- **FR-MEMT-12** Correlation MUST record whether it was declared or derived, and **derived
  correlation MUST NOT drive access or lifecycle decisions**.
- **FR-MEMT-13** A memory's type MUST be mutable, and the identifier MUST NOT encode it.
- **FR-MEMT-14** Re-typing MUST recompute TTL. Moving to a shorter TTL MUST preview what would be
  deleted before applying.
- **FR-MEMT-15** Bulk re-typing MUST run as a job with dry-run and per-item results.
- **FR-MEMT-16** Membership MUST be mutable after write — items addable and removable individually
  and by selector, the latter as a job.
- **FR-MEMT-17** Removing a member MUST NOT delete the data item; unmapping and deletion MUST be
  distinct operations.
- **FR-MEMT-18** Removing an item's last membership MUST place it in the project's `default`
  memory. No item may be left unattached.
- **FR-MEMT-19** Memories MUST support dynamic membership defined by a bounded selector, and
  dynamic membership MUST be recorded as `routed` rather than `explicit`.
- **FR-MEMT-20** A membership change MUST mark memory-level derived artifacts stale.
- **FR-MEMT-21** An item's effective expiry MUST be computed as the **maximum** TTL across its
  memberships. It MUST NOT be stored as a column on the item.
- **FR-MEMT-22** The reverse lookup MUST return the effective expiry together with the reason for
  it and the per-membership detail.
- **FR-MEMT-23** Removing a membership that would make an item deletable MUST surface that effect
  before the removal is applied.
