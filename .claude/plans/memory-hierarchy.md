# Plan — hierarchical memories, and change that travels up

**Requirement.** Two memories converge into a third. A change in either is
visible at the one above, and an alert can be set there.

Status: **plan only, nothing implemented.** Confirm before `/implement`.

---

## 1 · The structure already exists. The gap is propagation.

`memory_links` has been there since `0006` and was finished in `0029`:

```sql
relation IN ('part_of', 'derived_from', 'about', 'continues', 'supersedes')
created_by IN ('explicit', 'routed', 'agent')   -- who claimed it
confidence real                                 -- only meaningful for a guess
```

Both directions are indexed, because *"what is derived from this?"* and *"what
is this derived from?"* are both asked. So **nothing needs a new table to say
two memories roll up into a third.** What does not exist is anything that
happens when a child changes.

## 2 · Two hierarchies, and the schema already names them

The distinction that matters is already the difference between two existing
relations, and conflating them is the main way this feature goes wrong.

| | `part_of` | `derived_from` |
|---|---|---|
| The parent is | A **container** — the children's items *are* its items | A **generated artifact** — a summary, a rollup, a status |
| Reading it | A join. No model, no cost, never stale | Reads whatever was last generated |
| A child changing | Changes the parent by definition, instantly | Makes the parent **stale**; it must be recomputed |
| Costs | Nothing | A model call per recompute |

**`part_of` needs no propagation at all.** That is the whole point of it: the
parent has no separate state to keep in sync, so there is nothing to be stale.
`derived_from` is where propagation is real, and it is the existing staleness
problem rather than a new one — `current_generators`, `stale_generator` and
`POST /reprocess` already exist for exactly this.

## 3 · For alerts, propagate nothing. Widen the scope instead.

This is the recommendation, and it is a much smaller change than it sounds.

An alert scope is one line today:

```python
"SELECT data_id FROM memory_members WHERE memory_id = $1 AND data_id = ANY($2)"
```

Single-level, so an alert on the parent never sees a child's items. Make that a
recursive walk **down** `part_of` at match time and an alert on the parent sees
every descendant's changes — with **no new writes, no duplicated events, no
propagation storm, and no cycle risk in the write path**, because nothing is
being written at all. The hierarchy is read where it is used.

The alternative — emitting a parent transition every time a child changes —
costs an event per level per item, has to be kept consistent, and turns one bulk
crawl into thousands of alert evaluations. It is the obvious design and the
wrong one.

> This also follows the house rule the graph is built on: **the ACL is a
> predicate inside the query, never a post-filter.** Scope resolution belongs in
> the same place, for the same reason.

## 4 · The parent takes the strictest ACL of its children

`acl.strictest()` already exists and is already used for derived artifacts. A
rollup is a derived artifact, so this is not a new rule — but it must be applied
deliberately, because the failure is a disclosure.

A parent summarising four memories is visible only to whoever can read **all
four**. That will surprise people; it is the same conclusion the meeting-digest
case reached, and it is correct. The alternative is a rollup that says out loud
what one of its sources was restricted about.

## 5 · The four things that will actually break

Concrete, because the general versions are easy to agree with and still ship.

**Cycles.** `PRIMARY KEY (from_memory, to_memory, relation)` prevents a
duplicate link, not a loop. `A part_of B part_of A` makes any recursive walk
non-terminating. Reject a link that would close a cycle **at write time** —
which is one `WITH RECURSIVE` check in `link()` — rather than defending in every
reader.

**Depth.** Even acyclic, a deep chain multiplies work per evaluation. Cap it,
and **say when the cap was hit** — *"nothing else matched"* and *"we stopped
looking"* must not share a rendering.

**Fan-out on recompute.** One child changing marks every ancestor stale. A bulk
import changing 40 children marks the same ancestors stale 40 times. Staleness
must be a **flag, not a queue entry** — idempotent, so re-marking costs nothing
and one recompute clears it however many children moved.

**A diamond.** Two children rolling into one parent is the *requested* shape, so
a memory reachable by two paths is normal, not an edge case. Every walk must
`DISTINCT` on memory, or a shared ancestor is counted twice.

## 6 · What "a change goes up" should mean

Deterministic before probabilistic, which is the rule the rest of this system is
built on:

1. A child changes → every `derived_from` ancestor is **marked stale**. No
   model, no cost, cannot be wrong.
2. Stale is **visible** — on the memory, in the console, and queryable. A
   consumer can decline to use a stale rollup rather than silently using one.
3. Recompute is **deliberate** — scheduled, or triggered, through the existing
   compaction/reprocess path with its preview gate. Not automatic on write,
   which is the same mistake the alert system started with and had to undo:
   *"alert will trigger per data write, this is not practical."*
4. Alerts read **through** the hierarchy (§3) and need none of the above.

## 7 · Implementation steps

1. **Cycle rejection** in `memories.link()` — before anything reads the graph.
2. **`GET /memories/{id}/tree`** — ancestors and descendants, with depth and a
   cap that reports itself. Read-only, and it makes everything after it
   debuggable.
3. **Recursive `part_of` in alert scope** (§3) — the one change that delivers
   the requested behaviour. A sample alert on a parent, fired by a write to a
   child, is the proof.
4. **Strictest-ACL on rollup creation** (§4).
5. **`stale_at` on a derived memory**, marked on child change, cleared on
   recompute (§6).
6. **Console**: the tree on the memory screen, a stale badge, and the alert
   editor saying *"includes N child memories"* when a scope is hierarchical —
   because a scope that silently means more than it says is the alerts screen's
   original sin repeated.

Steps 1–3 are a complete vertical slice and worth stopping at: they deliver
exactly what was asked — two memories converging, a change visible above, an
alert on the parent — without building recomputation at all.

## 8 · Open questions

1. **Does a rollup have its own members, or only inherited ones?** I assume
   inherited-only for `part_of`, so there is one place a member lives. A parent
   with both is defensible but makes "what is in here" two questions.
2. **Should `about` and `continues` propagate?** I assume not: `about` is a
   topic pointer and `continues` is a sequence, and neither implies containment.
   Only `part_of` is walked, and `derived_from` marks stale.
3. **Who may link two memories?** Linking a memory you can read to one you
   cannot is an ACL question the current `link()` may not ask. Worth checking
   before §3 widens what a scope reaches.
