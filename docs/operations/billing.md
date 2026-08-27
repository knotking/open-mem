# Billing

[token-accounting.md](token-accounting.md) covers the **meter** — what a usage record must carry
and the six ways a naive counter under-reports. This covers everything after it: turning usage into
money, aggregating it per account, and letting someone see and dispute the number.

---

## Three stages, and conflating them is the mistake

```
METER  ─────────▶  RATE  ─────────▶  BILL
raw units          units × price      aggregate per account, per period
per event          per event          → an invoice someone can query
```

They are separated for one reason that only becomes visible later:

> **Prices change, and prices are sometimes wrong.**

If cost is computed once at meter time and the units are discarded, a price correction cannot be
applied to what already happened — the only options are re-deriving from an approximation or
absorbing the error. Keeping the **units** means any period can be re-rated; keeping only the
**cost** means the past is frozen at whatever the rate card said that day, including its mistakes.

### Every rated event records which rate card produced it

Same shape as [`generator_version`](schema.md), for the same reason:

```
rate_card_version = sha256(canonical_json({ prices, effective_from, currency }))
```

**A rated amount with no rate-card version is unauditable.** You cannot explain why a line item is
what it is, you cannot reproduce it, and when a customer asks why last month cost more you are
reasoning from memory. With it, *"this was rated under card `v7`, which raised enrichment output
by 15% on the 12th"* is one query.

---

## The pipeline is asynchronous, and that has a cost of its own

Metering must **never** be on the critical path of a write or a query. A billing outage that stops
ingestion is a billing outage that becomes an availability incident.

```
  worker / API ──emit──▶  usage events  ──▶  metering service  ──▶  rollups  ──▶  invoices
   (fire and forget)         (durable)          (async)            (periodic)     (on close)
```

| Stage | Runs | Produces |
|-------|------|----------|
| **Emit** | Inline, non-blocking | One event per billable action |
| **Meter** | Continuous consumer | Validated, deduplicated, attributed events |
| **Rate** | Continuous | Cost per event, stamped with `rate_card_version` |
| **Roll up** | Hourly, then daily | Aggregates per (account, dimension, period) |
| **Invoice** | On period close | An immutable statement with pointers to its rollups |

### Emission must be as durable as the operation it describes

"Fire and forget" is right for *latency* and wrong for *durability*. An event dropped because a
queue was full is **revenue lost silently** — and silence is this system's characteristic failure
everywhere else too.

So emission is a durable local append that a shipper drains, not an in-memory best effort. The
operation and its usage event commit together or the event is replayed.

### Async metering needs reconciliation, or drift is invisible

This is the finding that async billing usually learns the hard way.

If the meter under-counts — a dropped event, a consumer restart, a bug in attribution — **nothing
looks wrong**. The invoice is smaller. No alarm fires because a smaller number is not obviously an
error, and the first signal is a margin that quietly stops matching the provider's own bill.

So the meter is **reconciled against independent sources**:

| Reconcile | Against | Detects |
|-----------|---------|---------|
| Metered tokens per model, per day | The **provider's own usage API** | Dropped events, attribution bugs |
| Metered enrichments | `COUNT(artifacts)` with recorded token usage | Consumer gaps |
| Metered storage | Actual bucket and table sizes | Snapshot failures |

`billing.reconciliation_drift_pct` is the metric, and it is alertable in **both** directions —
under-counting loses money, over-counting bills a customer for work not done, and the second is
worse.

---

## What is metered

| Dimension | Unit | Notes |
|-----------|------|-------|
| **Enrichment tokens** | input · output · cached, per model | Three rates, not one |
| **Embedding tokens** | input | Cheap per unit, enormous in volume |
| **Query tokens** | input · output | **Read-side cost, currently a stated gap** — see below |
| **Items ingested** | count | The unit customers actually reason about |
| **Storage — blobs** | byte-days | A level, not an event — see below |
| **Storage — records and indexes** | byte-days | Vector indexes are the surprise here |
| **Egress** | bytes | Exports and bulk downloads |

### Read-side cost has to be metered, and currently is not

A query costs an embedding call plus a generation call. Today that is unmetered, which means the
one activity a user can perform in an unbounded loop is the one nobody is counting.

The [console shows query cost next to the answer](../ui-design.md) — **the cheapest possible
governance**, since read-side spend is otherwise invisible to the person generating it.

### Storage is a level, not an event

Tokens are events: they happen, you sum them. Storage is a **level**: it persists, and summing
"bytes stored" events double-counts every byte on every day it exists.

So storage is **sampled daily and billed in byte-days**. A missed snapshot is a hole, not a
mis-sum — which is why it is in the reconciliation table above, and why the snapshot job is
idempotent per (account, day) so a re-run repairs rather than duplicates.

---

## Attribution follows connection scope

Who pays for an ingested item is the same question as who can see it and what happens to it on
account deletion — so it has the same answer:

| Connection scope | Attributed to |
|------------------|---------------|
| `personal`, uploads, direct writes | The **user**, and their project |
| `shared` | The **project**, not the connecting user |

**This is the fourth mechanism connection scope decides**, after the ACL, account deletion and the
default memory. That consistency is the point: a member who connects the team's Slack should not
personally own its ingestion bill, for exactly the reason they do not personally own its data.

Every event carries `user_id`, `project_id` and `org_id`, so rollups exist at all three levels
without re-deriving attribution later.

### Excluded from billing, deliberately

The [demo tenant](onboarding.md) and internal test orgs are **ordinary data with a known org id**,
excluded in the **reporting layer** — never by a flag consulted in the data path. Same rule as
their exclusion from tenant counts.

---

## Money is an integer, and rounding happens once

Two rules that are boring until they are not:

- **Store amounts as integer minor units** — never a float. Floating-point currency produces
  invoices that do not sum to their own line items, and the bug surfaces at a customer.
- **Round once, at the invoice.** Rounding each event and summing produces drift proportional to
  event count — and this system's event counts are large. Rate at full precision, round at the
  boundary where money becomes a number someone pays.

## An invoice must be explainable down to its events

*"Why is this $412?"* has to be answerable, or a dispute has no resolution path.

```
invoice ──▶ line items ──▶ rollups ──▶ the event range each rollup covers
```

Rollups keep pointers to the event range that produced them, so drilling from a total to the
underlying calls is a query rather than an investigation. An invoice you cannot decompose is one
the customer has to take on trust — and the first time they do not, you find out whether your
metering was right.

**Invoices are immutable once closed.** A correction is a **credit note**, never an edit — the same
principle as versioning and audit: the record of what you charged is not something that changes
because the charge was wrong.

---

## Where it is viewed

### API

```
GET /api/v1/usage?scope=user|project|org&period=…&granularity=day|month
GET /api/v1/usage/current                      the open period, against budget
GET /api/v1/usage/estimate                     what an operation would cost, before running it
GET /api/v1/invoices · /invoices/{id}          closed periods, with line items
GET /api/v1/invoices/{id}/breakdown            line item → rollup → event range
```

`usage/estimate` is the one worth calling out: **a cost that can only be discovered after the fact
is a cost nobody controls.** It backs the bulk-import estimate, the crawler dry-run, and the
sandbox's *"enrich the rest?"* decision.

### UI

| Surface | Sees |
|---------|------|
| **A user** | Their own usage against their own budget · **what each query cost** |
| **An org admin** | Per-project and per-member breakdown · budget management · invoices |
| **Platform admin** | Cross-org aggregates and margin against provider cost — **metadata only** |

The user-level view is not a courtesy. Budgets are enforced at
[user scope as well as project](../settings.md), so a user who can be stopped by a limit must be able
to see the limit and their position against it.

---

## Where it lands

| Phase | Work |
|-------|------|
| **1** | Usage events emitted durably · `usage_records` with input/output/cached split · budgets at user and project scope · **query cost metered** |
| **2** | The async metering consumer · rating with `rate_card_version` · hourly and daily rollups |
| **4** | Storage snapshots as byte-days · `usage/estimate` backing bulk and crawler previews |
| **6** | Per-provider tokenizer for accurate estimates · cost comparison in the sandbox A/B |
| **8** | Invoicing, credit notes, breakdown drill-down, reconciliation against provider APIs |

**Emission is Phase 1** for the usual reason: it is a **column**. Usage not recorded at the moment
of the call cannot be reconstructed afterwards — the provider's aggregate bill will not tell you
which user, project or data type it belonged to.

---

## Requirements

- **FR-BILL-1** Metering, rating and billing MUST be separate stages. Raw units MUST be retained so
  any period can be re-rated.
- **FR-BILL-2** Every rated event MUST record `rate_card_version`.
- **FR-BILL-3** Metering MUST NOT be on the critical path of a write or a read.
- **FR-BILL-4** Usage event emission MUST be durable — an event MUST NOT be lost because a consumer
  is unavailable.
- **FR-BILL-5** Metered totals MUST be reconciled against independent sources, and drift MUST be
  alertable in both directions.
- **FR-BILL-6** Read-side cost MUST be metered.
- **FR-BILL-7** Storage MUST be sampled as a level and billed in byte-days, with an idempotent
  snapshot per (account, day).
- **FR-BILL-8** Attribution MUST follow connection scope: `personal` to the user, `shared` to the
  project.
- **FR-BILL-9** Every event MUST carry user, project and org, so rollups exist at all three levels.
- **FR-BILL-10** Amounts MUST be stored as integer minor units, and rounding MUST occur once, at
  the invoice.
- **FR-BILL-11** An invoice MUST be decomposable to the events that produced it.
- **FR-BILL-12** Closed invoices MUST be immutable; corrections MUST be credit notes.
- **FR-BILL-13** A cost estimate MUST be available before an expensive operation runs.
- **FR-BILL-14** A user MUST be able to see their own usage and their position against any budget
  that constrains them.
