# mem-dog Documentation

A self-hosted AI memory platform: ingests from hundreds of sources, enriches with typed agents,
builds retrieval indexes over a temporal knowledge graph, and serves teams under per-item privacy.

These documents are layered **what → how → with what → where**. Requirements and design speak in
*roles*; product names appear only in the technology and deployment documents, so the same design
can be filled three different ways.

---

## How to read this

Eight parts, in order. The published [blueprint artifact](https://claude.ai/code/artifact/c8a9e266-fef7-4b68-b521-dceef8d0574e)
is the same material as one continuous page.

| Part | Covers | Documents |
|------|--------|-----------|
| **I · Motivation** | Who this is for, what it answers, and the one bet everything rests on | [use-cases](use-cases.md) · [use-case catalog](use-cases-catalog.md) · [feature matrix](feature-matrix.md) · [design-principles](design-principles.md) |
| **II · The API** | The rules the surface obeys, the one write verb, and how settings resolve | [usage](usage.md) · [api](api.md) · [write-api](ingestion/write-api.md) · [settings](settings.md) |
| **III · Data model** | The things themselves — and the tables they physically live in | [memories](memories.md) · [cases](cases.md) · [normalization](ingestion/normalization.md) · [schema](operations/schema.md) · [blob layout](operations/blob-layout.md) |
| **IV · Ingestion** | Every producer that uses the write path, and how far it bends | [workers](ingestion/workers.md) · [extraction prompts](ingestion/extraction-prompts.md) · [custom workers](ingestion/custom-workers.md) · [customization](ingestion/write-customization.md) · [sources](ingestion/sources.md) · [connectors](ingestion/connectors.md) · [formats](ingestion/formats.md) · [uploads](ingestion/uploads.md) · [crawlers](ingestion/crawlers.md) · [bulk](operations/bulk-operations.md) |
| **V · Reads** | Finding it again, and proving the answer came from somewhere | [indexes](retrieval/indexes.md) · [quality](retrieval/quality.md) · [sandbox](ui-sandbox.md) |
| **VI · Lifecycle** | How a record changes, goes stale, and goes away | [versioning](retrieval/versioning.md) · [deletion](operations/deletion.md) |
| **VII · Models** | Choosing what runs, on which data, and what it costs | [model catalog](operations/model-catalog.md) · [model routing](operations/model-routing.md) · [multilingual](multilingual.md) |
| **VIII · Security and privacy** | Who can see what, and what we can prove afterwards | [tenancy](security/tenancy.md) · [access model](security/access-model.md) · [auth](security/auth.md) · [privacy foundations](security/privacy-foundations.md) · [compliance](security/compliance.md) · [audit](security/audit.md) · [activity memory](activity-memory.md) |
| **IX · The console** | One application, three audiences, six tensions | [ui design](ui-design.md) |
| **X · Operations** | Getting it running, keeping it honest, and knowing when it is not | [onboarding](operations/onboarding.md) · [billing](operations/billing.md) · [technology](operations/technology.md) · [deployment variants](operations/deployment-variants.md) · [telemetry](operations/telemetry.md) · [testing](operations/testing.md) · [implementation](operations/implementation.md) |
| **XI · The plan** | Sequence, open decisions, the market | [roadmap](roadmap.md) · [TBD](../TBD.md) · [competition](competition/README.md) |

> **[TBD.md](../TBD.md)** — twelve decisions that are designed but not decided, ordered by how
> expensive each becomes if made late.

## Start here

| Document | Read it for |
|----------|-------------|
| [usage.md](usage.md) | **Six scenarios against a running system** — write and ask, pull from an app, receive a webhook, build the graph, backfill a cold crawl, erase with proof |
| [use-cases.md](use-cases.md) | The five families in scope, how their conflicts resolve, and build order |
| [feature-matrix.md](feature-matrix.md) | Every feature, its phase, and whether it is in MVP |
| [ui-design.md](ui-design.md) | Information architecture, the six tensions that decide it, and screens by area |
| [ui-sandbox.md](ui-sandbox.md) | Upload a dataset, watch it enrich, chat against it — and see what the chat actually retrieved |
| [use-cases-catalog.md](use-cases-catalog.md) | All twelve published use cases with their implementation paths and honest status |
| [design-principles.md](design-principles.md) | The central bet, cross-cutting invariants, capability ownership |
| [memories.md](memories.md) | Typed lifecycle containers — configurable types, TTL, expiry policy, data mapping |
| [cases.md](cases.md) | Correlating information around a subject — patient timelines, legal matters, asset histories |
| [multilingual.md](multilingual.md) | Language detection, lexical config, and the embedder choice that decides cross-lingual retrieval |
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
| [ingestion/write-api.md](ingestion/write-api.md) | **The single write path** — one endpoint, registered producers |
| [ingestion/write-customization.md](ingestion/write-customization.md) | The nine write phases, which four are customizable, and why ACL is sealed before them — **post-MVP** |
| [ingestion/custom-workers.md](ingestion/custom-workers.md) | Customer code in data workers on a dedicated cluster, and the four invariants that don't relax — **post-MVP** |
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
| [security/access-model.md](security/access-model.md) | Principals, groups, public sharing, admin dual-role, settings |
| [security/auth.md](security/auth.md) | Identity abstraction, password auth, API keys, credential storage |
| [security/privacy-foundations.md](security/privacy-foundations.md) | **What cannot be retrofitted** — audit, provenance, encryption, classification |
| [security/compliance.md](security/compliance.md) | GDPR, HIPAA, and what the deployment variant decides |

## Operations

| Document | Covers |
|----------|--------|
| [operations/bulk-operations.md](operations/bulk-operations.md) | Batch writes, imports, and the four other bulk jobs |
| [operations/deletion.md](operations/deletion.md) | Cleanup vs erasure, the cascade, legal hold, verification |
| [operations/token-accounting.md](operations/token-accounting.md) | Usage records, budget enforcement, estimate-vs-actual |
| [operations/implementation.md](operations/implementation.md) | Stack choices, per-variant realisation, phased build |
| [operations/model-catalog.md](operations/model-catalog.md) | Model cards, selection, and the tier redesign |
| [operations/model-routing.md](operations/model-routing.md) | Tiers, fallback chains, what breaks at bulk |
| [operations/telemetry.md](operations/telemetry.md) | Detecting absence, not errors |
| [operations/testing.md](operations/testing.md) | Invariants as tests, nondeterminism, per-phase gates |
| [operations/technology.md](operations/technology.md) | Role → product, with swap cost |
| [operations/deployment-variants.md](operations/deployment-variants.md) | Local, GKE and cloud |

## Narrative

| Document | Covers |
|----------|--------|
| [presentation/blog.md](presentation/blog.md) | Long-form article — the whole story, from the bug that started it to what we'd tell someone starting over |
| [examples/](examples/README.md) | **Worked examples** — canonical flows with sequence diagrams, and medical, legal and telemetry walkthroughs with real records |
| [presentation/blueprint.md](presentation/blueprint.md) | **The whole documentation set as one file** — same eight parts, ~50k words, for sharing or offline reading |

## Market

| Document | Covers |
|----------|--------|
| [competition/](competition/README.md) | Landscape, feature matrix, honest scorecard |
| [competition/comparison-onyx.md](competition/comparison-onyx.md) | The closest competitor, previously undocumented |
| [competition/comparison-glean.md](competition/comparison-glean.md) | The category leader, and the market it structurally cannot serve |

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
