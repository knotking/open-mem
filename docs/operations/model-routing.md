# Model Routing at Bulk

Model Garden and Smart Routing already ship. The gap is not building them — it is that they were
designed for **event-driven, single-item, interactive** enrichment, and every new worker class
violates one of those assumptions.

## Tiers

| Tier | Used for |
|------|----------|
| Small | JSON, CSV, YAML, XML, IoT, classification |
| Medium | Code, email, chat, financial, summarisation |
| Large | PDFs, Office documents, web pages, reasoning |
| ~~Multimodal~~ | Images, visual PDFs, OCR — **deprecated as a tier** |
| ~~Omni~~ | Audio, video — **deprecated as a tier** |
| Embedding | Vector generation — **see the warning below** |

> **The multimodal and omni tiers are obsolete.** They existed because text models were text-only.
> Current model families are natively multimodal at every size, so modality is a *capability* to
> validate, not a tier to route to. Routing should select on **capacity × required capabilities**.
> See [model-catalog.md](model-catalog.md).

## Fallback chains

Each path has an ordered chain evaluated left to right; the first available model serves. A
provider outage degrades quality or cost, not availability.

**Two exceptions where fallback is unsafe:**

1. **Embeddings must never fall back.** Different models produce incomparable vector spaces. Defer
   with `embed_status = pending` instead. See [versioning](../retrieval/versioning.md).
2. **Regulated content must never fall back.** Falling through to a third-party provider is an
   undisclosed transfer. See [compliance](../security/compliance.md).

## Two credential classes

| Class | Enrich worker | Held where |
|-------|:-------------:|------------|
| **Integration credentials** (OAuth) | must **not** have | gateway / fetch worker, via proxy |
| **AI provider credentials** | **must** have today | resolved per-item, cached |

The invariant is "zero *integration* credentials". The proposed fix is an **LLM proxy** mirroring
the integration proxy, after which no worker holds a secret of either class.

## Collisions with the worker design

| Intersection | Problem |
|--------------|---------|
| Per-item routing in a shared pool | Every message resolves user → agent → tier → engine → credentials. Workers cannot be pinned to a model |
| **Head-of-line blocking** | One tenant's rate-limited provider stalls shared workers and starves everyone else. Needs per-(user, engine) concurrency caps |
| Credential cache × scaling | The per-worker cache means going from 2 to 40 workers multiplies credential fetches 20× |
| **Bulk operations spend user money** | A 50k-item backfill through a large tier on a user's own key is a large unbudgeted bill. Needs estimation up front, budget caps, forced tier-downgrade for bulk paths |
| Retry × fallback | Chain exhaustion triggers retry; the retry may land on a different model. Same input, different output — corrosive for reprocess |
| Worker vs model capacity | Scaling enrich workers past pod capacity relocates the queue from the broker to the model tier, where it is less observable |
