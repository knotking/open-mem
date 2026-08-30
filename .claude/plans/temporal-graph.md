# Plan — the temporal knowledge graph, in Postgres

**Requirement.** Make memdog's graph answer *what was true when*, and *what we
believed when* — bitemporally, with supersession instead of accumulation, and
with facts that an agent can assert directly without an LLM.

Status: **plan only, nothing implemented.** Confirm before `/implement`.

---

## 1 · The decision is already made, and it is not about cost

`0021_entities.sql` states why the graph is in Postgres:

> "The traversal has to carry the same visibility predicate as retrieval, and a
> separate store means either re-implementing that rule or post-filtering — and
> **post-filtering a graph leaks structure**, because 'three nodes are hidden
> here' discloses that they exist. Erasure has the same shape: **a second store
> is a second place personal data hides from `verify_erasure`**."

That is a privacy argument, not an operational one, and it survives any budget.
So this plan adds temporality **to the existing Postgres graph**. Graphiti stays
possible behind `GraphStore`, but it would first have to answer both objections
above — which no external graph store does today.

**Correction owed:** `architecture.md` and `technology.md` still list
Neo4j+Graphiti as the optional temporal layer, and `comparison-onyx.md` claims
`valid_at`/`invalid_at` we do not have. This work makes the claim true and the
Neo4j row wrong; both get fixed here.

---

## 2 · Two time axes, and conflating them is the classic failure

| Axis | Question | Already in memdog |
|---|---|---|
| **Valid time** | When was this true *in the world*? | `data_items.event_time`, `NOT NULL` |
| **Transaction time** | When did *we learn* it? | `created_at` everywhere |

`cases.md` already argues this distinction for timelines — *"a timeline ordered
by ingestion time is not merely imprecise, it is wrong in a way that looks
right"* — so the instinct is in the codebase; it just never reached the graph.

Bitemporal means both, together: **"as of 15 August, what did we believe was
true on 1 February?"** That is the question an auditor asks, and neither axis
alone can answer it. A backfill imported today about last year must not appear
in what we believed last month.

Valid time defaults to the source item's `event_time`. Extraction may override
it when a document states a date explicitly ("effective 1 January").

---

## 3 · Fact and evidence must be separated first

Today `entity_edges` conflates them. Its own comment says *"the same claim from
two documents is two pieces of evidence for one edge, not two edges"* — and then
the unique key is `(subject_id, predicate, object_id, source_data_id)`, so it
stores one **row per evidence** with no row for the fact itself.

That works while an edge has no properties of its own. Validity is a property of
the fact: two documents asserting the same thing with different dates would
otherwise produce two contradictory windows for one claim, and nothing to hang a
supersession on.

So split, and keep the existing table as the evidence side:

```
entity_facts                      -- NEW. The claim, once.
  fact_id        fct_<ulid>       org_id, project_id
  subject_id, predicate, object_id     → entities
  valid_from     timestamptz      -- when it became true; default source event_time
  valid_to       timestamptz      -- when it stopped; null = still true
  recorded_at    timestamptz NOT NULL DEFAULT now()    -- transaction time
  retracted_at   timestamptz      -- we no longer believe we ever knew this
  superseded_by  text → entity_facts                   -- what closed it
  basis          text CHECK (basis IN ('asserted','derived'))
  asserted_by_user_id, asserted_by_key_id              -- for basis='asserted'
  confidence     real
  access_level, shared_with[]     -- strictest() across evidence
  UNIQUE (project_id, subject_id, predicate, object_id, valid_from)

entity_edges                      -- EXISTING, becomes evidence
  + fact_id      text → entity_facts ON DELETE CASCADE
  (all current columns retained; unique key unchanged)
```

`valid_from` in the unique key is deliberate: the same claim true across two
separate periods is two facts, not one row edited twice.

**Migration `0033_temporal_graph.sql`** is additive plus a backfill: group
existing `entity_edges` by `(subject, predicate, object)`, create one
`entity_facts` row each with `valid_from = min(source event_time)`,
`recorded_at = min(created_at)`, `basis = 'derived'`, then set `fact_id`. No row
is deleted and no existing query breaks until it is changed to read facts.

---

## 4 · Supersession is deterministic, or it is dangerous

When does a new fact close an old one? The codebase's rule everywhere else is
**deterministic before probabilistic** — `cases.md` prefers an identifier join
over an LLM inference, `classify.py` runs six deterministic layers before any
model. Same here: **no LLM decides what stops being true.**

Predicate **cardinality** is declared:

| Cardinality | Predicates | Behaviour on a new fact |
|---|---|---|
| **single-valued** | `located_in`, `reports_to` | The prior open fact is closed: `valid_to` = new `valid_from`, `superseded_by` = new id |
| **multi-valued** (default) | everything else | Accumulates. Two employers, four collaborators |

**Default is multi-valued**, and single-valued is opt-in per predicate. Getting
this backwards is the expensive error: marking `works_for` single-valued would
silently close every second job as though the person had left it. Recoverable —
closing is non-destructive — but wrong in a way that reads as correct, so the
list stays short and conservative. `works_for` is **not** single-valued.

Supersession never deletes and never edits history: closing a fact writes
`valid_to` and leaves `recorded_at` alone, so "what we believed then" is intact.

---

## 5 · Direct assertion — the no-LLM path

`entity_edges.source_data_id` is `NOT NULL`, so today every fact must descend
from a document. On `entity_facts`, evidence is **optional**:

- `basis = 'derived'` — extraction produced it; one or more evidence rows.
- `basis = 'asserted'` — a principal stated it. No evidence row, no LLM, no
  cost. `asserted_by_*` records who, exactly as `domain_events` does.

`basis` reuses `case_members`' vocabulary on purpose: the asserted/inferred
distinction is already this codebase's idiom for "a person said so" versus "we
matched something", and a graph that cannot tell them apart is one nobody can
rely on for either purpose.

An asserted fact takes the asserter's connection scope for its ACL; a derived
one takes `acl.strictest()` across its evidence — the rule `acl.py` already
applies to every derived artifact, so a fact spanning a private and an org
source stays private.

---

## 6 · Reading it back

`GraphStore.neighbourhood()` gains two optional parameters, **both defaulting to
now**, so today's behaviour is unchanged:

```
neighbourhood(..., as_of: datetime | None, valid_at: datetime | None)
```

- `valid_at` filters `valid_from <= valid_at AND (valid_to IS NULL OR valid_to > valid_at)`
- `as_of` filters `recorded_at <= as_of AND (retracted_at IS NULL OR retracted_at > as_of)`

Both go **inside** the recursive CTE alongside the visibility predicate — never
as a filter over results, for the same reason `acl.py` gives and the structural
one `0021` gives.

New surface:

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/v1/entities/{id}/graph?valid_at=&as_of=` | Point-in-time traversal |
| `GET` | `/api/v1/entities/{id}/history` | Every fact touching this entity with its windows — the audit view |
| `GET` | `/api/v1/facts/conflicts?project_id=` | **Conflict surfacing** — open facts on a single-valued predicate with overlapping windows, each with its evidence |
| `POST` | `/api/v1/facts` | Assert directly. `data:write`. The no-LLM path |
| `POST` | `/api/v1/facts/{id}/retract` | Sets `retracted_at`; never deletes |

`/retrieve`'s graph arm passes `valid_at`/`as_of` through unchanged when absent.

**Conflict surfacing is what UC2 asks for**: *"graph traversal with conflict
surfacing when a doc and a later thread disagree."* With cardinality declared it
is a query, not a model call.

---

## 7 · Erasure — the part that must not be an afterthought

This is the second reason `0021` gives for staying in Postgres, so it has to
work here or the argument was hollow.

- Erasing a data item deletes its evidence rows (existing cascade).
- A **derived** fact whose last evidence is gone is **retracted**, not deleted:
  `retracted_at = now()`. Deleting it would erase the audit trail that we once
  believed it; keeping it live would assert a fact with no evidence.
- An **asserted** fact survives item erasure — it never depended on an item.
- `verify_erasure` gains a check over `entity_facts` and `entity_edges`, since
  its whole job is "re-checks every table that holds item-scoped data".

---

## 8 · Implementation steps

1. **`0033_temporal_graph.sql`** — `entity_facts`, `fact_id` on `entity_edges`,
   backfill, indexes: `(project_id, subject_id) WHERE valid_to IS NULL`,
   `(fact_id)`, and a GiST/btree pair for window overlap on the conflicts query.
2. **`graph.py`** — `Fact` dataclass, `CARDINALITY` map, `record_facts()`
   wrapping `record_edges()` (extraction writes evidence *and* the fact in the
   same transaction), supersession, temporal predicates in the CTE.
3. **`graph.py`** — `assert_fact()` / `retract_fact()`, the no-LLM path.
4. **`app.py`** — the five endpoints in §6.
5. **`retrieval.py`** — thread `valid_at`/`as_of` through the graph arm.
6. **`deletion.py` + `verify_erasure`** — §7.
7. **Docs** — `docs/graph.md` gains the temporal model; fix the Neo4j rows in
   `architecture.md` and `operations/technology.md`; `comparison-onyx.md`'s
   `valid_at` claim becomes true; `operations/schema.md` gains the table.

Steps 1–3 are one commit and are independently testable. 4–6 a second.

---

## 9 · Tests — `api/tests/test_temporal_graph.py`

- **Defaults unchanged** — every existing graph test passes untouched. The
  regression guard for "temporal is opt-in".
- **Point in time** — a fact valid Jan–Mar is returned for `valid_at=Feb`, absent
  for `valid_at=Apr`.
- **Bitemporal, the one that catches a conflated implementation** — backfill
  today a fact valid last year; `as_of` = last month must **not** return it,
  while `valid_at` = last year does.
- **Supersession** — single-valued predicate closes the prior fact with
  `superseded_by` set and `recorded_at` untouched; multi-valued accumulates.
- **Non-destructive** — after supersession the old fact is still readable at an
  earlier `as_of`.
- **Conflict surfacing** — two overlapping open facts on a single-valued
  predicate are both returned, with evidence.
- **Assertion** — `basis='asserted'` with no evidence and **no model call**
  (assert the extractor was never invoked).
- **ACL** — a fact derived from a private and an org item is private; traversal
  hides it via the CTE predicate, and the *count* of returned nodes does not
  disclose it.
- **Erasure** — erasing the only source retracts the derived fact, leaves an
  asserted one, and `verify_erasure` passes.

---

## 10 · Out of scope

Multi-hop temporal *reasoning* (as opposed to filtering), LLM contradiction
detection, entity-level validity (entities do not expire; facts about them do),
and Graphiti itself. The `GraphStore` seam carries the new parameters so a
Graphiti implementation could satisfy the same interface later — but §1's two
objections are its price of entry, not this plan's problem.

---

## 11 · Open question

**Which predicates are single-valued?** The plan proposes `located_in` and
`reports_to` only, and explicitly not `works_for`. This is the one decision that
silently corrupts data if it is wrong, and it is a domain call rather than a
technical one.
