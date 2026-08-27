# Mem-Dog — Architecture

Status: baseline, derived from the published mem-dog documentation set
(`BuildGeekAI/mem-dog/docs`, `main`).

Companion document: [Functional Requirements](functional-requirements.md).

> **Scope note.** This document describes the system *as deployed*, with technologies named.
> The design work that postdates it — the content contract, the eight-class worker model,
> crawlers, the model catalog and the staleness model — lives in
> [design-principles.md](design-principles.md), [ingestion/](ingestion/README.md),
> [retrieval/](retrieval/README.md) and [operations/](operations/README.md). Where the two
> disagree, those documents are current.

---

## 1. System Overview

Mem-Dog is a self-hosted memory platform assembled from loosely coupled
services. Ingestion, enrichment, storage and retrieval are separate tiers so
that each can fail, scale and be replaced independently.

```
  900+ Apps                        Users                      MCP Clients
  Slack, WhatsApp, Telegram…       Web UI (Next.js)           Claude Desktop, Cursor
        │                                │                          │
        ▼                                ▼                          ▼
  Webhook Gateway ───────────────▶  API (FastAPI)  ◀────────  MCP Server (SSE)
  per-user endpoints (whk_<ulid>)      │   │   │   │
  normalise → UniversalEnvelope        │   │   │   └──▶ Nango — OAuth, tokens, 900+ providers
        │                              │   │   └──────▶ Object store — raw binary
        ▼                              │   └──────────▶ Neo4j + Graphiti — temporal graph
  Webhook Pipeline                     ▼
  NATS JetStream                  Postgres 16 + pgvector
  ~40 typed agents                ├── mem_dog_blobs
  classify → analyse              ├── mem_dog_embeddings
  embed → extract entities        ├── mem_dog_entities
        │                         └── mem_dog_relationships
        └──── results ──────────────────┤
                                        ▼
                         Multi-Signal Search Engine
                    vector │ fts │ hybrid │ graph │ full
                                        │
                       Reranker (none │ RRF │ MMR │ cross-encoder)
                                        │
                          RAG Chat with [1][2] citations
```

---

## 2. Components

| Component | Stack | Role | Typical deployment |
|-----------|-------|------|--------------------|
| **API** | Python 3.12, FastAPI | 70+ REST endpoints, storage abstraction, auth, graph writes | Kubernetes (`mem-dog`), port 8080 |
| **UI** | Next.js 14, React 18, TypeScript | Dashboard, AI Studio, Playground, Settings | Serverless container, port 3000 |
| **Webhook Gateway** | Python 3.12, FastAPI, LiteLLM | Channel normalisation, identity resolution, integration tagging, API proxy | Kubernetes (`webhook-gateway`), port 8080 |
| **Webhook Pipeline** | Python 3.12, NATS JetStream, ADK | ~40 typed enrichment agents |
| **Crawl workers** | Python 3.12, scheduled, checkpointed | Pull ingestion — discovery and emission only | Kubernetes (`webhook-pipeline`), port 8080 |
| **DigiMe Agent** | Node.js, OpenClaw runtime | Conversational agent across 25+ channels | Kubernetes (`webhook-gateway`), port 18789 |
| **MCP Server** | Python 3.12, FastMCP, SSE | 8 tools for MCP clients | Co-located with API |
| **Postgres / Supabase** | Postgres 16 + pgvector, GoTrue, Kong | Structured data, embeddings, FTS, auth | Kubernetes (`supabase`), port 5432 |
| **Neo4j** | Neo4j 5.26 + Graphiti | Temporal knowledge graph | Kubernetes (`neo4j`), port 7687 |
| **Nango** | Self-hosted | OAuth, token refresh, credential encryption, provider catalog | Kubernetes (`nango`), port 3003 |
| **Ollama** | gemma3 4b/12b/27b, embeddinggemma | Local inference across tiers | Kubernetes (`webhook-pipeline`), port 11434 |

### Design intent

- **The API is the only writer of record.** The gateway, pipeline, MCP server
  and SDKs all reach storage through the API's HTTP surface, so access control
  and tenancy scoping live in one place.
- **The gateway is the only component that talks to the outside world's
  webhooks.** It handles untrusted input, identity resolution and
  normalisation, and never exposes credentials downstream.
- **The pipeline is stateless and replaceable.** It consumes from NATS and
  writes results back through the API; losing it delays enrichment but never
  loses data.

---

## 3. Data Flow

### 3.1 Channel ingestion

1. A channel delivers a message to the **Webhook Gateway**, either at a
   per-user endpoint (`POST /webhooks/{webhook_id}`) or the legacy channel path
   (`POST /webhooks/{channel_type}`).
2. The gateway resolves `user_id` — from the webhook record on the new path,
   from identity heuristics on the legacy path — and normalises the payload
   into a `UniversalEnvelope`.
3. The gateway fetches the user's active integration connections, tags each
   with a relevance label, and injects the references (not the credentials)
   into `meta_data.integrations`.
4. The gateway forwards the envelope to the API via `POST /api/v1/write`. It is a translator in
   front of the single write path, not a second write path.
5. The API stores the raw payload, creates a `tracing` memory, and publishes
   to NATS.
6. The pipeline consumes the message, classifies it, routes it to a typed
   sub-agent, and writes the viewpoint, embedding and entities back.

### 3.2 Direct ingestion

1. `POST /api/v1/write` stores content plus metadata and returns `data_<ulid>` — the same
   endpoint every producer uses.
2. Text content is additionally submitted to Graphiti as an episode,
   fire-and-forget, so graph latency never affects the write path.
3. Optionally, the item is forwarded to the pipeline for enrichment.

### 3.3 Enrichment

```
Ingest → Stage → Classify (deterministic cascade) → Route to typed agent
       → LLM analysis → Viewpoint → Embedding → Entity extraction → Graph write
```

**Classification cascade** — the LLM is the last resort, and the earlier layers
resolve roughly 80% of traffic:

| Order | Layer | Example |
|-------|-------|---------|
| 1 | Channel message detection | WhatsApp → `chat_message` |
| 2 | `source_type` field | `"pdf"` → `document_pdf` |
| 3 | Explicit `data_type` | supplied by caller |
| 4 | Payload heuristic | `latitude`/`longitude` → `sensor_gps` |
| 5 | MIME registry | `application/json` → `structured_json` |
| 6 | URL extension | `.csv` → `structured_csv` |
| 7 | LLM classifier (fallback) | small-tier model on ambiguous content |
| — | Catch-all | binary-blob agent |

**Agent families** — documents (8), media (4), communication (6), structured
(6), code and logs (4), sensor (5), spatial (2), specialised medical / legal /
financial (3), binary (2).

### 3.3a Crawl ingestion

Scheduled pull, for the majority of sources that never push. A leader-elected scheduler creates a
checkpointed run; the crawl worker discovers resources through the credential-injecting proxy,
deduplicates against what it has seen, and **emits** — all items through `POST /api/v1/write`
with `external_id` upsert, records carrying `Inline` content and files carrying a `Pending`
reference the API converts into a fetch job.

The crawler neither fetches bytes nor enriches. It writes through the same public API an external
producer would, so a crawled record and a webhook-delivered one are indistinguishable downstream.
The watermark advances only on successful completion. See
[ingestion/crawlers.md](ingestion/crawlers.md).

### 3.3b The content contract

Enrichment agents must not be able to observe how content arrived. All producers — inline payloads,
fetch workers, uploads, crawlers — converge on a two-case type:

```
ContentRef =
  │ Inline (text | bytes)
  │ Stored (storage_ref, mime_type, size, checksum)
```

`is_downloaded` is derived, never assignable; routing keys off server-sniffed MIME with
`source_type` demoted to a hint. This retires the class of defect where agents branched on
caller-supplied provenance fields. See [ingestion/workers.md](ingestion/workers.md).

### 3.4 Knowledge-graph dual write

Every extraction produces two writes with deliberately different latency
profiles:

```
Pipeline extracts entities
   ├──▶ POST /api/v1/graph/entities/batch  → Postgres   (synchronous, always on)
   └──▶ Graphiti episode ingestion         → Neo4j      (async, LLM-powered)
            ├── entity resolution (merges surface forms)
            ├── fact extraction with valid_at / invalid_at
            └── community detection (label propagation)
```

The Postgres layer is the availability floor: it needs no extra infrastructure
and is always queryable. The Neo4j layer adds temporal reasoning and is
optional — gated on `NEO4J_URI`.

### 3.5 Retrieval

| Mode | Engine | Mechanism |
|------|--------|-----------|
| `vector` | pgvector | Cosine similarity over embeddings |
| `fts` | Postgres `tsvector` | BM25 keyword matching |
| `hybrid` | pgvector + tsvector | Vector and BM25 merged by RRF (default) |
| `graph` | Graphiti + Neo4j | BFS traversal plus semantic search |
| `full` | all of the above | Hybrid and graph run in parallel, RRF-merged |

Retrieval is followed by optional reranking (`none`, `rrf`, `mmr`,
`cross-encoder`), then optional temporal filtering for `graph` and `full`.
The RAG chat endpoint builds numbered context from the final ranking, calls the
LLM, and returns an answer with inline `[1][2]` markers mapped back to source
data items.

---

## 4. Storage Architecture

### 4.1 Pluggable backends

The API writes through a storage abstraction selected by `STORAGE_BACKEND`:

| Backend | Structured data | Embeddings | Raw binary | Use case |
|---------|-----------------|------------|------------|----------|
| `local` | Filesystem (`~/.mem-dog`) | In-memory scan | Filesystem | Local development |
| `gcs` | Cloud buckets | Bucket | Bucket | Cloud (legacy) |
| `supabase` | `mem_dog_blobs` | `mem_dog_embeddings` (pgvector) | Object store (`RAW_BUCKET`) | Production |

The production backend is deliberately hybrid: structured data and vectors live
in Postgres where they can be joined and filtered, while raw binary lives in
object storage where size is cheap.

### 4.2 Core tables

| Table | Purpose |
|-------|---------|
| `mem_dog_blobs` | General blob store — metadata, memories, configs |
| `mem_dog_embeddings` | pgvector embeddings plus `tsvector` for BM25 |
| `mem_dog_entities` | Graph entities across 8 types |
| `mem_dog_relationships` | Directed entity relationships |
| `mem_dog_entity_data_mapping` | Entity ↔ source data item links |
| `organizations`, `projects`, `org_members` | Multi-tenant hierarchy |
| `agent_configs` | Per-user, per-agent pipeline configuration |
| `webhooks`, `webhook_events` | Endpoint registry and event log |
| `profiles` | User profile, default org/project |

`mem_dog_blobs` and `mem_dog_embeddings` carry nullable `org_id` and
`project_id`, which is what makes project scoping additive rather than a
breaking migration.

### 4.3 Identifier scheme

All identifiers are ULID-based with a type prefix, so they are time-sortable,
globally unique and self-describing: `data_<ulid>`, `mem_<ulid>`,
`whk_<ulid>`, `org_<ulid>`, `proj_<ulid>`, `ent_<ulid>`.

---

## 5. Tenancy Model

```
Organization (org_<ulid>)          — team or company
  ├── Members (user_id + role)     — owner / admin / member / viewer
  └── Project (proj_<ulid>)        — app or workspace
        ├── Memory                 — scoped to project
        ├── Data                   — associated with memory
        └── Embedding              — scoped to project
```

Scoping is applied by passing `project_id` on create and by
`?project_id=proj_…` on list endpoints. Omitting it returns everything the user
owns, which keeps single-tenant deployments unchanged. The UI holds the current
selection in React context and attaches it to every call.

---

## 6. AI and Model Layer

### 6.1 Tiers

| Tier | Default model | Used for |
|------|---------------|----------|
| Small | gemma3:4b | JSON, CSV, YAML, XML, IoT, classification |
| Medium | gemma3:12b | Code, email, chat, financial, summarisation |
| Large | gemma3:27b | PDFs, Office documents, web pages, reasoning |
| Multimodal | Qwen3-VL | Images, visual PDFs, OCR |
| Omni | Qwen3.5 | Audio, video, multi-format |
| Embedding | embeddinggemma / gemini-embedding-001 | Vector embeddings |

### 6.2 Fallback chains

Each path has an ordered chain evaluated left to right; the first available
model serves the request:

- **Pipeline** — smart-routing primary (Ollama Cloud) → Gemini → self-hosted Ollama
- **Chat with data** — local Ollama (≈15s timeout) → Gemini
- **Graphiti** — Gemini for LLM, embeddings and cross-encoder reranking

This is the availability strategy: a provider outage degrades answer quality or
cost, not service.

### 6.3 Model Garden

```
UI (ModelGarden)
  └─▶ API (ai_config)
        ├── GET  /api/v1/ai/provider-registry                        static catalog
        ├── POST /api/v1/ai/users/{uid}/engines                      create engine
        ├── POST /api/v1/ai/users/{uid}/engines/{eid}/test           connectivity
        ├── POST /api/v1/ai/users/{uid}/engines/{eid}/discover-models
        ├── GET  /api/v1/ai/users/{uid}/available-models             aggregated
        └── GET  /api/v1/ai/users/{uid}/engines/{eid}/credentials    internal only
              └─▶ Storage: {user_id}/engines/{engine_id}.json
                    └─▶ Pipeline model_client._resolve_credentials()
```

Credential resolution order in the pipeline: **user engine** (5-minute cache) →
**environment variable** → **none**. Provider keys are encrypted with Fernet
(AES-256) under `MASTER_ENCRYPTION_KEY`; API responses expose only
`has_api_key`.

---

## 7. Integration Layer

```
UI (IntegrationsManager)
  └─▶ API /api/v1/integrations — adapter layer, stable contract
        └─▶ Nango (self-hosted)
              ├── 900+ provider templates
              ├── OAuth2 authorization-code and PKCE flows
              ├── automatic token refresh before expiry
              └── AES-256-GCM credential encryption

Webhook Gateway
  ├── credentials.py — fetch connections by end_user_id
  ├── tag_connection() — channel-affinity relevance tagging
  └── /proxy/{provider}/{path} — credential-injecting reverse proxy
```

The adapter layer exists so that the API's public integration contract stays
stable even though Nango owns the catalog and the flows. Metadata Nango does not
track (`app_category`, `capabilities`, `channel_key`) is synthesised locally
from a static mapping.

The proxy retries upstream `429`s with exponential backoff (3 attempts) and can
normalise responses (`?normalize=contact|calendar_event`) into unified schemas.

---

## 8. Conversational Agent (DigiMe)

```
User (WhatsApp / Slack / Signal / Telegram / …)
  └─▶ OpenClaw runtime — channel adapters
        └─▶ DigiMe skills — bridge │ ingest │ query │ search
              └─▶ mem-dog API — ingest, search, chat
                    └─▶ formatted response back on the same channel
```

DigiMe is intentionally *not* part of the webhook pipeline. It calls the API
directly with per-user credentials, which keeps its latency budget
conversational rather than batch. Identity resolution maps each channel-specific
identity to a `user_id` before any API call, giving one instance multi-user
service with hard data isolation.

---

## 9. Networking

External traffic enters through an L7 global external load balancer:

| Path | Destination | Timeout |
|------|-------------|---------|
| `/gke-api/*` | `api` service (prefix stripped) | 120s |
| `/oc/*` | DigiMe node (prefix stripped) | 30s |
| `/webhooks`, `/channels`, `/query`, `/chat`, … | webhook gateway | 30s |

Internal traffic uses Kubernetes DNS —
`api.mem-dog.svc.cluster.local:8080`, `neo4j.neo4j.svc.cluster.local:7687`,
`nango-server.nango.svc.cluster.local:3003`. The UI reaches the API through
server-side rewrites so the browser never needs a direct cluster address.

---

## 10. Security Architecture

| Layer | Implementation |
|-------|----------------|
| Authentication | Supabase Auth (email + Google OAuth); per-user API keys (`md_*`); global API key |
| Auth evaluation | JWT → per-user key → global key → `401` |
| Authorization | Per-item ACLs — private / shared / public / restricted — plus `shared_with` |
| Tenancy isolation | `user_id` scoping on every table and every query; optional org/project scoping |
| Integration credentials | AES-256-GCM, managed by Nango, never exposed to the pipeline |
| AI provider keys | Fernet AES-256 under `MASTER_ENCRYPTION_KEY`; never returned by the API |
| Gateway | API-key auth, IP allowlist, per-user webhook endpoints |
| Privilege boundaries | Managed (`mdl-`) pods are UI-controllable; infrastructure pods are read-only |

Privacy posture: in local mode the full path — DigiMe → gateway → pipeline →
Ollama → Postgres/Neo4j — stays inside the operator's network. No third party
observes user data.

---

## 11. Observability

| Concern | Implementation |
|---------|----------------|
| Distributed tracing | OpenTelemetry across all components, persisted as `tracing` memories (3-day TTL) |
| Metrics | `http.server.request_count` counter labelled by method, path, status |
| Token accounting | Per-user, per-model, per-agent LLM usage |
| Surfacing | Insights dashboard and waterfall span viewer in the UI |

Storing traces as first-class memories means observability data is searchable
by the same engine as everything else, at the cost of a TTL-bounded write
volume.

---

## 12. Architectural Principles

1. **Deterministic before probabilistic.** Six heuristic layers precede any
   LLM classification call — cheaper, faster and more predictable.
2. **Degrade, don't fail.** Every AI dependency has an ordered fallback chain;
   the optional temporal graph, integration platform and cloud providers can
   each be absent without breaking the core.
3. **Decouple ingest from enrichment.** A durable queue between the API and the
   pipeline means enrichment backlogs never become ingestion outages.
4. **One writer of record.** All persistence flows through the API so tenancy
   and access control are enforced in exactly one place.
5. **Credentials never travel.** The pipeline receives connection references,
   never secrets; secrets are decrypted only at the point of upstream use.
6. **Additive configuration.** New fields default safely and new scoping is
   optional, so upgrades do not require migrations.
7. **Runtime configuration over redeployment.** Routing, agent configs and
   provider keys live in the database and take effect on the next invocation.
