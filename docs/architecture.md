# Architecture

The system as designed, **with technologies named**. Two companion documents deliberately do not:
[design-principles.md](design-principles.md) states the same structure in roles, and
[operations/technology.md](operations/technology.md) records what fills each role and what it
costs to swap.

## 1 · Shape

```
  PRODUCERS ── all preconfigured, none writes anonymously
  whk_ channels & apps   crw_ crawlers   key_ clients & ETL   upl_ uploads   agent
        │                     │                │                  │            │
        ▼                     │                │                  │            │
   ┌──────────┐               │                │                  │            │
   │ GATEWAY  │  translator in front of the write API, not a second one        │
   │ normalise│               │                │                  │            │
   │ resolve  │               │                │                  │            │
   └────┬─────┘               │                │                  │            │
        └─────────────────────┴────────┬───────┴──────────────────┴────────────┘
                                       ▼
                    ┌──────────────────────────────────────┐
                    │      POST /api/v1/write              │  FastAPI
                    │  producer → admission → ACL → audit  │
                    │  items[] · 207 · idempotency key     │
                    │  ContentRef: Inline | Stored | Pending│
                    └───────┬──────────────────────┬───────┘
                            │ commit (sync)        │ Pending
                            ▼                      ▼
                  ┌───────────────────┐   ┌─────────────────┐
                  │  Postgres 16      │   │  ingest.fetch   │
                  │  + pgvector       │   └────────┬────────┘
                  │  + tsvector       │            ▼
                  └─────────┬─────────┘   ┌──────────────────┐      ┌──────────────┐
                            │             │  W2 FETCH        │─────▶│ /proxy       │
                            │             │  stream → GCS    │      │ Nango        │
                  ┌─────────▼─────────┐   └────────┬─────────┘      │ ★ secrets ★  │
                  │  ingest.enrich    │◀───────────┘                └──────────────┘
                  │  NATS / Pub-Sub   │
                  └─────────┬─────────┘
                            ▼
              ┌──────────────────────────────┐     ┌────────────────────┐
              │  W1 ENRICH                   │◀───▶│  MODEL LAYER       │
              │  classify → normalize →      │     │  capacity ×        │
              │  route → viewpoint → embed   │     │  capability        │
              │  → entities → indexes        │     │  ✗ no fallback for │
              └──────────────┬───────────────┘     │    embeddings or   │
                             │                     │    regulated data  │
        ┌────────────────────┼──────────────┐      └────────────────────┘
        ▼ sync               ▼ async        ▼
  ┌─────────────┐   ┌────────────────┐  ┌──────────┐
  │ Postgres    │   │ Neo4j+Graphiti │  │ GCS      │
  │ ★ REQUIRED ★│   │ optional       │  │ blobs    │
  └──────┬──────┘   └────────────────┘  └──────────┘
         ▼
  POST /api/v1/retrieve — select × match × filter × rank, ACL in the query
         ▼
  RAG chat with [1][2] citations
```

## 2 · Components

| Component | Stack | Role |
|-----------|-------|------|
| **API** | Python 3.12, FastAPI | The single write path, retrieval, control plane. **Sole writer of record** |
| **Gateway** | Python 3.12, FastAPI | Channel normalisation, identity resolution, credential-injecting proxy |
| **Enrich workers (W1)** | Python 3.12, asyncio | Classification, normalization, typed agents, embedding, extraction |
| **Fetch workers (W2)** | Python 3.12, `httpx` streaming | Resolve `Pending` references; the only worker that reaches the proxy |
| **Crawl workers (W3)** | Python 3.12, checkpointed runs | Scheduled discovery — six strategies |
| **UI** | Next.js 14, TypeScript | A client of the API, never a privileged path |
| **MCP server** | Python 3.12, SSE | Data-plane tools only; no control-plane surface |
| **Postgres 16** | + pgvector, tsvector | Record store, vector index, lexical index, graph layer 1 |
| **NATS / Pub-Sub** | behind one interface | Decouples ingest latency from enrichment |
| **Neo4j + Graphiti** | optional | Bitemporal facts, entity resolution |
| **GCS** | | Raw binary, presigned uploads |
| **Nango** | self-hosted | OAuth, refresh, credential encryption, provider catalog |
| **Model layer** | Ollama local / cloud + Gemini | Capacity × capability routing |

## 3 · Data model

Six primitives. The distinctions between them are load-bearing.

| Primitive | Is | Is not |
|-----------|-----|--------|
| **Data item** | The unit of content, versioned, ULID | — |
| **[Memory](memories.md)** | A **lifecycle** container — how long this matters | A subject |
| **[Case](cases.md)** | A **subject** — what this is about, declared not inferred | A lifecycle container |
| **Producer** | A registered writer, carrying ACL scope and policy | A credential |
| **Entity** | Extracted, resolved, merged across sources | An identity — see cases |
| **Derived artifact** | Embedding, summary, claim, facet — with provenance and staleness | Authoritative |

Memories are many-to-many with items and **mutable after write**; effective expiry is the maximum
across memberships and is **computed, never stored**. Cases are declared with a stable external id
and correlate by identifier join rather than inference.

### Core tables

```
orgs · projects · org_members · groups · identities · api_keys
producers (inbound_auth, connection scope, policy)
data_items (org_id, project_id, access_level, shared_with[], language, event_time)
memory_types · memories (memory_key) · memory_members · memory_links
cases · case_members
embeddings (model_id, dim, tsvector language config)
entities · relationships · entity_data_mapping
derived_artifacts (source list, generator_version, served_by_model, fallback_depth, status)
generator_versions          immutable registry — prompt text, model, schema
crawlers · crawl_runs · crawl_frontier · crawl_seen
audit_events                append-only; INSERT and SELECT only
```

## 4 · The four flows

**Write** — every producer, one endpoint. Producer resolved, admission checked, ACL derived from
producer scope, audit written, commit synchronous. `Inline` and `Stored` go straight to the enrich
queue; `Pending` goes to fetch first. The write returns before any model runs.

**Enrich** — six deterministic classification layers before any LLM call, then normalization to a
canonical type, then a typed agent. Output is schema-validated regardless of prompt. Embeddings
record `model_id`; artifacts record what actually served them.

**Retrieve** — composable: `select × match × filter × rank`. ACL is applied **inside the query**,
never after ranking. Deduplicated by content hash so one document does not appear twice.

**Delete** — cleanup or erasure, distinct. Beyond one item it is a checkpointed job. Entities are
reference-counted; summaries are marked stale and rebuilt; audit records survive.

## 5 · Storage

`STORAGE_BACKEND` selects: `local` (filesystem), `gcs`, `supabase` (Postgres + GCS for binary).

**Colocating the vector and lexical indexes inside the record store is the architectural bet** — no
extra infrastructure, joins and ACL filters in one query, only two hard dependencies. It is also
why Postgres is every tenant's shared fate, and why filtered ANN at tens of millions of rows is the
load-bearing risk.

## 6 · Security

| Layer | Implementation |
|-------|----------------|
| Login | Firebase (cloud, GKE) · local password, Argon2id (air-gapped) — behind one `TokenVerifier` |
| API keys | User-created, capability-scoped, SHA-256 hashed, gateway-validated |
| Inbound | The same user keys where a provider can present one; signature or URL secret otherwise |
| Authorization | Per-item ACL over principals — user, group, project, org, public |
| Tenancy | Every query scoped; producer scope decides item ACL |
| Encryption | Envelope — KEK in Cloud KMS, per-tenant DEKs, **failing closed** |
| Admin | Platform grants orthogonal to org roles; **metadata, never content**; break-glass audited |
| Audit | Append-only, separate store, retention in years |

## 7 · Observability

OpenTelemetry throughout, with **trace context carried across the queue as a span link** — the
async continuation is not a child span.

Four signals, not three: metrics, traces, logs, and **domain events** as the audit substrate.
Designed around **detecting absence**, since this system's characteristic failure produces no error
— `producer.seconds_since_last_item` against each producer's own baseline is the highest-value
detector in it.

## 8 · Deployment

Three variants filling the same roles — see
[operations/deployment-variants.md](operations/deployment-variants.md). Local runs the queue
in-process and uses local password auth, which is what makes air-gapped operation a self-hosted
capability rather than a property of the hosted product.

## 9 · Principles

1. **Deterministic before probabilistic** — six heuristic layers precede any LLM call
2. **Degrade, don't fail** — with two deliberate exceptions where fallback is unsafe: embeddings
   (incomparable vector spaces) and regulated content (undisclosed transfer)
3. **One writer of record** — all persistence through the API, so tenancy is enforced once
4. **Credentials never travel** — workers get references; injection happens at a proxy
5. **Make wrong states unrepresentable** — a closed content contract beats a validated flag
6. **Declared beats inferred for identity** — cases and producers are declared; entities are not
7. **Write-time facts cannot be retrofitted** — tenancy, ACL, provenance and audit are columns
8. **Runtime config beats redeployment** — prompts, schemas, routing and crawlers live in the
   database
