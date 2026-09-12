#!/usr/bin/env bash
# Deploy the spine to Cloud Run against a private-IP Cloud SQL instance.
#
# Idempotent: every step is either create-if-absent or an update. Safe to
# re-run, which is the only kind of deploy script anyone should trust.
#
#   ./deploy/cloudrun.sh [TAG]
set -euo pipefail

PROJECT="${PROJECT:-memdog-dev-506718}"
REGION="${REGION:-us-central1}"
INSTANCE="${INSTANCE:-memdog-spine}"
SERVICE="${SERVICE:-memdog-api}"
DB_NAME="${DB_NAME:-memdog}"
RAW_BUCKET="${RAW_BUCKET:-memdog-spine-raw-dev}"
TAG="${1:-spine-1}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/memdog/memdog-api:${TAG}"
SA="memdog-api@${PROJECT}.iam.gserviceaccount.com"

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

step "Building ${IMAGE}"
# --platform is not optional: Cloud Run is amd64 and dev machines are not.
docker buildx build --platform linux/amd64 -t "$IMAGE" --push .

step "Resolving the instance's private IP"
DB_HOST=$(gcloud sql instances describe "$INSTANCE" --project "$PROJECT" \
  --format="value(ipAddresses[0].ipAddress)")
echo "    ${INSTANCE} -> ${DB_HOST}"

# The public demo is off unless PUBLIC_PROJECT_ID is passed. An unauthenticated
# endpoint that makes a model call per request is an open tap on the bill, so it
# is switched on deliberately at deploy time and never inherited from a default.
# Passing an empty value is how you turn it off again.
step "Deploying ${SERVICE}"
# Direct VPC egress rather than a Serverless VPC Access connector: the
# instance has no public IP (org policy forbids one), so the service reaches
# it over the VPC and speaks ordinary Postgres to a private address. No Cloud
# SQL socket, no proxy sidecar.
#
# `--no-cpu-throttling` is load-bearing, not a performance preference. The queue
# is in-process: `publish()` hands work to asyncio tasks in this same container,
# and the endpoint that triggers enrichment returns as soon as the work is
# *queued*. Under Cloud Run's default, CPU is throttled to near-zero the moment
# a response is sent, so anything outliving its request is starved rather than
# run.
#
# That is invisible until a job is large enough to matter. A summary or a
# transcription is one model call and finishes inside the request; a
# two-million-character document is ~1,900 chunks and ~19 sequential embedding
# calls, and never finished. Nothing errored and nothing was logged, because the
# task was not failing -- it was frozen. Found 2026-09-03, on a .docx that had
# parsed and summarised perfectly and would not become searchable.
# `REPO_ANALYSIS_JOB` belongs in the list below rather than being applied
# afterwards. `--set-env-vars` replaces the whole set, so a value added by hand
# with `--update-env-vars` survives exactly until the next deploy and then
# vanishes -- and repo analysis does not break loudly when it does. Snapshots go
# on being accepted and every one fails with "no repo analysis job is
# configured", which reads as a misconfiguration nobody made rather than as a
# deploy that dropped a variable. It happened once, immediately.

# `PUBLIC_DEMOS` is the same failure one variable over, and it could not be
# fixed the same way. It is the gallery registry -- JSON, and therefore full of
# commas, which is precisely the character `--set-env-vars` splits on -- so it
# was never added to that list. It lived only on the running service, and since
# `--set-env-vars` replaces the whole set, every deploy silently dropped it: the
# public gallery went dark and nothing said why. Found 2026-09-08, by diffing
# the live environment against this line before deploying.
#
# gcloud's `^delim^` escape would work until a blurb contains the delimiter -- an
# email address, a percentage -- which trades a certain bug for a latent one. A
# file has no delimiter to collide with, and JSON is valid YAML, so the whole
# environment is written as JSON and there is nothing to quote.
#
# **It defaults to what the service already has, not to empty.** It is data a
# human produced with `seed-demos`, not a toggle with a sensible constant, so
# the only correct default is what is already true. Pass `PUBLIC_DEMOS=''` to
# clear it deliberately.
if [ -z "${PUBLIC_DEMOS+set}" ]; then
  PUBLIC_DEMOS=$(gcloud run services describe "$SERVICE" --project "$PROJECT" \
    --region "$REGION" --format=json 2>/dev/null | python3 -c '
import json, sys
try:
    container = json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0]
except Exception:
    sys.exit(0)
for entry in container.get("env", []):
    if entry.get("name") == "PUBLIC_DEMOS":
        sys.stdout.write(entry.get("value", ""))
        break
' || true)
  [ -n "$PUBLIC_DEMOS" ] && echo "    carrying PUBLIC_DEMOS forward (${#PUBLIC_DEMOS} bytes)"
fi

ENV_FILE="$(mktemp -t memdog-env)"
trap 'rm -f "$ENV_FILE"' EXIT
DB_HOST="$DB_HOST" DB_NAME="$DB_NAME" RAW_BUCKET="$RAW_BUCKET" \
TAG="$TAG" PROJECT="$PROJECT" REGION="$REGION" PUBLIC_DEMOS="$PUBLIC_DEMOS" \
python3 - "$ENV_FILE" <<'ENVPY'
import json, os, sys

get = os.environ.get
project, region = get("PROJECT"), get("REGION")
# `or` rather than a plain get, to match bash's `${X:-default}` -- which falls
# back when the variable is *empty* as well as when it is unset.
env = {
    "DB_HOST": get("DB_HOST"), "DB_NAME": get("DB_NAME"), "DB_USER": "postgres",
    "EMBED_DIM": "768",
    "EMBED_ENGINE": get("EMBED_ENGINE") or "gemini",
    "EMBED_MODEL": get("EMBED_MODEL") or "gemini-embedding-001",
    "RAW_BUCKET": get("RAW_BUCKET"),
    "MEDIA_INTERPRETATION": "true",
    "LARGE_MEDIA": get("LARGE_MEDIA") or "false",
    "MAX_LARGE_MEDIA_BYTES": get("MAX_LARGE_MEDIA_BYTES") or "268435456",
    "MAX_TEXT_CHARS": get("MAX_TEXT_CHARS") or "4000000",
    "EXTRACT_ENGINE": "gemini",
    "MULTIMODAL_MODEL": get("MULTIMODAL_MODEL") or "gemini-3.7-flash",
    "TRANSCRIBE_MODEL": get("TRANSCRIBE_MODEL") or "gemini-3.5-transcribe",
    "FIREBASE_PROJECT_ID": project,
    "OTEL_GCP_PROJECT": project,
    "IMAGE_TAG": get("TAG"),
    "PUBLIC_PROJECT_ID": get("PUBLIC_PROJECT_ID") or "",
    "PUBLIC_MEMORY_ID": get("PUBLIC_MEMORY_ID") or "",
    "PUBLIC_TITLE": get("PUBLIC_TITLE") or "",
    "PUBLIC_SUBTITLE": get("PUBLIC_SUBTITLE") or "",
    "PUBLIC_DEMOS": get("PUBLIC_DEMOS") or "",
    "PUBLIC_DAILY_CAP": get("PUBLIC_DAILY_CAP") or "500",
    "PUBLIC_RATE_PER_HOUR": get("PUBLIC_RATE_PER_HOUR") or "20",
    "REPO_ANALYSIS_JOB": get("REPO_ANALYSIS_JOB")
        or f"projects/{project}/locations/{region}/jobs/memdog-repo-analysis",
    "URL_CONTEXT": get("URL_CONTEXT") or "true",
    "URL_CONTEXT_MODEL": get("URL_CONTEXT_MODEL") or "",
}
missing = [k for k, v in env.items() if v is None]
if missing:
    # Refuse rather than deploy a service missing DB_HOST. An env file written
    # with a null in it is accepted by gcloud and fails at startup, which
    # presents as a broken revision rather than a broken deploy script.
    sys.exit(f"cloudrun.sh: no value resolved for {', '.join(missing)}")
json.dump(env, open(sys.argv[1], "w"), indent=2, sort_keys=True)
ENVPY
# The Drive reader, if this deployment has one. A service-account *private key*
# belongs in Secret Manager and not in the env file, which is readable in the
# Cloud Run console by anyone who can see the service.
#
# **Optional on purpose, and conditional because `--set-secrets` fails on a
# secret that does not exist.** A deployment without one is not broken: the
# console asks `GET /drive/share-address`, is told `configured: false`, and says
# the feature is not switched on rather than showing a blank address. So the
# absence has to survive a deploy rather than stop one.
SECRETS="DB_PASSWORD=memdog-db-password:latest,MEMDOG_MASTER_KEY=memdog-master-key:latest,GEMINI_API_KEY=gemini-api-key:latest"
if gcloud secrets describe memdog-drive-key --project "$PROJECT" >/dev/null 2>&1; then
  SECRETS="${SECRETS},DRIVE_SERVICE_ACCOUNT=memdog-drive-key:latest"
  echo "    Drive reader: memdog-drive-key"
else
  echo "    no memdog-drive-key -- Drive folders will report themselves unconfigured"
fi

gcloud run deploy "$SERVICE" \
  --project "$PROJECT" --region "$REGION" \
  --image "$IMAGE" \
  --service-account "$SA" \
  --network default --subnet default --vpc-egress private-ranges-only \
  --env-vars-file "$ENV_FILE" \
  --set-secrets "$SECRETS" \
  --allow-unauthenticated \
  --min-instances 0 --max-instances 4 \
  --cpu 1 --memory 1Gi --timeout 600 \
  --no-cpu-throttling \
  --quiet

# The reconciler runs the same image with the same configuration, and must be
# deployed with the service rather than separately. It had drifted twenty tags
# behind and was missing EMBED_ENGINE entirely, so it re-embedded with the old
# model and concluded nothing was stale -- a repair job that quietly repaired
# the corpus back to the state it was supposed to be moving away from.
#
# Anything that reads `current_generators` has to agree with the service about
# what "current" means, or its idea of stale is the inverse of the truth.
step "Deploying the reconcile job"
JOB_ENV="DB_HOST=${DB_HOST},DB_NAME=${DB_NAME},DB_USER=postgres,EMBED_DIM=768,EMBED_ENGINE=${EMBED_ENGINE:-gemini},EMBED_MODEL=${EMBED_MODEL:-gemini-embedding-001},RAW_BUCKET=${RAW_BUCKET},MEDIA_INTERPRETATION=true,LARGE_MEDIA=${LARGE_MEDIA:-false},MAX_LARGE_MEDIA_BYTES=${MAX_LARGE_MEDIA_BYTES:-268435456},MAX_TEXT_CHARS=${MAX_TEXT_CHARS:-4000000},EXTRACT_ENGINE=gemini,MULTIMODAL_MODEL=${MULTIMODAL_MODEL:-gemini-3.7-flash},TRANSCRIBE_MODEL=${TRANSCRIBE_MODEL:-gemini-3.5-transcribe},OTEL_GCP_PROJECT=${PROJECT},IMAGE_TAG=${TAG}"
JOB_SECRETS="DB_PASSWORD=memdog-db-password:latest,MEMDOG_MASTER_KEY=memdog-master-key:latest,GEMINI_API_KEY=gemini-api-key:latest"

# `memdog-seed` is here for the same reason the reconciler is: it was created by
# hand against whatever image was current that day, and a job pinned to an image
# nobody redeploys drifts until it runs code the service no longer has. The seed
# drives the API in-process, so a stale one seeds a corpus the running service
# would not have produced.
#
# Creating it costs nothing -- a job is not billed until executed -- and it is
# the only way to seed at all, since Cloud SQL is private-IP and the seed needs
# to be inside the VPC.
# `memdog-bootstrap` is in here for the reason the others are, and it had drifted
# furthest: created by hand, pinned to an image twenty tags old, and repurposed
# along the way to run `grant-key` instead of a bootstrap. A job nobody
# redeploys runs code the service no longer has, and this one issues the first
# credential -- the worst possible thing to run from a stale build.
#
# `shared`, not `personal`, and that argument is load-bearing. A personal
# connection binds its producer to the user who bootstrapped it, and the write
# path refuses anybody else:
#
#     this producer is bound to another user's personal connection
#
# The console sends the *signed-in user's* identity, not a service credential,
# so with `personal` every account except the bootstrap owner is refused at
# Add data. That is correct behaviour for a personal deployment and wrong for a
# console several people sign into. Found 2026-09-04, immediately after a
# from-scratch rebuild: the previous tenant's connection was shared, this
# argument recreated it as personal, and the screen broke for everyone but the
# owner.
#
# Restoring it to `bootstrap-to-secret` costs nothing: `refuse_if_occupied`
# turns it into a no-op on a deployment that already has a tenant, and a fresh
# project needs exactly this. The secret *name* in the args is config; the
# credential itself goes to Secret Manager and never to stdout, which on a Cloud
# Run Job is Cloud Logging.
for job_spec in "memdog-reconcile:reconcile" "memdog-crawl-tick:crawl-tick" \
                "memdog-alert-tick:alert-tick" \
                "memdog-bootstrap:bootstrap-to-secret,owner@memdog.dev,shared,${PROJECT},memdog-demo-key" \
                "memdog-seed:seed,--demo"; do
  job="${job_spec%%:*}"
  command="${job_spec##*:}"
  # The seed is not like the other two. It enriches forty-odd records
  # synchronously in one shot, and it must not be retried: a second attempt
  # finds the org the first one created and fails with that as its reason,
  # which reads as a broken seed rather than a duplicate run.
  case "$job" in
    memdog-seed) cpu=2; memory=2Gi; retries=0 ;;
    # A retried bootstrap finds the tenant the first attempt created and fails
    # with that as its reason, which reads as a broken bootstrap.
    memdog-bootstrap) cpu=1; memory=1Gi; retries=0 ;;
    *)           cpu=1; memory=1Gi; retries=1 ;;
  esac
  if gcloud run jobs describe "$job" --project "$PROJECT" --region "$REGION" >/dev/null 2>&1; then
    verb=update
  else
    verb=create
  fi
  gcloud run jobs "$verb" "$job" \
    --project "$PROJECT" --region "$REGION" \
    --image "$IMAGE" \
    --service-account "$SA" \
    --network default --subnet default --vpc-egress private-ranges-only \
    --set-env-vars "$JOB_ENV" \
    --set-secrets "$JOB_SECRETS" \
    --command python --args="-m,memdog,$command" \
    --max-retries "$retries" --task-timeout 1800 \
    --cpu "$cpu" --memory "$memory" \
    --quiet
done

step "Done"
gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" \
  --format="value(status.url)"
