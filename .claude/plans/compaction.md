# Plan — compaction, and the lifecycle underneath it

**Requirement.** Compact a memory: fold its members into a derived artifact so
the corpus stops growing without bound, the way mem0 does — but without mem0's
central move, which is to overwrite.

Status: **shipped.** `api/src/open_mem/compaction.py` (600 lines), 12 tests.
Folding, the job card, provenance as a source list, and owner-scoped summaries
are live; a private summary is readable by its owner, which it once was not.

---

## 1 · The finding that reorders everything

**TTL is declared and never enforced.** `memory_types.ttl_seconds` and
`on_expiry` are stored, `effective_expiry()` is computed — and it is called in
exactly one place, to *display* on an item's detail view. There is no sweep. A
`conversation` memory with a one-hour TTL is still there next year, and
`orphan_delete` and `archive` have never run.

So the request is really two features stacked, and the lower one is missing:

| | State |
|---|---|
| **Expiry** — TTL actually lapsing, and `on_expiry` firing | Declared, unenforced |
| **Compaction** — members folded into an artifact, originals archived | Designed in `memories.md`, unbuilt |

Building compaction first would produce a system that compacts on request and
still never expires anything, which is the smaller half of the problem.

---

## 2 · The difference from mem0, and it is the whole design

**mem0 compacts by overwriting.** Its reconciliation decides ADD / UPDATE /
DELETE against existing memories, and what it replaces is gone.

open-mem cannot do that, and not as a matter of taste. The temporal graph just
shipped on the opposite premise: a claim is **closed**, never replaced, so
`as_of` can still answer what was believed in March. A compaction that destroys
its inputs would make `as_of` lie about everything it touched.

`memories.md` already states the rule:

> Archived originals remain searchable with `include_archived=true`, so
> compression is a **retrieval default rather than a deletion**.

That single sentence is the design. Compaction changes what retrieval returns by
default; it does not change what exists.

### And it is about volume, not truth

Worth separating explicitly, because mem0 conflates them and the conflation is
tempting:

| Question | Mechanism |
|---|---|
| *Is this still true?* | `entity_facts` — supersession, already built |
| *Do we still need all of it in the working set?* | Compaction |

A compaction that decided facts were obsolete would be a second, weaker
supersession with no validity window and no evidence trail. Compaction reads
members and writes a summary. It does not adjudicate.

---

## 3 · Compaction is a generator, not a special case

The use-case catalog already found this: **derived artifacts are hardcoded to
`summary`**, and study guides, flashcards, obligation extracts and customer
briefings are the same operation with a different output schema. Its proposal
was `POST /api/v1/memories/{id}/derive {generator}` over the existing
`generator_versions` registry.

Compaction is one generator among those. Building it as a one-off would mean
building the generic mechanism twice, and the second time is always the one that
does not get the staleness handling.

Everything follows from that registry for free: an artifact records the
`generator_version` that produced it, so changing the prompt makes every summary
detectably stale; W7 reprocess already knows how to rebuild what a stale
generator produced; and erasure already cascades through `artifact_sources`.

---

## 4 · Three triggers, deliberately not one

| Trigger | When | Who runs it |
|---|---|---|
| **Expiry** | TTL lapses on a type whose `on_expiry` is `archive` | The sweep (§5) |
| **Threshold** | A memory passes a declared member count or byte budget | The sweep |
| **Explicit** | `POST /memories/{id}/derive` | A person or an agent |

The first two need the sweep that does not exist. The third is an endpoint and
could ship first, which makes it the natural way to get the generator path
tested before anything runs unattended.

---

## 5 · The sweep, and the ordering trap

`open-mem-alert-tick` already runs every minute and already carries an
absence-events step. Expiry belongs beside it rather than in a fourth job — but
**it must emit before it destroys.**

A `conversation` memory expiring with `on_expiry: orphan_delete` takes its
members with it. An event emitted afterwards notifies somebody about data that
can no longer be shown: a citation to nothing. So the order is fixed —

```
1. find memories whose effective expiry has passed
2. emit `memory.expiring`, carrying enough of the record to stay meaningful
3. then apply on_expiry
```

`effective_expiry` is the **maximum** TTL across an item's memberships and is
**computed, never stored** — `0006_memories.sql` is explicit that a stored
answer is wrong the moment somebody adds or removes a member. The sweep must
recompute per item rather than trusting a column, and `orphan_delete` must only
take members that are orphaned *across every memory*, not merely absent from
this one.

---

## 6 · What archiving actually is

A new column, not a new state.

```sql
ALTER TABLE data_items ADD COLUMN archived_at timestamptz;
```

`state` is the readiness staircase — `stored` → `searchable` → `enriched` — and
archival is orthogonal to it: an archived item is still enriched, still
embedded, still findable when asked for. Folding archival into `state` would
make "is this searchable" and "is this in the working set" the same question,
and they are not.

Retrieval excludes archived by default and includes it on `include_archived`.
That is a one-clause change to the filter, and it is the entire user-visible
effect of compaction.

---

## 7 · The summary has to point into its sources

`artifact_sources` already carries `span_start` and `span_end`, which is the
prerequisite the catalog flagged as **cheap now and impossible after compression
runs**:

> A viewpoint that says *"three papers dispute this"* is worth nothing without
> the ability to open each one at the sentence.

So a compaction artifact records, per member, the span it drew on. Without it a
summary can name its sources and not point into them, and every citation in a
compacted memory silently degrades to a document-level reference — which reads
as working.

**Erasure already has the join it needs.** `artifact_sources` exists precisely to
answer *which artifacts absorbed this?*, `verify_erasure` already counts it, and
the deletion path already marks summaries stale. Compaction adds volume to that
path, not a new problem — provided the member list is a list.

---

## 8 · Implementation steps

1. **`0038_archival.sql`** — `data_items.archived_at`; `memory_types.compact_after_items`
   and `compact_after_bytes`, both nullable; partial index for the sweep.
2. **`compaction.py`** — `derive(memory_id, generator)`: gather visible members,
   run the generator, write the artifact with `artifact_sources` **including
   spans**, mark members archived. One transaction: an artifact whose sources
   were not recorded is unerasable, and an archive without an artifact is data
   loss.
3. **`POST /api/v1/memories/{id}/derive`** — explicit trigger, the testable one.
4. **Expiry sweep** in `alert-tick`: recompute effective expiry, emit
   `memory.expiring`, then apply `on_expiry` (§5).
5. **`retrieval.py`** — exclude `archived_at IS NOT NULL` by default;
   `include_archived` on the filter.
6. **`deletion.py`** — erasing an archived member still cascades; `verify_erasure`
   gains an archived-item check so "erased" cannot mean "merely archived".
7. **Docs** — `memories.md` compression section becomes description rather than
   design; `usage.md` gains a scenario; the catalog's Gap 2 closes for UC9.

Steps 1–3 are one commit and testable without any scheduling.

---

## 9 · Tests — `api/tests/test_compaction.py`

- **Compaction does not delete.** After compacting, every member is still
  fetchable by id and still returned with `include_archived=true`. *The test the
  feature exists to pass.*
- **`as_of` is unaffected.** A temporal query over a compacted memory returns
  what it returned before. Compaction is volume, not truth.
- **Spans are recorded**, and a citation opens its source at the right offset.
- **Erasing a compacted member** marks the artifact stale and `verify_erasure`
  passes — archived is not a hiding place.
- **Expiry emits before it destroys**, and the event still identifies the
  subject once the members are gone.
- **`orphan_delete` respects other memberships** — an item in two memories, one
  expiring, survives.
- **Re-compacting is idempotent** while members and `generator_version` are
  unchanged, and produces a new artifact when either moves.
- **A compacted memory is still countable** — `list_memories` member counts do
  not silently drop archived members without saying so.

---

## 10 · Open questions

1. **Does compaction ever delete?** The plan says never — archive only. A
   retention policy that genuinely must delete is `POST /deletions`, which
   already exists and produces a certificate. Keeping them separate means
   "compact" can never be the verb that loses data.
2. **Threshold defaults.** A memory type could carry `compact_after_items`. My
   assumption is null everywhere by default: automatic compaction on a corpus
   nobody has looked at is how people discover the feature by missing something.
3. **Should the summary itself be a member of the memory?** It makes the memory
   self-describing and makes recursive compaction possible; it also makes member
   counts mean two things.
