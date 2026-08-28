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
  --set-env-vars "DB_HOST=${DB_HOST},DB_NAME=${DB_NAME},DB_USER=postgres,EMBED_DIM=768,RAW_BUCKET=${RAW_BUCKET},MEDIA_INTERPRETATION=true,EXTRACT_ENGINE=gemini,MULTIMODAL_MODEL=${MULTIMODAL_MODEL:-gemini-3.7-flash},TRANSCRIBE_MODEL=${TRANSCRIBE_MODEL:-gemini-3.5-transcribe}" \
  --set-secrets "DB_PASSWORD=memdog-db-password:latest,MEMDOG_MASTER_KEY=memdog-master-key:latest,GEMINI_API_KEY=gemini-api-key:latest" \
  --allow-unauthenticated \
  --min-instances 0 --max-instances 4 \
  --cpu 1 --memory 1Gi --timeout 600 \
  --quiet

step "Done"
gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" \
  --format="value(status.url)"
