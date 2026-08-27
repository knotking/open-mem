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
| `temporal graph store` | Neo4j + Graphiti | FalkorDB, Memgraph, none | **low** — gated by `is_graphiti_enabled()` |
| `credential broker` | Nango, self-hosted | Paragon, Merge, custom | medium |
| `inference layer` | **MVP: Gemini Flash + Gemini embeddings only.** Later: Ollama / Ollama Cloud + Gemini | any OpenAI-compatible endpoint | **low** — the catalog abstracts it |
| `identity provider` | GoTrue → Firebase / local | any OIDC provider | **high today** (hardcoded at two sites); **low after abstraction** |
| `orchestrator` | Kubernetes + KEDA / Cloud Run | ECS, Nomad | medium |

## The bet has a cost

Colocating the **vector index and lexical index inside the record store** is what makes "only two
required roles" possible — no extra infrastructure, no sync problem, joins and ACL filters in one
query. It is also why the capacity plan says *"Postgres is the shared fate"*: every workspace
competes for the same instance, and filtered ANN search over tens of millions of rows is the
load-bearing risk.

**The floor is low because the ceiling is shared.**

## MVP inference: one provider, one model, both pinned

MVP uses **the latest Gemini Flash for generation and Gemini embeddings for RAG**, and nothing
else. No tiered routing, no fallback chain, no local engine. The catalog abstraction stays and is
filled with exactly one engine — the usual pattern: ship the seam, fill it once.

Three consequences, two of which need a decision now.

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

### Choose the embedding dimension deliberately — it is not changeable later

Gemini embeddings support several output dimensions. The choice sets the pgvector column width,
and **changing it is a full re-embed of the corpus**, not a migration.

| | Smaller (e.g. 768) | Larger (e.g. 3072) |
|---|---|---|
| Index size and memory | Lower | ~4× |
| Filtered ANN latency at scale | Better | Worse — and this is the named load-bearing risk |
| Recall ceiling | Slightly lower | Higher |

Given that *"Postgres is the shared fate"* and filtered ANN over tens of millions of rows is the
scaling risk already on record, **the smaller dimension is the better default** — with the caveat
that it should be measured on a real corpus in the [sandbox](../ui-sandbox.md), which is exactly
the A/B comparison that surface exists for.

### What a cloud-only MVP costs, stated rather than discovered

| Claim in the positioning | Status under a Gemini-only MVP |
|--------------------------|-------------------------------|
| Self-hosted | Still true — the platform runs locally |
| **Air-gapped** | **Not true in MVP.** Every enrichment call leaves the machine |
| **$0 local inference** | **Not true in MVP.** Flash is cheap, not free |
| Regulated / BAA workloads | **Requires the provider agreement to be in place** — there is no local fallback to route to |

This is a reasonable MVP trade: one provider is dramatically simpler, and Flash plus its embeddings
are cheap enough that cost is not the constraint at MVP volume. **But air-gap and $0 are load-bearing
in the competitive positioning**, and they return only when the local engine does.

The mitigation is already designed and costs nothing now: the catalog seam, `model_id` on every
artifact, and the per-project allow-list stay in place. Adding Ollama later is registering an
engine, not re-architecting — and the allow-list means a regulated project can be prevented from
reaching the cloud provider the moment a local one exists.

## Abstract the queue before you need to

NATS JetStream and Pub/Sub differ in ack deadlines, ordering guarantees and redelivery semantics.
The cloud variant already replaces one with the other, so the queue must sit behind an interface —
otherwise there are two ingestion paths to keep correct and the worker retry policy has to be
written twice.
