# Plan — project status tracking, as signals over a case

**Goal.** Pull from the systems a project actually lives in — Jira, GitHub,
Confluence, Slack, Workday, CI — correlate them, and produce **signals**: a
standing, evidenced assessment of how the project is doing. Package the whole
thing as a **template** so a second project costs a form rather than a project.

Status: **plan only, nothing implemented.** Confirm before `/implement`.

---

## 1 · A project is a Case. It is not a new primitive.

`cases.md` describes the shape exactly, three years before anyone asked for
this:

> A patient timeline. All telemetry from one machine. A legal matter. Each is
> the same shape: **a long-lived subject that accumulates heterogeneous data
> from many sources over time, and must be retrieved and reasoned about as a
> unit.**

`PROJ-123` is that. So a project is `case_type: "project"` with the Jira key as
its `external_id`, and everything the case machinery already does — declared
identity rather than inferred, its own ACL, asserted-versus-inferred membership,
a timeline ordered by `event_time` and not by when we happened to ingest it —
applies without a line of new code.

**The dangerous alternative, already rejected in that document:** making a
project an *entity*. Entity resolution actively merges similar things, and two
projects called "Migration" must never become one. Authoritative subjects cannot
be probabilistic.

## 2 · The correlation is deterministic, and already runs

`route_case()` runs at write time: an explicit `case` on the write is
**asserted**, an identifier match against an existing case is **inferred**, and
what it matched on is recorded so a wrong correlation can be traced rather than
guessed at.

That is the entire cross-source story, and it needs no model:

```
Jira PROJ-123           → external_id, asserted
Slack "…blocked on PROJ-123"  → identifier match, inferred
GitHub PR "PROJ-123: retry"   → identifier match, inferred
Confluence "PROJ-123 design"  → identifier match, inferred
```

**This is where the design earns its keep, and also where it will fail in
practice.** A Slack message saying *"the auth thing is slipping"* names no
identifier and will not join. That is not a bug to fix in code — it is the
limit, and any honest deployment states it: coverage is a function of whether
people cite keys. Measure it (`inferred` members per case per source) rather
than assuming it.

## 3 · What a signal is, and why it is genuinely new

Not an alert: alerts fire on a **transition**, and "this project is slipping" is
not a transition — nothing happened, which is precisely the point. Not
retrieval: that is a pull, and nobody polls a hundred projects by hand.

> A **signal** is a standing, scheduled assessment of one case, producing a
> typed verdict with the evidence that supports it.

```jsonc
{ "case_id": "cas_…", "signal": "delivery_risk",
  "verdict": "at_risk", "confidence": 0.8,
  "because": [ {"data_id": "…", "span": [120, 240]}, … ],
  "assessed_at": "2026-09-01T02:00:00Z" }
```

Three properties follow from decisions already made here:

**It is a derived artifact**, so `generator_version` makes it reproducible and
detectably stale, `artifact_sources` carries the spans that let a claim be
opened at the sentence, and erasure cascades through it. All free.

**Its history is bitemporal.** *"On 3 March we assessed this at risk"* is
exactly what `entity_facts` models — a signal that changes **supersedes** rather
than overwrites, so `as_of` answers what a status report said at the time. A
project tracker that cannot reconstruct last month's status is a dashboard, not
a record.

**A change in a signal is an alert surface.** `signal.changed` joins the eight
that exist, and the whole alert stack — rules, backtest, delivery — applies
unchanged.

## 4 · What exists, and what does not

| Needed | State |
|---|---|
| Connectors — Jira, GitHub, Confluence, Linear, Asana, Notion, Workday | **In the catalog, unblocked** |
| Webhooks — Slack, GitHub, Linear | **Shipped** (9 providers) |
| Case correlation by identifier | **Shipped** (`route_case`) |
| Timeline by `event_time` | **Shipped** |
| Cross-source contradiction | **Shipped** (`/graph/conflicts`) |
| Bitemporal history | **Shipped** (`entity_facts`) |
| Alert on change | **Shipped** — needs one new surface |
| Scheduled job with preview + runs + metrics | **Shipped** (compaction; the same shape) |
| **A derive registry** — artifacts are hardcoded to `summary` | **Missing** |
| **Signals** — scheduled derive over a case | **Missing** |
| **Time-based triggers** — "14 days before the date" | **Missing** |
| **Templates** | **Missing** |

Four gaps, and the second and third are the ones project tracking cannot do
without. The use-case catalog already named the first (*"derived artifacts are
hardcoded to summary… study guides, obligation extracts and customer briefings
are the same operation with a different output schema"*) and the third
(*"time-based triggers are **not** standing queries — nothing arrives that day"*).

## 5 · The signals, grouped by what they cost

Grouped this way on purpose: the valuable ones are mostly not the model ones,
and a plan that leads with LLM assessment gets the economics backwards.

### A · Deterministic — no model, ship first

| Signal | What it reads | Why it is worth having |
|---|---|---|
| **Stalled** | No case activity for N days | Caused by *absence*, so nothing else can see it |
| **Unowned work** | Issues with no assignee | One join, constant nagging value |
| **Review bottleneck** | PRs open beyond N days, grouped by reviewer | Names the person, not the queue |
| **Deadline approaching** | A date facet, N days out | Needs the time-based trigger (§4) |
| **Owner departed** | Workday says left; Jira still assigns them | **Cross-source, and impossible in any single tool** |
| **Scope added after freeze** | Issues created after a date on the case | Deterministic, and the argument people actually have |

### B · Uses the graph and the two clocks — built, unused

| Signal | Mechanism |
|---|---|
| **Contradiction across sources** | Jira says done, GitHub has open PRs, Slack says blocked → `/graph/conflicts` |
| **Decision drift** | A Confluence spec and a later thread disagree → supersession with both still readable |
| **Bus factor** | One person authored most material on the case → `entity_mentions` counts |
| **Status as of a date** | *What did we believe on 1 March?* → `as_of` |

### C · Needs a model — last, and gated

| Signal | Note |
|---|---|
| **Narrative risk** | "Is this project in trouble?" over the case's recent material |
| **Blocker extraction** | Turning threads into named blockers with owners |
| **Status digest** | The weekly summary, with citations |

`llm` mode already refuses rather than guessing when no model is available, and
already runs one call per batch. Both apply here unchanged.

## 6 · Templates: what makes this a product

A template is a bundle applied to a case, so the second project costs a form:

```jsonc
{ "template": "software_project",
  "case_type": "project",
  "identifier_pattern": "^[A-Z]{2,10}-\\d+$",     // the Jira key shape
  "sources":  ["jira", "github_issues", "confluence", "slack"],
  "normalization": "work_item_v1",
  "signals":  ["stalled", "unowned_work", "review_bottleneck",
               "deadline_approaching", "owner_departed"],
  "alerts":   [{"on": "signal.changed", "where": {"to": ["at_risk"]}}] }
```

Ship two or three, not twelve: `software_project`, and one deliberately
different — `vendor_engagement` or `audit_readiness` — because a second shape is
what proves the template is a mechanism rather than one hard-coded flow.

## 7 · Two risks worth naming before building

**Correlation coverage is the whole product, and it is not a code problem.** If
people do not cite keys, cases stay thin and every signal is computed over a
fraction of the truth — while looking exactly as confident. So: report coverage
per case per source as a first-class number, and let a thin case say so rather
than emitting a signal from three documents.

**Multi-source ACL is sharper here than anywhere else so far.** A project case
spanning Slack, Jira and **Workday** puts HR data beside engineering chatter in
one timeline. `acl.strictest` already means a signal drawing on a Workday record
inherits its visibility — which is correct and will surprise people, because the
status summary then becomes invisible to most of the team. The honest answer is
that a signal reading HR data *is* HR-sensitive, and the template should say so
at the point of adding the source rather than after a report goes missing.

## 8 · Sequencing

1. **Derive registry** — `POST /memories/{id}/derive {generator}` over the
   existing `generator_versions`. Closes the catalog's Gap 2 for UC9 on the way,
   and nothing else can be built without it.
2. **Signals** — a scheduled derive over a **case**, reusing the compaction job
   shape wholesale: job + algorithm + schedule + preview + runs + metrics. The
   scheduler is already written; a signal is a different verb on the same frame.
3. **Time-based triggers** — a sweep over date facets on the same tick.
   Independently the highest-value small thing here: deadlines are most of what
   project tracking is, and no transition ever fires for them.
4. **`signal.changed` alert surface** — one entry in the surface registry.
5. **Templates** — last, because a template can only package mechanisms that
   exist.

Steps 1–2 make one real signal end to end. That is the point to stop and look at
it against a live project before building eleven more.

## 9 · Open questions

1. **Does a signal live on a case, a memory, or both?** The plan assumes a case.
   A memory-scoped signal ("this working set has gone cold") is also coherent
   and would reuse the compaction scheduler more directly.
2. **Is `project` a `case_type`, or does it deserve its own table?** Case gives
   identity, ACL and correlation for free. What it lacks is *structure* —
   milestones, dependencies, a plan. If dependencies matter, that is
   `entity_edges` with a `depends_on` predicate, not a new table.
3. **Where does the identifier pattern live** — the template, the project
   settings, or the normalization schema? It decides whether one project can
   have two key shapes.
