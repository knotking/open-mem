# Design Principles

## The central bet

Reading the system end to end, exactly two **roles** are non-negotiable: a **record store** and
the **API surface**. Every other capability — the temporal graph, the credential broker, the
channel gateway, the conversational agent, the inference layer — can be absent without taking the
system down.

That single property drives the deployment story, the fallback chains, and the phasing of
everything else.

> Stated as roles rather than products deliberately. Which technology fills each role is an
> implementation choice, recorded in [operations/technology.md](operations/technology.md).

## Dependency criticality

| Role | Status | Degradation if absent |
|------|--------|----------------------|
| `record store` | **required** | Total. Durable, queryable home for data, memories, embeddings, graph layer 1 and tenancy |
| `API surface` | **required** | Total. Sole writer of record — all tenancy and ACL enforcement lives here |
| `blob store` | prod | Raw binary ingestion unavailable; text paths continue |
| `durable queue` | enrich | Ingest still succeeds; items land unenriched until drained |
| `inference layer` | enrich | Deterministic classification survives; no analysis, embeddings or chat |
| `temporal graph store` | optional | Lose temporal facts, point-in-time queries, graph and full retrieval modes |
| `credential broker` | optional | Lose OAuth integrations and the credential-injecting proxy |
| `channel gateway` | optional | Lose channel ingestion. API, SDK, MCP and UI unaffected |
| `conversational agent` | optional | Lose messaging-app access to the corpus |
| `tool server` | optional | Lose MCP-client integration |

## Cross-cutting invariants

| Invariant | Enforced where |
|-----------|----------------|
| **Every query is scoped by `user_id`** | API storage layer, unconditionally |
| **Integration credentials never travel** | Workers receive references; injection happens at a proxy |
| **Deterministic before probabilistic** | Six heuristic layers precede any LLM classification call; ~80% never reach an LLM |
| **Degrade, don't fail** | Every AI dependency has an ordered fallback chain — with two exceptions (embeddings, regulated content) where fallback is unsafe |
| **One writer of record** | All persistence flows through the API so access control is enforced in one place |
| **Additive configuration** | New fields default safely; new scoping is optional. Upgrades need no migration |
| **Runtime config beats redeployment** | Routing, agent configs, schemas and crawler configs live in the database and take effect on the next invocation |
| **Provenance must not be trustable** | Agents branch on a closed content contract, never on caller-supplied fields |
| **Derived artifacts inherit the strictest source ACL** | Filtering happens in the query, never post-rank |
| **IDs are ULID + type prefix** | All create paths |

## Capability ownership

`●` owns · `○` participates · `—` not involved.
**GW** gateway · **API** core · **PIPE** pipeline · **UI** web app · **DM** conversational agent ·
**MCP** tool server.

| Capability | GW | API | PIPE | UI | DM | MCP | Requires |
|-----------|:--:|:---:|:----:|:--:|:--:|:---:|----------|
| Inbound webhook receipt | ● | — | — | — | — | — | record store |
| Identity resolution | ● | — | — | — | ● | — | record store |
| Envelope normalisation | ● | — | — | — | — | — | — |
| Integration relevance tagging | ● | — | — | — | — | — | credential broker |
| Direct data write | — | ● | — | ○ | ○ | ○ | record + blob store |
| Versioning & diffs | — | ● | — | ○ | — | — | record store |
| Authentication | ○ | ● | — | ○ | ○ | ○ | record store |
| Per-item access control | — | ● | — | — | — | ○ | record store |
| Org / project scoping | — | ● | — | ● | — | — | record store |
| Durable ingest handoff | — | ● | ● | — | — | — | durable queue |
| Classification cascade | — | — | ● | — | — | — | inference (last resort) |
| Typed sub-agent analysis | — | — | ● | ○ | — | — | inference |
| Per-agent configuration | — | ● | ● | ● | — | — | record store |
| Embedding generation | — | ○ | ● | — | — | — | inference + vector index |
| Entity extraction | — | ○ | ● | — | — | — | inference |
| Relational graph write | — | ● | ○ | — | — | — | record store |
| Temporal graph write | — | ● | ○ | — | — | — | graph store |
| **Crawl discovery** | — | ○ | ● | ● | — | — | credential broker |
| Vector search | — | ● | — | ○ | ○ | ○ | vector index |
| Lexical search | — | ● | — | ○ | ○ | ○ | lexical index |
| Graph & temporal search | — | ● | — | ○ | ○ | ○ | graph store |
| Reranking | — | ● | — | ○ | — | — | inference |
| RAG answer with citations | — | ● | — | ○ | ○ | ○ | inference |
| Memory TTL & expiry | — | ● | — | ○ | — | ○ | record store |
| Memory compression | — | ● | ○ | ○ | — | — | inference + record store |
| OAuth & token refresh | — | ○ | — | ● | — | — | credential broker |
| Credential-injecting proxy | ● | — | — | — | — | — | credential broker |
| Model tiers & fallback | — | ● | ● | ● | — | — | record store + inference |
| Provider key encryption | — | ● | — | ○ | — | — | record store |
| Managed pod lifecycle | — | ● | — | ● | — | — | orchestrator |
| Conversational channel agent | — | ○ | — | — | ● | — | inference |
| MCP tool surface | — | ○ | — | — | — | ● | — |
| Tracing & token accounting | ○ | ● | ○ | ● | ○ | ○ | record store |
