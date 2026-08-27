# Memories

A memory is a **typed container with a lifecycle**. Data items are mapped into memories; the type
decides how long they live, whether they compress, and what happens when they expire.

This is the axis on which the product differs most from adjacent memory layers — those offer three
or four fixed scopes. Here the set is configurable, which only works if the lifecycle semantics are
specified rather than implied.

> Not to be confused with a [case](cases.md). A memory is a **lifecycle** container — it answers
> "how long does this matter?". A case is a **subject** — it answers "what is this about?". A
> conversation expires; a patient does not.

## Types are configuration, not code

The system ships a working set — `conversation`, `session`, `timeline`, `tracing`, `user`,
`factual`, `episodic`, `semantic`, `organizational` — and organisations define their own.

```
MemoryType
  name              conversation | session | factual | <custom>
  scope             global | org | project
  category          conversation | session | user | organizational | custom
  default_ttl       duration, or null for never
  compressible      bool
  compress_after    item count or token threshold
  on_expiry         orphan_delete | keep_members | archive
  indexes           which index types to build for members
  locked            admin lock against lower-scope override
```

Precedence is the same as everywhere else — **project → org → shipped default** — with admin locks.
One mechanism, not a fourth.

Per-memory overrides remain: `ttl_hours` on an individual memory, or `no_expiry` to pin one.

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

## How data gets mapped at write time

Three ways, in precedence order:

| Mechanism | Example |
|-----------|---------|
| **Explicit** — the write names a memory | A client that manages its own sessions |
| **Producer default** — the producer declares a target type | A chat channel producer writes into `conversation` |
| **Routing rule** — key-based grouping, auto-creating the memory | Messages sharing a thread id collect into one conversation memory |

The routing rule is what makes conversation memory work without every caller tracking session
state. It is also the one that needs a cap: an unbounded key space creates unbounded memories.

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
- **FR-MEMT-2** The system MUST ship a working default set; no configuration may be required to
  store a memory.
- **FR-MEMT-3** A memory type MUST declare default TTL, compressibility, expiry policy and which
  index types to build for its members.
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
