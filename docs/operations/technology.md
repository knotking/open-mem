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
| `inference layer` | Ollama / Ollama Cloud + Gemini | any OpenAI-compatible endpoint | **low** — Model Garden abstracts it |
| `identity provider` | GoTrue → Firebase / local | any OIDC provider | **high today** (hardcoded at two sites); **low after abstraction** |
| `orchestrator` | Kubernetes + KEDA / Cloud Run | ECS, Nomad | medium |

## The bet has a cost

Colocating the **vector index and lexical index inside the record store** is what makes "only two
required roles" possible — no extra infrastructure, no sync problem, joins and ACL filters in one
query. It is also why the capacity plan says *"Postgres is the shared fate"*: every workspace
competes for the same instance, and filtered ANN search over tens of millions of rows is the
load-bearing risk.

**The floor is low because the ceiling is shared.**

## Abstract the queue before you need to

NATS JetStream and Pub/Sub differ in ack deadlines, ordering guarantees and redelivery semantics.
The cloud variant already replaces one with the other, so the queue must sit behind an interface —
otherwise there are two ingestion paths to keep correct and the worker retry policy has to be
written twice.
