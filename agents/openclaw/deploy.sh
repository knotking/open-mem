#!/usr/bin/env bash
# Deploy OpenClaw to Cloud Run, pointed at open-mem.
set -euo pipefail

PROJECT="${PROJECT:-memdog-dev-506718}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-open-mem-openclaw}"
TAG="${1:-openclaw-1}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/open-mem/open-mem-openclaw:${TAG}"
SA="open-mem-api@${PROJECT}.iam.gserviceaccount.com"
API_URL="${API_URL:-https://open-mem-api-r5ifa3vgqq-uc.a.run.app}"

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

step "Building ${IMAGE}"
# Build and push as two steps. `buildx --push` with the default `docker` driver
# uploads every layer and *then* fails with "No such image", which reads as a
# broken build after the push has already succeeded.
if [ "${SKIP_BUILD:-0}" != "1" ]; then
  docker build --platform linux/amd64 -t "$IMAGE" .
  docker push "$IMAGE"
fi

step "Deploying ${SERVICE}"
gcloud run deploy "$SERVICE" \
  --project "$PROJECT" --region "$REGION" \
  --image "$IMAGE" \
  --service-account "$SA" \
  --set-env-vars "OPENMEM_API_URL=${API_URL},OPENCLAW_CONFIG_DIR=/home/node/.openclaw,OPENCLAW_MODEL=${OPENCLAW_MODEL:-google/gemini-3.5-flash},IMAGE_TAG=${TAG}" \
  --set-secrets "OPENMEM_API_KEY=open-mem-demo-key:latest,GEMINI_API_KEY=gemini-api-key:latest,OPENCLAW_GATEWAY_TOKEN=openclaw-gateway-token:latest" \
  --allow-unauthenticated \
  --min-instances 0 --max-instances 2 \
  --cpu 2 --memory 2Gi --timeout 900 \
  --quiet

# Cloud Run's invoker IAM check rejects any Authorization header it cannot
# validate as a Google identity token, before the container sees it -- and
# OpenClaw's gateway token is exactly such a header. Granting allUsers permits
# anonymous requests but does NOT stop that validation, so this is required, not
# cosmetic. Learned on open-mem-hermes, where the symptom was an unauthenticated
# /health returning 200 while every authenticated call got a GFE 401.
step "Disabling the invoker IAM check"
gcloud run services update "$SERVICE" \
  --project "$PROJECT" --region "$REGION" --no-invoker-iam-check --quiet

step "Done"
gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" \
  --format="value(status.url)"
