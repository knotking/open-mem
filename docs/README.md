# mem-dog Documentation

A self-hosted AI memory platform: ingests from hundreds of sources, enriches with typed agents,
builds retrieval indexes over a temporal knowledge graph, and serves teams under per-item privacy.

These documents are layered **what → how → with what → where**. Requirements and design speak in
*roles*; product names appear only in the technology and deployment documents, so the same design
can be filled three different ways.

---

## Start here

| Document | Read it for |
|----------|-------------|
| [use-cases.md](use-cases.md) | The five families in scope, how their conflicts resolve, and build order |
| [design-principles.md](design-principles.md) | The central bet, cross-cutting invariants, capability ownership |
| [cases.md](cases.md) | Correlating information around a subject — patient timelines, legal matters, asset histories |
| [roadmap.md](roadmap.md) | Phased plan across local, GKE and cloud — plus open decisions |

## Requirements & architecture

| Document | Covers |
|----------|--------|
| [functional-requirements.md](functional-requirements.md) | ~130 numbered requirements with `FR-<area>-<n>` IDs |
| [architecture.md](architecture.md) | System design, data flow, storage, security, observability |

## Ingestion

| Document | Covers |
|----------|--------|
| [ingestion/](ingestion/README.md) | Entry points and the push/pull split |
| [ingestion/workers.md](ingestion/workers.md) | Eight worker classes, the content contract, failure policy |
| [ingestion/crawlers.md](ingestion/crawlers.md) | The pull half — scheduled, configurable discovery |
| [ingestion/uploads.md](ingestion/uploads.md) | Presigned direct-to-storage uploads |
| [ingestion/sources.md](ingestion/sources.md) | Capability profiles and support tiers |
| [ingestion/connectors.md](ingestion/connectors.md) | The catalog — live, deployed, documented, reachable |
| [ingestion/normalization.md](ingestion/normalization.md) | Canonical types and user-defined schemas |
| [ingestion/formats.md](ingestion/formats.md) | Format tiers and the gotchas that decide them |

## Retrieval

| Document | Covers |
|----------|--------|
| [retrieval/indexes.md](retrieval/indexes.md) | What the pipeline builds and what each index unlocks |
| [retrieval/versioning.md](retrieval/versioning.md) | Two version axes, staleness, the embedding-space bug |

## Interfaces

| Document | Covers |
|----------|--------|
| [api.md](api.md) | Endpoint groups, conventions, capability scoping, personas, SDK layering |

## Security & compliance

| Document | Covers |
|----------|--------|
| [security/tenancy.md](security/tenancy.md) | Org/project model, privacy holes, scale posture |
| [security/auth.md](security/auth.md) | Identity abstraction, password auth, API keys, credential storage |
| [security/compliance.md](security/compliance.md) | GDPR, HIPAA, and what the deployment variant decides |

## Operations

| Document | Covers |
|----------|--------|
| [operations/bulk-operations.md](operations/bulk-operations.md) | Batch writes, imports, and the four other bulk jobs |
| [operations/token-accounting.md](operations/token-accounting.md) | Usage records, budget enforcement, estimate-vs-actual |
| [operations/implementation.md](operations/implementation.md) | Stack choices, per-variant realisation, phased build |
| [operations/model-catalog.md](operations/model-catalog.md) | Model cards, selection, and the tier redesign |
| [operations/model-routing.md](operations/model-routing.md) | Tiers, fallback chains, what breaks at bulk |
| [operations/telemetry.md](operations/telemetry.md) | Detecting absence, not errors |
| [operations/technology.md](operations/technology.md) | Role → product, with swap cost |
| [operations/deployment-variants.md](operations/deployment-variants.md) | Local, GKE and cloud |

## Narrative

| Document | Covers |
|----------|--------|
| [presentation/blog.md](presentation/blog.md) | Long-form article — the whole story, from the bug that started it to what we'd tell someone starting over |

## Market

| Document | Covers |
|----------|--------|
| [competition/](competition/README.md) | Landscape, feature matrix, honest scorecard |
| [competition/comparison-onyx.md](competition/comparison-onyx.md) | The closest competitor, previously undocumented |

---

## Findings worth knowing before you read anything else

These surfaced during review and each is load-bearing:

| Finding | Where |
|---------|-------|
| **Embeddings must never fall back** — different models produce incomparable vector spaces, and the documented chain switches between them with no error | [retrieval/versioning.md](retrieval/versioning.md) |
| **The inference fallback chain crosses a legal boundary invisibly** — regulated content can be transmitted to a third party automatically | [security/compliance.md](security/compliance.md) |
| **Compression defeats erasure** — deleting a source leaves its content inside summaries and derived facts | [security/compliance.md](security/compliance.md) |
| **Encryption fails open** — documented behaviour stores provider keys in plaintext with a warning if the key is unavailable | [security/auth.md](security/auth.md) |
| **API keys are identity-scoped but not capability-scoped** — an MCP key can delete the organization | [api.md](api.md) |
| **Nothing invalidates facts when a source is revised** — temporal queries return confidently wrong answers | [retrieval/versioning.md](retrieval/versioning.md) |
| **W7 reprocess is a prerequisite three times over** — agent tuning, schema evolution and index regeneration all need it | [ingestion/workers.md](ingestion/workers.md) |
| **This system's characteristic failure is silence, not errors** | [operations/telemetry.md](operations/telemetry.md) |
| **Self-hosting is not a differentiator** — it is table stakes; a MIT-licensed competitor ships it with SOC 2 | [competition/](competition/README.md) |
| **Prod v1 ships without either differentiator** — the conversational agent is cut and the temporal graph deferred | [operations/deployment-variants.md](operations/deployment-variants.md) |

---

## Conventions

- **Requirements state roles, not products.** `record store`, not "Postgres". Product choices live
  in [operations/technology.md](operations/technology.md) with their swap cost.
- **Worker classes** are referenced as `W1`–`W9`; the canonical table is in
  [ingestion/workers.md](ingestion/workers.md).
- **Use-case families** are referenced as A–E; the canonical list is in [use-cases.md](use-cases.md).
- A shareable rendering of this material exists as a published artifact; these documents are the
  source of truth.
