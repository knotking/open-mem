# Standing open-mem up in an empty GCP project

The order below is not a preference. Private services access must exist before
a private-IP Cloud SQL instance can be created, the instance must exist before
`cloudrun.sh` can resolve its address, and the secrets must exist before the
service can start. Everything after step 7 is idempotent and re-runnable.

Values are the live ones from `memdog-dev-506718`. Substitute freely; the
constraints are what matter.

```bash
PROJECT=memdog-dev-506718
REGION=us-central1
```

## 1. Enable the APIs

```bash
gcloud services enable --project $PROJECT \
  run.googleapis.com sqladmin.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com servicenetworking.googleapis.com \
  cloudscheduler.googleapis.com compute.googleapis.com storage.googleapis.com \
  identitytoolkit.googleapis.com iamcredentials.googleapis.com \
  cloudtrace.googleapis.com logging.googleapis.com monitoring.googleapis.com
```

`servicenetworking` and `iamcredentials` are the two that are easy to miss and
whose absence produces an error naming something else entirely.

## 2. Private services access — before the database

Org policy `constraints/sql.restrictPublicIp` is enforced and inherited: a Cloud
SQL instance **with a public IP is rejected at creation**. Private IP requires a
peered range allocated to `servicenetworking`, and it has to exist first.

```bash
gcloud compute addresses create open-mem-sql-range --project $PROJECT \
  --global --purpose=VPC_PEERING --addresses=10.100.0.0 --prefix-length=16 \
  --network=default

gcloud services vpc-peerings connect --project $PROJECT \
  --service=servicenetworking.googleapis.com \
  --ranges=open-mem-sql-range --network=default
```

**The range cannot live inside `10.128.0.0/9`.** The `default` network is
auto-mode and reserves that whole block for its own subnets. Hence `10.100.0.0/16`.

## 3. Cloud SQL

```bash
gcloud sql instances create open-mem-spine --project $PROJECT \
  --database-version=POSTGRES_16 --tier=db-f1-micro --edition=ENTERPRISE \
  --region=$REGION --network=default --no-assign-ip \
  --storage-size=10GB --no-backup

gcloud sql databases create open_mem --instance=open-mem-spine --project $PROJECT
gcloud sql users set-password postgres --instance=open-mem-spine --project $PROJECT \
  --password="$(openssl rand -base64 32)"     # same value goes into step 5
```

**`--edition=ENTERPRISE` is required.** The default is `ENTERPRISE_PLUS`, which
rejects shared-core tiers with a message that never mentions editions.

`--no-backup` is a dev choice. For anything that matters, drop it.

Note the private address — `cloudrun.sh` resolves it on every run, so it does
not need to be recorded anywhere:

```bash
gcloud sql instances describe open-mem-spine --project $PROJECT \
  --format='value(ipAddresses[0].ipAddress)'
```

pgvector needs no manual step: `0001_spine.sql` creates the extension, and
migrations run on API startup.

## 4. Registry, bucket, service account

```bash
gcloud artifacts repositories create open-mem --project $PROJECT \
  --repository-format=docker --location=$REGION
gcloud auth configure-docker ${REGION}-docker.pkg.dev

gcloud storage buckets create gs://open-mem-spine-raw-dev --project $PROJECT --location=$REGION

gcloud iam service-accounts create open-mem-api --project $PROJECT
SA=open-mem-api@${PROJECT}.iam.gserviceaccount.com

# Project level.
for role in roles/secretmanager.secretAccessor roles/run.developer \
            roles/cloudtrace.agent roles/monitoring.metricWriter; do
  gcloud projects add-iam-policy-binding $PROJECT --member="serviceAccount:$SA" --role="$role"
done

# Bucket level, not project level -- object access is scoped to the one bucket.
gcloud storage buckets add-iam-policy-binding gs://open-mem-spine-raw-dev \
  --member="serviceAccount:$SA" --role=roles/storage.objectAdmin --project $PROJECT
```

**No `roles/cloudsql.client`, deliberately.** That role is for the Cloud SQL
auth proxy, and this deployment does not use one — it reaches a private address
over the VPC and authenticates to Postgres with a password. Granting it papers
over nothing and suggests a connection path that does not exist.

`run.developer` is what lets the scheduled tick execute a job; see step 9.

The live project grants `roles/secretmanager.admin` rather than
`secretAccessor`, because `bootstrap-to-secret` *writes* a secret version. If
you never run that job, `secretAccessor` is enough and is the better grant.

## 5. Secrets

```bash
for s in open-mem-db-password open-mem-master-key open-mem-demo-key \
         open-mem-web-api-key gemini-api-key; do
  gcloud secrets create $s --project $PROJECT --replication-policy=automatic
done
```

Then add a version to each:

- `open-mem-db-password` — the password set in step 3.
- `open-mem-master-key` — `openssl rand -base64 32`. Encrypts stored credentials;
  **losing it is unrecoverable, and rotating it orphans everything encrypted
  under the old one.**
- `gemini-api-key` — from AI Studio. Required: `EMBED_ENGINE=gemini` and
  `EXTRACT_ENGINE=gemini` are the deployed defaults, so without it the service
  starts and then fails every enrichment.
- `open-mem-demo-key` — written by the bootstrap job in step 8, not by hand.
- `open-mem-web-api-key` — the Firebase Web API key (step 7).

Pipe values in; never pass a credential on a command line where it lands in
shell history.

## 6. Deploy

```bash
cd api && ./deploy/cloudrun.sh spine-1
```

This builds `linux/amd64`, pushes, deploys `open-mem-api`, and creates the three
managed jobs. Migrations apply on first start, so the schema arrives with the
service.

Two choices inside it worth understanding before changing them:

- **Direct VPC egress, not a connector and not the Cloud SQL socket.** With a
  private-IP instance the service speaks ordinary Postgres to an ordinary
  address over the VPC: no `--add-cloudsql-instances`, no proxy sidecar, no
  Serverless VPC Access connector to size and pay for.
- **`--allow-unauthenticated` is deliberate and narrow.** The API's own
  credential is the only authenticator, because Cloud Run IAM in front would
  mean two bearer tokens on one request. Everything except health requires a
  key; health discloses the embedding model, its dimension and queue depth, and
  no tenant data.

## 7. Firebase Auth

Auth is Firebase, behind the `TokenVerifier` seam in `api/src/open_mem/auth.py`
(`FirebaseVerifier` in `firebase.py`, `CompositeVerifier` resolving either an
API key or a Firebase token to one `Principal`). Supabase is entirely gone.

Add Firebase to the same GCP project, enable the sign-in providers, and put the
Web API key into `open-mem-web-api-key`. The API needs only
`FIREBASE_PROJECT_ID`, which `cloudrun.sh` already sets to `$PROJECT`; it
verifies RS256 against cached Google certificates and needs no service-account
JSON.

## 8. First credential — without logging it

`python -m open_mem bootstrap` prints the API key, which is right for a human at a
terminal and **wrong for a Cloud Run Job, whose stdout is Cloud Logging** — a
durable, widely readable store. Same command, very different blast radius.

So the job runs `bootstrap-to-secret`, which writes the credential straight into
Secret Manager and prints only ids. Create it once (it is not in `cloudrun.sh`):

```bash
gcloud run jobs create open-mem-bootstrap --project $PROJECT --region $REGION \
  --image ${REGION}-docker.pkg.dev/${PROJECT}/open-mem/open-mem-api:spine-1 \
  --service-account $SA \
  --network default --subnet default --vpc-egress private-ranges-only \
  --set-env-vars "DB_HOST=<private-ip>,DB_NAME=open_mem,DB_USER=postgres,EMBED_DIM=768" \
  --set-secrets "DB_PASSWORD=open-mem-db-password:latest,OPENMEM_MASTER_KEY=open-mem-master-key:latest" \
  --command python \
  --args="-m,open_mem,bootstrap-to-secret,owner@example.com,personal,${PROJECT},open-mem-demo-key" \
  --max-retries 0

gcloud run jobs execute open-mem-bootstrap --project $PROJECT --region $REGION
gcloud secrets versions access latest --secret open-mem-demo-key --project $PROJECT
```

**Never put a credential in a job's `--args`.** That is config: readable by
anyone who can describe the job, and it outlives the run.

`python -m open_mem revoke-key <prefix>` retires one — which is how the very first
key, issued before `bootstrap-to-secret` existed and therefore logged, was
retired.

## 9. The reconcile schedule

`POST /write` commits and queues. If the instance dies in between, the row is
durable and the job is not — an in-process queue loses it outright. The item
then sits one rung below where it belongs, forever, looking exactly like an item
that is merely behind. So **the rows are the record of outstanding work**, and a
sweep republishes what is missing.

```bash
gcloud scheduler jobs create http open-mem-reconcile-tick --project $PROJECT \
  --location $REGION --schedule="*/10 * * * *" \
  --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT}/jobs/open-mem-reconcile:run" \
  --http-method=POST --oauth-service-account-email=$SA

gcloud iam service-accounts add-iam-policy-binding $SA --project $PROJECT \
  --member="serviceAccount:service-266276359448@gcp-sa-cloudscheduler.iam.gserviceaccount.com" \
  --role=roles/iam.serviceAccountTokenCreator
```

Both grants are needed and neither is obvious: the invoking account needs
`roles/run.developer`, and **the Cloud Scheduler service agent** needs
`serviceAccountTokenCreator` on it, because Scheduler impersonates it to mint
the token. Without the second, the job simply never fires and the scheduler job
surfaces no error at all. Use the target project's own project number in that
service-agent address.

A 300s grace period keeps the sweep from racing a worker already handling an
item, and both workers rebuild rather than append, so a spurious re-enqueue
costs a little compute and nothing else. A `Pending` item is never swept — it is
not behind, it is waiting for a fetch worker that does not exist yet.

### The alert sweep

A second schedule, the same two grants, and a **one-minute** interval rather
than ten. The reconciler repairs enrichment, where lateness costs nothing a user
sees; an alert ten minutes late is a different product, and for a deadline it
may be worthless.

```bash
gcloud scheduler jobs create http open-mem-alert-sweep --project $PROJECT \
  --location $REGION --schedule="* * * * *" \
  --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT}/jobs/open-mem-alert-tick:run" \
  --http-method=POST --oauth-service-account-email=$SA
```

It is the floor under the in-process consumer, not a spare wheel: Cloud Run
scales to zero, so a coalescing window in flight dies with its instance. The
alert watermark in Postgres is the record of what has actually been evaluated,
and this re-derives the rest.

## 10. Seed and UI

```bash
gcloud run jobs execute open-mem-seed --project $PROJECT --region $REGION
cd ui && ./deploy.sh ui-1
```

`open-mem-seed` runs `seed --demo` with `--max-retries 0`, because it enriches
forty-odd records synchronously in one shot and must not be retried: a second
attempt finds the org the first one created and fails with that as its reason,
which reads as a broken seed rather than a duplicate run. It exists as a job at
all because Cloud SQL is private-IP and the seed has to run inside the VPC.

The UI holds both credentials **server-side** and is the only thing the browser
talks to — `ui/Dockerfile` deliberately takes no `NEXT_PUBLIC_*` build args.
Its four runtime vars are `OPENMEM_API_URL`, `OPENMEM_API_KEY`,
`OPENMEM_PROJECT_ID`, `OPENMEM_PRODUCER_ID`, plus `FIREBASE_WEB_API_KEY`.
`ui/deploy.sh` hardcodes `API_URL` — override it with `API_URL=…` in a new
project, or the UI silently points at the old one.

## Cost, and tearing down

Roughly **$8–10/month**, almost all of it the `db-f1-micro` instance with 10 GB.
Cloud Run scales to zero and costs nothing idle.

```bash
gcloud sql instances delete open-mem-spine --project $PROJECT   # the expensive part
```

## Not production-shaped yet

Two seams are still dev-only, and both are honest about it:

- The blob store writes to the container's filesystem, which on Cloud Run is
  *memory* — bytes written by an instance die with it. `BlobStore` is where GCS
  goes; until then only inline text survives a restart, which is all the spine
  indexes anyway.
- The queue is in-process, so enrichment is per-instance. Correct, but not
  durable across a restart mid-job. Pub/Sub behind the `Queue` seam is the
  answer, and the reconciler is what makes the gap survivable meanwhile.
