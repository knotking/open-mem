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

**Goal: write an item, find it by search, get it back.** Nothing else.

| Area | Work |
|------|------|
| **Write** | Producer registry (client keys only) · `POST /api/v1/write` · `items[]` · `207` · idempotency key |
| **Content** | `ContentRef` defined in full; only `Inline` implemented |
| **Commit** | Synchronous, durable, returns `data_id` and readiness state |
| **Async boundary** | Queue abstraction with the **in-process** implementation — establishes the shape without a broker |
| **Index** | Chunk · embed with `model_id` + `dim` recorded · lexical index |
| **Read** | `GET /data/{id}` · `POST /api/v1/retrieve` with vector, lexical and hybrid |
| **Auth** | `TokenVerifier` seam, with only the API-key verifier behind it |
| **Guards** | Capability-scoped keys · admission control (payload size, quota) |
| **Telemetry** | Write and read counters · `embed.distinct_models_per_index` · queue lag |

**Exit:** an SDK call writes an item; a semantic search finds it; the response cites it.

### What is deliberately absent

No enrichment agents. No gateway. No fetch worker. No uploads. No crawlers. No graph. No cases. No
normalization. Single-tenant scoping by `user_id` only.

### Two things built right rather than deferred

**The content contract from the start.** `ContentRef` is defined with all three cases even though
only `Inline` is implemented, so `Stored` and `Pending` slot in later without the enrichment side
ever learning to branch on provenance.

**Embeddings never fall back.** The `model_id` column and the defer-on-unavailable behaviour ship
with the first embedding call, not after a corpus has been contaminated.

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
| **Local** | Lean compose · local password auth behind the existing `TokenVerifier` seam · model catalog with hardware feasibility | **Unplug the network and everything still works** |
| **Cloud** | Managed queue behind the existing abstraction · hosted auth as another verifier · pooling audit · Secret Manager · crawl runs as jobs | Single-tenant prod, SaaS-ready |

**Gate:** run the credential-broker request-scoped-lifecycle spike before committing to the cloud
topology. Four more spikes are in [operations/implementation.md](operations/implementation.md); one
could *remove* work — the broker's own sync engine may replace part of Phase 5.

---

## Phase 7 — team and cases

| Work | Depends on |
|------|-----------|
| Sharing surface, RBAC enforcement | Connection ACL (3) |
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

## Cross-cutting, placed by phase

| Capability | Lands | Note |
|-----------|-------|------|
| **Observability** | 1 → 8 | Detectors ship with the thing they watch, never after |
| **Domain event store** | 2 | The audit substrate must exist before events accumulate |
| **Token accounting** | 2 → 5 | Usage records with depth; estimate-vs-actual with crawler dry-run |
| **Model catalog** | 6 | Needs staleness fields (2) for the impact preview |
| **Trace context across the queue** | 1 | Span links, with the queue abstraction |
| **Delete cascade** | hooks 2, build 8 | |

---

## Two sequencing risks

**Cloud before Local is a trap.** Cloud is the revenue path so it pulls first — but it cuts the
conversational agent and defers the temporal graph, shipping without either differentiator into the
most crowded quadrant. Local is cheaper, proves the privacy claim, sidesteps the compliance
apparatus entirely, and is where the unique capability lives.

**Phase 1 will feel too small.** A write endpoint and a search endpoint is not an impressive demo.
It is the only slice that proves both halves of the system work, and every later slice assumes it.

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
