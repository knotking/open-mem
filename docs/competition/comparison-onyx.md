# open-mem vs Onyx: Detailed Comparison

**Last updated:** August 2026

Onyx (formerly Danswer) is an MIT-licensed enterprise AI platform bundling 40+ connectors, hybrid
search over an OpenSearch-backed vector index, permission-aware retrieval, AI chat, multi-step deep
research and custom agents. It is the leading open-source alternative to Glean.

**Onyx is the most directly competitive product open-mem faces for the team-memory use case, and
until now it appeared in no comparison document.** It is also the product that most directly
contests open-mem's privacy positioning — it is self-hostable, air-gapped-capable, and permissively
licensed.

---

## At a Glance

| | open-mem | Onyx |
|-|---------|------|
| **What it is** | Private AI memory platform — multi-channel ingestion, 40-agent enrichment, RAG query engine | Enterprise AI search and assistant over connected company data |
| **Focus** | End-to-end data lifecycle with a typed memory model | Search, chat and agents grounded in company knowledge |
| **Deployment** | Self-hosted (Docker, GKE, Mac Mini) + planned Cloud Run | Self-hosted (Docker Compose, Helm, Terraform) + cloud |
| **Air-gapped** | Yes, on self-hosted variants | Yes — marketed as the defining capability |
| **License** | Proprietary | **MIT** |
| **Pricing** | Free self-hosted | Free community, $20/user/mo cloud |
| **Compliance** | None | SOC 2 Type II, GDPR, SSO (OIDC/SAML), SCIM, RBAC, audit trails |

---

## Feature-by-Feature

### Data ingestion

| Feature | open-mem | Onyx |
|---------|---------|------|
| Connectors | 300+ documented, 900+ reachable via Nango | 40+ |
| **Messaging channels** | 25+ (WhatsApp, Telegram, Signal, Discord, Slack…) via DigiMe | None |
| Push ingestion | Per-user webhooks, real-time via queue | Connector sync |
| Direct upload | Text, file, URL, camera, voice, video | File upload |
| Data types | 60+ including IoT, medical, geospatial, sensor | Documents and text |
| Enrichment | 40 typed sub-agents, 6-layer classification, tiered model routing | Indexing, chunking, embedding, LLM knowledge graph |

**Verdict: open-mem leads clearly.** Onyx ingests documents from business systems. open-mem ingests
from business systems *and* messaging channels *and* arbitrary data types, with typed analysis per
type. The connector gap is roughly 7× before counting the untapped Nango catalog.

### Permissions and access control

| Feature | open-mem | Onyx |
|---------|---------|------|
| Per-item ACL | 4 levels + `shared_with` | Inherited from source systems |
| **Source ACL sync** | None — permissions set in open-mem only | **Pulls ACLs from source**: private Slack channels, ACL'd Confluence spaces, private repos |
| Filter point | Designed for query-time; currently varies | **Pre-retrieval**, not at the chat layer |
| RBAC | 4 org roles | RBAC + SCIM provisioning |
| SSO | None | OIDC/SAML — Okta, Entra ID, AWS IAM |
| Audit | Tracing memories | Full audit trails |

**Verdict: Onyx leads decisively.** This is the sharpest gap in the comparison. Onyx solved a
problem open-mem has not started: **when you ingest a private Slack channel, who should see it?**
Onyx answers by syncing the source system's ACLs and filtering before retrieval. open-mem requires
permissions to be managed separately, which does not survive contact with real corpora — a
connector that ingests everything a user can see, and then exposes it to everyone in the org, is
a data leak by construction.

open-mem's designed answer — connection-scoped ACL inheritance with query-time filtering — is the
right shape, but it is a design and theirs is shipped.

### Memory model

| Feature | open-mem | Onyx |
|---------|---------|------|
| Typed memories | Open type set — name + TTL + expiry policy, re-typable | None — it is a search index |
| TTL / expiry | Per-type defaults, overridable | None |
| Versioning | Every mutation, with diffs | Re-index on change |
| Compression | LLM summarization with archive | None |
| Temporal facts | Bitemporal in Postgres — valid time *and* transaction time | LLM knowledge graph, non-temporal |

**Verdict: open-mem leads.** Onyx has no memory abstraction — it indexes documents and searches
them. Conversation state, session scoping, decaying context and point-in-time queries have no
equivalent. For agent memory, Onyx is not a competitor at all; for team *knowledge*, the memory
model may matter less than search quality.

### Search and retrieval

| Feature | open-mem | Onyx |
|---------|---------|------|
| Vector search | pgvector | OpenSearch-backed |
| Keyword | Postgres `tsvector` BM25 | Hybrid built in |
| Graph | Recursive CTE + semantic, ACL inside the traversal | LLM-built knowledge graph |
| Modes | 5 (vector, fts, hybrid, graph, full) | Hybrid + reranking |
| Rerankers | 4 (none, RRF, MMR, cross-encoder) | Reranking included |
| Temporal filtering | Yes — `valid_at` and `as_of`, independently | No |
| Deep research | No | **Multi-step deep research** |
| Custom agents | Per-agent pipeline configs | **Custom agents with MCP tool use** |

**Verdict: roughly comparable, different strengths.** open-mem has more retrieval modes and
temporal filtering. Onyx has multi-step deep research and user-definable agents — capabilities
open-mem lacks entirely, and which are increasingly what buyers evaluate.

### Deployment and operations

| Feature | open-mem | Onyx |
|---------|---------|------|
| Docker Compose | Yes | Yes |
| Kubernetes | GKE manifests | **Official Helm chart** |
| IaC | Manual scripts | **Terraform for AWS / Azure / GCP** |
| Air-gapped | Yes | Yes, with local models |
| Managed cloud | Planned (Cloud Run) | Available |
| Local models | Ollama, tiered | Configurable — OpenAI, Anthropic, open weights |

**Verdict: Onyx leads on operational maturity.** Helm charts and Terraform modules for three clouds
against manual deploy scripts is a meaningful adoption difference for the exact buyer both products
target — the team that wants to self-host.

---

## Where each wins

### Choose open-mem when

- Data arrives from **messaging channels**, not just business systems
- You need a **typed memory model** — sessions, TTL, decaying context, point-in-time queries
- You ingest **non-document data**: sensor, medical, geospatial, IoT
- You need **connector breadth** beyond 40 sources
- You are building **agent memory**, where Onyx does not compete

### Choose Onyx when

- You need **permission-aware search over existing company systems** today
- **Compliance is a requirement** — SOC 2, SSO, SCIM, audit
- **License matters** — MIT versus proprietary
- You want **deep research and custom agents** out of the box
- You want **operational maturity** — Helm, Terraform, an install base

---

## Assessment

Onyx is not a memory platform, and open-mem is not an enterprise search product. They collide on the
team-memory use case, and on that ground **Onyx is currently stronger**: permission-aware retrieval,
compliance, licensing and deployment tooling all favour it, and those are precisely the criteria a
team evaluating self-hosted knowledge tooling applies.

open-mem's advantages — channels, typed memory, data-type breadth, connector count — are real but
sit *outside* the evaluation criteria for that buyer. They matter enormously for personal memory
and agent infrastructure, where Onyx does not compete at all.

**The strategic implication:** do not compete with Onyx on enterprise knowledge search. It is ahead
on the axes that decide those deals, and it is free. Compete where it structurally cannot follow —
messaging channels, typed memory semantics, and data types outside the document world — and close
the permission gap because that one is a correctness issue regardless of competition.

---

## Sources

- [Onyx — Open-Source Glean Alternatives for Enterprise Search (2026)](https://onyx.app/insights/glean-alternatives)
- [Onyx — Self-Hosted AI Platform for Enterprise](https://onyx.app/solutions/secure-ai)
- [Onyx — Enterprise Search Tools 2026](https://onyx.app/insights/enterprise-search-tools-2026)
- [NeuralChainAI — Self-Hosted Enterprise Search: Onyx for Regulated Teams](https://neuralchainai.com/solutions/self-hosted-enterprise-search-ai/)
- [elest.io — Onyx: Free Open Source AI Platform with Connectors, Agents & Knowledge Base](https://blog.elest.io/onyx-free-open-source-ai-platform-with-connectors-agents-knowledge-base/)
