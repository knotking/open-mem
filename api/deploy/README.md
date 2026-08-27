# Deploying the spine

Target: **`memdog-dev-506718`**, `us-central1`. Cloud Run in front of a
private-IP Cloud SQL Postgres 16.

```
  Cloud Run (memdog-api)                     Cloud SQL (memdog-spine)
  ┌────────────────────┐   direct VPC egress ┌──────────────────────┐
  │ uvicorn :8080      │────────────────────▶│ POSTGRES_16          │
  │ in-process queue   │   private ranges    │ private IP only      │
  │ embed worker       │                     │ pgvector + tsvector  │
  │ filesystem blobs   │                     └──────────────────────┘
  └────────────────────┘
        ▲ secrets: memdog-db-password, memdog-master-key
```

```bash
./deploy/cloudrun.sh [tag]        # idempotent: build, push, deploy
```

## Four things this project's constraints forced

**1. `constraints/sql.restrictPublicIp` is enforced.** A Cloud SQL instance with
a public IP is rejected at creation. So the instance is private-IP only, which
means private services access (a peered `/16` allocated to
`servicenetworking`) had to exist before the instance could be created.

**2. The allocated range cannot live inside `10.128.0.0/9`.** The `default`
network is auto-mode, which reserves that block for its subnets. The peering
range is `10.100.0.0/16`.

**3. Direct VPC egress, not a connector, and not the Cloud SQL socket.** With a
private-IP instance the service reaches Postgres over the VPC at an ordinary
address — no `--add-cloudsql-instances`, no proxy sidecar, no Serverless VPC
Access connector to size and pay for.

**4. `db-f1-micro` requires `--edition=ENTERPRISE`.** The default is
`ENTERPRISE_PLUS`, which rejects shared-core tiers with a message that does not
mention editions.

## The service is public, and that is deliberate but narrow

`--allow-unauthenticated` is set so the API's *own* credential is the only
authenticator. Putting Cloud Run IAM in front as well would mean two bearer
tokens on one request, and the `Authorization` header can only carry one.

Everything except `GET /healthz` requires an API key. `/healthz` discloses the
embedding model, its dimension and the queue depth — no tenant data.

## Cost

Roughly **$8–10/month** for the `db-f1-micro` instance with 10 GB of storage,
plus a few cents for the image and secrets. Cloud Run scales to zero and costs
nothing while idle. Backups are off (`--no-backup`); this is a dev target.

Tear the expensive part down with:

```bash
gcloud sql instances delete memdog-spine --project memdog-dev-506718
```

## Not production-shaped yet

The filesystem blob store writes to the container's ephemeral disk, so bytes
written by an instance die with it. Cloud Run's filesystem is also *memory* —
this is exactly the variant where streaming to GCS is mandatory rather than
merely correct. The `BlobStore` seam is where GCS goes; until then, only inline
text survives a restart, which is all the spine indexes anyway.

The queue is still in-process, so enrichment is per-instance. With
`--max-instances 4` a write handled by one instance is embedded by that same
instance, which is correct but not durable across a restart mid-job — Pub/Sub
behind the `Queue` seam is the Phase 4 answer.
