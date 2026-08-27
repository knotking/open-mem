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

## Phase 0 — unblock

Independent of the spine. Neither blocks it; both are urgent on their own terms.

| Item | Why |
|------|-----|
| **Rotate committed secrets** | Present in git history — deletion does not fix exposure |
| `minReplicaCount ≥ 1` | The running deployment cannot meet any availability target at zero |

---

## Phase 1 — the spine

**Goal: write an item, find it by search, get it back.** Nothing else — except three things that
cannot be added afterwards.

### 1a · The write and read path

| Area | Work |
|------|------|
| **Write** | Producer registry (client keys only) · `POST /api/v1/write` · `items[]` · `207` · idempotency key |
| **Content** | `ContentRef` defined in full; only `Inline` implemented |
| **Commit** | Synchronous, durable, returns `data_id` and readiness state |
| **Async boundary** | Queue abstraction with the **in-process** implementation — the shape without a broker |
| **Index** | Chunk · embed · lexical index |
| **Read** | `GET /data/{id}` · `POST /api/v1/retrieve` with vector, lexical and hybrid |
| **Auth** | `TokenVerifier` seam, with only the API-key verifier behind it |
| **Guards** | Capability-scoped keys · admission control |

### 1b · Write-time facts — high priority, and not retrofittable

These are **columns, not features**. Anything that must be recorded on every row from the first row
cannot be added later: you cannot reconstruct which org owned a row, or which model embedded it,
after the fact. Retrofitting any of the three means a corpus-wide migration against data whose true
values are gone.

#### Tenancy — users, orgs, projects

| Work | Why now |
|------|---------|
| `org_id` and `project_id` **populated**, not nullable-and-ignored | Backfilling ownership onto existing rows is guesswork |
| Membership table with roles | The producer registry needs an owner, so tenancy is already implied |
| Per-item `access_level` + `shared_with` | Data written without an ACL has no defensible default later |
| **Every query scoped, ACL applied in the query** | Post-rank filtering silently breaks top-K, and retrofitting it into every query path is the expensive version |

*Not yet:* invite flows, role-management UI, ethical walls, break-glass, permission sync.

Retrofitting multi-tenancy is among the most expensive migrations there is. "Single-tenant for now"
is almost always regretted.

#### Model configuration

| Work | Why now |
|------|---------|
| Engine registration — provider, credential, **encrypted, failing closed** | Phase 1 already calls an embedding model; something has to hold that credential |
| Assignment per purpose | Even with one purpose, the *mechanism* must exist so choice is data rather than a constant |
| **`model_id` + `dim` recorded on every embedding** | Without it, a contaminated index cannot even be identified retroactively |
| `generator_version` fingerprint recorded per artifact | Establishes provenance before there is a corpus to fix |
| Engine reachability validation | Fail at configuration time, not at 3am |

*Not yet:* the curated catalog with model cards, hardware feasibility, staleness-impact preview.

The distinction that matters: **the selection mechanism is Phase 1; the catalog UX is Phase 6.**
If models stay hardcoded until then, every artifact produced in phases 1–5 carries no provenance
and the catalog's arrival becomes a corpus-wide staleness event.

#### Access model and settings

| Work | Why now |
|------|---------|
| **Rename `public` → `org`; `public` means external** | After both meanings exist it is a migration against a field people already reasoned about wrongly |
| **`shared_with` holds principals**, not user IDs | Enumerating users breaks on every membership change and silently fails to revoke |
| **Groups** as a sharing target | Sharing with *engineering* rather than eleven people is what makes the model get used correctly |
| **Platform grants orthogonal to org roles** | Also what retires the global unscoped key |
| **Admin sees metadata, not content** | Support tooling showing customer content by default is a privacy hole arriving as a feature |
| Settings precedence with **locks** | "Only approved providers", "public sharing off" must be enforced, not suggested |
| API/UI parity | The UI is a client of the API, never a privileged path |

*Phase 2–3:* share links with expiry and inventory · break-glass · access-history view.
*Later:* SCIM-managed groups, ethical walls.

See [security/access-model.md](security/access-model.md).

#### Privacy

Privacy has the same property as tenancy, and one control is not merely expensive to retrofit but
**impossible**: "who accessed this record in March?" has no answer if you were not recording in
March. See [security/privacy-foundations.md](security/privacy-foundations.md).

| Work | Why now |
|------|---------|
| **Access audit on every read** — append-only, separate store | Past access is unknowable. No migration recovers it |
| **Provenance on every derived artifact** — source set as a *list* | A summary spanning forty items, written without its source list, is **unerasable** later |
| **Encryption at rest, failing closed** | Retrofitting per-tenant keys onto a single-key corpus is a full re-encrypt |
| **Classification flag at ingest** — regulated / personal | Gates the inference fallback; without the field, classification means re-scanning the corpus |
| **Classification-gated inference** | Every call made before this exists is an untracked disclosure |
| Log discipline — never content, never prompts | Zero cost, irreversible if wrong |

*Not yet:* DSAR tooling, export UX, SSO, break-glass, ethical walls, legal hold, residency, the
delete-cascade implementation — its hooks are the provenance row above.

#### Write-path telemetry

You cannot tell whether the spine works if you cannot see it work.

| Metric | Purpose |
|--------|---------|
| `write.requests` by producer, status | Is anything arriving at all |
| `write.items` by outcome — created · updated · rejected | Upsert effectiveness |
| `write.rejected` by reason | Quota, validation, auth, payload size — distinguishable |
| `write.duration` | The commit must stay a database write |
| **`producer.seconds_since_last_item`** | Against each producer's own baseline. A silent stop errors nowhere |
| **`embed.distinct_models_per_index`** | Must be exactly 1. Ships with the first embedding call |
| **`ingest → searchable` latency** | The user-facing SLI that component metrics cannot show |
| Queue lag, readiness-state transitions | Where items are stuck |

*Not yet:* the full catalogue, dashboards, alerting, SLI reporting.

### Exit

An SDK call writes an item **into a project, owned by an org, with an ACL**; a semantic search
scoped to that project finds it; the response cites it; and the write path is observable end to end
with the model that embedded it recorded on the row.

### What is deliberately absent

No enrichment agents. No gateway. No fetch worker. No uploads. No crawlers. No graph. No cases. No
normalization.

### Two things built right rather than deferred

**The content contract from the start.** `ContentRef` is defined with all three cases even though
only `Inline` is implemented, so `Stored` and `Pending` slot in later without the enrichment side
ever learning to branch on provenance.

**Embeddings never fall back.** The defer-on-unavailable behaviour ships with the first embedding
call, not after a corpus has been contaminated.

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

Quotas before raising ceilings · SSO/SAML · SCIM · immutable audit log · **delete cascade** ·
legal hold and its interaction with erasure · composable retrieval primitives · soak to target
workspace count.

> **Design the delete-cascade hooks in Phase 2**, when derived artifacts first exist. Retrofitting
> provenance after compression has absorbed content is the expensive version.

---

> **Every phase gate is a test, not a demo.** See [operations/testing.md](operations/testing.md)
> for the per-phase gates and the invariant suite.

## Cross-cutting, placed by phase

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
| **Delete cascade** | hooks 2, build 8 | |

---

## Two sequencing risks

**Cloud before Local is a trap.** Cloud is the revenue path so it pulls first — but it cuts the
conversational agent and defers the temporal graph, shipping without either differentiator into the
most crowded quadrant. Local is cheaper, proves the privacy claim, sidesteps the compliance
apparatus entirely, and is where the unique capability lives.

**Phase 1 will feel too small, and is not.** A write endpoint and a search endpoint is not an
impressive demo — but the slice also carries tenancy, model provenance and write telemetry, because
all three are write-time facts. The temptation is to cut those to reach a demo faster. Each one cut
becomes a migration against data whose true values no longer exist.

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
| Separate batch endpoint? | **No** — one write verb, `items[]` |

### Still open

| Decision | Note |
|----------|------|
| **Own auth entirely, or keep a hosted IdP?** | The `TokenVerifier` seam defers this to Phase 6 — but owning it means building MFA, reset and abuse handling, and it is the only version satisfying the air-gapped pillar without a second code path |
| **Materialisation policy** | Always store (recommended) / threshold / derived-only |
| **Default ACL for a team upload** | Private-by-default is consistent; users dragging into a *team* space often expect team visibility |
| **Is media in scope for v1?** | Transcription infrastructure, and the largest cost exposure of any format group |
| **Backfill depth on first connect** | 30 days / 1 year / everything |
| **Cross-project cases** | Spanning breaks the project isolation boundary everything else relies on |

---

Stack choices and per-variant realisation are in
[operations/implementation.md](operations/implementation.md).
