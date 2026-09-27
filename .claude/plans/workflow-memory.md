# Plan — workflow memory

**Requirement.** Add a primitive to open-mem for **long-running state machine
instances** — a versioned definition whose state graph may contain cycles, an
instance whose state advances on input from heterogeneous actors (robots, AI
agents, humans) or on the expiry of a deadline, and a read path any permitted
user can query for current state and history.

Status: **shipped.** `api/src/open_mem/workflows.py` (648 lines), 15 tests.
Definitions, conditional transitions, the race check, actor guards, deadlines
and re-folding state from the log are live, with a Workflows console section.

**Confirmed architecture (Parag).** The workflow **engine lives outside
open-mem**. It calls open-mem's API to record input, and it **receives triggers**
from open-mem when state changes. So open-mem is the *system of record* — state,
history, deadlines, notification — and the outside system is the *executor*.
Every decision below follows from that split, and it is what makes D4's line
("delivers that it happened; does not interpret an answer") a description of
the deployment rather than a self-imposed limit.

---

## 1 · One naming correction, because it changes the design

A **cyclic DAG is a contradiction** — the A in DAG is *acyclic*. What is being
described is a **directed state graph that may contain cycles**, which is the
right shape for a workflow and is strictly harder than a DAG in three ways
this plan has to answer:

- **No topological order.** You cannot precompute a linear sequence of states,
  so "progress" cannot be measured as depth. `stuck` is not derivable from
  position; it needs a deadline.
- **Termination is not guaranteed.** `review → changes_requested → review` is a
  legitimate cycle and an infinite loop is indistinguishable from it, except by
  a budget. See D8.
- **Re-entry.** A state can be visited many times, so "when did this enter
  `blocked`" has many answers and the history — not the current state — is the
  thing worth storing well.

The plan uses **state graph**; nothing else changes because of the wording.

---

## 2 · What this lands on

open-mem already has most of the hard parts, and the plan's main job is to avoid
building second versions of them.

| Existing | What it gives this feature |
|---|---|
| `domain_events` (`0017`) | An append-only log with a `bigserial` sequence, `caused_by`, actor columns, and the stated philosophy that **the log is the record and the queue is only dispatch**. The transition log is the same idea applied to one instance. |
| `cases` / `case_members` (`0011`) | The pattern for a **declared, never inferred** subject: stable `external_id`, upsert on `(project, type, external_id)`, membership carrying `basis` provenance. |
| `memories` (`0006`) | TTL and `on_expiry` semantics, and the discipline that **effective expiry is computed, never stored** because membership is mutable. |
| `crawlers` (`0020`) | The exact mechanics for time-driven work: `next_due_at`, a `tick` that selects what is due, `config_version` invalidating an approval on edit, `overlap` refusing to queue a backlog. |
| `Principal` (`auth.py`) | Robots, agents and humans are already one type — `user_id` + optional `key_id` + `capabilities`. No new actor model is needed. |
| `acl.py` | ACL as a **predicate inside the query**, never a post-filter. |

**Nothing workflow-shaped exists yet** — `grep -ri workflow` over `api/src` and
`docs` returns only prose about the `procedural` memory type. This is greenfield.

---

## 3 · Which primitive? A new one.

`cases.md` already argues this shape of question, so this follows its form.

| Candidate | Why not |
|---|---|
| **Memory** | A memory answers *how long does this matter*. A workflow instance has a lifecycle but its **state is not a lifecycle** — `awaiting_signature` is not a TTL policy. Re-typing a memory is cheap; re-stating an instance is the whole domain. |
| **Case** | Closest. A case is a declared subject with a stable external id and its own ACL — and a workflow instance **is** a subject. But a case has no state, no transition log, no config version and no deadline, and adding all four to `cases` would make every patient and legal matter carry a state machine it does not have. |
| **Entity** | **The dangerous one, for the same reason cases.md gives.** Entities are extracted and entity resolution actively merges similar ones. Two purchase orders in the same state must never merge. Authoritative instances cannot be probabilistic. |
| **`crawl_runs`-style status column** | That is a fixed five-state lifecycle in code. This one is caller-defined, cyclic and versioned. |

**Decision: new tables, related to a case rather than replacing it.** A patient
is a case; *the intake workflow for that patient* is an instance that may
reference `cas_…`. Keeping them separate means one patient can carry three
concurrent workflows and closing a workflow does not close the patient.

---

## 4 · Decisions and risks

### D1 — State is a fold over the transition log. The column is a cache. **(recommended)**

Three options:

| | Pros | Cons |
|---|---|---|
| **A. `current_state` column only** | One row read; trivial | No history; "how did it get here" unanswerable; a bad write is unrecoverable |
| **B. Log only, fold on read** | Perfect history; state is provably derived | Every state query folds N transitions — unacceptable for the *"any user can query state"* requirement |
| **C. Log is the record, column is a cache written only inside the same transaction as the append** ✅ | O(1) reads; full history; a re-fold can *prove* the cache correct | Two things to keep in step |

Take **C**, and pay for the con explicitly with a verifier — `verify_state()`
that re-folds the log and compares, the same shape as the existing
`verify_erasure()`. That single function is what makes C honest rather than
merely convenient, so it is not optional and ships in the same commit.

### D2 — Concurrency: a conditional append, or the whole thing is a lie

Two actors — a robot poller and a human clicking approve — read state `review`
in the same second and both fire a transition. Naive `UPDATE … SET state` gives
last-write-wins and **silently loses a transition**, which in a workflow is not
a race, it is a corrupted history.

Every transition therefore carries **`expected_seq`** (or `expected_state`), and
the append is conditional:

```
INSERT … SELECT … WHERE current_seq = $expected     -- 0 rows ⇒ 409
```

Loser gets **409 with the current state in the body**, so a robot can re-read
and decide, rather than retrying blind. `crawlers.control_run` already returns
409 for "run is already completed", so the status code is consistent with the
codebase.

### D3 — Deadlines are transitions, not a separate mechanism

A state may declare `ttl_seconds` and an `on_timeout` target. **The queue cannot
do this** — `queue.py` has only retry backoff (`deferrals`), no scheduled
delivery. So it uses the crawler mechanics verbatim: a `deadline_at` column, a
partial index, and a tick job.

The subtle part: **a timeout fires through the same conditional append as any
other input.** A timer that raced a real input and lost simply gets 0 rows and
does nothing. Any other design double-fires.

`deadline_at` is recomputed on every transition, and is **null when the current
state declares no TTL** — so the index stays small.

> Note while implementing: `crawling._tick` selects due rows with `ORDER BY
> next_due_at LIMIT $1` and **no `FOR UPDATE SKIP LOCKED`**. With
> `--max-instances 4` two ticks could double-start a crawl. The workflow tick
> must use `SKIP LOCKED`; whether to fix the crawler one is a separate call.

### D4 — A transition **publishes an event outward**; it still does not orchestrate

Directed by Parag: *when a transition fires, the trigger's event must be
communicated outside.* The user records input, the transition fires, and an
external system hears about it. This is now a **first-class requirement**, not
a door left open.

The boundary moves but does not disappear. Two things were previously collapsed
into one refusal, and only the second is still out:

| | v1 |
|---|---|
| **Announcing that state changed** — signed, retried, ordered delivery to a subscriber | **IN — required** |
| **Executing business effects and reacting to their outcome** — saga, compensation, retrying downstream work, child workflows, waiting on a reply | **OUT** |

The line: **open-mem delivers "this happened"; it does not wait for or interpret
an answer.** A 200 from a subscriber means received, not agreed. If a subscriber
wants to advance the workflow, it comes back through `POST /input` like any
other actor — which keeps one write verb and keeps the transition log the record.

**The load-bearing consequence: a failed delivery must never roll back or block
the transition.** The transition committed; delivery is at-least-once afterwards
and independently. Any other choice means one unreachable subscriber wedges the
state machine, and the state machine is the thing of value.

#### There is no outbound path in open-mem today

`webhooks.py` is **inbound only** — its own docstring says so, and
`/producers/{id}/test-delivery` signs a payload and posts it to open-mem's *own*
receive path, which is a self-test rather than delivery. `0018_webhooks.sql`
records what arrived. So this is genuinely new surface, and the plan grows by
one worker, two tables and a security control.

Three existing pieces carry most of it:

- **`events.dispatch_pending`** is already an at-least-once driver:
  `attempts`, `MAX_ATTEMPTS = 5`, `last_error`, and `redeliver_after_seconds`
  which recovers events stranded at `dispatched` — written for exactly the
  "lost between publish and handler" case.
- **The inbound HMAC scheme** — `x-signature` + `x-signature-timestamp`,
  SHA-256, secret encrypted at rest, **with a rotation overlap window**
  (`previous_signing_secret_ct`, added in `0018` because rotation without one
  is an outage). Outbound should sign **identically**, so a subscriber verifies
  the same way open-mem asks providers to, and rotation is already solved.
- **`fetching.validate_url`** — the SSRF refusal. See D11; it is not optional.

### D10 — Delivery is ordered per instance, and `caused_by` already does it

A subscriber that observes `approved` before `review` is worse off than one that
observes both late — it will conclude the workflow skipped a step. Delivery must
therefore be **ordered within an instance** (and needs no ordering at all
across instances).

This needs **no new machinery**. `dispatch_pending` already holds an event back
while `caused_by` is unfinished — that is how "enrichment never runs before the
data exists" is made true across processes. So **chain each transition's event
`caused_by` to the previous transition's event on the same instance**, and
per-instance ordering falls out of the existing query.

The rest of the delivery contract, mirroring what open-mem already demands of
inbound providers:

- **Idempotency** — every delivery carries `x-delivery-id` and the
  `(instance_id, seq)` pair, so a receiver can dedupe a retry. open-mem requires
  this of providers; it should not ship an outbound path that fails its own bar.
- **At-least-once, with backoff**, capped at `MAX_ATTEMPTS`, then **dead-lettered
  to a visible `failed` row** — never dropped silently. A subscriber that has
  been down for an hour must be discoverable in one query.
- **A dead-lettered delivery does not stall the instance's later deliveries
  forever.** After the cap, the chain is released and the gap is recorded, so
  one bad delivery is not a permanent outage for that instance. This is the one
  place ordering is deliberately traded for liveness, and it must be visible.
- **Per-subscription concurrency of one**, which gives ordering for free and
  stops a busy instance stampeding a small endpoint.

### D11 — Outbound webhooks are an SSRF hole aimed at this deployment specifically

The most serious risk in this plan, and it is deployment-specific rather than
theoretical. A subscription URL is **caller-supplied**, and open-mem will POST to
it from inside the VPC — where Cloud SQL sits at private IP `10.100.0.3`,
reachable by direct VPC egress, and the GCP metadata server answers at
`169.254.169.254`. A subscriber pointed at either is an authenticated request
from a trusted position.

`fetching.validate_url` already refuses private and link-local addresses,
metadata hosts and non-http schemes. It must be applied **twice**:

1. **At registration** — reject the URL immediately, where the error is useful.
2. **At every delivery attempt** — because a hostname that resolved to a public
   address at registration can resolve to `10.100.0.3` later. Registration-time
   validation alone is defeated by DNS rebinding, and this is exactly the
   pattern `crawlers._get` re-validates for on every redirect hop.

And, unlike the crawler: **outbound deliveries do not follow redirects at all.**
A 3xx is a failed delivery. Re-validating each hop is defensible for a crawler
whose job is following links; for a signed POST it is unnecessary risk for no
benefit.

Two more, cheaply:

- **`https` only** for subscription URLs, since the payload carries workflow
  state. Allow `http` only for a loopback target in dev, if at all.
- **The signing secret is never readable back** through the API — shown once at
  creation, like `bootstrap-to-secret` refuses to print into a log. A secret a
  `config:write` credential can read back is a secret shared with everyone
  holding one.

> Deployment note: Cloud Run runs with `--vpc-egress private-ranges-only`, so
> public traffic already leaves directly rather than through the VPC. That is
> the right setting and it limits blast radius, but it does **not** substitute
> for `validate_url` — private ranges are precisely what still routes inward.

### D5 — Definitions are versioned; instances pin their version

Same rule as `crawlers.config_version`. Editing a definition **must not**
retroactively re-interpret a running instance, and must not strand an instance
in a state the new version deleted. So:

- `config_version` bumped on every edit to states or transitions
- instance stores `definition_version` and is evaluated against it
- a new version is opt-in per instance via an explicit `migrate` call that
  **requires a state mapping** for any state that disappeared, and refuses
  otherwise

### D6 — Actors are already modelled; guards get them for free

Robots and AI agents authenticate with API keys, humans with Firebase — both
land on one `Principal`. So a transition records `actor_user_id` / `actor_key_id`
exactly as `domain_events` does, plus a derived `actor_kind`
(`human | key | system`), where `system` is the timeout tick.

This makes a genuinely useful guard nearly free: a transition may declare
`requires_capability` or `requires_actor_kind: human`, which expresses **"a
robot may propose, only a person may approve"** using the capability model that
already exists. Recommended for v1 — it is a few lines and it is the thing
regulated buyers ask for first.

### D7 — Who can read state

The requirement says *any user can query the state*. Two different reads:

- **Current state** — cheap, non-sensitive, the thing dashboards poll. Readable
  at the instance's ACL.
- **History and inputs** — carries payloads and actor identity. ACL-filtered per
  transition, as a predicate in the query.

Recommend the instance carries its **own ACL like a case** (default `org`), and
that history rows inherit it, with the linked data items keeping theirs. Do not
let a transition payload become a way to read an item the caller could not
otherwise see.

### D8 — Cycles need a budget

`review → changes_requested → review` is legitimate; a timer ping-ponging
between two states forever is not, and they look identical. Add
`max_transitions` on the definition (default e.g. 1000) and a
`transitions_per_hour` ceiling. On breach the instance moves to a terminal
`errored` state with the reason recorded — **visible failure beats a silent
loop that bills for it**. `usage.py`/`quota.py` already exist if metering is
wanted later.

### D9 — Why this belongs in open-mem at all

Worth stating in the docs, because a reviewer will ask. The value is not
orchestration; it is that **the workflow's history becomes memory**. Each
transition may reference a `data_id` (the email that triggered it, the signed
PDF, the agent's reasoning), those items enrich and embed through the existing
path, and `/retrieve` and `/ask` can then answer *"why is order 4471 still in
`blocked`, and what did the agent say when it put it there?"* — citing the
passage. That question is unanswerable in a conventional workflow engine and it
is the reason to build this here rather than adopt one.

---

## 5 · Data model

New migration **`api/src/open_mem/migrations/0033_workflows.sql`** (next free
number; `0032_item_metadata.sql` is current). Sketch, not final DDL:

```
workflow_definitions
  definition_id   wfd_<ulid>      org_id, project_id
  external_id     stable, UNIQUE (project_id, external_id)
  name, description
  config          jsonb   -- states[], transitions[], initial, terminal[]
  config_version  int NOT NULL DEFAULT 1
  max_transitions int NOT NULL DEFAULT 1000
  created_at, updated_at, deleted_at

workflow_instances
  instance_id     wfi_<ulid>      org_id, project_id
  definition_id   → workflow_definitions
  definition_version int          -- pinned; see D5
  external_id     UNIQUE (project_id, definition_id, external_id)
  case_id         → cases  NULL   -- the subject this workflow is about
  current_state   text NOT NULL   -- CACHE. See D1.
  current_seq     bigint NOT NULL DEFAULT 0   -- optimistic concurrency token
  deadline_at     timestamptz     -- null when the state has no TTL
  status          text CHECK (status IN ('running','completed','errored','cancelled'))
  access_level, shared_with[]     -- its own ACL, like a case
  transition_count int NOT NULL DEFAULT 0
  created_at, updated_at, closed_at

workflow_transitions              -- THE RECORD
  transition_id   wft_<ulid>
  instance_id     → workflow_instances ON DELETE CASCADE
  seq             bigint NOT NULL          -- per instance, gapless
  from_state, to_state, trigger  text      -- trigger: input name | '@timeout'
  actor_user_id, actor_key_id    text
  actor_kind      text CHECK (actor_kind IN ('human','key','system'))
  data_id         → data_items NULL        -- the input, as memory
  payload         jsonb NOT NULL DEFAULT '{}'
  event_id        → domain_events NULL
  occurred_at     timestamptz NOT NULL DEFAULT now()
  UNIQUE (instance_id, seq)

workflow_instance_members         -- mirrors case_members, so retrieval works
  instance_id, data_id, basis, added_at

workflow_subscriptions            -- WHERE a trigger's event goes (D4)
  subscription_id wfs_<ulid>      org_id, project_id
  definition_id   → workflow_definitions  NULL = all definitions in project
  event_names     text[]          -- '{*}' for every event this definition emits
  url             text NOT NULL   -- https only; validate_url at write AND send
  signing_secret_ct bytea NOT NULL          -- encrypted, never readable back
  previous_signing_secret_ct bytea          -- rotation overlap, as producers do
  signing_secret_rotated_at timestamptz
  enabled         boolean NOT NULL DEFAULT true
  created_at, updated_at

workflow_deliveries               -- mirrors webhook_deliveries, outbound
  delivery_id     wfx_<ulid>
  subscription_id → workflow_subscriptions ON DELETE CASCADE
  transition_id   → workflow_transitions   ON DELETE CASCADE
  status          text CHECK (status IN ('pending','delivered','failed','dead'))
  attempts        int NOT NULL DEFAULT 0
  response_status int
  last_error      text
  next_attempt_at timestamptz
  created_at, delivered_at
  UNIQUE (subscription_id, transition_id)   -- at-least-once, deduped at source
```

Indexes for delivery: `(status, next_attempt_at) WHERE status = 'pending'` for
the sender; `(subscription_id, created_at DESC)` for the "is my endpoint
healthy" query; `(status) WHERE status = 'dead'` so a dead letter is one query
away rather than a log search.

**The transition declares what it emits; the subscription declares where it
goes.** Parag's wording — *the event as defined in the trigger* — is the first
half: a transition entry carries `"emit": {"event": "order.approved"}`. The
second half is deliberately **not** in the definition body, because an endpoint
and a signing secret have a different lifecycle and a different ACL from a state
graph. A secret living in a config blob that any `config:write` credential can
read back is a secret shared with everyone holding one (D11).

Indexes: `(project_id, current_state)` for the dashboard query;
`(deadline_at) WHERE deadline_at IS NOT NULL AND status = 'running'` for the
tick; `(instance_id, seq DESC)` for history; `(data_id)` reverse lookup.

**Definition config shape** (validated by a Pydantic model, as `CrawlerConfig`
is in `crawlers.py:146`):

```jsonc
{
  "initial": "draft",
  "states": {
    "draft":    {},
    "review":   {"ttl_seconds": 172800, "on_timeout": "escalated"},
    "approved": {"terminal": true}
  },
  "transitions": [
    {"from": "draft",  "on": "submit",  "to": "review"},
    {"from": "review", "on": "approve", "to": "approved",
     "requires_actor_kind": "human",
     "emit": {"event": "order.approved"}},        // fired outward, D4
    {"from": "review", "on": "reject",  "to": "draft"}      // the cycle
  ]
}
```

Validation must reject at definition time: an unreachable state, a transition
naming a state that does not exist, no initial state, a terminal state with
outgoing transitions, and an `on_timeout` target that does not exist. Cycles are
**allowed** — that is the point — so the validator must not use a DAG check.

---

## 6 · API surface

Follows `app.py` conventions (`@app.post("/api/v1/…")`, capability via
`principal.require`, module holds the logic).

| Method | Path | Capability | Notes |
|---|---|---|---|
| `PUT` | `/api/v1/workflows` | `config:write` | Upsert a definition by `external_id`; bumps `config_version` |
| `GET` | `/api/v1/projects/{id}/workflows` | `data:read` | List definitions |
| `POST` | `/api/v1/workflows/{id}/instances` | `data:write` | Start; idempotent on `external_id` |
| `POST` | `/api/v1/instances/{id}/input` | `data:write` | **The one verb that moves state.** Body: `{trigger, expected_seq?, data_id?, payload?}` → 200 with new state, or **409** with current |
| `GET` | `/api/v1/instances/{id}` | `data:read` | Current state, seq, deadline, counts — the poll endpoint |
| `GET` | `/api/v1/instances/{id}/history` | `data:read` | ACL-filtered transitions, newest first |
| `GET` | `/api/v1/instances/{id}/verify` | `data:read` | Re-fold, prove the cache (D1) |
| `POST` | `/api/v1/instances/{id}/migrate` | `config:write` | Move to a newer definition version with a state map (D5) |
| `GET` | `/api/v1/projects/{id}/instances` | `data:read` | Filter by state, definition, overdue — the dashboard |

**Subscriptions — where a trigger's event goes (D4):**

| Method | Path | Capability | Notes |
|---|---|---|---|
| `POST` | `/api/v1/workflow-subscriptions` | `config:write` | Register a URL; **returns the signing secret once and never again** |
| `GET` | `/api/v1/workflow-subscriptions` | `config:write` | List; secret never included |
| `POST` | `/api/v1/workflow-subscriptions/{id}/rotate` | `config:write` | New secret, old one honoured for an overlap window |
| `DELETE` | `/api/v1/workflow-subscriptions/{id}` | `config:write` | |
| `GET` | `/api/v1/workflow-subscriptions/{id}/deliveries` | `config:write` | Attempts, statuses, last error — mirrors `/producers/{id}/deliveries` |
| `POST` | `/api/v1/workflow-subscriptions/{id}/replay` | `config:write` | Re-send a dead-lettered delivery after the endpoint is fixed |

**The delivery open-mem sends**, signed exactly as the inbound path expects
providers to sign (D4), so a subscriber verifies the same way:

```http
POST <subscription.url>
x-delivery-id: wfx_...
x-signature-timestamp: 1735689600
x-signature: <hmac-sha256(secret, timestamp + "." + body)>
content-type: application/json

{"event": "order.approved", "instance_id": "wfi_...", "seq": 7,
 "from_state": "review", "to_state": "approved", "trigger": "approve",
 "actor_kind": "human", "occurred_at": "...", "payload": {...}}
```

`seq` is in the body on purpose: it is what lets a receiver detect that it
missed one, which is the failure at-least-once delivery cannot rule out.

Plus: `retrieve`'s `filter` gains `instance_id`, and `mcp.py` gains a
`workflow_state` tool so an AI agent driving the workflow can read it the same
way it reads everything else.

---

## 7 · Implementation steps

1. **`0033_workflows.sql`** — the five tables above. Additive, no backfill.
2. **`api/src/open_mem/workflows.py`** — new module, the control plane and the
   fold: `upsert_definition`, `validate_config` (Pydantic, cycle-permitting),
   `start_instance`, `apply_input` (the conditional append), `get_state`,
   `history`, `verify_state`, `migrate_instance`. Mirrors `cases.py` in shape.
3. **`apply_input`** is the core and should be written first and alone: resolve
   definition version → find the transition for `(from_state, trigger)` → check
   guards (D6) → check budget (D8) → **one transaction**: conditional
   `UPDATE … WHERE current_seq = $expected`, insert transition, recompute
   `deadline_at`, `events.emit("workflow.transitioned", …)`.
4. **`tick(pool, limit)`** in the same module — selects `deadline_at <= now()`
   `FOR UPDATE SKIP LOCKED`, calls `apply_input` with `trigger='@timeout'`,
   `actor_kind='system'`.
5. **`events.py`** — register `workflow.transitioned` in `TOPIC_FOR_EVENT`
   pointing at a new `workflow_deliver` topic, and **chain `caused_by` to the
   previous transition's event on the same instance**, which buys per-instance
   ordering from the existing dispatch query (D10). Note this is the opposite
   of the plan's earlier draft: the event now has a consumer, so it does *not*
   go in `NO_CONSUMER`.
5b. **`api/src/open_mem/workflow_delivery.py`** — new module, the sender:
   fan a transition out to matching subscriptions, sign with HMAC-SHA256 as
   `0018` does inbound, `validate_url` on **every attempt**, `follow_redirects=
   False`, backoff, dead-letter at `MAX_ATTEMPTS` and release the chain (D10).
   Concurrency of one per subscription. Delivery failure is **never** allowed to
   propagate back into `apply_input`.
6. **`app.py`** — the nine endpoints in §6.
7. **`deletion.py`** — purge `workflow_instance_members` and null
   `workflow_transitions.data_id` on item erasure. **A transition must survive
   the deletion of its input item** — erasing an email cannot erase the fact
   that the order was approved. Verify this in `verify_erasure`.
8. **`retrieval.py`** — `instance_id` filter, joined like `case_id`.
9. **`mcp.py`** — `workflow_state` read tool.
9b. **`crypto.py`** — reuse the existing envelope for `signing_secret_ct`;
   subscriptions store secrets exactly as producers do, no second scheme.
10. **`__main__.py`** — a `workflow-tick` subcommand, alongside `crawl-tick`.
11. **`api/deploy/cloudrun.sh`** — add `open-mem-workflow-tick` to the job loop,
    and a Cloud Scheduler entry. **This changes the deploy process, so
    `.claude/skills/deploy-gcp/` must be updated in the same commit** — new job
    in the inventory, new scheduler grant in `provision.md`.

Sequencing: 1–4 one commit (the engine, testable in isolation), 5–5b a second
(**outbound delivery — the part with the security control, reviewed on its
own**), 6–9b a third (the surface), 10–11 a fourth (operations).

---

## 8 · Tests — `api/tests/test_workflows.py`

The ones that matter are the concurrency and cycle cases; the CRUD ones are
cheap and go in for coverage.

- **Concurrent input** — two `apply_input` calls with the same `expected_seq`;
  exactly one succeeds, one 409s, `seq` advances by exactly 1, and the log has
  one row. *This is the test the feature exists to pass.*
- **Timeout does not double-fire** — a deadline elapses while a real input is
  in flight; assert one transition, not two.
- **Cycle terminates** — `review ⇄ changes_requested` driven past
  `max_transitions` lands in `errored` with a reason, and does not loop.
- **`verify_state` catches a divergence** — corrupt `current_state` directly in
  the fixture, assert the verifier reports it. Without this, D1's cache is
  unproven.
- **Guard** — a key-authenticated actor cannot fire a `requires_actor_kind:
  human` transition; a Firebase principal can.
- **Version pinning** — editing a definition mid-flight does not change a
  running instance's legal transitions; `migrate` without a map for a deleted
  state is refused.
- **ACL** — a second tenant's credential gets 404 on state and history; history
  is filtered by predicate, not post-filter (assert row counts, not just
  visibility).
- **Deletion** — erasing an input item leaves the transition, nulls `data_id`,
  and `verify_erasure` passes.
- **Validation** — unreachable state, dangling target, terminal-with-outgoing,
  and a cycle that must be *accepted*.

**Outbound delivery (D4, D10, D11)** — these carry the security weight:

- **A transition emits, and the delivery is signed verifiably.** Compute the
  HMAC in the test and compare; a delivery test that trusts its own signer
  proves nothing.
- **An unreachable subscriber does not block the transition.** Point a
  subscription at a dead endpoint, fire a transition: state advances, the
  transition is logged, the delivery is `pending`/`failed`. *This is the test
  the boundary exists to pass.*
- **Ordering per instance** — three fast transitions with a slow endpoint arrive
  as `seq` 1, 2, 3, never out of order.
- **Dead letter releases the chain** — after `MAX_ATTEMPTS` the delivery is
  `dead` and later transitions on that instance still deliver, with the gap
  visible.
- **Redelivery is deduped at source** — `UNIQUE (subscription_id,
  transition_id)` means a re-dispatch does not create a second delivery row.
- **SSRF, four cases, all refused**: `http://10.100.0.3:5432`,
  `http://169.254.169.254/…`, `file:///etc/passwd`, and a **host that resolves
  public at registration and private at send time** — the last one is the whole
  reason validation runs twice, so it needs a resolver fake rather than being
  assumed.
- **A 302 is a failed delivery**, not a followed one.
- **The signing secret is never returned** by create-list-get, and rotation
  honours the previous secret for the overlap window.

`conftest.py` already provides the pool/principal fixtures; follow
`test_sharing_cases.py` for the ACL-pair pattern.

---

## 9 · Documentation

- **`docs/workflows.md`** — new. Must carry §1 (why cycles change things), §3
  (why not a case/memory/entity — the table), D4 (records, does not execute)
  and D9 (why this belongs in a memory system).
- **`docs/README.md`** — add to Part III · Data model, beside memories and cases.
- **`docs/functional-requirements.md`** — new `## 10e. Workflow instances`,
  following the `10d. Cases` shape and numbering (`FR-WF-n`).
- **`docs/operations/schema.md`** — the five tables.
- **`docs/api.md`** — the endpoints, the 409 contract, and **the outbound
  delivery contract**: headers, signature construction, `seq` gap detection and
  the dedupe requirement. A receiver is written against this page, so it has to
  be exact rather than descriptive.
- **`docs/security/`** — the SSRF control (D11) and why validation runs twice.
  This is a deliberate outbound request to a caller-supplied address from inside
  a VPC that reaches Cloud SQL; it belongs in the security docs, not a comment.
- **`CHANGELOG.md`** — via the `changelog` skill after each commit; the new
  Cloud Run job and scheduler grant are the lines a deployer needs.
- **`.claude/skills/deploy-gcp/`** — step 11 above.

---

## 10 · Explicitly out of scope for v1

Saga/compensation, child or nested workflows, human task queues and assignment,
a visual editor, and **anything that waits on or interprets a subscriber's
response**. Outbound notification has moved *in* (D4); executing business
effects and reacting to their outcome stays out, because that is the external
engine's job.

Also out, and worth naming so they are not mistaken for oversights: subscriber
filtering beyond event name, fan-out to a queue rather than HTTP (Pub/Sub is a
natural v2 and the `Queue` seam is where it goes), and per-subscriber payload
templating — the delivery shape is fixed so a receiver can be written once.

---

## 11 · Open questions

1. ~~Is D4 the right boundary?~~ **Resolved.** The engine is external, open-mem
   notifies it, and does not interpret the reply.
2. **Should a definition be per-project or per-org?** Plan assumes project,
   matching `cases`. Org-level shared definitions are a small change now and an
   awkward one later.
3. **Is `case_id` on the instance enough**, or does an instance need its own
   `identifiers[]` for correlation from inbound data (the mechanism
   `cases.md` prefers over inference)?
4. **Default instance ACL** — plan assumes `org`, so "any user can query the
   state" is true within the org by default. If instances should default to
   `private`, the dashboard query changes shape.
5. **Does the external engine want a catch-up read, or only pushes?** If it can
   be down for an hour, it needs `GET /instances/{id}/history?since_seq=` to
   reconcile rather than relying on redelivery. Cheap now, awkward to retrofit —
   **I recommend including it in v1**; the plan assumes so.
6. **Subscriptions per project or per definition?** Schema allows both
   (`definition_id` nullable). Fine as sketched; flagging that the wildcard case
   means a new definition starts delivering to an existing subscriber the moment
   it is created, which may surprise.
7. **Is HTTP delivery enough for v1**, or does the external engine want Pub/Sub?
   The `Queue` seam already exists; HTTP first is the assumption.
