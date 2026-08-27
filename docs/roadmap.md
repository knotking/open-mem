# Roadmap

## Shape

Two shared phases, then three variant tracks that converge.

```
Phase 0 ──▶ Phase 1 ──┬──▶ 2  Demo        (GKE — mostly exists)
correctness  foundation ├──▶ 3  Local MVP  (air-gap provable)
                        └──▶ [spike] ─▶ 4  Cloud MVP
                                            │
                        4b Crawlers ────────┤
                                            ▼
                              5  Team ──▶ 6  Scale & compliance
```

The variants sit at very different maturity, so a single linear plan does not fit.

| Variant | Today | Blocked on |
|---------|-------|-----------|
| **GKE** | running, 4 connectors live | correctness bugs |
| **Local** | compose file exists | password auth — no external IdP offline |
| **Cloud** | decided, unbuilt | queue swap, auth swap, broker spike |

> **The demo is blocked by a bug, not by missing features.** Slack, Gmail and Drive already ingest
> on GKE and RAG chat with citations already works — but the recorded `is_downloaded` failure means
> Gmail and Drive content silently produces no viewpoints, so the demo corpus never enriches.
> Phase 0 makes the existing system demonstrable and contains no new features.

---

## Critical path

**Content contract → write path and producer registry → the four seams → everything else.**

Almost every later capability resolves against one of those. What blocks what:

| Foundation | Unblocks |
|-----------|----------|
| **Content contract** | Correct ingestion for every producer; the Gmail/Drive fix |
| **Write path + producer registry** | ACL inheritance · admission control · rate limits · quota attribution · freshness detection · external producers · bulk · crawlers |
| **Identity abstraction** | Local password auth **and** cloud hosted auth — without it the variants fork |
| **Queue abstraction** | The cloud variant's different broker |
| **Staleness fields** | Agent tuning · schema evolution · index rebuilds · model catalog |
| **Connection/producer ACL** | Team memory · cases · anything multi-tenant |

---

## Phase 0 — correctness and security

No features. All of it blocking, and everything built above it inherits its data.

| Item | Why | Variant |
|------|-----|---------|
| **Rotate committed secrets** | Present in git history — deletion does not fix exposure | all |
| **Content contract** — `ContentRef`, derived `is_downloaded`, mime-primary routing | Fixes the silent Gmail/Drive viewpoint failure | all |
| **Embedding `model_id` + `dim`; stop embedding fallback** | Mixed vector spaces corrupt ranking with no error | all |
| `producer.seconds_since_last_item` | Highest-value detector, and cheap. A dead connection errors nowhere | all |
| `embed.distinct_models_per_index` | Ships with the fix it verifies. Must be exactly 1 | all |
| `minReplicaCount ≥ 1` | Availability target unmeetable at zero | GKE |
| Watch state → durable store | Subscription renewal dies with the volume | all |

**Exit:** ingestion is correct, and the two detectors that prove it are live.

---

## Phase 1 — foundation

Behaviour-neutral and required by every variant. It ships nothing demoable, which creates pressure
to skip it — skipping it means a forked auth path, a forked queue path, and no way to reprocess a
corpus when a prompt changes.

### 1a · The write path

The centrepiece, because seven separately-specified concerns collapse into it.

| Work | Notes |
|------|-------|
| **Producer registry** | `whk_` · `crw_` · `key_` · `upl_`. Nothing writes anonymously |
| **`POST /api/v1/write`** | `items[]` always, `207` always. Batch is not a separate verb |
| `Pending` resolution through the public path | What makes external producers genuinely first-class |
| Admission control in one place | Quota · budget · queue depth · payload size |
| **ACL inheritance from producer scope** | One rule, all paths |
| Readiness states — `stored` · `searchable` · `enriched` | Or every client invents a polling heuristic |
| Idempotency keys | Retries currently duplicate |
| Converge the two error formats | Only one carries a machine-readable code |

### 1b · The four seams

Each one is what lets a variant differ without forking the codebase.

| Seam | Unblocks |
|------|----------|
| **Identity abstraction + `identities` table** | Local password auth and cloud hosted auth |
| Asymmetric token signing | Removes the forge-anywhere weakness |
| **Queue abstraction** | In-process (local) · broker (GKE) · managed (cloud) |
| Trace context across the queue hop | Span links, not child spans |

### 1c · Workers

| Work | Notes |
|------|-------|
| W1 / W2 split with a fetch subject | Opposite resource profiles; one pool blocks both ways |
| Per-producer rate limiting | Subsumes per-(provider, user) |
| **W8 mutate** — revision → version + fact invalidation | Crawlers re-discover constantly; without it every re-crawl duplicates or leaves stale facts |

### 1d · Substrate for iteration

| Work | Notes |
|------|-------|
| Staleness fields + `generator_version` fingerprint | `sha256(canonical_json(config))` — a join, not a manual bump |
| **W7 reprocess** | Prerequisite three times over: agent tuning, schema evolution, index rebuilds |
| Delete-cascade hooks — design, not implementation | Erasure stays possible later |
| **Domain event store**, separate from logs and traces | The audit substrate must exist before events accumulate |

### 1e · Guards

| Work | Notes |
|------|-------|
| Capability-scoped keys | An MCP key must not be able to delete an org |
| **Classification-gated inference** | Closes the regulated-data fallback hazard |
| Token usage records incl. failed and retried calls | Under-counting biases toward surprise bills |

---

## Variant tracks

| Phase | Scope | Exit |
|-------|-------|------|
| **2 · Demo (GKE)** | Verify four live connectors end to end; conversational agent on one channel; RAG chat with citations; **backfill-on-connect**; `enrich.classification_layer` and queue-lag metrics | A repeatable five-minute demo |
| **3 · Local MVP** | One-command lean compose (in-process queue, no Redis, no broker); local password auth; local upload signer; **model catalog with hardware feasibility** | **Unplug the network and everything still works** |
| **4 · Cloud MVP** | Serverless topology, managed data tier, hosted auth, cloud inference, Pub/Sub impl, pooling audit, Secret Manager | Single-tenant prod, SaaS-ready |
| **4b · Crawlers** | Full config store, run lifecycle, scheduler behind one interface, dry-run, remaining strategies; bulk import reusing the crawl run entity | Pull-only catalogue with zero bespoke code |
| **5 · Team & cases** | Sharing surface, RBAC enforcement, **permission sync from source systems**, tier-1 connector depth; case primitive, correlation, case-scoped retrieval | Team memory addressable |
| **6 · Scale & compliance** | Quotas before ceilings, SSO/SAML, SCIM, immutable audit log, delete cascade, legal hold, soak to target | Enterprise-addressable |

**Gate on Phase 4:** run the credential-broker request-scoped-lifecycle spike first. The no-cluster
design assumes it holds no background workers. Four other spikes are listed in
[operations/implementation.md](operations/implementation.md); one of them could *remove* work — the
broker's own sync engine may replace part of the crawler.

### Backfill is Phase 2, not 4b

Without it a user connects a source and sees *nothing* until new data arrives — empty on day one,
nothing to demo, and "ask about something from last week" does not function. The `enumerate`
strategy with a bounded window is MVP-critical. The full crawler framework stays at 4b.

---

## Cross-cutting, placed by phase

Capabilities that span the tracks rather than sitting in one.

| Capability | Lands | Depends on |
|-----------|-------|-----------|
| **Observability** | 0 → 5 | Two detectors in Phase 0; event store in 1; catalogue in 2; SLI dashboards and runbooks in 5 |
| **Token accounting** | 1 → 4 | Usage records in 1; estimate-vs-actual reconciliation in 4b with dry-run |
| **Model catalog** | 3 | Staleness fields (1) for the impact preview |
| **Normalization** | 3 → 4 | `identifiers[]` and `event_time` are the correlation substrate for cases |
| **Cases** | 5 | Normalization · staleness · producer ACL. Additive once those exist |
| **Bulk import** | 4b | The write path (1) and the crawl run entity |
| **Delete cascade** | 6 | Hooks designed in 1 — brutal to retrofit after compression |

---

## Deliberately deferred

| Not yet | Instead |
|---------|---------|
| Bespoke connectors | Ship the tier-3 default path — breadth is nearly free, depth is Phase 5 |
| Composable retrieval | Needed for the agent family; presets suffice until then |
| Expensive LLM indexes | Structural and facet indexes first — deterministic and free |
| Web (`traverse`) crawling | `enumerate` and `query` first — same machinery, no politeness or scope-escape risk |
| Runtime-adaptive agentic crawlers | Agents author configs for human approval; declarative workers execute |
| W9 implementation | Hooks in Phase 1; build when governance lands |
| Media transcription | Largest cost exposure of any format group; gate behind explicit opt-in |

---

## Two sequencing risks

**Ordering Cloud before Local is a trap.** Cloud is the revenue path so it pulls first — but it
cuts the conversational agent and defers the temporal graph, shipping without either
differentiator into the most crowded quadrant. Local is cheaper, proves the privacy claim,
sidesteps the entire compliance apparatus, and is where the unique capability lives.

**Phase 1 has nothing to show.** A full phase ending with no demo creates pressure to skip parts of
it. Every part of it is something that forks the codebase or becomes unfixable if skipped.

---

## Decisions

### Closed by the "support everything" scope decision

| Question | Settled as |
|----------|-----------|
| Team RBAC in mem-dog, or delegated to the host? | **mem-dog enforces natively**; the host model is one broad service principal |
| Global privacy default? | **None** — visibility is producer/connection-scoped |
| Fixed search modes or composable primitives? | **Composable** |
| Can the delete cascade wait? | **No** |
| Keep the global `API_KEY`? | **No** |
| Separate batch endpoint? | **No** — one write verb, `items[]` |

### Still open

| Decision | Note |
|----------|------|
| **Own auth entirely, or keep a hosted IdP?** | Owning it means building MFA, reset and abuse handling — but it is the only version satisfying the air-gapped pillar without a second code path. **Gates the most downstream work** |
| **Materialisation policy** | Always store (recommended) / threshold / derived-only |
| **Default ACL for a team upload** | Private-by-default is consistent; users dragging into a *team* space often expect team visibility. Very hard to change later |
| **Is media in scope for v1?** | Drags in transcription infrastructure and the largest cost exposure |
| **Backfill depth on first connect** | 30 days / 1 year / everything — materially changes sizing and first-run cost |
| **Cross-project cases** | A patient seen by two departments. Spanning breaks the project isolation boundary everything else relies on |

---

Stack choices and the per-variant realisation of each phase are in
[operations/implementation.md](operations/implementation.md).
