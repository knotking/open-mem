# Deployment Variants

The variants are not scaled versions of each other — they make different technology choices and
therefore support different subsets of the use-case families.

## Role fulfilment by variant

| Role | Local | GKE (dev today) | Cloud (prod v1) |
|------|-------|-----------------|-----------------|
| `record store` | Postgres in compose | Supabase in-cluster | Cloud SQL PG16 + pgvector |
| `data access` | Kong + PostgREST | Kong + PostgREST | Kong + PostgREST on Cloud Run |
| `blob store` | filesystem | GCS | GCS |
| `durable queue` | **in-process** — one instance, nothing to distribute | NATS in-cluster | **Pub/Sub** |
| `inference` | **Ollama local** — free | Ollama pods, tiered | **Ollama Cloud + Gemini**, no GPU pool |
| `temporal graph` | off | Neo4j optional | **deferred** |
| `credential broker` | Nango in compose | Nango in-cluster | Nango on Cloud Run |
| `identity` | **local password** | GoTrue | **Firebase Auth** |
| `conversational agent` | optional | DigiMe in cluster | **cut** |
| `crawl scheduling` | in-process ticker | cluster CronJob + advisory lock | managed scheduler → HTTP |
| `crawl execution` | background task | Deployment | **Cloud Run Jobs** — a 3h backfill is not a request |
| `rate-limit store` | in-process | Redis pod | Memorystore |
| `secrets` | `.env` | k8s Secrets | Secret Manager |
| `scaling` | n/a | KEDA | Cloud Run, `min-instances ≥ 1` |

## The three

**Local** — laptop or Mac Mini. Single user or household. Docker Compose, local models, filesystem
blobs. **$0 recurring and genuinely air-gap capable** — the only variant that satisfies the privacy
pillar in full, and the only one that sidesteps the compliance apparatus entirely.

**GKE** — dev today. Self-hosted cluster across six namespaces, tiered inference pods, KEDA
autoscaling. Richest capability set — the only variant running every component at once.

**Cloud** — prod v1. Serverless: Cloud Run plus managed services, no cluster at all. Reverses the
dev topology. Two components were forcing a cluster; one was cut and the other verified
request-scoped.

## Which variant serves which use case

| Family | Local | GKE | Cloud | Note |
|--------|:-----:|:---:|:-----:|------|
| A · Personal memory | ● | ● | ○ | Cloud loses conversational access with the agent cut |
| B · Team memory | — | ● | ○ | Cloud defers the temporal graph, weakening institutional recall |
| C · Agent infrastructure | ● | ● | ● | MCP works everywhere |
| D · Embedded backend | — | ○ | ● | Cloud is the intended host-SaaS target |
| E · Governance | ○ | ○ | ● | Managed tier makes audit and residency tractable |

## Where security artifacts live

"Secrets go in Secret Manager" is too coarse. Five distinct classes with different requirements,
and conflating them is how a KEK ends up retrievable as a string.

| Artifact | Local | GKE | **Cloud (GCP)** |
|----------|-------|-----|-----------------|
| **Key-encryption key (KEK)** | file, dev-only | k8s Secret | **Cloud KMS** — never leaves |
| **Per-tenant data keys (DEK)** | wrapped, in the record store | same | same — wrapped by KMS, ciphertext in Cloud SQL |
| **Per-tenant secrets** — AI provider keys, webhook signing | envelope-encrypted in the record store | same | same — **not** Secret Manager |
| **OAuth tokens** | credential broker's own store | same | broker's Cloud SQL, AES-256-GCM |
| **Platform secrets** — broker encryption key, third-party platform keys | `.env` | k8s Secret | **Secret Manager** |
| **JWT signing key** | file | k8s Secret | **Cloud KMS asymmetric signing** |
| **`md_*` API keys** | hashed in the record store | same | same — hashed, never encrypted |
| **Policy and settings** | record store | same | Cloud SQL — not secrets |

### Secret Manager and KMS are not interchangeable

**Secret Manager stores and returns a value.** Correct for something the application must hold —
the credential broker's encryption key, a platform-level third-party key.

**KMS performs cryptographic operations without releasing the key.** Correct for the KEK, because
the whole point of envelope encryption is that the key-encryption key never enters application
memory. Putting a KEK in Secret Manager gives you one string away from total compromise, which is
the situation envelope encryption exists to avoid.

Same reasoning for JWT signing: **KMS asymmetric signing** means the private key never exists in a
process, so a memory disclosure cannot forge tokens.

### Per-tenant secrets do not belong in Secret Manager

Secret Manager is built for a bounded set of platform secrets, not one entry per tenant per
provider. Wrong quota model, wrong access model, and no way to scope reads per tenant.

Per-tenant secrets are **envelope-encrypted in the record store**: plaintext → tenant DEK → wrapped
by the KMS KEK → ciphertext in Cloud SQL. Rotation is a KMS key version bump plus a DEK re-wrap,
not a re-encrypt of the corpus.

### Two credentials that should not exist at all

| Removed by | What it removes |
|-----------|-----------------|
| **Workload Identity** | Service account **key files**. Cloud Run services assume an identity; there is no key to leak, rotate or commit |
| **Cloud SQL IAM database authentication** | The database **password**. The service account authenticates directly |

Both matter given the launch blocker already on record — secrets committed to git history. The best
defence is a credential that does not exist.

Signed URLs still require the service account to hold `roles/iam.serviceAccountTokenCreator` **on
itself**, which is easy to miss and fails only at runtime.

### Where the audit log physically lives

[Privacy foundations](../security/privacy-foundations.md) requires an append-only audit record on
every read, with retention in years. That is not Cloud Logging — wrong retention model, awkward for
"who accessed this record in March", and it is operational logging rather than a compliance
artifact.

**Write to Cloud SQL, export to BigQuery.**

- The write is **transactional with the read it records**, so no code path can skip it
- Immutability is enforced by the database: the application role holds `INSERT` and `SELECT` on the
  audit table and **no `UPDATE` or `DELETE` grant**. Immutable by permission, not by discipline
- Cloud SQL keeps a hot window; BigQuery holds the long tail cheaply and answers the year-scale
  question
- Separate from `tracing` memories entirely — those are sampled, three-day, and observability

## Two capability gaps worth naming

**Cloud cuts the conversational agent**, so "reach your memory from any messaging app" — a
headline capability and the only genuinely unique one — exists only on self-hosted variants.

**Cloud defers the temporal graph**, one of the two differentiated capabilities.

Prod v1 therefore ships without the two features that most distinguish the product. That may be
correct for a first release, but it should be a stated trade rather than an emergent one.

## Launch blockers on record

- **Committed secrets** in `k8s/nango/nango-secrets.yaml` and `k8s/lean/*` — present in git
  history, so **rotation** is required, not deletion
- **`minReplicaCount: 0`** across the autoscaling manifests, which fights any availability target
- **Gmail watch state persisted only to `/data`**, which does not survive beyond one volume — the
  dead `_WATCH_BLOB_KEY` constant shows blob storage was the original intent

## Verify before provisioning

Deploy the credential broker image to Cloud Run **in dev first**, to confirm the self-hosted build
tolerates a request-scoped lifecycle. It is the one component whose internals are outside our
control, and the cloud topology assumes it holds no background workers.
