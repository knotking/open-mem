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

| Phase | Goal | Exits when | UI it ships |
|-------|------|-----------|-------------|
| **0 · Unblock** | Two independent urgencies | Secrets rotated, replicas above zero | — |
| **1 · Spine** | Write → store → read | An item is written into a project with an ACL, found by scoped search, cited, and the read is audited | The **sandbox**: upload, staircase, search, **retrieval trace**. No chat |
| **2 · Depth** | Make what is stored good | Items classify, summarise and entity-extract; changing a prompt can rebuild what the old one produced | Enrichment inspector · prompt editor with test-before-save |
| **3 · Connectors** | Data arrives on its own | Connect a mailbox; mail ingests, enriches and is findable without an API call | **Connect flows** — blocking, there is no API-only OAuth path |
| **4 · Uploads &amp; bulk** | The other producer shapes | A 500 MB file ingests from a browser; ten thousand records land without stalling the platform | Drag-and-drop, progress, per-item results |
| **5 · Crawlers** | Pull from what will not push | A connector backfills three years and stays current on a schedule | **Crawler dry-run preview** — blocking; a guardrail whose value is visual |
| **6 · Variants** | Local and cloud harden | **Unplug the network and everything still works** | First-run setup · **model catalog UX** with proposals and feasibility |
| **7 · Team &amp; cases** | Sharing and correlation | A patient timeline assembles across sources, ordered by event time | Sharing, members, groups, **case timeline** |
| **8 · Scale &amp; compliance** | Enterprise-addressable | Erasure completes and verifies; soak holds at target | Admin console · audit search · **deletion preview** |

**Every exit is a test, not a demo** — see [operations/testing.md](operations/testing.md).

## Capabilities that span phases

| Capability | Lands | Note |
|-----------|-------|------|
| **Write-path telemetry** | **1** | You cannot tell the spine works if you cannot see it work |
| **Tenancy model** | **1** | Columns, not features. Retrofitting multi-tenancy is the expensive migration |
| **Observability, broader** | 2 → 8 | Detectors ship with the thing they watch, never after |
| **Domain event store** | 2 | The audit substrate must exist before events accumulate |
| **Token accounting** | 2 → 5 | Usage records with depth; estimate-vs-actual with crawler dry-ru## Where the UI is genuinely blocking

 errors | **On the critical path** — see below |
| **6 · Variants** | First-run setup, model picker with hardware feasibility | Local onboarding is the product's first impression |
| **7 · Team & cases** | Sharing, members, groups, **case timeline** | A timeline is inherently visual; a JSON timeline is not a timeline |
| **8 · Scale** | Admin console, audit search, public-share inventory, **deletion preview and confirmation** | The inventory catches a six-month-old mistake; the deletion preview stops one being made |

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

> **Phase 1 ships the column and the enforcement point. The surface follows later.**

A column cannot be backfilled truthfully — you cannot reconstruct which org owned a row, which
model embedded it, or who read it last March. An enforcement point cannot be retrofitted cheaply.
**A UI can be built any time.** So §1b lists each concern twice: what must exist now, and what
deliberately does not.

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
| **Bulk** | **The run entity** — checkpointed, resumable, dry-run, per-item results · bulk write at scale with admission control on queue depth · `enrich: false` default for bulk · **selector-based delete as a job** |
| **Account deletion** | `DELETE /users/{id}/data` — **revoke first**, then cascade as a run · `personal` connections deleted, `shared` retained with attribution removed · the deletion's own audit record survives it |
| **Auth** | `TokenVerifier` seam, API-key verifier behind it |

### Why bulk and account deletion are here rather than Phase 4

**The run entity was already latent in Phase 1 and simply unnamed.** The
[sandbox](ui-sandbox.md) ships here, and its core interaction — *upload a dataset, watch the
staircase, see per-item results* — **is a run**. Building it once as a sandbox one-off and again
properly later is the worse version of the same work. The write verb is already bulk: `items[]`
with a `207` from the first commit.

| Lands in Phase 1 | Stays later |
|------------------|-------------|
| The run entity · bulk write at scale · **selector-based delete** · **account data deletion** | Bulk reprocess, update and export — **empty boxes, not deferrals**, since their subjects do not exist yet |

Two conditions move with it. **Dry-run and preview are Phase 1 too** — a selector delete without a
preview, against a corpus the user just uploaded, removes the guardrail while keeping the feature.
And account deletion **revokes before it cascades**: a live account that is half-deleted is a leak
that regenerates, where a revoked one is inert.

**Cost:** roughly a week to ten days Phase 1 did not have, against erasure working from the first
release and four later phases inheriting the machinery. Full treatment in
[bulk-operations.md](operations/bulk-operations.md) and [deletion.md](operations/deletion.md).

### 1b · Write-time facts — column and enforcement only

| Concern | **Phase 1 — column + enforcement** | **Later — the surface** |
|---------|-----------------------------------|------------------------|
| **Tenancy** | `org_id` / `project_id` **populated** · membership with roles · **every query scoped** | Invite flows · role management UI · org switcher |
| **Access** | `shared_with` holds **principals** · `public` renamed **`org`** · groups table + query-time resolution · **derived artifacts inherit strictest source** | Groups UI · share links with expiry · public-share inventory · ethical walls |
| **Memories** `P0` | **Type = name + TTL + expiry policy**, definable · type **mutable**, not in the id · `default` type so nothing is orphaned · **`memory_key`** unique per (project, type), upsert · many-to-many membership, **mutable after write** (add/remove, single and by selector) · **`memory_links`** · both mapping directions, with **effective expiry computed, never stored** · **`orphan_delete`** expiry | Type editor · memory browser · compression · automatic promotion rules · derived correlation as a retrieval signal |
| **Privacy** | **Audit record written on every read** · **`audit_events` for access changes — ACL, shares, roles, key grants** · **provenance as a source *list* on every derived artifact** · encryption at rest failing closed · classification flag at ingest | DSAR tooling · export · break-glass · access-history view · delete cascade |
| **Model config** | Engine registration, encrypted, failing closed · assignment per purpose · **`model_id` and `generator_version` recorded per artifact** · **model allow-list per project, failing closed** · **chunk size validated against the embedding model's input limit** | Curated catalog · cards · hardware feasibility · staleness preview |
| **Admin** | Platform grants **orthogonal** to org roles · admin sees metadata, **never content** · global unscoped key retired | Platform console · usage reporting · support tooling |
| **Settings** | Precedence user → project → org → platform, with **lock** semantics · reads return **effective value, source level and lock state** · key capabilities default to **nothing granted** · activity capture **off**, user-only | Full settings surface · policy editor · [full register](settings.md) |
| **Customization** | **Write phase order fixed, ACL assigned before any hook point** · security fields **read-only** in the item envelope · **`handler_digest` in the `generator_version` input set**, constant for built-ins | Normalization schema editor · redaction rules · validation policy · custom worker handlers · all of it |
| **Telemetry** | `write.*` counters by producer and reason · **`producer.seconds_since_last_item`** · **`embed.distinct_models_per_index`** · ingest→searchable | Dashboards · alerting · full catalogue |

Eight rows, not forty-four items. Each left-hand cell is something that becomes a migration — or,
for audit, becomes *impossible* — if deferred. Each right-hand cell can be built against existing
data whenever it is wanted.

### Exit

An SDK call writes an item **into a project, owned by an org, with an access level**; a semantic
search **scoped to that project** finds it; the response cites it; the read is **audited**; and the
row records **which model embedded it**.

And it can be taken back: **a selector deletes a set after showing what it would delete, and an
account deletion revokes, cascades, and reports what it retained.**

Expressed as tests rather than a demo — see [operations/testing.md](operations/testing.md).

### Deliberately absent

No enrichment agents. No gateway. No fetch worker. No connectors. No crawlers. No graph. No cases.
No normalization. No customization of any kind. No settings surface beyond the API.

**Two things are built right rather than deferred**, because both are cheap now and corrupting
later: `ContentRef` is defined with all three cases though only `Inline` is implemented, so
`Stored` and `Pending` slot in without enrichment ever learning to branch on provenance — and
**embeddings never fall back**, shipped with the first embedding call rather than after a corpus
has been contaminated.

---

## Phase 2 — depth

> **Why the catalog lands in Phase 2 rather than Phase 6.** The *surface* — cards, feasibility,
> proposals — is Phase 6 UX. But `model_cards` and `data_type_profiles` are **tables the assignment
> resolves against**, and the sensitivity field is what makes the allow-list automatic rather than
> remembered. Ship the tables with the second engine, and the proposal UI has something to read
> when it arrives. Ship them late and every assignment made in between is hand-authored against
> knowledge nobody wrote down.


**Goal: make what is stored good.** The spine already proves it is findable.

| Work | Notes |
|------|-------|
| **W1 enrich worker** | Moves enrichment off the write path properly |
| **Classification cascade** | Six deterministic layers, LLM as layer 7 |
| Typed agents | Start with a handful, not forty |
| Entity extraction → graph layer 1 | Record store tables; no external graph yet |
| **Staleness fields + `generator_version` fingerprint** | `sha256(canonical_json(config))` |
| **`model_cards` + `data_type_profiles`** | Stored and queryable — what the mapping is derived from |
| **`agent_config` as one object** | Prompt + schema + model per `data_type`, so they cannot drift apart |
| **Assignment per `(purpose, data_type)`** | MedGemma for clinical, a 270M classifier for layer 7 · **embeddings accept `*` only** |
| **Sensitivity-driven candidacy** | A cloud engine is never a *candidate* for a `phi` type |
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
| Bulk **export** | Portability and workspace offboarding — long-running, resumable, produces an archive |

**Bulk write, the run entity and selector-based delete moved to Phase 1** — see
[above](#bulk-is-in-phase-1-because-the-sandbox-already-needs-it). What remains here is the upload
half, plus export once there is an artifact graph worth exporting.

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
| **Local** | Lean compose · local password auth behind the existing `TokenVerifier` seam · **model catalog UX** — cards, hardware feasibility, **derived assignment proposals with exclusions shown**, `declared_by` provenance, staleness preview | **Unplug the network and everything still works** — including the models |
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
| Which models in MVP? | **Gemini Flash for generation and Gemini embeddings for RAG, plus Ollama Cloud (token-authenticated) as a second generation engine.** **Embeddings stay on one engine — not negotiable**, since mixed vectors corrupt an index silently. No tiering policy yet. **All versions pinned, never a rolling alias**, or `generator_version` lies. Two engines exercises the catalog seam, and Ollama Cloud's local twin speaks the same protocol — so restoring air-gap later is a base-URL change against an adapter already in production |

### Still open

| Decision | Note |
|----------|------|

| **Add write phase 5 — transform / redact?** | The one customization request the design cannot serve at all. Not-storing beats storing-then-erasing on every axis. Recommend yes; cost is a declarative rule type and the discipline that it never calls a model inline |
| **Allow `reject` as a validation policy?** | Recommend yes, but **blocked for webhook producers** — enabling it there converts a compliance preference into silent data loss |
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
