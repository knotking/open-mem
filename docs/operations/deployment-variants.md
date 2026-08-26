# Deployment Variants

The variants are not scaled versions of each other — they make different technology choices and
therefore support different subsets of the use-case families.

## Role fulfilment by variant

| Role | Local | GKE (dev today) | Cloud (prod v1) |
|------|-------|-----------------|-----------------|
| `record store` | Postgres in compose | Supabase in-cluster | Cloud SQL PG16 + pgvector |
| `data access` | Kong + PostgREST | Kong + PostgREST | Kong + PostgREST on Cloud Run |
| `blob store` | filesystem | GCS | GCS |
| `durable queue` | NATS in compose | NATS in-cluster | **Pub/Sub** |
| `inference` | **Ollama local** — free | Ollama pods, tiered | **Ollama Cloud + Gemini**, no GPU pool |
| `temporal graph` | off | Neo4j optional | **deferred** |
| `credential broker` | Nango in compose | Nango in-cluster | Nango on Cloud Run |
| `identity` | **local password** | GoTrue | **Firebase Auth** |
| `conversational agent` | optional | DigiMe in cluster | **cut** |
| `crawl scheduling` | cron container | cluster CronJob | managed scheduler |
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
