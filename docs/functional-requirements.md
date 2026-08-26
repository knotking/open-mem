# Mem-Dog — Functional Requirements

Status: baseline, derived from the published mem-dog documentation set
(`BuildGeekAI/mem-dog/docs`, `main`).

Mem-Dog is a self-hosted private AI memory system. It ingests data from
messaging channels, third-party apps, direct API calls and a conversational
agent; enriches it with a pipeline of specialised AI agents; stores it in a
dual-layer (relational + temporal graph) knowledge store; and exposes it
through search, RAG chat, SDKs and an MCP server.

Requirements are identified as `FR-<area>-<n>`. Keywords **MUST**, **SHOULD**
and **MAY** carry their usual RFC 2119 meaning.

---

## 1. Product Goals

| Goal | Requirement |
|------|-------------|
| Private by design | All data processing MUST be able to run entirely inside the operator's network, including fully offline / air-gapped operation. No third party may receive user data in local mode. |
| Fast locally | Classification MUST be deterministic wherever possible, with an LLM call only as a last resort. |
| Cost efficient | The system MUST be operable with zero per-token and zero per-seat cost on self-hosted hardware. |
| Genuinely capable | The system MUST support temporal knowledge-graph reasoning, multiple retrieval strategies, and grounded answers with citations. |

---

## 2. Ingestion

### 2.1 Ingestion channels

- **FR-ING-1** The system MUST accept data from four independent entry points:
  messaging channels (via webhook gateway), third-party app integrations,
  the direct REST API, and the DigiMe conversational agent.
- **FR-ING-2** Each user MUST be able to provision one or more private webhook
  endpoints, identified by a `whk_<ulid>` token, addressable at
  `POST /webhooks/{webhook_id}`.
- **FR-ING-3** A legacy per-channel path (`POST /webhooks/{channel_type}`) MUST
  remain supported, resolving the user through identity heuristics rather than
  a webhook record.
- **FR-ING-4** The gateway MUST normalise every inbound payload into a single
  `UniversalEnvelope` representation before forwarding it to the API.
- **FR-ING-5** The gateway MUST resolve a `user_id` for every envelope. Events
  that cannot be attributed to a user MUST NOT be ingested.
- **FR-ING-6** The gateway MUST attach the user's active integration
  connections to the envelope as tagged references
  (`meta_data.integrations`), classified by relevance
  (`channel_match` / `category_match` / `available`).
- **FR-ING-7** Integration credentials MUST NOT be exposed to the enrichment
  pipeline — only connection references.

### 2.2 Direct ingestion

- **FR-ING-8** `POST /api/v1/data` MUST store content together with caller
  metadata and return a data item identifier.
- **FR-ING-9** Text content ingested directly SHOULD also be submitted to the
  temporal graph as an episode, fire-and-forget, without blocking the response.
- **FR-ING-10** Direct ingestion MUST support optional forwarding to the
  enrichment pipeline.

### 2.3 Webhook management

- **FR-ING-11** Users MUST be able to create, list, enable, disable and delete
  their webhook endpoints.
- **FR-ING-12** A disabled webhook MUST return `200 OK` and MUST NOT process
  the event.
- **FR-ING-13** Every webhook invocation MUST be logged with timestamp, status
  (success / rejected / error), payload size, processing time, and the
  resulting data item ID.
- **FR-ING-14** Per-webhook statistics MUST be retrievable via
  `GET /api/v1/webhooks/{webhook_id}/stats` and visible in the UI.

---

## 3. Data Items

- **FR-DATA-1** Every ingested artefact MUST become a data item with a
  time-sortable, globally unique identifier of the form `data_<ulid>`.
- **FR-DATA-2** The system MUST support at least 60 MIME types across text,
  images, documents, structured formats, audio and video.
- **FR-DATA-3** Every mutation of a data item MUST create a new version, with
  diff, timestamp and mutating user recorded.
- **FR-DATA-4** Full version history MUST be retrievable via
  `GET /api/v1/data/{data_id}/versions`.
- **FR-DATA-5** Data items MUST support arbitrary key-value tags for
  organisation and filtered retrieval.
- **FR-DATA-6** Data items MUST carry a per-item access level (see §8).
- **FR-DATA-7** List endpoints MUST support `limit` / `offset` pagination.

---

## 4. Memories

- **FR-MEM-1** The system MUST provide ten memory types across four
  categories:

  | Category | Types | Default TTL |
  |----------|-------|-------------|
  | Conversation | `conversation` | 1 hour |
  | Session | `session` / `timeline` / `tracing` | 24 h / 7 d / 3 d |
  | User | `user`, `factual`, `semantic` (never expire); `episodic` (temporary); `custom` (varies) | — |
  | Organizational | `organizational` | never |

- **FR-MEM-2** Memory identifiers MUST follow `mem_<type>_<ulid>`.
- **FR-MEM-3** Callers MUST be able to override the default TTL with
  `ttl_hours`, or disable expiry with `no_expiry=true`.
- **FR-MEM-4** Expired memories MUST be cleaned up automatically.
- **FR-MEM-5** Memories MUST support the same access-control model as data
  items.

### 4.1 Compression

- **FR-MEM-6** The system MUST support LLM-driven compression of a memory
  group into a single structured summary.
- **FR-MEM-7** Compression MUST extract and store, as first-class fields:
  summary, key facts, entities, action items and topics.
- **FR-MEM-8** Compression MUST be triggerable manually
  (`POST /api/v1/memories/compress`) and automatically on configurable
  thresholds (`min_items`, `max_tokens`, applicable `memory_types`).
- **FR-MEM-9** Long-lived types (`factual`, `semantic`, `organizational`) MUST
  NOT be auto-compressed by default.
- **FR-MEM-10** Originals MUST be archived rather than deleted, excluded from
  default queries, and retrievable with `include_archived=true`.

---

## 5. Enrichment Pipeline

### 5.1 Flow

- **FR-PIPE-1** Every ingested item MUST traverse:
  `stage → classify → route → LLM analyse → viewpoint → embed → extract
  entities → graph write`.
- **FR-PIPE-2** The pipeline MUST consume work asynchronously from a durable
  message stream so that ingestion latency is decoupled from enrichment.

### 5.2 Classification

- **FR-PIPE-3** Classification MUST be a deterministic cascade evaluated in
  order: channel-message detection → `source_type` field → explicit
  `data_type` → payload field heuristics → MIME registry → URL extension.
- **FR-PIPE-4** An LLM classifier MUST be used only when every deterministic
  layer fails.
- **FR-PIPE-5** The deterministic layers SHOULD resolve approximately 80% of
  traffic without an LLM call.
- **FR-PIPE-6** An explicit `data_type` in the payload MUST short-circuit the
  remaining detection layers.
- **FR-PIPE-7** Unclassifiable content MUST fall back to a generic binary-blob
  agent rather than failing ingestion.

### 5.3 Agents

- **FR-PIPE-8** The pipeline MUST provide ~40 typed sub-agents covering
  documents, media, communication, structured data, code and logs, sensor
  data, spatial data, specialised domains (medical / legal / financial) and
  binary content.
- **FR-PIPE-9** Each agent MUST be individually configurable per user through
  the following processing flags: `classify`, `summarize`,
  `extract_entities`, `extract_actions`, `embed`, `analyze_sentiment`,
  `extract_topics`.
- **FR-PIPE-10** Users MUST be able to override an agent's system prompt.
- **FR-PIPE-11** Users MUST be able to override an agent's structured output
  schema.
- **FR-PIPE-12** Users MUST be able to assign an agent to a model tier.
- **FR-PIPE-13** Agent configuration changes MUST take effect on the next
  pipeline invocation, without redeployment.

---

## 6. Knowledge Graph

- **FR-KG-1** The system MUST maintain a relational graph layer that requires
  no additional infrastructure beyond the primary database.
- **FR-KG-2** The relational layer MUST store entities of at least eight
  types — person, organization, product, location, date, URL, concept,
  event — plus directed relationships and entity-to-data mappings.
- **FR-KG-3** Entities MUST be deduplicated by canonical form via a unique
  index.
- **FR-KG-4** Entities and relationships MUST be queryable via
  `/api/v1/graph/entities` and `/api/v1/graph/relationships`.
- **FR-KG-5** When a graph database is configured, the system MUST perform a
  dual write: synchronous relational write plus asynchronous temporal-graph
  episode ingestion.
- **FR-KG-6** The temporal layer MUST assign `valid_at` / `invalid_at` bounds
  to every extracted fact.
- **FR-KG-7** The temporal layer MUST perform LLM-driven entity resolution
  (merging surface forms such as "John Smith" / "J. Smith" / "John") and
  community detection.
- **FR-KG-8** The system MUST answer point-in-time questions
  ("Who was CEO of Acme in 2024?") by filtering on temporal bounds.
- **FR-KG-9** The temporal layer MUST be optional. With it disabled, all
  non-temporal functionality MUST continue to work.
- **FR-KG-10** Retrieval MUST support entity-aware RAG: match query terms to
  known entities, traverse to related entities, and inject that structured
  context into the generation prompt.

---

## 7. Search and RAG

- **FR-SRCH-1** The system MUST provide five retrieval modes:

  | Mode | Behaviour |
  |------|-----------|
  | `vector` | Cosine similarity over embeddings |
  | `fts` | BM25 keyword matching |
  | `hybrid` | Vector + BM25 merged by Reciprocal Rank Fusion (default) |
  | `graph` | Graph traversal plus semantic search over the knowledge graph |
  | `full` | All signals executed in parallel and RRF-merged |

- **FR-SRCH-2** The system MUST provide four reranking strategies: `none`,
  `rrf`, `mmr` (diversity), and `cross-encoder` (LLM-scored).
- **FR-SRCH-3** `graph` and `full` modes MUST support temporal filtering by
  point in time and by range.
- **FR-SRCH-4** Search MUST be scopeable by memory type, time range, tags,
  project and access level.
- **FR-SRCH-5** `POST /api/v1/ai/query/chat` MUST return a natural-language
  answer grounded in retrieved context with inline `[1][2]` citation markers
  and a machine-readable citation list mapping each marker to its source
  data item.
- **FR-SRCH-6** Retrieval MUST honour access control — results MUST be limited
  to what the authenticated principal may read.
- **FR-SRCH-7** Search and chat MUST be exercisable interactively from the UI
  with live mode and reranker selection.

---

## 8. Access Control, Auth and Tenancy

### 8.1 Authentication

- **FR-AUTH-1** The API MUST accept three bearer credentials, evaluated in
  order: identity-provider JWT → per-user API key (`md_*`) → global API key.
- **FR-AUTH-2** A valid JWT MUST resolve `user_id` from the `sub` claim, and
  MUST auto-provision a user profile on first login.
- **FR-AUTH-3** Per-user API keys MUST be creatable
  (`POST /api/v1/api-keys`) with a name and optional expiry, MUST resolve in
  O(1) to their owning user, and MUST be displayed exactly once at creation.
- **FR-AUTH-4** Users MUST be able to revoke an API key.
- **FR-AUTH-5** The global API key MUST grant unscoped access and MUST NOT be
  exposed to end users or client-side code.
- **FR-AUTH-6** Unmatched credentials MUST return `401`.
- **FR-AUTH-7** Login MUST support email and Google OAuth.

### 8.2 Authorization

- **FR-AUTH-8** Every data item and memory MUST carry one of four access
  levels: `private` (default, owner only), `shared` (owner plus `shared_with`),
  `public` (any authenticated user in the organization), `restricted`
  (`shared_with` only).
- **FR-AUTH-9** Every table and every query MUST be scoped by `user_id`.

### 8.3 Organizations and projects

- **FR-ORG-1** The system MUST support a two-level tenancy hierarchy:
  organization (`org_<ulid>`) → project (`proj_<ulid>`).
- **FR-ORG-2** Memories, data and embeddings MUST be assignable to an
  organization and project.
- **FR-ORG-3** The system MUST support four roles: `owner` (full control,
  delete org, manage members), `admin` (manage members, manage projects),
  `member` (create / read / update), `viewer` (read-only).
- **FR-ORG-4** The API MUST provide full CRUD for organizations, members and
  projects, with role checks on mutating operations.
- **FR-ORG-5** Existing list endpoints MUST accept `?project_id=` for scoping.
  Omitting it MUST return all of the user's data (backward compatible).
- **FR-ORG-6** The UI MUST expose a project selector whose selection is
  propagated as `project_id` on every API call.

---

## 9. AI Configuration (AI Studio)

### 9.1 Model Garden

- **FR-AI-1** Each user MUST be able to register their own AI providers, from
  a catalog of 16+ covering hosted, self-hosted and gateway providers.
- **FR-AI-2** Provider API keys MUST be encrypted at rest with an authenticated
  symmetric cipher, under a key supplied by deployment configuration. Encryption MUST
  **fail closed** — if the key is unavailable the write is refused, never downgraded to
  plaintext.
- **FR-AI-3** API responses MUST never return a stored key; they MUST return a
  `has_api_key` boolean instead.
- **FR-AI-4** Users MUST be able to test provider connectivity and discover the
  provider's available models on demand.
- **FR-AI-5** Engine configuration MUST be isolated per user.
- **FR-AI-6** Credential resolution MUST fall back from user engine →
  environment variable → none, so that env-var-only deployments keep working
  without migration.

### 9.2 Smart routing

- **FR-AI-7** The system MUST provide five model tiers — small, medium, large,
  multimodal, omni — with sensible local defaults per tier.
- **FR-AI-8** Each tier MUST have a configurable, ordered fallback chain
  evaluated left-to-right; the first available model handles the request.
- **FR-AI-9** Each sub-agent MUST be assignable to a tier.
- **FR-AI-10** Per-data-type tier overrides MUST be supported.
- **FR-AI-11** Routing configuration MUST take effect immediately, without
  redeployment.

### 9.3 Infrastructure management

- **FR-AI-12** The UI MUST allow creating, scaling, restarting and deleting
  managed inference pods (prefixed `mdl-`) without shell access.
- **FR-AI-13** Core platform pods MUST be visible for monitoring but MUST NOT
  be modifiable from the UI.
- **FR-AI-14** Scaling a managed pod to zero MUST pause it while preserving
  cached model weights on persistent storage.
- **FR-AI-15** Each pod MUST report CPU, memory, GPU utilisation, request rate
  and P50/P95 latency.
- **FR-AI-16** Pod management MUST require the `admin` or `owner` role.

---

## 10. Integrations

- **FR-INT-1** The system MUST support OAuth2 connections to 300+ third-party
  providers, including authorization-code and PKCE flows.
- **FR-INT-2** Access tokens MUST be refreshed automatically before expiry;
  callers MUST NOT need to perform manual refresh.
- **FR-INT-3** Integration credentials MUST be encrypted at rest with
  AES-256-GCM.
- **FR-INT-4** Users MUST be able to browse the provider catalog, connect via
  an OAuth popup or by entering an API key, list connections, and delete them.
- **FR-INT-5** The system MUST expose a credential-injecting reverse proxy,
  `{METHOD} /proxy/{provider_key}/{path}`, so clients can call upstream APIs
  without handling credentials.
- **FR-INT-6** The proxy MUST retry upstream `429` responses with exponential
  backoff (3 attempts).
- **FR-INT-7** The proxy SHOULD offer optional response normalisation
  (`?normalize=contact|calendar_event`) into unified schemas.
- **FR-INT-8** Provider metadata not tracked upstream (`app_category`,
  `capabilities`, `channel_key`) MUST be synthesised from a local mapping.

---

## 11. Conversational Agent (DigiMe)

- **FR-DM-1** A single agent instance MUST serve multiple users across 25+
  messaging channels with full data isolation between them.
- **FR-DM-2** The agent MUST resolve channel-specific identities (phone
  number, Slack ID, …) to a mem-dog `user_id` before acting.
- **FR-DM-3** The agent MUST provide four skills — bridge, ingest, query and
  semantic search — routed by message content or command prefix.
- **FR-DM-4** The agent MUST call the API using per-user credentials, acting
  strictly on behalf of the resolved user.
- **FR-DM-5** Responses MUST be formatted for the originating channel and
  returned in the same conversation.
- **FR-DM-6** The agent MUST run persistently and reconnect to all channels
  automatically after interruption.

---

## 12. Programmatic Access

### 12.1 REST API

- **FR-API-1** The API MUST expose 70+ REST endpoints under `/api/v1/`,
  grouped as: data, search, memories, ai, webhooks, organizations,
  integrations, mcp.
- **FR-API-2** All identifiers MUST be ULID-based with a type prefix
  (`data_`, `mem_`, `whk_`, `org_`, `proj_`).
- **FR-API-3** Errors MUST use a consistent JSON shape
  (`{"detail": ..., "status_code": ...}`) with conventional HTTP status codes
  including `429` for rate limiting.
- **FR-API-4** An OpenAPI specification MUST be served, with Swagger UI at
  `/api/v1/docs` and ReDoc at `/api/v1/redoc`.

### 12.2 SDKs

- **FR-SDK-1** Official clients MUST be provided for Python, TypeScript, Go,
  Rust and Ruby.
- **FR-SDK-2** The Python client MUST offer both a full client and a
  simplified facade, with async support.
- **FR-SDK-3** Clients MUST accept `org_id` / `project_id` at construction and
  apply them to scoped calls.
- **FR-SDK-4** Adapters MUST be provided for common agent frameworks
  (LangChain, CrewAI, OpenAI).

### 12.3 MCP server

- **FR-MCP-1** The system MUST expose an MCP server over SSE at
  `/api/v1/mcp/sse`.
- **FR-MCP-2** It MUST provide eight tools: `mem_dog_add`, `mem_dog_search`,
  `mem_dog_get`, `mem_dog_list`, `mem_dog_delete`, `mem_dog_entities`,
  `mem_dog_memories`, `mem_dog_chat`.
- **FR-MCP-3** It MUST authenticate with the same credentials as the REST API.
- **FR-MCP-4** It MUST enforce the same access-control rules — tools MUST only
  return data the authenticated user may see.
- **FR-MCP-5** It MUST be configurable from standard MCP clients (Claude
  Desktop, Cursor) through a URL plus `Authorization` header.

---

## 13. User Interface

- **FR-UI-1** The UI MUST provide a dashboard, AI Studio, playground and
  settings.
- **FR-UI-2** AI Studio MUST provide five sub-tabs: Search & Chat, Models,
  Routing, Agents, Infrastructure.
- **FR-UI-3** Settings MUST allow managing API keys, organizations/projects,
  and connected apps.
- **FR-UI-4** The Integrations view MUST show webhook endpoints and their
  event statistics.
- **FR-UI-5** The UI MUST include an insights dashboard and a waterfall span
  viewer for traces.

---

## 14. Observability

- **FR-OBS-1** All components MUST emit OpenTelemetry distributed traces.
- **FR-OBS-2** Traces MUST be persisted as `tracing` memories subject to the
  standard TTL.
- **FR-OBS-3** The API MUST expose an `http.server.request_count` counter
  labelled by method, path and status.
- **FR-OBS-4** LLM token usage MUST be tracked per user, per model and per
  agent.

---

## 15. Deployment and Operations

- **FR-OPS-1** The system MUST be deployable via Docker Compose for local
  development with a single `docker compose up`.
- **FR-OPS-2** The system MUST be deployable to Kubernetes, to managed cloud
  services (GCP/GKE, AWS, Azure), and to single-machine hardware.
- **FR-OPS-3** The storage backend MUST be selectable at deploy time via
  `STORAGE_BACKEND` among `local`, `gcs` and `supabase`, without code changes.
- **FR-OPS-4** The temporal graph, integration platform and cloud model
  providers MUST all be individually optional.
- **FR-OPS-5** Documented resource requirements MUST be published per
  component.

---

## 16. Non-Functional Expectations

| Area | Expectation |
|------|-------------|
| Privacy | Zero third-party data exposure in local mode; air-gapped operation supported. |
| Cost | $0/month recurring on self-hosted hardware; no per-token or per-seat fees. |
| Latency | Deterministic classification for ~80% of items; local chat path bounded by a short timeout (~15s) before falling back to a cloud model. |
| Availability | Every AI dependency has an ordered fallback chain; loss of a cloud provider must degrade quality, not availability. |
| Scalability | Ingestion decoupled from enrichment via a durable queue; inference capacity scales horizontally by replica count. |
| Compatibility | Configuration additions must default safely so existing deployments require no migration. |
