# Implementation Plan & Stack

*Proposals to validate against the codebase, not settled decisions. This was written from the
design documents; dependency manifests and the pipeline's existing framework may already provide
several of these, or already have an established alternative in-repo.*

## Constraints

1. **No new languages.** Python 3.12 and TypeScript. The channel agent stays Node.
2. **No new infrastructure unless a role demands it.** Reach for Postgres before adding a service.
3. **Every choice must work in all three variants** — or be behind the interface that lets them
   differ.

That third constraint is the one that does the work. It is why the queue, the scheduler, the
object store and the identity provider all need interfaces, and why almost nothing else does.

---

## Component stack

| Component | Tech | Why this one |
|-----------|------|-------------|
| Content contract | Pydantic discriminated union (`Field(discriminator=...)`) | Closed sum type *with* runtime validation; `is_downloaded` as `computed_field` so it is structurally unsettable |
| MIME sniffing | `filetype` / `puremagic` | Pure-Python, no native dep in slim images. libmagic is more accurate where you can carry it |
| Queue abstraction | `Protocol` over `nats-py`, `google-cloud-pubsub`, and an in-process impl | The interface is small; the *semantics* differ — ack deadlines, ordering keys, redelivery |
| Fetch worker | `httpx` streaming + resumable upload | Bytes never buffer |
| Retry / backoff | `tenacity` | Composable, jitter built in |
| Rate limiting | Redis token bucket via Lua | Must be atomic **across replicas** — in-process limiters silently fail past one pod |
| Crawl scheduling | Postgres schedule table + `pg_try_advisory_lock` | Leader election with zero new infrastructure, identical across variants |
| Crawl state | Postgres (`crawl_runs`, `crawl_frontier`) | Durable and resumable — precisely what Redis is wrong for |
| robots.txt | `protego` | Handles crawl-delay and wildcards; stdlib `robotparser` does not |
| HTML parsing | `selectolax` | Substantially faster than BeautifulSoup at crawl volumes |
| Field mapping | `jmespath` | Well-specified and boring. Do not invent a DSL |
| Chunking | `semantic-text-splitter` | Standalone; avoids pulling a framework in for one function |
| Password hashing | `argon2-cffi` | Argon2id |
| Token verification | `PyJWT` RS256 + JWKS cache, behind a `TokenVerifier` Protocol | Local and OIDC implementations from one seam |
| Trace propagation | OTel `TraceContextTextMapPropagator` | Inject into message headers, extract in the worker — closes the queue-hop gap |
| Model catalog | Static YAML → Pydantic, merged with discovery | Same pattern as `nango_provider_meta.py`; air-gap friendly |
| Config UI forms | `react-hook-form` + `zod` | Crawler configs need schema-driven forms with live validation |
| Integration tests | `testcontainers` + `respx` | Real Postgres and queue in tests; mock HTTP at the boundary |

### Three non-obvious calls

**API keys hash with SHA-256, not Argon2.** Passwords need a slow KDF because they are low-entropy
and human-chosen. API keys are high-entropy random tokens — a slow hash buys nothing and costs you
on every request. Different threat model, different primitive.

**`generator_version` is a fingerprint, not a number.**
`sha256(canonical_json({prompt, model_id, schema, parser_version, chunker_version}))`. Staleness
detection becomes a join rather than a manual bump someone forgets, and it catches changes nobody
thought to version.

**Postgres advisory locks instead of an orchestrator.** The obvious answer for crawl scheduling is
Temporal or Airflow. Both are real dependencies with their own operational burden, and Temporal in
particular is hard to justify in the local variant. A schedule table plus `pg_try_advisory_lock`
covers scheduling, leader election and resumability at target scale.

### Deliberately not added

**Celery** — its worker model does not fit queue-driven asyncio, and you would run two queue
systems. **A separate vector DB** — pgvector-in-the-record-store *is* the architectural bet.
**Kafka** — the chosen brokers suffice. **Playwright** — ship `traverse` without JS rendering; add
it opt-in per config if real sites demand it. **A second graph database.**

---

## Per-variant realisation

| Concern | Local | GKE | GCP (Cloud Run) |
|---------|-------|-----|-----------------|
| Record store | Postgres container | Supabase in-cluster | Cloud SQL PG16 + pgvector |
| **Pooling** | direct, small pool | PgBouncer / pooler | **Cloud SQL connector + PgBouncer — mandatory** |
| Data access | Kong + PostgREST | Kong + PostgREST | Kong + PostgREST on Cloud Run |
| Queue | **in-process** (single instance) | NATS StatefulSet | Pub/Sub |
| Queue client | in-memory impl | `nats-py` | `google-cloud-pubsub` |
| Blob store | filesystem | GCS | GCS |
| **Presigned upload** | local signed-token endpoint | GCS signed URL | GCS signed URL via IAM `SignBlob` |
| Inference | Ollama container | Ollama pods, tiered | Ollama Cloud + Gemini |
| Scheduler | in-process ticker | CronJob + advisory lock | Cloud Scheduler → HTTP |
| **Crawl execution** | background task | Deployment | **Cloud Run Jobs** |
| Enrich / fetch workers | asyncio tasks | Deployments + KEDA | Cloud Run services on Pub/Sub push |
| Stream workers (W6) | n/a | **StatefulSet + sharding** | not supported — needs a cluster |
| Rate-limit store | in-process | Redis pod | Memorystore |
| Auth | local password | GoTrue → abstracted | Firebase Auth |
| Secrets | `.env` | k8s Secrets | Secret Manager |
| Autoscale | none | KEDA on queue depth | Cloud Run concurrency, `min-instances ≥ 1` |
| Telemetry sink | stdout / local collector | OTel → Prometheus + Grafana | Cloud Trace + Monitoring |

### GCP — five things that will bite

**1. Connection exhaustion.** Cloud Run can spin up a hundred instances; each holding a pool of ten
is a thousand connections against a Cloud SQL instance that allows far fewer. This is *the* classic
serverless-plus-Postgres failure. Small pools, lazy initialisation, and a pooler in front — not
optional.

**2. `/tmp` is memory.** Cloud Run's filesystem is in-memory and counts against the instance's
memory limit. Buffering a 500 MB download to disk does not degrade — it OOMs. Streaming straight
to the object store is **mandatory here**, where elsewhere it is merely correct.

**3. Ack deadline versus inference latency.** Pub/Sub push has a maximum ack deadline. A slow
enrichment call plus a fallback chain can exceed it, causing redelivery and duplicate work. Either
ack on receipt and track completion separately, or use pull subscriptions with `min-instances ≥ 1`.
Decide deliberately; the default will bite.

**4. Crawl runs are jobs, not requests.** A three-hour backfill does not fit a request-driven
service. Cloud Run **Jobs** are the right primitive — task-based, long-running, resumable via the
checkpoint in Postgres. Enrich and fetch stay as push-driven services.

**5. Signed URLs need a signer.** The service account needs
`roles/iam.serviceAccountTokenCreator` on itself to sign without a key file. Easy to miss, fails
only at runtime.

### Local — three things that will bite

**1. Presigned uploads have no signer.** Filesystem storage cannot issue a presigned URL. Two
options: run MinIO for S3 parity, or keep the API shape and have the local backend return a URL
pointing at a local upload endpoint with a signed token. **Prefer the second** — same contract,
one fewer container, and the contract is what matters.

**2. The full stack does not fit a laptop.** Postgres, NATS, Redis, Ollama, Kong, PostgREST, Nango,
API and UI is a lot of memory before a model is loaded. Ship a **lean profile**: drop Nango (no
integrations), drop Redis (in-process limiting is correct at one instance), and run the **queue
in-process** — with one instance there is nothing to distribute. This is the third payoff of the
queue interface.

**3. Model choice is constrained by the machine.** This is where the catalog's hardware-feasibility
check earns its place: offering a 70B model to a 16GB laptop is a bad first experience.

### GKE — two things that will bite

**1. Stream workers are not Deployments.** W6 holds one connection per account. A Deployment with
three replicas ingests everything three times. It needs a StatefulSet with account sharding and
leader election — a different deployment shape from every other worker.

**2. KEDA needs queue depth exposed.** Scaling enrich workers on CPU is wrong; they are blocked on
inference, not compute. Scale on consumer lag, which means the broker's metrics have to reach KEDA.

---

## Build order — where to actually start

The phases say *what*. This says *what you do on Monday*.

### Step 0 — read the codebase

Everything in this document was derived from documentation. Nobody has opened `api/` or `webhook/`
while writing it, so several choices below will change on contact.

| Read | Because |
|------|---------|
| Dependency manifests | Several proposed libraries may already be present, or have an established in-repo alternative |
| `storage.py` and the PostgREST path | ~6,400 lines whose shape constrains the schema work |
| The two JWT verify sites in `main.py` | The `TokenVerifier` seam is a refactor of these, not a greenfield |
| The pipeline's ADK framework | It may already provide a worker or queue abstraction that would otherwise be duplicated |
| Existing migrations and table shapes | Determines whether Phase 1's schema is additive or a parallel set |

Roughly half a day. Do it before writing anything.

**In parallel and non-blocking: rotate the committed secrets.** They are in git history, that is
live exposure, and it has no dependency on the build order.

### Steps 1–8 — to the first milestone

Order is dependency-driven; each step is unblocked by the one before it.

| # | Step | Why here |
|---|------|----------|
| **1** | **Schema** — orgs, projects, members, groups, producers, data items with `org_id`/`project_id`/`access_level`/principal `shared_with`, `derived_artifacts` with source **list** + `served_by_model`, `generator_versions` registry, `audit_events`, embeddings with `model_id`/`dim` | Everything in Phase 1b is columns. Design them **once, together**. Highest-leverage single artifact in the plan — get it wrong and every later slice inherits a migration |
| **2** | **Crypto foundation** — KMS envelope encryption, encrypt/decrypt helpers, **failing closed** | Nothing can safely store a credential before this, and step 3 needs to |
| **3** | **Auth seam + producer registry** — `TokenVerifier` with the API-key verifier; `identities` and `api_keys` tables; capability scopes; producer `inbound_auth` | Every write needs a producer and a credential. Firebase and the gateway arrive later **behind this seam**, so building it now costs an implementation and skipping it costs a fork |
| **4** | **Model config, minimal** — one engine, one embedding assignment, `generator_versions` populated | Step 6 embeds. This is the difference between provenance from row one and a corpus-wide staleness event later |
| **5** | **Write endpoint** — `POST /write`, `Inline` only, `items[]`, `207`, commit, ACL from producer, **audit record written** | The spine's first half |
| **6** | **Embed path** — queue abstraction with the in-process implementation, `model_id` recorded, defer-never-fallback | Makes what was written findable |
| **7** | **Read** — `GET /data/{id}` and `POST /retrieve`, vector + lexical + hybrid, **ACL applied inside the query** | Closes the spine |
| **8** | **Invariant gate** — adversarial cross-tenant across every retrieval path, kill-after-`2xx` durability, `distinct(model_id) == 1` property | The exit criterion |

Tests are written **alongside** each step. Step 8 is the gate, not when testing begins — see
[testing.md](testing.md).

### The milestone

> Write an item into a project owned by an org with an access level → a search scoped to that
> project finds it → the response cites it → the read is audited → the row records which model
> embedded it.

At that point both halves of the system are real and every later slice widens a spine that works.

### Nothing open blocks starting

Worth stating plainly, because it is easy to assume otherwise:

| Open decision | When it is needed |
|---------------|------------------|
| Own auth vs hosted IdP | Deferred by the `TokenVerifier` seam — Phase 6 |
| Materialisation policy | Phase 4, with uploads and fetch |
| Default ACL for a team upload | Phase 4 |
| Media in v1 scope | Phase 4–5 |
| Backfill depth on first connect | Phase 5 |
| Cross-project cases | Phase 7 |

**None of them gates step 1.** That is itself an argument for this ordering — the work that must
be decided last is also the work that happens last.

### UI, in this window

Slice 1 needs a **thin console** — one page: write something, search, inspect a result. Not a
product surface. It exists because retrieval quality cannot be judged from a JSON body, and it is
built after step 7, against the API rather than beside it.

Full UI sequencing is in [the roadmap](../roadmap.md).

## Phased implementation

### Phase 0 — correctness

**No new dependencies.** A Pydantic model change, two columns, a config value, and a rotation.

| Work | Tech |
|------|------|
| Content contract | Pydantic discriminated union; `computed_field` |
| MIME sniffing at ingest | `filetype` |
| `model_id` + `dim` on embeddings | migration |
| Disable embedding fallback | config + guard in the model client |
| Rotate committed secrets | `git-filter-repo` awareness — rotation, not history rewriting alone |

### Phase 1 — foundation

The phase that introduces the interfaces.

| Work | Tech |
|------|------|
| Queue abstraction | `Protocol` + three impls (in-process, NATS, Pub/Sub) |
| Trace propagation across the hop | OTel propagator into message headers |
| `TokenVerifier` seam | `PyJWT`, JWKS cache via `httpx` + TTL |
| Asymmetric signing | RS256 keypair; serve JWKS |
| Worker split (W1/W2) | asyncio, `httpx` streaming, `tenacity` |
| Per-(provider, user) limits | Redis Lua token bucket — in-process impl for local |
| Staleness fields | Postgres tables; fingerprint via `sha256(canonical_json(...))` |
| Capability-scoped keys | FastAPI dependency; SHA-256 key hashes + prefix column |
| Classification-gated inference | Policy check before the fallback chain |

### Phase 2 — demo (GKE)

| Work | Tech |
|------|------|
| Backfill-on-connect | `enumerate` strategy only; Postgres checkpoint |
| Crawl scheduling | schedule table + `pg_try_advisory_lock` |
| `minReplicas ≥ 1` | manifest change |
| Watch state → Postgres | migration off the volume |

### Phase 3 — local MVP

| Work | Tech |
|------|------|
| Lean compose profile | in-process queue, no Redis, no Nango |
| Local password auth | `argon2-cffi`, lockout counters, reset tokens |
| Local upload signer | signed-token endpoint behind the same API shape |
| Model catalog + hardware check | static YAML → Pydantic; VRAM detection or declared |
| **Air-gap acceptance test** | disconnect network in CI; full flow must pass |

### Phase 4 — cloud MVP

| Work | Tech |
|------|------|
| **Broker lifecycle spike** | deploy the credential broker to Cloud Run *in dev* first |
| Pub/Sub impl | `google-cloud-pubsub`, push subscriptions |
| Crawl runs as jobs | Cloud Run Jobs |
| Pooling | Cloud SQL connector + PgBouncer; audit pool sizes against max connections |
| Scheduler | Cloud Scheduler → authenticated HTTP |
| Secrets | Secret Manager |
| Signed URLs | IAM `SignBlob`; grant `serviceAccountTokenCreator` |
| Telemetry sink | OTel exporter → Cloud Trace / Monitoring |

### Phase 4b — full crawlers

Remaining strategies (`query`, `tree`, `feed`, `search`, then `traverse`), dry-run, politeness via
`protego`, `selectolax` parsing, per-host concurrency, config UI with `react-hook-form` + `zod`.

### Phases 5–6 — team, scale, compliance

Permission sync from source systems; SSO via OIDC behind the existing `TokenVerifier` seam; SCIM;
immutable audit log as a separate append-only store with real retention; delete cascade; k6 soak.

---

## Spikes to run before committing

| Spike | Question it answers | Blocks |
|-------|--------------------|--------|
| **Credential broker on Cloud Run** | Does the self-hosted image tolerate a request-scoped lifecycle? | Phase 4 topology |
| **Pub/Sub ack deadline vs enrichment p99** | Push or pull? | Phase 4 worker shape |
| **Cloud SQL connection ceiling under Cloud Run fan-out** | What pool size survives max instances? | Phase 4 sizing |
| **In-process queue parity** | Does the local impl satisfy the same contract tests? | Phase 3 |
| **Broker sync engine overlap** | Does the credential broker's own sync engine replace part of the crawler? | Phase 4b scope |

The last one could remove work rather than add it — worth running early.
