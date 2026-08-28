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

Everything except the health endpoint requires an API key. Health discloses the
embedding model, its dimension and the queue depth — no tenant data.

**Health is `GET /api/v1/health`, not `/healthz`.** Google Front End intercepts
exactly `/healthz` and answers 404 before the request reaches the container —
verified against Google's own `cloudrun/hello` image, where every path returns
200 except that one. A service can be perfectly healthy and appear to be
returning 404s for its own liveness probe.

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


## Issuing the first credential, without putting it in the log

`python -m memdog bootstrap` prints the API key. That is right for a human at a
terminal and **wrong for a Cloud Run Job**, whose stdout is Cloud Logging — a
durable, widely-readable store. Same command, very different blast radius.

So the job runs `bootstrap-to-secret`, which writes the credential straight into
Secret Manager and prints only the ids:

```bash
gcloud run jobs execute memdog-bootstrap --region us-central1
gcloud secrets versions access latest --secret memdog-demo-key
```

`python -m memdog revoke-key <prefix>` revokes one, which is how the first key —
issued before this existed, and therefore logged — was retired.

**Do not put a credential in a job's `--args` either.** That is config, it is
readable by anyone who can describe the job, and it outlives the run.

## Reaching the service

Public access is refused by an inherited org policy, so the service requires
Cloud Run IAM. That means the platform owns the `Authorization` header, and a
request cannot carry two credentials — hence `X-API-Key`, which reaches the same
verifier as the bearer path.

```bash
URL=https://memdog-api-266276359448.us-central1.run.app
TOKEN=$(gcloud auth print-identity-token \
  --impersonate-service-account=memdog-api@memdog-dev-506718.iam.gserviceaccount.com \
  --audiences="$URL" --include-email)
curl -H "Authorization: Bearer $TOKEN" -H "X-API-Key: $KEY" "$URL/api/v1/health"
```

A user account cannot mint a custom-audience identity token, which is why this
impersonates the service account.


## The reconciler, and why the queue is not the record of work

`POST /write` commits and queues. If the instance dies in between, the row is
durable and the job is not — an in-process queue loses it outright, and every
broker eventually stops redelivering. The item then sits one rung below where it
belongs, forever, looking exactly like an item that is merely behind.

So the **rows are the record of outstanding work**, and a scheduled sweep
republishes what is missing:

```bash
gcloud run jobs execute memdog-reconcile --region us-central1   # one sweep
```

`memdog-reconcile-tick` (Cloud Scheduler, every 10 minutes) drives it. Two
grants are needed and neither is obvious:

- the invoking service account needs `roles/run.developer`
- **the Cloud Scheduler service agent** (`service-<number>@gcp-sa-cloudscheduler`)
  needs `roles/iam.serviceAccountTokenCreator` on that service account, because
  Scheduler impersonates it to mint the OAuth token. Without this the job simply
  never fires, with no error surfaced on the scheduler job.

A grace period (default 300s) keeps the sweep from racing a worker that is
already handling an item, and both workers rebuild rather than append, so a
spurious re-enqueue costs a little compute and changes nothing else.

**A `Pending` item is never swept.** It is not behind — it is waiting for a
fetch worker that does not exist yet, and re-enqueueing it every ten minutes
would be a busy loop against a worker that will never come.
