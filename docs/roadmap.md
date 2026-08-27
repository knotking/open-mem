# Phased Roadmap

## A shared trunk, then three variant tracks

The three variants sit at very different maturity levels, so a single linear plan does not fit.

| Variant | Today | Blocked on |
|---------|-------|-----------|
| **GKE** | running, 4 connectors live | correctness bugs |
| **Local** | compose file exists | password auth — no external IdP offline |
| **Cloud** | decided, unbuilt | queue swap, auth swap, broker spike |

> **The demo is blocked by a bug, not by missing features.** Slack, Gmail and Drive already ingest
> on GKE and RAG chat with citations already works — but the recorded `is_downloaded` failure means
> Gmail and Drive content silently produces no viewpoints, so the demo corpus never enriches.
> **Phase 0 is what makes the existing system demonstrable**, and it contains no new features.

```
Phase 0 ──▶ Phase 1 ──┬──▶ Phase 2  Demo        (GKE)
 correctness  foundation ├──▶ Phase 3  Local MVP
                        └──▶ [spike] ──▶ Phase 4  Cloud MVP
                                              │
                    Phase 4b Crawlers ────────┤
                                              ▼
                              Phase 5 Team ──▶ Phase 6 Scale + compliance
```

## Phase 0 — correctness and security

No features. All of it blocking.

| Item | Why | Variant |
|------|-----|---------|
| **Rotate committed secrets** | Present in git history — deletion does not fix exposure | all |
| **Content contract** — `ContentRef`, derived `is_downloaded`, mime-primary routing | Fixes the silent Gmail/Drive viewpoint failure | all |
| **Embedding `model_id` + `dim`; stop embedding fallback** | Mixed vector spaces corrupt ranking with no error | all |
| `minReplicaCount ≥ 1` | Availability target unmeetable at zero | GKE |
| Watch state → durable store | Subscription renewal dies with the volume | all |

**Exit:** ingestion is correct. Everything built above this inherits its data.

## Phase 1 — foundation

Behaviour-neutral. Required by every variant and every use-case family.

| Item | Unblocks |
|------|----------|
| **Identity abstraction + `identities` table** | local password auth *and* cloud hosted auth |
| Asymmetric token signing | Removes the forge-anywhere weakness |
| **Queue abstraction** | Cloud's different queue technology |
| Worker split W1 / W2 + fetch subject | Reliable fetch, fan-out, per-provider limits |
| **Connection scope + ACL inheritance** | All privacy work, both families |
| Capability-scoped keys | MCP safety, service identities |
| Staleness fields + W7 reprocess | Config iteration, schema evolution, index rebuilds |
| **Classification-gated inference** | Closes the regulated-data fallback hazard |
| **W8 mutate** — revision → version + fact invalidation | Crawlers re-discover constantly; without this every re-crawl duplicates or leaves stale facts |
| Delete-cascade hooks (design, not implementation) | Erasure remains possible later |

**The load-bearing one:** the identity abstraction is what lets local and cloud diverge without
forking. Build Cloud first with hosted auth hardcoded and Local never happens.

## Variant tracks

| Phase | Scope | Exit |
|-------|-------|------|
| **2 · Demo (GKE)** | Verify four live connectors end-to-end; conversational agent on one channel; RAG chat with citations; **backfill-on-connect** | A repeatable five-minute demo |
| **3 · Local MVP** | One-command compose, local password auth, local models, filesystem blobs, no external accounts | **Unplug the network and everything still works** |
| **4 · Cloud MVP (GCP)** | Serverless topology, managed data tier, hosted auth, cloud inference | Single-tenant prod, SaaS-ready |
| **4b · Crawlers** | Full config store, run lifecycle, scheduler behind one interface, `query` + remaining strategies | The ~8% pull-only catalog with zero bespoke code |
| **5 · Team** | Sharing surface, RBAC enforcement, **permission sync from source systems**, tier-1 connector depth | Family B addressable |
| **6 · Scale & compliance** | Quotas before ceilings, SSO/SAML, SCIM, immutable audit log, delete cascade, soak to target | Enterprise-addressable |

**Gate on Phase 4:** run the credential-broker request-scoped-lifecycle spike first. The
no-cluster design assumes it holds no background workers.

### Correction: backfill is MVP, not Phase 4b

An earlier version of this plan placed all crawler work at Phase 4b. That is wrong for **backfill
specifically**. Without it a user connects a source and sees *nothing* until new data arrives —
the system is empty on day one, the demo has nothing to show, and "ask about something from last
week" does not function.

The `enumerate` strategy with a bounded window (30 days) is MVP-critical and belongs in Phase 2.
The full crawler framework — remaining strategies, scheduling, web traversal — stays at 4b.

### Model catalog

Model cards, capability-validated assignment and the capacity × capability routing change fit
naturally alongside Phase 3, since the local variant is where model choice matters most. The
staleness-impact preview depends on Phase 1's staleness fields. See
[operations/model-catalog.md](operations/model-catalog.md).

## Deliberately deferred

| Not yet | Instead |
|---------|---------|
| Bespoke connectors | Ship the tier-3 default path — breadth is nearly free, depth is Phase 5 |
| Composable retrieval | Needed for the agent family; presets suffice until then |
| Expensive LLM indexes | Build structural and facet indexes first — deterministic and free |
| Web (`traverse`) crawling | Ship `enumerate` and `query` first — same machinery, no politeness or scope-escape risk |
| Runtime-adaptive agentic crawlers | Agents author configs for human approval; declarative workers execute |
| W9 implementation | Design the hooks in Phase 1; build when governance lands |
| Media transcription | Largest cost exposure of any format group; gate behind explicit opt-in |

## Two sequencing risks

**Ordering Cloud before Local is a trap.** Cloud is the revenue path so it pulls first — but it
cuts the conversational agent and defers the temporal graph, shipping without either
differentiator into the most crowded quadrant. Local is cheaper, proves the privacy claim,
sidesteps the compliance apparatus, and is where the unique capability lives.

**Phase 1 feels like no progress.** A full phase with nothing demoable creates pressure to skip
it. Skipping it means a forked auth path, a forked queue path, and no way to reprocess a corpus
when a prompt changes.

## Open decisions

### Closed by the "support everything" scope decision

| Question | Settled as |
|----------|-----------|
| Team RBAC in mem-dog, or delegated to the host? | **mem-dog enforces natively**; the host model is one broad service principal |
| Global privacy default? | **None** — visibility is connection-scoped and space-aware |
| Fixed search modes or composable primitives? | **Composable** |
| Can the delete cascade wait? | **No** |
| Keep the global `API_KEY`? | **No** |

### Still open

| Decision | Note |
|----------|------|
| **Own auth entirely, or keep a hosted IdP?** | Owning it means building MFA, reset and abuse handling — but it is the only version that satisfies the air-gapped pillar without a second code path |
| **Materialisation policy** | Always store (recommended) / threshold / derived-only |
| **Default ACL for a team upload** | Private-by-default is consistent; users dragging into a *team* space often expect team visibility. Very hard to change later |
| **Is media in scope for v1?** | Drags in transcription infrastructure and the largest cost exposure |
| **Backfill depth on first connect** | 30 days / 1 year / everything — materially changes sizing and first-run cost |
