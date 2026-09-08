# Use cases, and the memory shape each one needs

Twelve published use cases, read for one question: **what structure does each need its
memories to be in, and does the mechanism exist?**

| Document | Covers |
|----------|--------|
| [../use-cases.md](../use-cases.md) | The five families, the tensions between them, and the unifying rule |
| [../use-cases-catalog.md](../use-cases-catalog.md) | All twelve, stage by stage, with the findings each one forced |
| this file | The hierarchy across all twelve — what nests, what does not, and what is declared but dead |

---

## What can nest, and what cannot

Three containment facts decide everything below, and two of them are easy to assume the
other way round.

| Level | What it is |
|-------|------------|
| **Record** | `data_items` — the unit carrying bytes, an ACL and a state |
| **Memory** | A set of records, with a type carrying TTL, expiry policy, enrichment and whether it is a timeline. `memory_members` maps `memory_id → data_id`: a memory holds **records, never memories** |
| **Relation** | Memory to memory, via `memory_links` — a `(from, to, relation)` set with **no ordering column**. The only place a hierarchy can live |
| **Checkpoint** | A dense `seq` and a `previous_id` chain **over records**, inside one memory. The ordering exists here and nowhere else |
| **Case** | A **fourth axis, not a level** — correlation by `(case_type, external_id)`, joining records that already share an identifier across systems |

So the two mechanisms each hold half of the obvious model. Checkpoints have time-order but
over records; links reach memory to memory but carry no order. **Nothing in the schema
expresses an ordered array of memories.**

## The five relations

`memory_links.relation` is a closed vocabulary. Counted by what reads them in
`src/memdog/`, excluding the validator that lists them.

| Relation | Means | Read by | State |
|----------|-------|---------|-------|
| `part_of` | Containment. A parent's members *are* its children's members, so nothing propagates and nothing goes stale — the tree is walked at read time | `compaction.py`, `memories.contained_memories` (alert scope, tree) | **Load-bearing.** The only relation the console offers |
| `derived_from` | Generation. A rollup built *from* children, so a child changing marks the parent stale — it does not make the parent out of date, it makes it **wrong** | the staleness ancestry walk in `memories.py` | **Wired, no UI.** Creatable only through the API |
| `continues` | This memory follows that one in time — the closest thing in the schema to an ordered chain of memories | nothing | **Declared only** |
| `about` | This memory is about that one. Distinct from the `about` predicate in the entity graph, which is a different table and *is* read | nothing | **Declared only** |
| `supersedes` | This memory replaces that one | nothing | **Declared only** |

## The matrix

*Grain* is what one memory holds; *shape* is the structure the use case needs across memories.

| # | Use case | Fam | Grain — one memory is… | Shape it needs | Mechanism | Status |
|---|----------|-----|------------------------|----------------|-----------|--------|
| 1 | Personal Knowledge Base | A | One thread — `conversation`, 1h TTL | Promotion to `factual` when it turns out durable | A move between memories, not a link | Works |
| 2 | Team Memory | B | A durable body of knowledge — `organizational`, no TTL | Containment: space → area → topic | `part_of` | Works |
| 3 | Customer Intelligence | B | One ticket thread, plus a durable account memory | Correlation by account id | Case `customer` | **API only** |
| 4 | Research & Analysis | A/B | One literature review, selector-populated | Rollup across papers, citations intact | `derived_from` | **API only** |
| 5 | Compliance & Audit | E | A retention class — TTL plus `on_expiry` | None. Flat by design | Memory type policy | Works |
| 6 | IoT & Sensor Data | B/D | Raw readings — `tracing`, 3d, `orphan_delete` | Cheap raw tier → durable aggregate | `derived_from` back to the raw | **API only** |
| 7 | Legal & Contract | B/E | A matter's documents | Correlation, asserted vs inferred kept apart | Case `matter` | **API only** |
| 8 | Healthcare & Clinical | B/E | A patient record set, joined on MRN | Correlation — must never fuzzy-merge | Case `patient` | **API only** |
| 9 | Education & Training | A/B | A `course` — static, curated membership | Course → module → lesson | `part_of` | Works |
| 10 | Sales Enablement | B | A deal's history across two CRMs | Summary hierarchy — the "catch me up" path | `derived_from` + case `deal` | **API only** |
| 11 | Media Monitoring | A/B | A short-TTL firehose | Promotion of matches to a durable memory | A move, not a copy | Works |
| 12 | Meeting Intelligence | B | One meeting — a `session` memory | A recurring series, in order | `continues` to the previous instance | **Not wired** |

## What the matrix exposes

**Only one hierarchy is reachable by a person.** `part_of` is the sole relation the console
offers. `derived_from` — the one with staleness behaviour behind it, and the shape three use
cases need — exists only as a `curl`. So the rollup that goes stale when its sources change is
designed, built, tested, and unreachable from the product.

**Use case 12 was designed against a relation nothing reads.** The catalog specifies a
`continues` link from each meeting to the previous instance of the series. The relation is in
the vocabulary and no code path reads it, so the link can be written and will never affect an
answer. Meeting Intelligence is the clearest case, but any recurring artefact — a nightly
export, a weekly report — wants the same edge.

**Correlation is a separate axis, and it is currently API-only.** Four use cases — customer,
matter, patient, deal — join on `(case_type, external_id)` rather than by nesting. That is the
right mechanism: an identifier join, never entity resolution, because two patients named John
Smith must not merge. The Cases screens were removed from the console in September 2026 and the
endpoints stayed, so any of those four is a product gap today. See [../cases.md](../cases.md).

**Promotion is not a link, and should not become one.** Use cases 1 and 11 move an item from a
cheap short-TTL memory into a durable one when it proves worth keeping. That is a membership
change, not an edge — modelling it as a relation would leave the item in the firehose, expiring
on the firehose's schedule, while a link claimed otherwise.

## The proposal

**Three levels, two orthogonal axes** — not a deeper tree.

Keep the levels at three: record, memory, container. Every use case above fits and nothing asks
for a fourth. Nested memory *types* in particular are not the answer: the schema comment on
`memory_types` records that an earlier draft shipped ten types with semantics baked into each
and nearly all of it collapsed to a TTL plus an expiry policy.

Treat lineage and time as axes, not levels. `derived_from` answers *where did this come from*;
`continues` answers *what came before this*. Neither is containment, and forcing either into
`part_of` loses the property that makes it useful — staleness for the first, order for the
second.

Then three changes, cheapest first:

1. **Wire `continues`.** It needs a sequence and a reader. That unblocks use case 12 as designed
   and gives every recurring artefact an ordered chain — without promoting checkpoints to
   memories, which would make each one individually expirable, a foot-gun for a structure whose
   whole point is the chain.
2. **Put `derived_from` in the console.** The mechanism is built. It is one more option beside
   the existing parent picker, and it turns three use cases from API-only into product.
3. **Decide `about` and `supersedes`: wire them or delete them.** A closed vocabulary that
   accepts a relation nothing acts on is a promise the system does not keep — the link is
   stored, returns from the API, and changes no answer. `supersedes` is the sharper one, because
   a superseded memory currently answers queries exactly as loudly as the memory that replaced
   it.

---

Grounded in `docs/use-cases-catalog.md`, `migrations/0006_memories.sql`,
`0011_cases_shares_schemas.sql`, `0053_memory_checkpoints.sql`, and the relation reads in
`memories.py` and `compaction.py`. Status reflects the repository at `247dbaf`; nothing
described here has been changed.
