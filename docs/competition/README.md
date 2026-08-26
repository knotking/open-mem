# Competitive Landscape

**Researched:** August 2026 · **Status:** current

This supersedes the framing in the existing `docs/comparisons/` set (last updated March 2026),
which compares mem-dog only against the agent-memory category — mem0, Zep, BerryDB — plus one
connector platform and two data warehouses.

That framing has a structural problem: **it omits the two categories that compete most directly
for mem-dog's team and personal use cases.** Enterprise search (Onyx, Glean) and local-first
personal AI (Khoj, OpenClaw) are absent entirely, and Onyx in particular is the closest competitor
mem-dog has.

---

## Corrections to existing claims

Three claims in current documentation do not survive research.

### 1. "Self-hosted and air-gapped" is not a differentiator

Repeated positioning treats private, air-gapped, $0 deployment as mem-dog's strongest moat.
It is table stakes in this category.

**Onyx** is MIT-licensed, ships 40+ connectors, and supports fully air-gapped deployment with
local models and zero internet connectivity — plus SOC 2 Type II, GDPR, SSO via OIDC/SAML,
SCIM, RBAC and full audit trails. It markets air-gapped operation as its defining capability
and targets defense, aerospace and regulated industries with it.

**Khoj** is an open-source self-hostable "second brain" running entirely on local models via
Ollama or llama.cpp.

Private-first is the price of entry, not the advantage.

### 2. The connector ceiling is understated, not overstated

Documentation claims **300+ integrations**. Nango — the platform mem-dog delegates OAuth and the
provider catalog to — supports **900+ APIs**. The reachable ceiling is roughly triple what is
claimed, on what is arguably mem-dog's strongest axis. This is worth correcting in `index.mdx`,
`platform-overview.mdx` and the comparison set.

### 3. Onyx is missing from the comparison set

For the team-memory use case, Onyx is the most directly competitive product in the market and
appears in no comparison document. See [comparison-onyx.md](comparison-onyx.md).

---

## Who competes for which use case

| Use case | Direct competitors | Nature of the threat |
|----------|-------------------|----------------------|
| **Personal memory** | Khoj, OpenClaw, Jan, Limitless | Free, permissively licensed, local-first, already shipping |
| **Team memory** | **Onyx**, Glean, GoSearch | Onyx is MIT and air-gapped; Glean has enterprise maturity and 100+ connectors |
| **Agent infrastructure** | Mem0, Zep, Letta, Cognee, Supermemory | Crowded and well funded; Mem0 has the widest integration surface |
| **Embedded backend** | Mem0, Zep, Supermemory | API-first by design — their home turf |
| **Governance** | Onyx, Glean | Already ship SOC 2, SCIM and audit trails |

---

## Feature matrix

`●` strong · `○` partial · `—` absent. Figures as researched August 2026; this category moves fast.

| Factor | mem-dog | Onyx | Glean | Mem0 | Zep | Cognee | Khoj |
|--------|---------|------|-------|------|-----|--------|------|
| Category | memory + search | ent. search | ent. search | agent memory | agent memory | agent memory | personal |
| **License** | proprietary | MIT | closed | Apache 2.0 | OSS core | OSS | OSS |
| Self-host | ● | ● | — | ● | ● | ● | ● |
| Air-gapped | ● | ● | — | ○ | ○ | ○ | ● |
| **Connectors** | 300+ (900+ reachable) | 40+ | 100+ | — | — | — | few |
| Ingestion mode | channels + connectors + API | connectors | connectors | SDK only | SDK only | SDK only | files + web |
| **Messaging channels** | ● 25+ | — | — | — | — | — | ○ |
| Typed memory + TTL | ● 10 types | — | — | ○ scopes | ○ implicit | ○ | — |
| Temporal knowledge graph | ○ optional | ○ LLM KG | — | ○ graph tier | ● native | ● | — |
| **Permission-aware retrieval** | ○ designed | ● ACL sync, pre-filter | ● | ○ scoping | ○ scoping | ○ | — |
| Multi-modal (audio/video) | ○ planned | ○ | ○ | — | — | ○ | — |
| Typed enrichment agents | ● 40 | — | — | — | — | ○ ECL | — |
| MCP server | ● | ● | ○ | ● | ● | ● | ○ |
| **SSO / SCIM / audit** | — | ● SOC 2, SAML, SCIM | ● | ○ ent. tier | ○ ent. tier | — | — |
| Entry price | $0 self-host | $0 community | $50+/user/mo | free tier | free tier | $0 self-host | free |
| Paid tier | — | $20/user/mo | $50+/user/mo | $249/mo graph | $125/mo flex | $200/mo · 10 users | paid cloud |

---

## Honest scorecard

### Leads

| Factor | Against whom |
|--------|-------------|
| **Messaging-channel ingestion and conversational access** | Nobody else treats WhatsApp, Telegram, Signal and Slack as first-class memory channels |
| **Typed memory model** — 10 types, TTL, categories, versioning | mem0 has scopes; Zep is implicit; Onyx and Glean have no memory model at all |
| **Typed enrichment across 60+ data types** | Others index documents; none classify IoT, medical, geospatial or sensor data |
| **Connector breadth** | 2× Glean, 7× Onyx; the memory layers have none |

### Ties

| Factor | Against whom |
|--------|-------------|
| Self-hosting and air-gap | Onyx and Khoj — both permissively licensed |
| Temporal knowledge graph | Zep — mem-dog runs Zep's own Graphiti engine |
| Search modes and reranking | Zep matches mode-for-mode and reranker-for-reranker |
| MCP tool surface | Everyone ships one now |

### Trails

| Factor | Against whom |
|--------|-------------|
| **Permission-aware retrieval** | Onyx syncs ACLs from source systems and filters pre-retrieval — shipped, while mem-dog's is designed |
| **Enterprise compliance** — SOC 2, SSO, SCIM, audit | Onyx and Glean both ship it; mem-dog has none |
| **License** | Proprietary against MIT and Apache incumbents in every adjacent category |
| Ecosystem surface | Mem0 aligns with LangChain, CrewAI, AWS Agent SDK |
| Maturity and community | Mem0 ~50k GitHub stars; Onyx MIT with an active install base |

---

## Strategic read

mem-dog competes on **three fronts simultaneously** — personal memory, team search, agent memory —
against a specialist incumbent on each, while being **proprietary against permissively licensed
rivals**.

No single axis is defensible:

- Onyx matches the privacy and air-gap story under a more permissive license
- Zep owns the temporal graph engine mem-dog runs
- Mem0 owns the agent-integration surface
- Nango owns the connector catalog mem-dog delegates to

The defensible position is the **intersection**: connector breadth *and* memory semantics *and*
private deployment *and* conversational channel access. That combination is genuinely unoccupied.

But an intersection is only a moat if the combination is what customers buy. If they buy one axis
at a time, the intersection reads as three half-products competing with four full ones.

### The finding that should change a decision

**Messaging-channel ingestion is the clearest differentiator — and it is cut from production v1.**

Per the v1 architecture decision, DigiMe/openclaw-node is removed from the serverless production
topology, and Graphiti/Neo4j is deferred. Cloud v1 therefore ships without *either* capability
that distinguishes mem-dog, landing it in the most crowded quadrant of the market with no
differentiation and a proprietary license.

Two further considerations:

- DigiMe is built on the **OpenClaw runtime, which is MIT-licensed and local-first**. The
  capability is replicable by anyone who chooses to build it.
- If channels are the moat, cutting them from the commercial variant while keeping them in the
  free self-hosted one inverts the usual monetisation logic.

### What would strengthen the position

1. **Ship permission-aware retrieval properly** — it is the gap Onyx exploits, and the design
   already exists (connection-scoped ACL inheritance, query-time filtering).
2. **Decide the license question deliberately.** Competing proprietary against MIT in a
   developer-tools category is a choice with consequences; it should be made, not defaulted into.
3. **Reconsider cutting channels from v1**, or accept that v1 competes on price and breadth alone.
4. **Correct the 300+ figure to reflect the real 900+ ceiling** — it undersells the strongest axis.
5. **Enterprise compliance is the entry ticket** for the team use case. Without SSO and audit,
   Family B is not addressable regardless of feature parity.

---

## Documents

| Document | Covers |
|----------|--------|
| [comparison-onyx.md](comparison-onyx.md) | Onyx — the closest competitor; previously undocumented |

Existing comparisons live in `docs/comparisons/` (mem0, Zep, BerryDB, Nango, Snowflake/Databricks)
and are dated March 2026. They need a refresh pass and the corrections listed above.

---

## Sources

- [Onyx — Open-Source Glean Alternatives for Enterprise Search (2026)](https://onyx.app/insights/glean-alternatives)
- [Onyx — Self-Hosted AI Platform for Enterprise](https://onyx.app/solutions/secure-ai)
- [GoSearch — Glean Alternatives Compared (2026)](https://www.gosearch.ai/blog/what-are-the-top-3-glean-alternatives-2026/)
- [MCP.Directory — Mem0 vs Letta vs Zep vs Cognee (2026)](https://mcp.directory/blog/mem0-vs-letta-vs-zep-vs-cognee-2026)
- [Mnemoverse — AI Memory Solutions, Q3 2026](https://mnemoverse.com/docs/library/ai-memory-solutions-2026-q3)
- [Cognee — Open-Source AI Memory Tools](https://www.cognee.ai/blog/guides/best-open-source-ai-memory-tools-for-llm-agents-and-developers)
- [The AI Agent Index — Cognee Review (2026)](https://theaiagentindex.com/agents/cognee)
- [Nango — Best Unified API Platforms (2026)](https://nango.dev/blog/best-unified-api/)
- [Ampersand — 10 Best Unified API Platforms (2026)](https://www.withampersand.com/blog/the-10-best-unified-api-platforms-in-2026)
- [Khoj — self-hostable AI second brain](https://github.com/khoj-ai/khoj)
- [Vellum — Best Personal AI Assistants with Memory (2026)](https://www.vellum.ai/blog/best-personal-ai-assistants-with-memory)
