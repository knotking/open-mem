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

step "Deploying ${SERVICE}"
# Direct VPC egress rather than a Serverless VPC Access connector: the
# instance has no public IP (org policy forbids one), so the service reaches
# it over the VPC and speaks ordinary Postgres to a private address. No Cloud
# SQL socket, no proxy sidecar.
gcloud run deploy "$SERVICE" \
  --project "$PROJECT" --region "$REGION" \
  --image "$IMAGE" \
  --service-account "$SA" \
  --network default --subnet default --vpc-egress private-ranges-only \
  --set-env-vars "DB_HOST=${DB_HOST},DB_NAME=${DB_NAME},DB_USER=postgres,EMBED_DIM=768,EMBED_ENGINE=${EMBED_ENGINE:-gemini},EMBED_MODEL=${EMBED_MODEL:-gemini-embedding-001},RAW_BUCKET=${RAW_BUCKET},MEDIA_INTERPRETATION=true,EXTRACT_ENGINE=gemini,MULTIMODAL_MODEL=${MULTIMODAL_MODEL:-gemini-3.7-flash},TRANSCRIBE_MODEL=${TRANSCRIBE_MODEL:-gemini-3.5-transcribe},FIREBASE_PROJECT_ID=${PROJECT},OTEL_GCP_PROJECT=${PROJECT},IMAGE_TAG=${TAG}" \
  --set-secrets "DB_PASSWORD=memdog-db-password:latest,MEMDOG_MASTER_KEY=memdog-master-key:latest,GEMINI_API_KEY=gemini-api-key:latest" \
  --allow-unauthenticated \
  --min-instances 0 --max-instances 4 \
  --cpu 1 --memory 1Gi --timeout 600 \
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
JOB_ENV="DB_HOST=${DB_HOST},DB_NAME=${DB_NAME},DB_USER=postgres,EMBED_DIM=768,EMBED_ENGINE=${EMBED_ENGINE:-gemini},EMBED_MODEL=${EMBED_MODEL:-gemini-embedding-001},RAW_BUCKET=${RAW_BUCKET},MEDIA_INTERPRETATION=true,EXTRACT_ENGINE=gemini,MULTIMODAL_MODEL=${MULTIMODAL_MODEL:-gemini-3.7-flash},TRANSCRIBE_MODEL=${TRANSCRIBE_MODEL:-gemini-3.5-transcribe},OTEL_GCP_PROJECT=${PROJECT},IMAGE_TAG=${TAG}"
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
for job_spec in "memdog-reconcile:reconcile" "memdog-crawl-tick:crawl-tick" \
                "memdog-alert-tick:alert-tick" \
                "memdog-seed:seed,--demo"; do
  job="${job_spec%%:*}"
  command="${job_spec##*:}"
  # The seed is not like the other two. It enriches forty-odd records
  # synchronously in one shot, and it must not be retried: a second attempt
  # finds the org the first one created and fails with that as its reason,
  # which reads as a broken seed rather than a duplicate run.
  case "$job" in
    memdog-seed) cpu=2; memory=2Gi; retries=0 ;;
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
