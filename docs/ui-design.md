# UI Design

[roadmap.md](roadmap.md#ui-placed-by-phase) decides **when** each surface ships. This decides
**what it is** — the information architecture, the components that recur, and the six tensions that
actually determine the design.

The sequencing is already handled by the parity rule: everything settable in the UI is settable
through the API, so the UI can never be ahead of the API. Note the direction — **the API is a
superset**. Not every endpoint needs a screen, and pretending otherwise is how a data model becomes
a navigation menu.

---

## The mistake to avoid: navigating the data model

The system has many primitives — items, memories, cases, producers, connections, crawlers,
generators, versions, keys. The naive UI gives each one a nav entry, and the result is unusable
by anyone who has not read these docs.

**People do not arrive with a primitive in mind. They arrive with a question.**

| The question | The primitive it happens to touch |
|--------------|-----------------------------------|
| *"What did we promise Acme?"* | retrieval, cases, intent index |
| *"Why is this still here?"* | memory membership, effective expiry |
| *"Why did nothing arrive from Slack this week?"* | connection health, producer telemetry |
| *"What will this delete?"* | deletion cascade preview |
| *"Is the expensive model worth it?"* | model catalog, generator versions |

So the top level is **five areas built around questions**, not eleven built around tables:

```
  ASK        retrieval, answers, and the trace behind them
  DATA       browse, inspect, and understand any single record
  SOURCES    what is connected, what is flowing, what stopped
  CONFIGURE  models, prompts, memory types            (mostly post-MVP)
  OPERATE    keys, usage, audit, team
```

Plus a separate, deliberately limited **admin console** — see below.

Memories and cases are **not** top-level nav. They are filters in `DATA` and panels in the item
inspector, for a reason developed below.

---

## Six tensions, and how each resolves

### 1 · The dashboard's job is surfacing absence, not activity

This system's [characteristic failure is silence](operations/telemetry.md). A revoked connection
returns nothing. A broken crawler looks exactly like "no new data". A stalled enrichment queue
looks like a quiet week.

An activity dashboard — items ingested, queries served, a chart trending up — is **actively
misleading here**, because every one of those failures makes the chart look calm.

So the landing surface is a **quiet list of things that stopped**:

```
  ⚠  Slack · personal          no items in 6 days   (was ~40/day)
  ⚠  Drive crawler             last run failed · 401 · needs reauthorise
  ⚠  enrichment backlog        2,481 items · ~40 min
  ✓  everything else nominal
```

`producer.seconds_since_last_item` — already a required metric — is the primary signal, and it is
the one no component-level health check can produce. A green pod serving zero items is green.

**When nothing is wrong, this screen should be nearly empty.** A dashboard that always has
something to show trains people to ignore it.

### 2 · Memories and cases are orthogonal, so neither can own the navigation

An item is in memories (a **lifecycle** grouping — how long does this matter?) and in cases (a
**subject** grouping — what is this about?). Both are real, both are many-to-many, and they cut
across each other.

Pick one as the primary navigation and the other becomes invisible. Give both top-level nav and
users reasonably ask which one is "the real" grouping.

**Resolution:** neither. `DATA` is the browser, and both appear twice — as **filters** in the
browser, and as **panels** in the item inspector. An item's page answers both "which memories hold
this?" and "which subjects is this about?" without either claiming primacy.

This is also where the [reverse lookup](memories.md) earns its place in the UI:
`GET /data/{id}/memories` is literally the "why is this still here, and why did that vanish" panel,
and it must show the **effective expiry with its reason**, not a list of timestamps to reduce by
hand.

### 3 · One item inspector, many kinds of item

A Slack message, a DICOM study, a sensor reading and a contract are all data items. One hardcoded
renderer cannot serve them, and a `switch` that grows a case per source is the
[provenance-branching bug](ingestion/workers.md) rebuilt in the front end.

**Resolution: a typed renderer registry keyed on the canonical normalized type**, with an explicit
raw fallback — the same registry pattern the source adapters use.

The fallback matters more than the renderers. An unrecognised type must render **raw and say so**,
never guess at a presentation. A confident-looking wrong rendering of clinical data is worse than
an honest JSON dump.

Every inspector, whatever the renderer, carries the same tabs:

| Tab | Shows |
|-----|-------|
| **Content** | The typed rendering, or raw |
| **Normalized** | The projection, with the schema version that produced it |
| **Enrichment** | Viewpoint, entities, facets — each with `model_id` and `generator_version` |
| **Memberships** | Memories and cases, with `added_by` — explicit, routed or agent |
| **History** | Versions and diffs |
| **Provenance** | Which producer, which connection, when, and `event_time` vs ingestion time |

### 4 · Asserted and inferred must never look alike

[Case membership](cases.md) and [memory correlation](memories.md) both distinguish **declared**
from **derived**. Declared is authoritative; derived is suggestive and must never drive an access
or lifecycle decision.

If the UI renders them identically, that distinction is destroyed at the only point where it
matters — a person looking at a screen and deciding something. Someone reads an inferred link as
fact, acts on it, and the careful modelling underneath was for nothing.

**Resolution:** derived membership renders visually subordinate — muted, grouped separately, and
labelled with its basis ("shared 11 items", "same identifier"). And **derived items are excluded
from any count presented as authoritative.** "This matter contains 47 documents" must mean 47
asserted documents, not 47 including nine guesses.

### 5 · Destructive operations are frequently non-obvious, so the preview is one component

The system has an unusual number of operations whose destructiveness is not apparent from their
name:

| Operation | The non-obvious consequence |
|-----------|----------------------------|
| Re-type a memory to a shorter TTL | Members may be **already expired** under the new type |
| Remove a memory membership | May remove the **last** thing keeping an item alive |
| Narrow a redaction rule | Prior removals are **unrecoverable** — W7 cannot un-redact |
| Change a prompt or model | Invalidates derived artifacts across the corpus |
| Run a crawler | May create 47,000 items and consume the month's budget |
| Delete an item | Cascades through chunks, embeddings, entities, summaries |

These are not six features. They are **one component used six times**: state what will happen,
count it, and require confirmation. The crawler dry-run already establishes the pattern, and the
roadmap already calls it a blocking dependency — a dry-run returning JSON nobody reads does not
prevent the mistake it exists to prevent.

> **If an operation needs a paragraph of explanation before someone can safely click it, that
> paragraph belongs in the confirmation, computed against their actual data.**

### 6 · Three audiences, one application

Family A wants "ask my life". Family B wants shared knowledge and sharing controls. Families C and
D want keys, quotas, traces and tenancy. These are close to three different products.

**Resolution: one application, role-based defaults, progressive disclosure.** `ASK` is the landing
surface for everyone. `OPERATE` is where developers live and personal users never go. Nothing is
hidden by role — it is *ordered* by role, because a personal user who later runs a team should not
have to discover a different product.

The one genuine split is the admin console, and it is a split for a reason that is not about
audience.

---

## Every user gets the whole loop, on their own data

The console described so far is implicitly an **operator's** console. That is a mistake in the
framing, because the person who most needs to answer *"does this actually work?"* is the individual
user — and they need to answer it without an admin, without a second environment, and without
affecting anyone else's data or budget.

So the self-service loop is a first-class flow, not a permission tier:

```
  settings  →  connect or upload  →  sandbox  →  query own data  →  see cost and behaviour
     ▲                                                                       │
     └───────────────────────── tune and repeat ─────────────────────────────┘
```

This is also the **activation path**. A user who completes this loop once has evaluated the product
on their own corpus; a user who cannot complete it has evaluated nothing, whatever the marketing
said.

| Capability | Scope | Already designed? |
|------------|-------|-------------------|
| **Own settings** | Precedence starts at `user`, above project and org, with org locks | Yes — [settings precedence](roadmap.md) |
| **Own sandbox** | A personal project with a TTL | Yes — [ui-sandbox.md](ui-sandbox.md) |
| **Query own data** | Every query is already `user_id`-scoped | Yes — [tenancy](security/tenancy.md) |
| **Own token usage** | Usage is tracked per user, model and agent | Yes — [token accounting](operations/token-accounting.md) |
| **Own operational view** | Producer freshness, queue state, failures | Partly — the metrics exist, the per-user projection does not |

Four of five need a surface, not a mechanism. The fifth needs a decision.

### Telemetry is normally admin-only, and making it per-user changes what it contains

This is the part that does not fall out for free.

A user's operational view must answer *"is my data flowing, and is anything stuck?"* It must not
answer *"what is the cluster doing"* or *"what is anyone else spending."* Same pipeline, same
metrics, **different projection** — and the projection is not cosmetic, because an unfiltered
metric leaks:

| Metric | Admin sees | User sees |
|--------|-----------|-----------|
| `producer.seconds_since_last_item` | Every producer | **Their connections only** |
| Enrichment queue depth | Absolute, cluster-wide | **Their position and ETA** — absolute depth reveals other tenants' volume |
| Token spend | Per org, per user, per model | **Their own**, against their own budget |
| Error classes | All, with counts | **Theirs**, with the remediation |

> **Metrics are reads, and reads are ACL-filtered.** A dashboard is a query with a chart attached.
> The discipline that stops retrieval leaking across tenants has to reach the telemetry surface
> too, or the observability layer becomes the way around it.

The failure this prevents is specific and easy to ship by accident: a shared Grafana-style panel
exposed to end users, showing aggregate queue depth and per-model call volume. It looks like
transparency. It is a side channel disclosing another tenant's ingest volume, working hours and
data mix.

### Per-user sandboxes need per-user budgets

A personal sandbox is a real project running real enrichment. Without a per-user budget, one
person's 100k-row experiment consumes the team's monthly allowance, and the first anyone knows is
a rejected write somewhere unrelated.

The [budget mechanism](operations/token-accounting.md) already enforces at project scope. Personal
sandboxes need it at **user** scope as well — same enforcement point, one more principal — and the
[sample-first default](ui-sandbox.md) keeps the common case nearly free regardless.

### What the user's own view actually shows

| Panel | Answers |
|-------|---------|
| **My connections** | Is anything I connected silent, or needing reauthorisation? |
| **My recent writes** | What arrived, and where is it on the readiness staircase? |
| **My usage** | Tokens, storage and cost this period, against my budget |
| **My queries** | What I asked, what was retrieved, and what it cost |
| **My settings** | Model preference, defaults, retention — within org locks |

**"What it cost" on a query is unusual and worth keeping.** Retrieval cost is otherwise invisible
to the person generating it, which is how a well-meaning user runs a full-corpus semantic search on
a loop. Showing the number next to the answer is the cheapest possible governance.

---

## The admin console cannot render content

Platform admin is scoped to **metadata, never content** — a deliberate constraint, and one that has
to be enforced in the component layer rather than by remembering.

A support engineer debugging "why did this item not enrich?" needs the item's id, size, type,
producer, state, error and timing. They do not need its text, and in a regulated deployment they
must not have it.

**Resolution:** the admin console is built from a **metadata-only component set** that has no
content renderer available to it. Not a permission check inside a shared component — a separate set
that structurally cannot display content, so the failure mode is a missing panel rather than a
disclosure.

Admin sees orgs, quotas, queue depths, error classes, connection health and audit trails. Every one
of those is answerable without reading anyone's data, and the console proves it by not being able to.

---

## The component that recurs everywhere: readiness

`stored → searchable → enriched` is not a sandbox detail. It appears wherever data is displayed
shortly after arriving:

- Upload progress
- Search results — *"answering over 126 enriched of 500"*
- The item inspector — enrichment tab, before the agent has run
- Connection first-sync
- After a W7 reprocess

**"I just uploaded it and search cannot find it" is a support ticket that is not a bug — but only
if the state is visible.** One component, used everywhere data can be younger than its indexes.

---

## A UI decision that closes a security finding

Keys are identity-scoped but **not capability-scoped**: an API key inherits all the user's rights,
so a key pasted into an MCP client can delete the org, because deletion is a tool.

The fix is a scope on the key — and **the UI is where it becomes real**. If key creation presents
capability selection as a required step with **no "all capabilities" default**, the over-scoped key
stops being the path of least resistance. The same key created through the API can still request
broad scope explicitly; nobody gets there by accident.

```
  Create key
  ─────────────────────────────────────────
  Name          [ Claude Desktop MCP      ]
  Capabilities  ☑ read    ☐ write
                ☐ delete  ☐ admin
  Projects      [ personal ▾ ]
  Expires       [ 90 days ▾ ]
```

Defaults are policy. A checkbox pre-checked is a decision made on the user's behalf, and for
`delete` that decision should always be theirs.

---

## Screens, by area

### ASK
| Screen | Answers |
|--------|---------|
| Query + answer | The actual question, with citations |
| **Trace panel** | What was retrieved, scored, and *excluded with the reason* — ACL, threshold, or not yet enriched |
| Scope selector | Which project, memory or case is being searched |

The [sandbox](ui-sandbox.md) is this surface with an upload step and configuration comparison
attached. It is not a separate screen — which is the point.

### DATA
| Screen | Answers |
|--------|---------|
| Faceted browser | Filter by type, source, date, state, memory, case |
| Item inspector | The six tabs above |
| Memory browser | Members, time remaining, effective expiry with reason |
| Case timeline | Subject-ordered by `event_time`, asserted and inferred visually distinct |

### SOURCES
| Screen | Answers |
|--------|---------|
| Connection list | What is connected, **when each last delivered**, what needs reauthorise |
| Connect flow | OAuth — genuinely blocking, there is no API-only path |
| Crawler config | Scope, schedule, **dry-run preview before first run** |
| Run history | Per-run item counts and per-item errors |

### CONFIGURE — mostly post-MVP
| Screen | Answers |
|--------|---------|
| Model assignment | Which model per purpose, with hardware feasibility |
| Prompt editor | Test-before-save, plus **what changing this invalidates** |
| Memory types | Name, TTL, expiry policy |
| Normalization · redaction · validation | Post-MVP, per [write-customization.md](ingestion/write-customization.md) |

### OPERATE
| Screen | Answers |
|--------|---------|
| Keys | Capability-scoped creation, last used, revoke |
| Usage | Tokens, storage, cost against budget |
| Audit | Who accessed what, when — the query that is impossible to retrofit |
| Team | Members, roles, groups, sharing |

---

## What MVP ships

Consistent with [MVP being simple](roadmap.md): Phase 1's UI is **one screen and one inspector**.

| Ships | Does not ship |
|-------|---------------|
| Upload a dataset | Connect flows — Phase 3 |
| The readiness staircase | Chat — Phase 2 |
| Search, with the **trace** | Memory type editor — the column exists, the surface does not |
| Item inspector, raw renderer + one typed | Any configuration screen |
| | Admin console — Phase 8 |

**No chat in Phase 1, deliberately.** If retrieval is wrong, chat cannot be right; a conversational
layer over an uninspected retrieval path would let you ship without noticing.

---

## Requirements

- **FR-UI-1** Navigation MUST be organised by user question, not by data-model primitive.
- **FR-UI-2** The landing surface MUST surface **absence** — producers that stopped, connections
  needing reauthorise, backlogs — and MUST be near-empty when nothing is wrong.
- **FR-UI-3** Memories and cases MUST both be reachable from an item without either being the
  primary navigation.
- **FR-UI-4** The item inspector MUST use a typed renderer registry with an explicit raw fallback.
  An unrecognised type MUST render raw and say so, never guess.
- **FR-UI-5** Derived or inferred membership MUST be visually subordinate to declared membership,
  MUST state its basis, and MUST be excluded from counts presented as authoritative.
- **FR-UI-6** Destructive and high-cost operations MUST share one preview-and-confirm component
  that states the consequence computed against the caller's actual data.
- **FR-UI-7** Readiness state MUST be visible wherever data may be younger than its indexes.
- **FR-UI-8** A retrieval response MUST be able to show what was excluded and why.
- **FR-UI-9** Key creation MUST require explicit capability selection, with no default granting
  full capability.
- **FR-UI-10** The admin console MUST be built from a component set with no content renderer, so
  content disclosure is structurally impossible rather than permission-checked.
- **FR-UI-11** The effective-expiry view MUST show the reason, not only the computed date.
- **FR-UI-13** A non-admin user MUST be able to complete the full loop — settings, upload or
  connect, sandbox, query, and review cost — without administrator involvement.
- **FR-UI-14** Telemetry presented to a user MUST be scoped to their own producers, data and
  spend. Absolute cluster-wide figures MUST NOT be exposed to end users.
- **FR-UI-15** Token budgets MUST be enforceable at user scope, not only project scope.
- **FR-UI-16** A query response MUST be able to show what it cost.
- **FR-UI-12** Anything the UI can do MUST be doable through the API. A UI capability reaching past
  the API is a bug in the API.
