# Alerts

Declare an event worth knowing about. It is recorded when it happens, and read
two ways — **polled** from a cursor, or **pushed** to an endpoint you register.

> Not to be confused with retrieval. `/retrieve` answers *what matches this
> question now*. An alert answers *tell me when this happens*, which is a
> different mechanism with a different failure mode: retrieval that returns
> nothing is visible, and an alert that fires nothing is silence.

## What an alert watches

A **surface** — a kind of transition — and a **selector** over its fields. The
vocabulary is closed per surface, and served by `GET /api/v1/alerts/surfaces`
so a console never hardcodes a second copy of it.

| Surface | Fires when | Selector fields |
|---|---|---|
| `fact.asserted` | A claim enters the graph | `predicate`, `basis`, `subject_type`, `object_type` |
| `fact.superseded` | A claim is closed by a later one | as above |
| `fact.retracted` | A claim is withdrawn | `predicate`, `basis` |
| `data.revised` | A record's content actually changed | `source`, `data_type`, `producer_id` |
| `memory.member_added` | An item joins a memory | `memory_type`, `added_by` |
| `memory.retyped` | A memory is promoted or demoted | `from_type`, `to_type` |
| `case.member_promoted` | An inferred membership is confirmed | `case_type` |
| `acl.changed` | A record's visibility changes | `from_level`, `to_level`, `data_type` |

```jsonc
POST /api/v1/alerts
{
  "project_id": "prj_…",
  "name": "a person relocates",
  "surface": "fact.superseded",
  "where": { "predicate": ["located_in"], "subject_type": ["person"] }
}
```

## Three surfaces are mostly about refusing to fire

An alert that fires on noise is one people learn to ignore, so:

- **A revision that changed nothing is not an event.** `data.revised` compares
  checksums, because a re-crawl and a re-parse produce byte-identical revisions
  constantly. It also skips the first revision — the write already announced
  that record, and saying it twice makes every new item look like an edit.
- **A second document agreeing is a corroboration, not an assertion.**
- **Re-adding an item already in a memory is not a membership event**, and
  landing in the `default` memory is not one either: that is where an item with
  no memory and no matching rule goes, which is the *absence* of a signal.

`data.revised` carries both the old and new `source` for a reason worth
knowing: `write` and `reprocess` mean the upstream document changed, while
`parse` and `interpret` mean the same bytes were read better. **Only the first
is a change in the world**, and a selector needs to tell them apart.

## Evaluation is per window, never per write

A write records the transition and nothing more. Alerts read forward from a
**watermark** — a `domain_events.sequence` — and evaluate in batches.

Per-write evaluation would mean N alerts by M writes, with every write paying
for every alert; one crawl importing ten thousand items would trigger ten
thousand rounds. Instead a consumer wakes on a transition and **waits**,
coalescing for a few seconds before evaluating once over everything that
arrived. A burst becomes a handful of windows, and six revisions of one
document inside a window are one candidate.

**The queue is not the record.** Cloud Run scales to zero, so a window in flight
dies with its instance — the watermark in Postgres is what has actually been
evaluated, and `memdog-alert-tick` re-derives the rest every minute. A lost
message costs latency, never an alert.

Two rules follow, both borrowed from crawlers:

- **The watermark advances only after matches are written.** One that moved
  first would step over transitions nobody looked at, silently and permanently.
- **A capped batch reports what it deferred.** Silent truncation reads as
  "nothing else matched", which here is the worst available lie.

## Backtest before you turn it on

An alert cannot be enabled until **this wording** has been backtested. Editing
what it matches bumps `config_version`, drops the approval, and switches the
alert off — the same reason editing a crawler invalidates its dry run, rather
than carrying an approval forward onto a question that has changed. Renaming
does none of that.

```bash
POST /api/v1/alerts/{id}/backtest   # runs history through the live path
POST /api/v1/alerts/{id}/enabled    # 409 until the backtest matches this version
```

A backtest **records nothing and delivers nothing**. What it reports is what a
live run would do, because it *is* the live run with its writes withheld.

A new alert also starts watching from the head of the log, not the beginning of
it. One that fires a hundred notifications about last month the moment it is
saved is one somebody switches off.

## Reading what fired

```bash
GET /api/v1/alert-events?since=<sequence>&alert_id=&limit=
```

The cursor is a **sequence, not a timestamp**. Two events in the same
millisecond would otherwise come back in whichever order the planner liked, and
a poller re-reading from a time would skip one.

### The event stores no access level, on purpose

Visibility belongs to the subject and is resolved when someone reads — a fact
through its evidence, an item on its own terms, a memory through ownership or a
visible member. A copy taken when the alert matched would be stale the moment
the record was re-shared, and would ignore a revocation in between.

**Notification is the one side channel around every other access check in the
system.** Every other read path filters; a webhook that did not would be the
door left open. So a subscription has an owner, delivery is filtered by that
owner's rights *at send time*, and a delivery whose recipient has since lost
sight of the subject is dropped rather than retried.

## Pushing instead of polling

```bash
POST /api/v1/event-subscriptions   { "project_id": "…", "url": "https://…" }
```

- **`https` only**, and the URL is validated at registration **and on every
  send** — a host that resolved to a public address yesterday can resolve to a
  private one today, and this service reaches Cloud SQL over the VPC.
- **Redirects are not followed.** A 3xx is a failed delivery.
- **Signed** with HMAC-SHA256 over `{timestamp}.{raw body}`, the same scheme
  memdog asks providers to use inbound, so a subscriber verifies one way.
- **The signing secret is shown once.** It can be rotated, never read back, and
  the previous secret keeps verifying for an overlap so a rotation is not an
  outage for deliveries in flight.
- **At-least-once, then dead-lettered visibly** after five attempts, and
  replayable once the endpoint is fixed. A subscriber down for an hour is
  findable in one query rather than inferred from silence.

```http
x-delivery-id: wfx_…
x-signature-timestamp: 1735689600
x-signature: <hmac-sha256(secret, "1735689600." + body)>
```

## Two modes

**`rule`** is a selector and nothing else. No model, no cost, and it is what
eight of the eight surfaces are served by.

**`llm`** adds a description in words, judged **after** the selector has already
narrowed the batch:

```jsonc
{ "mode": "llm",
  "describe": "a customer signals they may leave",
  "where": { "data_type": ["email"] } }
```

The selector stays **required** in this mode. Without one, every transition in
the project would reach a model, and the cost is unbounded in exactly the way
nobody notices until the bill.

Judging happens in **one call per run** over the whole surviving batch. Per
candidate would cost what per-write evaluation was rejected for, so the batch is
what makes the mode affordable at all.

Two refusals hold it together:

- **An unavailable model defers; it never guesses.** Extraction falls back to a
  local heuristic because a worse summary is recoverable and a missing one
  stalls an item forever. A judgement is not like that — a wrong yes is a false
  alarm and a wrong no is a silence nobody notices — so the run fails, the
  watermark does not move, and the sweep tries again. The same choice embeddings
  make, for the same reason.
- **A candidate the model does not mention is not matched.** Models omit things.
  Inventing a match from an omission would fabricate alerts; treating it as an
  error would stall a batch on one bad row.

## Scope: which subjects count at all

`where` asks a question about the event's own fields. **Scope asks a different
one** — *is this subject even mine to care about* — and it cannot be a payload
field, because a transition does not know which memory its item is in, which
case it belongs to, or who wrote it. Those are joins.

```jsonc
{ "surface": "data.revised",
  "where":   { "source": ["write", "reprocess"] },
  "scope":   { "memory_id": "mem_…" } }
```

| Scope | Bounds it to |
|---|---|
| `memory_id` | items in that memory |
| `case_id` | items on that case |
| `producer_id` | what that source wrote |
| `entity_id` | claims naming that entity |

Applied **after the selector and before any model** — it is a join, so it is
cheaper than a judgement and there is no reason to pay for judging something
that was never in scope. One query per scope key over the whole batch, not one
per event.

Denormalising membership onto each transition would have avoided the join and
gone stale the moment somebody moved an item; letting the selector run
subqueries would have been a query language nobody asked for. Changing a scope
changes what matches, so it invalidates the backtest like any other edit.

## Conditions are generic

A field name **or a dotted path**, with an operator. A payload shape this module
has never seen is still reachable, so a surface added later needs no new
vocabulary here:

```jsonc
{
  "predicate":    ["located_in"],                       // implicitly "in"
  "basis":        {"op": "not_in", "value": ["asserted"]},
  "detail.score": {"op": "gt", "value": 5},
  "detail.note":  {"op": "contains", "value": ["north"]},
  "detail.tag":   {"op": "exists", "value": false}
}
```

Operators: `in` · `not_in` · `eq` · `ne` · `contains` · `gt` · `lt` · `exists`.
Every one is total against a missing value, so **a path into nothing is false,
never an error** — one odd record must not stall a batch.

The *root* of a path must be a field the surface emits; anything below it is
free. An unknown root is almost always a typo, and a selector that silently
never matches is the worst way to discover one.

## What is not built

Aggregate alerts ("sentiment turned negative"), source-health detection ("a
crawler that found things finds nothing"), and the bitemporal diff endpoint.
All three are **poll-shaped rather than transition-shaped** — they need a
scheduled evaluator over a query result, not a watermark over a log.
