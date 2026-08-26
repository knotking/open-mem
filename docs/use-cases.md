# Use Cases

Five families, all in scope. Supporting every family means building to the **maximum** of each
dimension rather than the average: the strictest privacy requirement applies everywhere, the
tightest latency budget governs the eager path, the largest scale target governs capacity, and
the broadest connector catalog governs coverage.

## What the indexes enable

| What the pipeline builds | What it makes possible |
|---|---|
| Vector + BM25 + hybrid | Universal search across every source at once |
| Graph + temporal facts | "Who was CEO in 2024", "what changed since January" |
| Cross-source entity resolution | One person or org view spanning CRM, email, chat and calls |
| Normalized facets | `invoices > 10k`, `open tickets by assignee` — analytics without a warehouse |
| Intent index | "What did I commit to?", meeting follow-through |
| Summary hierarchy | Briefings — "catch me up on Acme" |
| Claim index | Contradiction detection, evidence assembly |
| Timeline memory | Activity reconstruction, journaling |

## The five families

| Family | Covers | Needs |
|--------|--------|-------|
| **A · Personal memory** | Universal search, ask-your-life Q&A with citations, timeline reconstruction, contact recall, commitment tracking; reachable from any messaging app | Breadth of connectors, cheap local inference |
| **B · Team memory** | Shared knowledge base, onboarding, meeting intelligence, customer 360, institutional memory, cross-tool reporting | Per-item privacy, deep connectors |
| **C · Agent infrastructure** | Memory layer for other AI — MCP tools, persistent context across sessions, RAG backend | Sub-second retrieval, composable primitives |
| **D · Embedded backend** | Private memory behind someone else's product — workspaces, scoped keys, project-isolated retrieval | Tenancy contracts, quotas, purge and export |
| **E · Governance** | Data inventory, erasure and DSAR execution, audit trail | Falls out of the architecture — *if designed early* |

## The tensions, and how each resolves

| Tension | Resolution |
|---------|------------|
| **Personal and team want opposite privacy defaults.** A wants everything indexed and surfaced; B must guarantee no teammate sees your personal mail. | **New mechanism** — data inherits its ACL from the *connection* that produced it, not the space it lands in. |
| **Product vs platform RBAC.** B needs mem-dog to enforce; D delegates to the host. | **Unify** — one enforcement path. The host model becomes the case where a service identity is a single broad principal. |
| **Breadth vs depth.** A wants 300 shallow connectors; B's customer 360 needs three handled deeply. | Already solved by the tier model — Tier 3 for breadth, Tier 1 for depth. An ordering, not a contradiction. |
| **Cost profiles diverge.** A on local models is $0; D at 1k workspaces is a firehose. | Configuration — per-org budget caps plus tier policy. |
| **Latency expectations diverge.** C needs sub-second in-loop; B tolerates ten seconds. | Already solved by the eager / deferred / adaptive index split. |

### The unifying rule

**ACL inheritance follows the connection, not the container.**

A connection carries a scope — `personal` or `shared` — set at connect time. Personal Gmail
inside a team org produces `private` items regardless of project defaults; a team Slack connected
as `shared` produces member-visible items. Same org, same pipeline, opposite defaults, no
contradiction.

It also closes the proxy authorization hole: permission becomes "owns this connection, or it is
shared" rather than "authenticated to the org".

## What supporting everything rules out

1. **No single global privacy default** — visibility must be connection-scoped and space-aware.
2. **No fixed five-mode retrieval API** — Family C requires composable primitives.
3. **No host-delegated-only RBAC** — mem-dog must enforce natively.
4. **The delete cascade cannot be deferred** — governance is table stakes for B and D.
5. **The global unscoped `API_KEY` must go** — it voids every ACL the other families depend on.

## Build order

Build what the most families share first. Required by every family:

```
content contract → worker split → identity abstraction and auth →
connection-scoped ACL model → tenancy scoping → W7 reprocess
```

Then roughly: **A** (breadth, tier-3 default path) → **D** (embedded) → **C** (agent, composable
retrieval) → **B** (team, sharing surface and deep connectors).

**One deliberate inversion:** build **E** early despite ranking it last in urgency. The delete
cascade is cheap to design now and expensive to retrofit once compression has baked content into
summaries in production.

## Where the differentiation sits

| Capability | Position |
|---|---|
| Universal search | commodity |
| RAG backend | commodity — mem0, Zep, LlamaIndex |
| Temporal knowledge graph | contested — Zep owns the engine mem-dog runs |
| Self-hosted, air-gapped, $0 | **table stakes** — Onyx does it under MIT |
| **Messaging-channel ingestion** | **genuinely unique** |
| **Connectors × memory × private** | the wedge — the intersection is unoccupied |

See [competition/](competition/) for the full analysis.
