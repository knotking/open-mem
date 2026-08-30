# Technology Choices

Everything in the design docs is stated in **roles**. This is where roles meet products. Keeping
the two separate is not pedantry: the same design runs on three very different stacks, and a role
that names a product cannot be re-filled.

## Role → implementation

| Role | Chosen | Viable alternatives | Swap cost |
|------|--------|--------------------|-----------|
| `record store` | Postgres 16 | any mature RDBMS | **high** — system of record |
| `vector index` | pgvector, inside the record store | Qdrant, Weaviate, Pinecone | medium |
| `lexical index` | Postgres `tsvector` | OpenSearch, Elasticsearch | medium |
| `data access` | Kong + PostgREST via `supabase-py` | raw psycopg | **high** — `storage.py` is ~6,400 lines |
| `blob store` | GCS | S3, Azure Blob, filesystem | **low** — abstracted by `STORAGE_BACKEND` |
| `durable queue` | NATS JetStream / **Pub/Sub in cloud** | Kafka, SQS, Redis Streams | medium — **already swapped per variant** |
| `temporal graph store` | **Postgres** — `entity_facts`, bitemporal | Neo4j + Graphiti, FalkorDB | **low** — one `GraphStore` Protocol |
| `credential broker` | Nango, self-hosted | Paragon, Merge, custom | medium |
| `inference layer` | **MVP: Gemini Flash + Gemini embeddings, plus Ollama Cloud (token-authenticated).** Later: local Ollama | any OpenAI-compatible endpoint | **low** — the catalog abstracts it |
| `identity provider` | GoTrue → Firebase / local | any OIDC provider | **high today** (hardcoded at two sites); **low after abstraction** |
| `orchestrator` | Kubernetes + KEDA / Cloud Run | ECS, Nomad | medium |

## The bet has a cost

Colocating the **vector index and lexical index inside the record store** is what makes "only two
required roles" possible — no extra infrastructure, no sync problem, joins and ACL filters in one
query. It is also why the capacity plan says *"Postgres is the shared fate"*: every workspace
competes for the same instance, and filtered ANN search over tens of millions of rows is the
load-bearing risk.

**The floor is low because the ceiling is shared.**

## MVP inference: Gemini for RAG, Ollama Cloud alongside it

MVP registers **two engines**:

| Engine | Auth | Used for |
|--------|------|----------|
| **Gemini** — latest Flash, plus Gemini embeddings | API key | **Generation and all embeddings** |
| **Ollama Cloud** — open-weight models | Account token | Generation, alternative and comparison |

No local engine yet, and no tiered routing policy — but two engines rather than one, which is a
better MVP than it looks.

### Two engines exercises the seam that one engine does not

A catalog abstraction filled with exactly one entry is untested. Every assumption baked into it —
that `model_id` is recorded, that `served_by_model` is populated, that the per-project allow-list
is consulted, that credentials are held per engine and encrypted — is unfalsifiable while there is
only one thing to select between.

**Registering a second engine at MVP proves the seam works before anything depends on it.**

And the second engine is well chosen for a reason beyond capability: **Ollama Cloud speaks the same
protocol as local Ollama.** The air-gapped variant — the one that restores the $0 and
no-data-leaves-the-machine claims — becomes largely a *base URL and credential change* against an
adapter already in production, rather than a new integration attempted late under pressure.

The riskiest deferred capability in the plan gets de-risked by a choice made for other reasons.
Worth naming so it is not lost.

Three consequences still need a decision now.

### Embeddings stay on one engine, and that is not negotiable

Generation may be served by either engine. **Embeddings may not.**

Two embedding models produce **incomparable vector spaces**. Mixed vectors in one index do not
error — they silently corrupt ranking, and without a `model_id` column the affected rows cannot
even be identified afterwards. A second engine that *can* embed is precisely the condition under
which this happens by accident.

So: Gemini embeddings for RAG, exclusively, and `embed.distinct_models_per_index` is the metric
that catches a violation. Ollama Cloud is a generation engine in this design regardless of what
else it can do.

### Credentials: two providers, one path

The Ollama Cloud token is a provider credential and takes the existing path — held in the model
catalog, **encrypted, failing closed**, never in an environment variable on a worker, never
reaching enrichment code. Same as the Gemini key. Two providers is the point at which "we have a
credential path" stops being a claim.

The per-project allow-list also stops being theoretical. With two engines, *"this project may not
use provider X"* is an enforceable statement rather than a placeholder — which matters for the
regulated case, where the answer is that the chain **fails closed** rather than falling through to
an unapproved provider.

### Pin the version. Never point at a floating alias

`generator_version` is a hash over prompt, **model id**, schema, parser and chunker. Point the
config at a rolling alias like `-latest` and the provider can change the model underneath it
without the id changing.

> **The fingerprint would then be a lie**, and every guarantee resting on it — reproducibility,
> staleness detection, reprocess targeting, trend pinning — silently stops holding while
> continuing to look correct.

So the configured value is an explicit pinned version, and moving to a new one is a deliberate
change that enqueues W7 reprocess. "Latest Flash" is a **procurement decision reviewed
periodically**, not a runtime behaviour.

This applies with more force to the embedding model. A silently-swapped embedding model produces
**incomparable vectors in the same index** — the corruption `embed.distinct_models_per_index`
exists to catch, arriving through the one door nobody is watching.

### Expanding the Ollama Cloud model set is a later-stage catalog operation

Once the adapter is in production, adding models from Ollama Cloud is **registering catalog
entries**, not integration work — which is the payoff for building the seam properly at MVP. Later
stages widen the set: larger open-weight models for harder extraction, smaller ones for cheap
high-volume classification, and per-purpose assignment across them.

Three gates apply, and they are the ones already established rather than new ones:

| Gate | Why |
|------|-----|
| **Each model is a distinct `model_id`** | It enters `generator_version`, so output is attributable and reproducible |
| **Reassigning a purpose enqueues W7 reprocess** | Or the corpus is knowingly mixed — the same rule as any config change |
| **Embeddings remain on a single engine** | Regardless of how many generation models are registered |

The [sandbox A/B comparison](../ui-sandbox.md) is what makes the widened set useful rather than
merely available: *is the larger model worth it on my corpus?* is a question with a
corpus-specific answer, and registering ten models without a way to compare them is ten guesses.

### Choose the embedding dimension deliberately — it is not changeable later

Gemini embeddings support several output dimensions. The choice sets the pgvector column width,
and **changing it is a full re-embed of the corpus**, not a migration.

| | Smaller (e.g. 768) | Larger (e.g. 3072) |
|---|---|---|
| Index size and memory | Lower | ~4× |
| Filtered ANN latency at scale | Better | Worse — and this is the named load-bearing risk |
| Recall ceiling | Slightly lower | Higher |

**Gemini embeddings are Matryoshka-trained, which changes this decision.** The first N dimensions
are a valid embedding on their own, so reducing 3072 → 768 later is a **truncation of stored
vectors** — no API calls, no re-embed. Increasing is still a full re-embed, because the information
was never stored.

That makes the asymmetry the deciding factor: **store at the larger dimension and index at whatever
performs.** Storage and filtered-ANN latency are real costs, but they are recoverable decisions;
re-embedding a corpus is not. Measure the retrieval quality difference on a real corpus in the
[sandbox](../ui-sandbox.md) — that is the A/B comparison that surface exists for.

### The embedding model caps input at 2,048 tokens — that is a chunker constraint

`gemini-embedding-001` accepts **2,048 input tokens**, the smallest among flagship embedding models.
Chunk size must be at or below it, and this is a Phase 1 constraint rather than a tuning knob.

A chunker configured beyond the limit does not fail loudly — it truncates, and the tail of every
long chunk is silently absent from the vector index while remaining keyword-findable, with nothing
recording that it happened. The registration path therefore **validates chunk size against the
embedding model's declared input limit and refuses the configuration**, rather than leaving it to
be discovered.

### What a cloud-only MVP costs, stated rather than discovered

| Claim in the positioning | Status under a Gemini-only MVP |
|--------------------------|-------------------------------|
| Self-hosted | Still true — the platform runs locally |
| **Air-gapped** | **Not true in MVP.** Every enrichment call leaves the machine |
| **$0 local inference** | **Not true in MVP.** Flash is cheap, not free |
| Regulated / BAA workloads | **Requires a provider agreement** — with no local engine there is nothing to fail closed *to*, so the allow-list refuses rather than degrades |

This is a reasonable MVP trade: one provider is dramatically simpler, and Flash plus its embeddings
are cheap enough that cost is not the constraint at MVP volume. **But air-gap and $0 are load-bearing
in the competitive positioning**, and they return only when the local engine does.

The mitigation is already designed and now partly proven: the catalog seam, `model_id` on every
artifact, and the per-project allow-list stay in place — and **the Ollama adapter is in production
from MVP**, pointed at Ollama Cloud. Restoring air-gap later means pointing that same adapter at a
local endpoint and registering it, not building an integration. The allow-list then prevents a
regulated project from reaching any cloud provider at all.

## Abstract the queue before you need to

NATS JetStream and Pub/Sub differ in ack deadlines, ordering guarantees and redelivery semantics.
The cloud variant already replaces one with the other, so the queue must sit behind an interface —
otherwise there are two ingestion paths to keep correct and the worker retry policy has to be
written twice.
