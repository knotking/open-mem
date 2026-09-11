# Cases — Correlating Information Around a Subject

A patient timeline. All telemetry from one machine. A legal matter. Each is the same shape: **a
long-lived subject that accumulates heterogeneous data from many sources over time, and must be
retrieved and reasoned about as a unit.**

Nothing in the current model expresses that.

## Why none of the existing primitives work

| Candidate | Why not |
|-----------|---------|
| **Project** | Designed as a workspace; the hierarchy is org → project. A hospital is not 100,000 projects, and the capacity plan targets ~1,000 |
| **[Memory](memories.md)** | Closest fit — it already contains data items — but memory types are *lifecycle* concepts (TTL, expiry, compression). No stable external identifier, no typed attributes, no membership provenance |
| **Tags** | No identity, no attributes, no access control, no lifecycle |
| **Graph entity** | **The dangerous one.** Entities are *extracted*, and entity resolution actively tries to merge similar ones. Two patients with the same name must never merge. Authoritative subjects cannot be probabilistic |

That last row is the important one. The graph is the obvious place to reach for and precisely the
wrong one: everything that makes entity resolution useful for knowledge extraction makes it unsafe
for identity.

**A case is declared, not inferred.**

## The primitive

```
Case
  case_id            cas_<ulid>
  external_id        caller-supplied, stable, unique per (project, case_type)
  case_type          patient | matter | asset | incident | <custom>
  attributes         typed, schema-validated per case_type
  identifiers[]      MRN, docket number, serial number, VIN …
  status             open | closed | archived
  acl                its own — not inherited from the project
  retention          policy, including legal hold
```

Membership is separate and carries provenance:

```
CaseMember
  case_id, data_id
  member_type        asserted | inferred     ← must be distinguishable
  confidence         for inferred members
  event_time         when the thing happened, not when we ingested it
  added_by, added_at
```

### Two fields that carry most of the weight

**`member_type`.** In a legal or clinical context there is a categorical difference between *this
document is in the case* and *this document appears related to the case*. Collapsing them produces
a system nobody can rely on for either purpose. Inferred members should be reviewable and
promotable to asserted.

**`event_time`.** See below — this is the one that breaks silently.

## Correlation: four mechanisms, in order of preference

| Mechanism | Deterministic? | Example |
|-----------|:--------------:|---------|
| **Explicit** — caller supplies `case_id` at write | yes | The host system already knows the patient |
| **Identifier match** — normalized `identifiers[]` field | **yes** | MRN `A12345` in a lab result matches the patient's MRN |
| Entity link — via the graph | no | A person entity connects to the case's subject |
| Proximity — time window plus partial identifier | no | Suggested only, always `inferred` |

The second mechanism is where **normalization pays off enormously**. If records normalize to
canonical types carrying an `identifiers[]` array, correlation becomes a **join on an identifier**
rather than an LLM inference. Deterministic before probabilistic, one layer up again.

This makes `identifiers[]` a required canonical field, not an optional one — a change to
[ingestion/normalization.md](ingestion/normalization.md).

## Event time is not ingestion time

A timeline ordered by ingestion time is not merely imprecise — it is **wrong in a way that looks
right**.

Consider backfilling three years of patient history. Every record was ingested this morning. Order
by `created_at` and the 2019 chest X-ray appears after this week's lab result. The timeline renders,
looks plausible, and is clinically misleading.

The crawler work makes this acute: **historical import is now a first-class path**, and historical
import is exactly what scrambles ingestion-ordered timelines.

So:

- `event_time` becomes a **first-class normalized field**, extracted per source
- Timelines order by `event_time`, falling back to ingestion time only when it is genuinely absent
- Items with no event time are **visibly marked**, not silently placed
- Both times are retained — "when did we learn this" is a real question, especially for audit

This is the transaction-time/valid-time distinction from
[retrieval/versioning.md](retrieval/versioning.md), arriving from a different direction.

## Case-level derived artifacts

A case is not just a bag of items. It accumulates its own derived layer:

| Artifact | Purpose |
|----------|---------|
| **Rolling summary** | "Catch me up on this matter" without reading 400 documents |
| **Timeline** | Ordered events with source attribution |
| **Key facts** | Case-level structured state — diagnosis, claim value, machine model |
| **Contradiction set** | Where members disagree — clinically and legally significant |
| **Case embedding** | Embed the summary, so **"find similar cases"** works — precedent search, similar presentations |

That last one is a genuine capability rather than a nicety, and it falls out almost free once a
case summary exists.

All of these are derived artifacts, which means the staleness model applies — and adds a new
trigger: **adding or removing a member invalidates case-level artifacts.** A case that gained a
document yesterday has a stale summary today. See [retrieval/versioning.md](retrieval/versioning.md).

## Access control

Case ACL is **not** project ACL, and this is the part most likely to be got wrong.

A hospital project contains every patient; access is per-patient or per-care-team. A firm's project
contains every matter; access is per-matter, and ethical walls exist specifically to prevent
cross-matter visibility.

```
effective access = most restrictive of ( item ACL , case ACL )
```

Item ACL still derives from the connection that produced it
([use-cases.md](use-cases.md)); the case then constrains further. A case can never *widen* access
to an item.

Two additions the domains demand:

- **Break-glass** — emergency override with mandatory justification and an immutable audit record.
  A clinical system without it is unsafe; one without the audit trail is unaccountable.
- **Ethical walls** — explicit deny lists at case level, which must survive membership changes.

## Retention, and a conflict worth surfacing

Cases carry retention policy, and one setting collides with something already designed:

> **A case under legal hold must not be deleted, including on an erasure request.**

That directly conflicts with the delete cascade in
[security/compliance.md](security/compliance.md). The resolution is that legal obligation
generally prevails — but the system must **represent the conflict explicitly** rather than resolve
it silently in either direction:

- An erasure request touching a held case returns a **partial completion** naming what was
  withheld and why
- The hold, its scope and its authority are recorded and auditable
- Release of the hold **re-queues** the deferred erasure

Silently deleting held data and silently ignoring an erasure request are both serious failures.
The only defensible behaviour is to do what is possible and say precisely what was not.

## API

Designed for a host system that already owns the subject.

```http
PUT  /api/v1/cases                         # upsert by external_id — idempotent
GET  /api/v1/cases/{id}
GET  /api/v1/cases?identifier=MRN:A12345   # resolve by identifier
POST /api/v1/cases/{id}/members            # assert membership
GET  /api/v1/cases/{id}/timeline           # ordered by event_time
GET  /api/v1/cases/{id}/summary            # derived, with staleness state
POST /api/v1/cases/{id}/retrieve           # case-scoped retrieval
POST /api/v1/cases/similar                 # case-level embedding search
```

Writes accept membership inline, so a host does not need two round trips:

```json
POST /api/v1/write
{
  "producer_id": "key_01JQRS...",
  "items": [{
    "content": { "kind": "inline", "text": "..." },
    "case": { "external_id": "MRN-A12345", "case_type": "patient" },
    "event_time": "2019-03-14T09:20:00Z"
  }]
}
```

`PUT` on cases rather than `POST`, keyed by `external_id`, because the host system's identifier is
the source of truth and re-sending it must not create a second case.

Bulk writes carry case assignment per item — see
[operations/bulk-operations.md](operations/bulk-operations.md).

## What exists today

Three of the eight endpoints above are built, and they are the three the primitive rests on:

```http
PUT  /api/v1/cases                         # upsert by (project, case_type, external_id)
GET  /api/v1/projects/{id}/cases           # what subjects exist, with visible member counts
GET  /api/v1/cases/{id}/timeline           # ordered by event_time, oldest first
```

Writes accept `case`, `identifiers` and `event_time` inline, on both the JSON path and the upload
path — a document large enough to need an upload session files against the same subject and keeps
the same date as the identical file sent inline.

Membership provenance is built and is **visible rather than held for operator review**, which
answers one of the open questions below in the direction the console could actually act on:
`asserted` and `inferred` are distinguished, an inferred member carries the identifier it
`matched_on` and its confidence, and an assertion arriving later **upgrades** an inference and
never the reverse.

The console reaches all of it from **Cases**: declare a subject and the identifiers it is known
by, list what exists, and open one history. Records join a subject at write time, under *Where it
goes, and what it is about* on **Add data** — which is also where a scanned or backfilled document
is dated by when it happened rather than when it arrived.

Not built, and not pretended to be: typed `attributes`, `status`, case-level ACL and the
intersection with item ACL, break-glass, ethical walls, per-case retention and legal hold, the
derived layer (summary, key facts, contradiction set, case embedding), and the `summary`,
`retrieve`, `similar` and resolve-by-identifier endpoints. The case ACL gap is the load-bearing
one: today a case constrains nothing, so what a reader sees on a timeline is exactly what the
item ACL already allowed them to see.

## What changes elsewhere

| Area | Change |
|------|--------|
| **Normalization** | `identifiers[]` and `event_time` become required canonical fields |
| **Ingestion** | `case` accepted on write; correlation rules evaluated; bulk writes carry per-item case assignment |
| **Retrieval** | `case_id` becomes a filter dimension; case-scoped RAG; case-level embeddings for similarity |
| **Indexes** | Case summary, timeline and contradiction set join the index set |
| **Versioning** | Membership change becomes a staleness trigger for case artifacts |
| **Access control** | Effective ACL is the intersection of item and case; break-glass; ethical walls |
| **Compliance** | Per-case retention, legal hold, per-case access audit |
| **Telemetry** | Case size distribution, orphan items, inferred-membership confidence, cases with stale summaries |
| **API** | Case CRUD, membership, timeline, case-scoped retrieve, similarity |

## Sequencing

Cases depend on work already planned, which is convenient:

1. **Normalization** must land first — `identifiers[]` and `event_time` are the correlation
   substrate
2. **Staleness fields** must exist — case artifacts are derived like any other
3. **Connection-scoped ACL** must exist — case ACL composes with it rather than replacing it

Given those, the case layer itself is additive: a table, a membership table, correlation rules, and
a retrieval filter. The expensive parts are the derived artifacts and the access-control
composition, not the primitive.

## Open questions

- **Case types: fixed or user-defined?** User-defined is more useful and needs the same
  schema-versioning machinery as normalization. Probably reuse it rather than build a second.
- **Should inferred membership be surfaced to end users at all**, or held for review by an operator?
  Domain-dependent; clinical and legal argue for review-first.
- **Cross-project cases.** A patient seen by two departments in different projects — does the case
  span them, or does each project hold its own? Spanning breaks the project isolation boundary that
  everything else relies on.
