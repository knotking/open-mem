# Plan — CDC between checkpoints, and the event it maps to

**Requirement.** For a memory that is a checkpoint timeline, answer *what
changed between these two points*, and put that answer on the event stream
where a consumer can subscribe to it.

Status: **the adjacent case is shipped; the range case is not, and the event
that carries either one is thinner than the read is.** This plan separates
what already works from what does not, names one defect that makes a declared
feature silently match nothing, and then builds the missing grain.

---

## 1 · What is already supported

The chain from a write to an outbound delivery exists end to end:

| Step | Where | What it does |
|---|---|---|
| **Capture** | `memories.py:131-145` | A member added to a timeline type becomes a checkpoint row, in the write transaction. One INSERT. |
| **Announce work** | `checkpoint.captured` → topic `checkpoint` (`events.py:54`) | The summarise-and-compare is queued, not inline. |
| **Compare** | `checkpoints.run_check` (`checkpoints.py:183`) | Describes the record, compares against `previous_id`. Checksum short-circuit, drift guard, `first`/`changed`/`unchanged`/`incomparable`. |
| **Structured delta** | `extraction.CHANGE_PROPERTIES` (`extraction.py:562`) | `kind`, `statement`, `earlier_value`, `later_value`, `significance` — not prose. |
| **Announce the change** | `checkpoint.changed` → topic `alerts` (`events.py:47`) | A `domain_events` row, in a transaction, with a monotonic `sequence`. |
| **Subscribe** | `alerts.evaluate_gap` → `observed_events` → `event_delivery.enqueue` | Selector or LLM match, then **push** (webhook) or **poll** (`GET /api/v1/alert-events`). |
| **Read** | `GET /api/v1/memories/{id}/checkpoints` | Timeline newest-first, each entry carrying its `change_fields.changes`. |
| **Repair** | `recheck`, `reconcile.py:201`, `pending()` | A stuck checkpoint is swept and re-published. |

So **CDC between *adjacent* checkpoints is done, and it is already mapped to an
event.** What follows is what that sentence does not cover.

---

## 2 · The defect: a selector that can never match

`alerts.SURFACES` declares `checkpoint.changed` selectable on
`{memory_type, changes}` (`alerts.py:57`). `alerts.validate` accepts a
`where` whose root is a declared field. But the payload emitted by `run_check`
is:

```python
payload={"memory_id": ..., "checkpoint_id": ..., "seq": ..., "changes": N}
```

— `checkpoints.py:358-361`. There is no `memory_type` in it.

So an alert saying *"tell me when a `vendor_feed` changes"* is accepted, saved,
enabled, backtested green against nothing, and **matches nothing forever**.
`dig` returns `None`, `in` is `False`, and a rule that matches nothing looks
exactly like a quiet week. `checkpoint.captured` gets this right
(`memories.py:144`); `checkpoint.changed` does not.

This is the same class of failure the alerts module already documents about
fact scoping — *"an alert on 'a reading of this book was replaced' looked
exactly like a quiet week"* (`alerts.py:150-158`). It is one line of payload
and one regression test, and it goes first because everything below makes the
surface wider and would carry the bug forward.

---

## 3 · The gap: the event carries a count, not the change

The delta lives in `artifacts.fields.changes`, reachable through the timeline
endpoint. The **event** carries `changes: 5`. A subscriber therefore gets a
webhook that says *something moved, five times* and must come back with a
second authenticated call to find out what.

That breaks two things that matter:

- **A selector cannot reach the content.** "Tell me when the price changes" or
  "only high-significance removals" are not expressible, because
  `significance`, `kind` and `statement` are not in the payload. The only
  available filter is the count.
- **A push delivery is not self-contained.** `event_delivery` sends the
  observed event. A CDC consumer building a downstream table has to make a
  round trip per event, with a token, against a record it may not be able to
  read.

**Fix: a bounded digest in the payload, not the whole delta.** An unbounded
`changes[]` on the write path is a jsonb row that grows with the document, and
the generator is already allowed 20 changes per comparison
(`derive.py:167`). So:

```python
payload = {
    "memory_id": ..., "memory_type": ...,          # §2
    "checkpoint_id": ..., "seq": ...,
    "change_artifact_id": ...,                     # the whole delta, fetchable
    "changes": 5,                                  # unchanged meaning
    "added": 1, "removed": 0, "changed": 4,        # scalars, so `gt` works
    "highest_significance": "high",                # scalar, so `in`/`eq` works
    "statements": [ ...first 5, 200 chars each ],  # so `contains` works
}
```

Every added field is a **scalar or a short bounded list**, chosen against the
operator table rather than for tidiness: `OPERATORS["in"]` is `v in arg`, which
is false for every list-valued `v`, so a list-valued payload field is only
reachable through `contains` and its stringify-and-substring semantics. Scalars
are what make a selector precise. `SURFACES["checkpoint.changed"]` widens to
match, and the widening is what `validate` checks against — so the two cannot
drift again without a test failing.

> **Not per-change events.** One event per change row would multiply delivery
> volume by the change count and tell a consumer nothing the digest plus
> `change_artifact_id` does not. Alerts coalesce and dedupe per event
> (`alerts.py` `AlertWorker`); per-change grain fights that directly.

---

## 4 · The gap: a check that ran and found nothing emits nothing

`run_check` emits only on `outcome == "changed"` (`checkpoints.py:352`).
`unchanged`, `incomparable`, `first` and `failed` are written to the row and
never reach the stream.

The table went to some trouble to keep *"compared and found nothing"* distinct
from *"never compared"* — two columns, and a constraint, and a comment saying
why. **The event stream collapses that distinction back down.** A consumer
maintaining a downstream watermark cannot tell a quiet feed from a broken one,
which is precisely the thing a change detector must never be ambiguous about.

**Fix: `checkpoint.checked`, emitted on every terminal exit of `run_check`,**
carrying `outcome` and `reason` alongside the §3 digest. New entries in
`TOPIC_FOR_EVENT` (→ `alerts`), `SURFACES` (`{memory_type, outcome, changes,
highest_significance}`) and `SUBJECT_OF` (→ `memory`).

This makes two alerts people actually want expressible for the first time:

- *"tell me when this feed stops being comparable"* — `{"outcome":
  ["incomparable"]}`, which today is only visible by eye in the console.
- *"tell me if the nightly export has not changed in a week"* — the absence
  case the reconciler already fires for other surfaces.

`checkpoint.changed` is left exactly as it is. Existing alerts keep their
meaning; the new surface is opt-in. The cost is alerts traffic roughly doubling
on a timeline that mostly does not change — acceptable, because the worker
coalesces, and a checkpoint check is already a model call, so one insert is
noise beside it.

---

## 5 · The headline gap: CDC between *any* two checkpoints

Today the only comparison is N against N-1. The question people ask a timeline
is *"what has changed since Monday"* or *"since the version I last synced"*,
and there is no way to ask it. Partially derivable client-side from the
timeline endpoint — which is exactly why it should be a server surface, before
three callers each write a different half-correct version of it.

Two ways to answer, and they give **different answers on purpose**:

| | How | Cost | Reports |
|---|---|---|---|
| **Composed** | Concatenate the stored `changes[]` of seq A+1…B | Free, deterministic | **Churn** — green→red→green is two changes |
| **Net** | One comparison of state(A) against state(B) | One model call | **Net** — green→red→green is nothing |

> **Recommend: both, defaulting to composed, and the response says which one it
> is.** Composed is free and every claim in it is already backed by a stored
> artifact a citation can open. Net is the one worth a model call, and it is
> what "what changed since I last synced" usually means. Silently picking one
> is the failure here: the two disagree, and a consumer that does not know
> which it got cannot reconcile them.

### The two things a range must not do quietly

**A range with a hole must not read as complete.** If any checkpoint in A+1…B
is `failed`, `incomparable`, or still `pending`, the composed answer is missing
that segment. It returns `gaps: [{seq, outcome, reason}]` and the consumer
decides. Returning the deltas that happen to exist, with nothing said, is the
same lie as a failed check reading as "nothing changed".

**A range must not be unbounded.** Cap the span (500 checkpoints) and refuse
past it with a message naming the cap, rather than truncating to the most
recent N and presenting it as the range that was asked for.

### Net answers are stored, not recomputed

A net range costs a model call, and the same range will be asked for
repeatedly by a polling consumer. It is stored as an ordinary
`checkpoint_change` artifact — members are the two **records**, so the ACL and
the erasure cascade work the way §`store_artifact` already guarantees — plus an
index row:

```sql
-- 0057_memory_change_ranges.sql
CREATE TABLE memory_change_ranges (
    range_id            text PRIMARY KEY,
    org_id, project_id, memory_id  ...,
    from_checkpoint_id  text NOT NULL REFERENCES memory_checkpoints ON DELETE CASCADE,
    to_checkpoint_id    text NOT NULL REFERENCES memory_checkpoints ON DELETE CASCADE,
    -- Part of the key, not a column beside it. Two ranges computed by
    -- different generator versions are different answers, and serving the old
    -- one after a prompt change is the drift this feature already refuses
    -- to do at the adjacent grain.
    generator_version   text NOT NULL,
    change_artifact_id  text REFERENCES artifacts ON DELETE SET NULL,
    status, outcome, reason, created_at ...,
    UNIQUE (memory_id, from_checkpoint_id, to_checkpoint_id, generator_version)
);
```

The same drift guard applies: if state(A) and state(B) were written by
different generator versions, the range is `incomparable` and says so, and
`recheck` on the earlier one is the way forward — identical to
`run_check`'s rule, reusing it rather than restating it.

### Surface

```
GET /api/v1/memories/{memory_id}/changes?from=<seq|checkpoint_id|timestamp>
                                        &to=<...>          # default: head
                                        &net=false
```

```json
{ "from": {"checkpoint_id": ..., "seq": 3, "at": "..."},
  "to":   {"checkpoint_id": ..., "seq": 7, "at": "..."},
  "basis": "composed",
  "changes": [ { "seq": 4, "checkpoint_id": "...", "kind": "changed",
                 "statement": "...", "earlier_value": "green",
                 "later_value": "red", "significance": "high" } ],
  "gaps":   [ { "seq": 6, "outcome": "incomparable", "reason": "..." } ],
  "counts": { "added": 1, "removed": 0, "changed": 4 } }
```

Each composed row carries its own `seq` and `checkpoint_id`, so every claim in
a range opens onto the record it came from.

### The event for a range

A range is a **read**, not a transition — nothing happened when someone asked
for one, so nothing is emitted. The event-shaped version of this question is a
*standing* range — *"every Monday, what changed since last Monday"* — and that
is `.claude/plans/standing-queries.md`, not this plan. The seam it will need is
`memory_change_ranges` plus a `checkpoint.range_computed` surface; both are
cheap to add on top of the above and neither is built here.

---

## 6 · Phases

| | Work | Touches |
|---|---|---|
| **1** | §2 defect + §3 digest. Payload widened, `SURFACES` widened, regression test that an alert on `memory_type` matches | `checkpoints.py`, `alerts.py`, `tests/test_checkpoints.py`, `tests/test_alerts.py` |
| **2** | §4 `checkpoint.checked` on every terminal exit | `checkpoints.py`, `events.py`, `alerts.py`, tests |
| **3** | §5 composed range: `from`/`to` resolution, gaps, cap, endpoint | new `checkpoints.range()`, `app.py`, tests |
| **4** | §5 net range: migration 0057, cached artifact, drift guard, `?net=true` | migration, `checkpoints.py`, `app.py`, tests |
| **5** | Console: a "since" control on the memory timeline; docs in `docs/memories.md` and `docs/alerts.md`; changelog | `ui/components/Console.tsx`, `ui/lib/types.ts`, docs |

Phases 1 and 2 are small and independently shippable, and 1 is a bug fix that
should not wait behind the rest. Phase 3 is the useful half of the headline
feature at zero model cost; phase 4 is the half that costs money and is the
right place to stop if the composed answer turns out to be what people meant.

Per `CLAUDE.md`: straight onto `main`, `commit-to-main` before each commit,
`changelog` after each. No CI, so each phase carries its tests in the same
commit.

---

## 7 · What could still go wrong, said out loud

- **Composed and net disagree and someone averages them.** Mitigated by
  `basis` being required in the response and never defaulted silently.
- **A digest that grows.** `statements` is capped at 5 × 200 chars; the
  generator is capped at 20 changes. Both caps are asserted in tests, because
  a payload that grows with the document is a write-path regression nobody
  notices until the events table is the largest thing in the database.
- **`checkpoint.checked` doubling alert volume.** Acceptable given coalescing;
  revisit if a project with a large quiet timeline sees the tick dominate.
- **The change prompt still names fields that were renamed.** `derive.py:167`
  instructs the model about `` `before` `` and `` `after` ``; the schema now
  has `earlier_value` and `later_value` (`extraction.py:576-591`), and the
  rename is documented there as *the* fix that took the generator from 7,944
  tokens to 534. The prompt is constraining fields that no longer exist while
  the ones that do go unmentioned. One-line fix, carried in phase 1 since
  everything below trusts this generator's output.
- **A range over a memory whose type stopped tracking change.** Existing
  checkpoints are kept when tracking is switched off (the console already says
  so), so a range over them stays valid and answerable. No special case
  needed — worth a test rather than a code path.
