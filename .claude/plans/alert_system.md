# Plan — the alert system: declare it, detect it, record it, deliver it

**Requirement.** Declare rules describing events worth knowing about. Evaluate
them as data is written. Record every match as a durable event. Make each event
reachable two ways — **pushed** to a subscriber, or **polled** from a cursor.

Status: **shipped.** `api/src/open_mem/alerts.py` (862 lines), 44 tests in
`tests/test_alerts.py`, and an Alerts section in the console. Surfaces, the
condition builder, backtest-before-enable and delivery state are all live.

---

## 1 · Async, debounced, with the schedule as the floor

Three things, and the middle one is what makes the first affordable:

| | When | What |
|---|---|---|
| **Capture** | Inside the write transaction | Record before → after. One insert, no alert logic |
| **Consume** | Asynchronously, off the `Queue` seam | Wake, **coalesce**, evaluate alerts whose watermark is behind |
| **Reconcile** | On a schedule | Sweep what the queue lost, deliver what is owed, fire absence events |

### Evaluation is per wake-up, never per write

Per-write evaluation is not practical: N alerts by M writes means every write
pays for every alert, and one crawl importing ten thousand items would trigger
ten thousand rounds — in `llm` mode, ten thousand model calls for a question
nobody asked urgently.

Being asynchronous does not fix that on its own. **Debounce does.** A consumer
that evaluates one message at a time is per-write evaluation with extra latency.
So the worker wakes on a transition and then *waits*: it coalesces for
`debounce_seconds` or until `batch_cap` transitions have accumulated, whichever
comes first, and evaluates once over the window.

```
transitions ──▶ queue ──▶ wake ──▶ coalesce (5s or 500) ──▶ evaluate once
                                        ▲
                          a burst of 10,000 collapses here
```

What the window buys is not merely cheapness:

- **`llm` mode becomes viable** — one call judging forty candidates, not forty
  calls. Per-message consumption could never do this.
- **Duplicates collapse** — six revisions of one document inside a window are
  one candidate.
- **Bursts are absorbed** — a crawl is a handful of windows, not ten thousand
  evaluations.

Capture stays synchronous because it is the only part that cannot be deferred:
**a transition is observable only while it happens.** Once `access_level` reads
`org`, the previous value is gone.

### The queue is not the record

Behind the `Queue` Protocol — `InProcessQueue` today, Pub/Sub when that lands.
That matters here more than elsewhere: Cloud Run runs `--min-instances 0`, so an
in-process consumer exists only while an instance is alive, and a window in
flight dies with it.

So the **watermark in Postgres is the record of what has been evaluated**, and
the queue is only how a worker learns there is something to do — the same
relation `domain_events` already has to dispatch. A lost message costs latency,
never an alert.

## 2 · What a rule watches

Rules are typed by **surface** — the kind of transition — because that decides
what fields the condition can even mention. Thirteen surfaces exist; these eight
are deterministic, need no model, and are the whole of v1:

| Surface | Emitted when | Carries |
|---|---|---|
| `data.revised` | A new `data_versions` row with a different checksum | `from_revision`, `to_revision`, `source` |
| `fact.asserted` | A row enters `entity_facts` | subject, predicate, object, `basis` |
| `fact.superseded` | `valid_to` written | the closing fact, `superseded_by` |
| `fact.retracted` | `retracted_at` written | reason |
| `memory.member_added` | `memory_members` insert | `added_by` — explicit \| routed \| agent |
| `memory.retyped` | `memories.type` changes | from, to |
| `case.member_promoted` | `case_members.basis` inferred → **asserted** | who confirmed |
| `acl.changed` | `access_level` or `shared_with` changes | from, to |

Two more that matter and are deliberately **not** write-triggered, because
nothing is written when they happen — see §7:

- `memory.expiring` — a TTL is about to lapse
- `item.erased` — a record is tombstoned

### The condition

Every rule carries a **deterministic selector**, evaluated as SQL:

```jsonc
{
  "surface": "fact.superseded",
  "where": {
    "project_id": "prj_…",
    "predicate": ["located_in"],
    "subject_type": ["person"]
  },
  "describe": null            // optional; see §3
}
```

`where` is a closed vocabulary per surface, for the same reason the predicate
list is closed: an open one degrades into unqueryable free text, and the value
of a typed condition is being able to ask for all rules that watch a field.

---

## 3 · The ladder — most rules never reach a model

1. **Surface match** — an index lookup. A `fact.superseded` transition is never
   shown to an alert watching `acl.changed`.
2. **Selector** — plain SQL over the transition payload. In `rule` mode this is
   the whole evaluation and the run ends here.
3. **`describe`** — `llm` mode only, reached **only if 1 and 2 passed**, and
   run **once over the surviving batch** rather than once per candidate.
   Schema-validated per candidate: `{data_id, matched, confidence,
   evidence_span}`. A candidate the model omits is treated as **not matched**,
   never as an error — a silent drop must not become a silent alert.

Eight of the eight v1 surfaces are fully served by steps 1–2. `describe` exists
for the case a selector cannot express — *"a customer signals they may churn"* —
and is deliberately last, not first. `classify.py` already establishes the
house rule that most traffic never reaches a model, and measures it rather than
asserting it; the same counter belongs here.

**A rule with a `describe` clause and no selector is refused.** It would run a
model against every write in the project, and the cost is unbounded in exactly
the way nobody notices until the bill.

---

## 4 · The event is recorded, then delivered

Two separate commitments, and conflating them is how a notification system
starts losing events.

```
observed_events                   -- THE RECORD
  event_id      oev_<ulid>
  sequence      bigserial          -- the poll cursor
  alert_id, config_version, run_id  -- which alert, which wording, which run
  surface       text
  org_id, project_id
  data_id, fact_id, memory_id, case_id, entity_id   -- whichever applies, nullable
  payload       jsonb              -- before/after, and the model's fields if any
  matched_by    text CHECK (matched_by IN ('selector', 'model'))
  confidence    real               -- null for selector matches
  evidence_span jsonb              -- for model matches, so a claim can be opened
  -- Visibility is the SUBJECT's, resolved at read time. Never stored as a copy:
  -- the subject's ACL is mutable and a copy is wrong the moment it changes.
  occurred_at   timestamptz NOT NULL DEFAULT now()

alerts                            -- THE CONFIGURATION, created via API/UI
  alert_id      alr_<ulid>         org_id, project_id
  name
  -- Both modes are one row and one evaluation path. `rule` stops after the
  -- selector; `llm` runs the description over whatever the selector left.
  mode          text CHECK (mode IN ('rule', 'llm'))
  surface       text
  where_clause  jsonb              -- required in BOTH modes; see §3
  describe      text               -- llm mode only
  model_id      text               -- null = the org's assigned extractor
  -- How long the consumer coalesces before evaluating. The knob that decides
  -- whether this is async batching or per-write evaluation wearing a queue.
  debounce_seconds int NOT NULL DEFAULT 5
  -- The sequence this alert has evaluated up to. Advanced ONLY by a completed
  -- run: a partial run that advanced it steps over transitions nobody ever
  -- looked at, and nothing comes back for them.
  watermark     bigint NOT NULL DEFAULT 0
  batch_cap     int NOT NULL DEFAULT 500
  overlap       text NOT NULL DEFAULT 'skip' CHECK (overlap IN ('skip','queue'))
  config_version int NOT NULL DEFAULT 1    -- bumped on any edit to matching
  backtested_version int                   -- config_version of the last backtest
  enabled       boolean NOT NULL DEFAULT false
  created_at, updated_at

alert_runs                        -- what each scheduled evaluation did
  run_id        alq_<ulid>         alert_id, config_version
  status        text CHECK (status IN ('running','completed','failed','skipped'))
  from_sequence, to_sequence bigint
  candidates, matches, deferred int    -- `deferred` is never silent (§7b)
  model_calls   int
  started_at, finished_at, error text

event_subscriptions + event_deliveries    -- as in the workflow plan (§6)
```

**Recording is the commitment; delivery is dispatch.** That is `domain_events`'
own philosophy — *"the log is the record, and the queue is only how events reach
a worker"* — applied one level up. A subscriber that is down loses nothing,
because the row is already there and the poll cursor still reaches it.

---

## 5 · Push and poll are one event read two ways

**Poll** — `GET /api/v1/events?since=<sequence>&alert_id=&limit=`. Cursor-based
on `sequence`, never on a timestamp: two events in the same millisecond are
ordered by the sequence and not by luck.

**Push** — subscriptions deliver signed HTTP, at-least-once, ordered per alert,
dead-lettered after `MAX_ATTEMPTS`. **This is the same outbound machinery the
workflow plan needs, and it should be built once.** open-mem has no outbound path
today — `webhooks.py` is inbound only, by its own docstring — so whichever
feature lands first builds it, including the SSRF control (`validate_url` at
registration *and* at every send; no redirects; https only).

### The rule that is not negotiable

**ACL is applied at read and at delivery, on the recipient's rights at that
moment — never at match time.**

A rule matching a private record must not notify someone who cannot see it.
Storing the subject's ACL onto the event and filtering against the copy fails
twice: the copy goes stale when the subject is re-shared, and a revocation
between match and delivery is ignored. So the event row stores no ACL, and both
reads join to the subject and apply `visibility_sql`.

This is the sharpest rule in the plan because **notification is a side channel
around the entire access model**: every other read path filters, and a webhook
that does not is the one door left open.

A subscription therefore has an **owner**, and delivery is filtered by that
owner's rights. A subscription with no owner cannot be created.

---

## 6 · Rules are versioned, and never re-scan

`rule_version` bumps on any edit to `surface`, `where_clause` or `describe`.
Every event records the version that fired it, or *"why did this stop matching
in March"* has no answer — the same argument `generator_versions` makes for
artifacts.

**Editing a rule never re-scans the corpus.** New rules and new versions apply
to writes from that moment. Otherwise one careless edit triggers a full scan and
a bill, and at any real corpus size the scan is not available anyway.

To see history, **backtest**:

```
POST /api/v1/alerts/{id}/backtest {"since": "2026-06-01T00:00:00Z"}
```

It replays historical transitions through **the same evaluation path**, reports
what would have fired, and **records nothing and delivers nothing**. Same
discipline as a crawler dry run — *"same code path, so what it reports is what a
live run would actually do"*. A `describe` rule is a guess until it has been
backtested, so this ships with the feature rather than after it.

---

## 7 · The two events with no write, and one ordering trap

`memory.expiring` and `item.erased` are caused by **absence**, so no write
triggers them. They come from the scheduled sweep that already exists for
expiry, not from the rule worker.

**The trap: an expiry event must fire *before* the deletion, not after.** A
`conversation` memory expiring with `on_expiry: orphan_delete` takes its members
with it. An event emitted afterwards notifies someone about data that can no
longer be shown — a citation to nothing. So the sweep emits, then deletes, and
the event payload carries enough of the record to remain meaningful once the
subject is gone.

**Erasure cuts the other way.** `observed_events` payloads may quote content, so
the log is a second place personal data hides from `verify_erasure` — the exact
objection `0021_entities.sql` raises against a second store. On erasure, events
referencing the item have their payload redacted in place, the row is kept so
the audit trail survives, and `verify_erasure` gains a check.

---

## 7b · The reconciler, and why it is not optional

The async path is the fast path. The scheduled tick is the floor beneath it, and
on this deployment the floor carries real weight:

| Without the tick | Why |
|---|---|
| A window lost with its instance is never evaluated | `--min-instances 0`, in-process queue |
| A failed delivery never retries | `next_attempt_at` implies something wakes to honour it |
| Absence events never fire | `memory.expiring` and `item.erased` have no write to ride on |

The rows are the record of outstanding work, and a sweep re-derives what is
missing rather than trusting a message to have survived.

### `open-mem-alert-tick`

A Cloud Run Job on Cloud Scheduler beside `open-mem-reconcile-tick`, every minute:

```
python -m open_mem alert-tick
  1. select alerts whose watermark is behind          FOR UPDATE SKIP LOCKED
  2. evaluate the gap in batches, up to batch_cap
  3. deliver what is owed by next_attempt_at
  4. sweep expiry and other absence-caused events (§7)
```

Steps 1–2 run **the same code** the async consumer runs. A reconciler that
re-implements the thing it repairs drifts from it, and the drift shows up as a
repair that quietly does something different from the work it stands in for —
which is exactly how `open-mem-reconcile` once re-embedded with a stale model and
concluded nothing was stale.

### Four rules carried over from crawlers, each already earned there

**The watermark advances only on a completed evaluation.** A partial one that
advanced it steps over transitions nobody looked at, and nothing comes back for
them. `crawlers.watermark` carries this comment already; the failure is silent
and permanent.

**`overlap: skip`** — an alert already evaluating when woken again is skipped,
not queued. Queueing guarantees a backlog that never drains for any alert slower
than its arrival rate.

**`batch_cap`, with the remainder recorded.** A burst must not make one window
unbounded, and the watermark carries the rest forward. **Silent truncation reads
as "nothing else matched"**, which for an alert system is the worst available
lie.

**`config_version` invalidates a backtest**, exactly as editing a crawler
invalidates its dry run.

### This changes the deployment

`open-mem-alert-tick` joins the job loop in `api/deploy/cloudrun.sh` so it cannot
drift onto a stale image, and `provision.md` gains a second scheduler entry —
with the grant that is easy to miss: **the Cloud Scheduler service agent needs
`roles/iam.serviceAccountTokenCreator`** on `open-mem-api@…`. Without it the job
never fires and the scheduler surfaces no error, which for an alert system is
silence indistinguishable from "nothing happened". `.claude/skills/deploy-gcp/`
is updated in the same commit, per its own rule.

## 8 · Implementation steps

1. **`0034_alerts.sql`** — `alerts`, `alert_runs`, `observed_events`,
   `event_subscriptions`, `event_deliveries`. Indexes: `(sequence)` for the
   cursor, `(alert_id, occurred_at DESC)`, `(status, next_attempt_at) WHERE
   status='pending'`.
2. **Transition capture** — emit `*.changed` domain events carrying before/after
   at each of the eight sites, inside the existing transactions. Small, and the
   only synchronous work.
3. **`api/src/open_mem/alerts.py`** — alert CRUD with `config_version` bumping and
   backtest invalidation, selector validation against a closed per-surface
   vocabulary, and `evaluate_batch()` implementing the §3 ladder — one batched
   model call in `llm` mode, none in `rule` mode.
4. **`AlertWorker`** — subscribes off the `Queue` seam, **coalesces** for
   `debounce_seconds` or `batch_cap` transitions, then calls the shared
   `evaluate_gap()`. The fast path.
4b. **`alert-tick`** — a `__main__.py` subcommand and a Cloud Run Job calling the
   **same** `evaluate_gap()`, plus owed deliveries and the absence sweeps.
   `FOR UPDATE SKIP LOCKED` throughout. Not optional (§7b).
5. **`api/src/open_mem/event_delivery.py`** — the outbound sender: HMAC signing as
   `0018` does inbound, `validate_url` per attempt, no redirects, backoff,
   dead-letter. **Shared with the workflow plan.**
6. **`app.py`** — alert CRUD, `GET /api/v1/events`, subscription CRUD + rotate +
   deliveries + replay, backtest.
7. **`deletion.py`** — payload redaction and the `verify_erasure` check (§7).
8. **Docs** — `docs/events.md`; the delivery contract in `docs/api.md` (headers,
   signature construction, cursor semantics); `use-cases-catalog.md`'s W10 gap
   closes for UC5, 6, 7 and 11.
9. **`api/deploy/cloudrun.sh` + `.claude/skills/deploy-gcp/`** — the new job and
   schedule, per §7b.

Steps 1–4 are one commit and testable without any network. 5–6 a second.

---

## 9 · Tests — `api/tests/test_alerts.py`

- **Write latency is independent of rule count** — 0 rules vs 50, assert the
  write path issues the same queries. The reason for the whole §1 split.
- **A selector-only rule never invokes a model** — assert the extractor is not
  called. The cost claim, measured rather than asserted.
- **A `describe` rule with no selector is refused at creation.**
- **ACL at delivery, not at match** — a rule matches a private item; a
  subscription owned by another user receives nothing, and `GET /events` returns
  nothing for them. Then share the item and assert it appears — proving the
  filter is live and not a stored copy.
- **Revocation between match and delivery is honoured.**
- **A write never evaluates an alert inline** — write with fifty enabled alerts
  and assert the write path issues no evaluation and no model call. The §1 claim.
- **A burst coalesces** — 500 transitions inside one debounce window produce
  **one** evaluation, not 500. This is the test that separates "async" from
  "per-write with extra latency", and it is the whole reason for the window.
- **The consumer and the tick agree** — evaluate the same gap through both paths
  and assert identical `observed_events`. A reconciler that drifts from the thing
  it repairs is worse than none.
- **An event survives the instance that captured it** — capture a transition,
  drop the in-process queue without draining, run `alert-tick`, assert the event
  is evaluated. Without this the feature passes everything else and does nothing
  in production.
- **`llm` mode makes one call for a batch of forty**, not forty calls. Assert
  the call count, not the result — this is the claim that makes the mode viable.
- **A candidate the model omits is not matched** — feed a response missing an id
  and assert silence rather than a fabricated alert.
- **A failed run does not advance the watermark**, and the next run re-reads the
  same range. The silent-permanent-gap failure.
- **`batch_cap` defers the remainder and records `deferred`** — 1,200
  transitions with a cap of 500 leaves a non-zero count and the next run picks
  them up. Truncation is never silent.
- **`overlap: skip`** — a due alert with a live run is recorded skipped, not
  queued.
- **A delivery is retried with no writes occurring.**
- **Two overlapping ticks do not double-send** — `SKIP LOCKED` holds.
- **Cursor is gapless and stable** — two events in the same millisecond come
  back in sequence order, and re-polling `since` never skips.
- **Rule edit does not re-scan** — assert no historical events appear.
- **Backtest records nothing and delivers nothing**, and reports the same
  matches a live run produced.
- **Expiry fires before deletion**, and the payload still identifies the subject
  after the members are gone.
- **Erasure redacts the payload**, keeps the row, and `verify_erasure` passes.
- One test per surface, asserting before/after is captured — `acl.changed` in
  particular, since the previous value is unrecoverable if missed.

---

## 10 · Out of scope for v1

Polled aggregate rules (*"sentiment turned negative"*), source-health detection
(*"a crawler that found things finds nothing"*), and the bitemporal diff
endpoint. All three are **poll-shaped, not write-shaped**, and none is served by
this mechanism — they need a scheduled evaluator and, for the first, a typed
projection to compare. The bitemporal diff is the cheapest of the three and
needs no worker at all; it is the natural next piece.

---

## 11 · Open questions

1. **Does `acl.changed` need its own capability to subscribe to?** It is the
   most security-sensitive surface, and `config:write` may be too coarse — a
   rule watching every ACL transition is a map of what is being hidden.
2. **Should `data.revised` fire for `parse`/`interpret` revisions?** Those are
   the same bytes read better, not a change in the world. My assumption: yes,
   emit with `source` in the payload, and let the selector filter — but it
   defaults to noisy.
3. **Delivery ordering: per rule, or per subject?** Plan says per rule. Per
   subject is more useful for a consumer tracking one entity, and more work.
