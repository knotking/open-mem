# Roadmap

## Sequencing principle

**Build a thin vertical slice that works end to end, then widen it.**

Prove write → store → read first. Once an item can be written and found, both halves of the system
are real and everything after is widening a spine that works, rather than integrating parts that
have never met.

```
1  SPINE          write → store → read            ← prove both halves
2  DEPTH          make what is stored good
3  CONNECTORS     the ingestion path
4  UPLOADS/BULK   the other producer shapes
5  CRAWLERS       scheduled pull
6  VARIANTS       local · cloud harden
7  TEAM & CASES
8  SCALE & COMPLIANCE
```

Each slice ends with something demonstrable. That is deliberate — the previous version had a large
foundation phase with nothing to show at the end, which creates pressure to skip parts of it.

### Relationship to what already runs

The GKE deployment runs today with four connectors live. This plan does **not** stop it. The spine
is built alongside, and producers migrate onto it slice by slice — the existing gateway becomes a
producer in slice 3 rather than being rewritten in place.

That also changes the framing of the known bugs. The `is_downloaded` failure and the embedding
fallback are not "fixes to schedule" — the new write path is built with the content contract and
the embedding-model column from the first commit, so those defects have nowhere to exist.

---

> **Where to actually start** — the step-by-step build order to the first milestone is in
> [operations/implementation.md](operations/implementation.md#build-order--where-to-actually-start).

## The plan at a glance

| Phase | Goal | Exits when |
|-------|------|-----------|
| **0 · Unblock** | Two independent urgencies | Secrets rotated, replicas above zero |
| **1 · Spine** | Write → store → read | An item is written into a project with an ACL, found by scoped search, cited, and the read is audited |
| **2 · Depth** | Make what is stored good | Items classify, summarise and entity-extract; changing a prompt can rebuild what the old one produced |
| **3 · Connectors** | Data arrives on its own | Connect a mailbox; mail ingests, enriches and is findable without an API call |
| **4 · Uploads &amp; bulk** | The other producer shapes | A 500 MB file ingests from a browser; ten thousand records land without stalling the platform |
| **5 · Crawlers** | Pull from what will not push | A connector backfills three years and stays current on a schedule |
| **6 · Variants** | Local and cloud harden | **Unplug the network and everything still works** |
| **7 · Team &amp; cases** | Sharing and correlation | A patient timeline assembles across sources, ordered by event time |
| **8 · Scale &amp; compliance** | Enterprise-addressable | Erasure completes and verifies; soak holds at target |

**Every exit is a test, not a demo** — see [operations/testing.md](operations/testing.md).

## Capabilities that span phases

| Capability | Lands | Note |
|-----------|-------|------|
| **Write-path telemetry** | **1** | You cannot tell the spine works if you cannot see it work |
| **Tenancy model** | **1** | Columns, not features. Retrofitting multi-tenancy is the expensive migration |
| **Observability, broader** | 2 → 8 | Detectors ship with the thing they watch, never after |
| **Domain event store** | 2 | The audit substrate must exist before events accumulate |
| **Token accounting** | 2 → 5 | Usage records with depth; estimate-vs-actual with crawler dry-run |
| **Model selection mechanism** | **1** | Engine registration and per-artifact provenance — not retrofittable |
| **Model catalog UX** | 6 | Cards, hardware feasibility, staleness-impact preview. Needs staleness fields (2) |
| **Trace context across the queue** | 1 | Span links, with the queue abstraction |
| **Deletion** | 1 → 8 | Single item in 1 · selector jobs and dry-run in 4 with the job machinery · entity refcounting and summary rebuild in 5 with reprocess · erasure, legal hold and verification in 8 |

---

## UI, placed by phase

Each slice ships the interface for what that slice made possible. There is no "UI phase", because
the parity requirement — everything settable in the UI is settable through the API — means **the UI
can never be ahead of the API**. That sequences it automatically.

| Slice | What the UI gains | Why then |
|-------|------------------|----------|
| **1 · Spine** | A **thin console**: write something, search, inspect a result | You cannot judge retrieval quality from a JSON body. One page, not a product |
| **2 · Depth** | Enrichment inspector — viewpoint, entities, classification layer reached; **prompt override editor with test-before-save and staleness preview** | You cannot tune a prompt without seeing what the last one produced, or what changing it invalidates |
| **3 · Connectors** | **Connect flows**, connection health, reauthorise | **On the critical path** — see below |
| **4 · Uploads** | Drag-and-drop, progress, per-item results | Uploads are inherently a browser feature |
| **5 · Crawlers** | **Config editor with dry-run preview**, run history, per-item errors | **On the critical path** — see below |
| **6 · Variants** | First-run setup, model picker with hardware feasibility | Local onboarding is the product's first impression |
| **7 · Team & cases** | Sharing, members, groups, **case timeline** | A timeline is inherently visual; a JSON timeline is not a timeline |
| **8 · Scale** | Admin console, audit search, public-share inventory, **deletion preview and confirmation** | The inventory catches a six-month-old mistake; the deletion preview stops one being made |

### Two places the UI is genuinely blocking

**OAuth connect flows need a browser.** There is no API-only path to connecting Gmail — the user
must be redirected, consent, and return. Phase 3 does not ship without UI; it is *how you connect
at all*.

**Crawler dry-run is a safety mechanism whose value is visual.** A dry-run that returns JSON nobody
reads does not prevent the mistake it exists to prevent. "This will create 47,213 items, take six
hours and consume 84% of your monthly budget" only works if someone sees it. Shipping the crawler
without the preview UI removes the guardrail while keeping the feature.

Everywhere else the UI can lag by a slice without harm.

### Relationship to the UI that exists

The running deployment has a working UI — dashboard, AI Studio, playground, settings. Same
strangler posture as the rest: it keeps serving the current system while the new console is built
against the new API. Convergence or retirement is a decision for around slice 3, once the connector
path has moved.

### It is also a reference implementation

Hosts embedding the platform build their own surfaces, and the
[ephemeral-token pattern](api.md) exists so they can do so without holding durable credentials.
Our UI is therefore the reference client as much as it is the product — which is a useful
discipline, because anything it can do only by reaching past the API is a bug in the API.

## Phase 0 — unblock

Independent of the spine. Neither blocks it; both are urgent on their own terms.

| Item | Why |
|------|-----|
| **Rotate committed secrets** | Present in git history — deletion does not fix exposure |
| `minReplicaCount ≥ 1` | The running deployment cannot meet any availability target at zero |

---

## Phase 1 — the spine

**Goal: write an item, find it by search, get it back.**

### The rule that keeps this a slice and not a foundation phase

Several concerns are **high priority but not fully built here**. The distinction:

> **Phase 1 ships the column and the enforcement point. The surface follows later.**

A column cannot be backfilled truthfully — you cannot reconstruct which org owned a row, which
model embedded it, or who read it last March. An enforcement point cannot be retrofitted cheaply —
adding ACL filtering to every query path afterwards touches everything. **A UI can be built any
time.**

So each concern below appears twice: what must exist now, and what deliberately does not.

### 1a · The write and read path

| Area | Work |
|------|------|
| **Write** | Producer registry · `POST /api/v1/write` · `items[]` · `207` · idempotency key |
| **Content** | `ContentRef` defined in full; only `Inline` implemented |
| **Commit** | Synchronous, durable, returns `data_id` and readiness state |
| **Async boundary** | Queue abstraction with the **in-process** implementation |
| **Index** | Chunk · embed · lexical index |
| **Read** | `GET /data/{id}` · `POST /api/v1/retrieve` — vector, lexical, hybrid |
| **Memories** | Type registry with TTL and expiry policy · many-to-many membership · both mapping directions |
| **Delete** | `DELETE /data/{id}` — single item, cascade over its own derived artifacts |
| **Auth** | `TokenVerifier` seam, API-key verifier behind it |

### 1b · Write-time facts — column and enforcement only

| Concern | **Phase 1 — column + enforcement** | **Later — the surface** |
|---------|-----------------------------------|------------------------|
| **Tenancy** | `org_id` / `project_id` **populated** · membership with roles · **every query scoped** | Invite flows · role management UI · org switcher |
| **Access** | `shared_with` holds **principals** · `public` renamed **`org`** · groups table + query-time resolution · **derived artifacts inherit strictest source** | Groups UI · share links with expiry · public-share inventory · ethical walls |
| **Memories** `P0` | **Type = name + TTL + expiry policy**, definable · type **mutable**, not in the id · `default` type so nothing is orphaned · **`memory_key`** unique per (project, type), upsert · many-to-many membership, **mutable after write** (add/remove, single and by selector) · **`memory_links`** · both mapping directions, with **effective expiry computed, never stored** · **`orphan_delete`** expiry | Type editor · memory browser · compression · automatic promotion rules · derived correlation as a retrieval signal |
| **Privacy** | **Audit record written on every read** · **provenance as a source *list* on every derived artifact** · encryption at rest failing closed · classification flag at ingest | DSAR tooling · export · break-glass · access-history view · delete cascade |
| **Model config** | Engine registration, encrypted, failing closed · assignment per purpose · **`model_id` and `generator_version` recorded per artifact** | Curated catalog · model cards · hardware feasibility · staleness-impact preview |
| **Admin** | Platform grants **orthogonal** to org roles · admin sees metadata, **never content** · global unscoped key retired | Platform console · usage reporting · support tooling |
| **Settings** | Precedence user → project → org → platform, with **lock** semantics | Full settings surface · policy editor |
| **Telemetry** | `write.*` counters by producer and reason · **`producer.seconds_since_last_item`** · **`embed.distinct_models_per_index`** · ingest→searchable | Dashboards · alerting · full catalogue |

Eight rows, not forty-four items. Each left-hand cell is something that becomes a migration — or,
for audit, becomes *impossible* — if deferred. Each right-hand cell can be built against existing
data whenever it is wanted.

### Exit

An SDK call writes an item **into a project, owned by an org, with an access level**; a semantic
search **scoped to that project** finds it; the response cites it; the read is **audited**; and the
row records **which model embedded it**.

Expressed as tests rather than a demo — see [operations/testing.md](operations/testing.md).

### Deliberately absent

No enrichment agents. No gateway. No fetch worker. No uploads. No crawlers. No graph. No cases.
No normalization. No settings UI. No share links.

### Two things built right rather than deferred

**The content contract from the start.** `ContentRef` is defined with all three cases even though
only `Inline` is implemented, so `Stored` and `Pending` slot in later without the enrichment side
ever learning to branch on provenance.

**Embeddings never fall back.** Defer-on-unavailable ships with the first embedding call, not after
a corpus has been contaminated.

---

## Phase 2 — depth

**Goal: make what is stored good.** The spine already proves it is findable.

| Work | Notes |
|------|-------|
| **W1 enrich worker** | Moves enrichment off the write path properly |
| **Classification cascade** | Six deterministic layers, LLM as layer 7 |
| Typed agents | Start with a handful, not forty |
| Entity extraction → graph layer 1 | Record store tables; no external graph yet |
| **Staleness fields + `generator_version` fingerprint** | `sha256(canonical_json(config))` |
| **W7 reprocess** | You will want to tune prompts on day two. Without this, tuning is write-only |
| Telemetry | `enrich.classification_layer` — measures the ~80% claim rather than asserting it |

**Exit:** written items are classified, summarised and entity-extracted; changing a prompt can
rebuild what the old one produced.

---

## Phase 3 — connectors

**Goal: data arrives on its own.** This is where the ingestion path is built.

| Work | Notes |
|------|-------|
| **Gateway as a producer** | Translator in front of the write API — it does not become a second write path |
| `whk_` producer type | Per-user webhook endpoints |
| **Connection scope → ACL inheritance** | Personal vs shared. The rule that reconciles personal and team memory |
| **W2 fetch worker + `Pending`** | Most events carry a pointer, not a payload |
| Credential proxy integration | The boundary: only W2 reaches it |
| **Tier-3 default path** | JSON records ingest with no per-provider code — this is what makes breadth cheap |
| Tier-1 adapters | Gmail, Slack, Drive — the three already live |
| W4 subscription renewal | Watch state in the record store, not on a volume |
| Telemetry | `producer.seconds_since_last_item` — a dead connection errors nowhere |

**Exit:** connect Gmail; mail arrives, enriches, and is findable without anyone calling an API.

---

## Phase 4 — uploads and bulk

Both are producer shapes the spine already anticipates.

| Work | Notes |
|------|-------|
| Presigned upload flow | `Stored` content; bytes never traverse the API |
| Local upload signer | Filesystem storage has no signer — same API shape, local token |
| MIME sniffing server-side | Declared type is a hint, never the router |
| Bulk writes at scale | Same verb, more items; admission control on queue depth |
| `enrich: false` default for bulk | Full enrichment must be asked for and budgeted |
| **Selector-based deletion as a job** | Same run entity as bulk import — checkpointed, dry-run, per-item errors |

**Exit:** drag a 500 MB file into the UI and it ingests; push ten thousand records and the platform
stays responsive.

---

## Phase 5 — crawlers

**Goal: pull from what will not push.**

| Work | Notes |
|------|-------|
| `crw_` producer type · config store · dry-run gate | Created disabled; enabling requires a dry-run |
| Run lifecycle | Checkpointed, resumable, pause/resume/cancel |
| **`enumerate` strategy + backfill-on-connect** | Without backfill a new connection shows nothing until new data arrives |
| Three-layer dedupe | `external_id` · etag · content hash |
| **W8 mutate** | Crawlers re-discover constantly; without it every re-crawl duplicates or leaves stale facts |
| Per-provider and per-host limits | Bulk priority never starves live ingestion |
| Then: `query`, `tree`, `feed`, `search` | Templated HTTP covers most REST APIs without adapters |
| Later: `traverse` | Web crawling adds politeness and scope-escape risk — ship it last |

**Exit:** a Salesforce crawler backfills three years and keeps itself current on a schedule.

---

## Phase 6 — variants

The seams from Phase 1 are what make this cheap rather than a fork.

| Variant | Work | Exit |
|---------|------|------|
| **Local** | Lean compose · local password auth behind the existing `TokenVerifier` seam · **model catalog UX** on top of Phase 1's selection mechanism | **Unplug the network and everything still works** |
| **Cloud** | Managed queue behind the existing abstraction · hosted auth as another verifier · pooling audit · Secret Manager · crawl runs as jobs | Single-tenant prod, SaaS-ready |

**Gate:** run the credential-broker request-scoped-lifecycle spike before committing to the cloud
topology. Four more spikes are in [operations/implementation.md](operations/implementation.md); one
could *remove* work — the broker's own sync engine may replace part of Phase 5.

---

## Phase 7 — team and cases

| Work | Depends on |
|------|-----------|
| Sharing surface, invite flows, role management UI | Tenancy model (1) · connection ACL (3) |
| **Permission sync from source systems** | The gap competitors already ship |
| Normalization: `identifiers[]`, `event_time` | Depth (2) |
| Case primitive, membership, correlation | Normalization + staleness |
| Case-scoped retrieval, timeline, similar-case | Case primitive |
| Tier-1 connector depth | Connectors (3) |

---

## Phase 8 — scale and compliance

Quotas before raising ceilings · SSO/SAML · SCIM · immutable audit log · **erasure with
verification** · legal hold and partial completion · composable retrieval primitives · soak to target
workspace count.

> **Design the delete-cascade hooks in Phase 2**, when derived artifacts first exist. Retrofitting
> provenance after compression has absorbed content is the expensive version.

---

> **Every phase gate is a test, not a demo.** See [operations/testing.md](operations/testing.md)
> for the per-phase gates and the invariant suite.

## Two sequencing risks

**Cloud before Local is a trap.** Cloud is the revenue path so it pulls first — but it cuts the
conversational agent and defers the temporal graph, shipping without either differentiator into the
most crowded quadrant. Local is cheaper, proves the privacy claim, sidesteps the compliance
apparatus entirely, and is where the unique capability lives.

**Phase 1 looks small and carries a lot.** A write endpoint and a search endpoint is not an
impressive demo, and the slice also carries tenancy, access principals, audit, provenance, model
recording and write telemetry. The temptation is to cut those to reach the demo faster.

Apply the rule instead of cutting: ship the **column and the enforcement point**, defer the
**surface**. Cutting a surface costs a sprint later. Cutting a column costs a migration — and for
the audit record, costs a question that can never be answered.

---

## Decisions

### Closed

| Question | Settled as |
|----------|-----------|
| Team RBAC in mem-dog, or delegated? | **mem-dog enforces natively**; the host model is one broad service principal |
| Global privacy default? | **None** — visibility is producer/connection-scoped |
| Fixed search modes or composable? | **Composable** |
| Can the delete cascade wait? | **Build late, design early** |
| Keep the global `API_KEY`? | **No** |
| Auth provider? | **Firebase for login; user-created API keys validated at the gateway; both resolved to one identity.** Air-gap is served by a local password verifier behind the same seam — so **air-gapped operation is a self-hosted capability, not a property of the hosted product** |
| Inbound authentication? | **The same user-created keys** where the provider can present one; signature or URL secret otherwise, declared per producer |
| Separate batch endpoint? | **No** — one write verb, `items[]` |

### Still open

| Decision | Note |
|----------|------|

| **Scope W10 standing queries?** | Four published use cases need push; media monitoring is *only* a push product, so shipping it without delivery ships nothing. Recommend scoping W10 with media monitoring and stating the deferral for the rest |
| **`compress` → `derive`?** | Study guides, flashcards, obligation extracts and customer briefings are one operation with different output schemas. Recommend yes — reuses the existing generator registry, and the endpoint has no clients yet |
| **Materialisation policy** | Always store (recommended) / threshold / derived-only |
| **Default ACL for a team upload** | Private-by-default is consistent; users dragging into a *team* space often expect team visibility |
| **Is media in scope for v1?** | Transcription infrastructure, and the largest cost exposure of any format group |
| **Backfill depth on first connect** | 30 days / 1 year / everything |
| **Cross-project cases** | Spanning breaks the project isolation boundary everything else relies on |

---

Stack choices and per-variant realisation are in
[operations/implementation.md](operations/implementation.md).
