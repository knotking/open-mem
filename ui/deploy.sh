#!/usr/bin/env bash
# Deploy the sandbox UI to Cloud Run.
#
# The UI holds both credentials server-side and is the only thing the browser
# talks to. It reaches the API as a service identity, so the API stays behind
# Cloud Run IAM rather than being opened up for a browser.
set -euo pipefail

PROJECT="${PROJECT:-memdog-dev-506718}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-memdog-sandbox}"
TAG="${1:-ui-1}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/memdog/memdog-sandbox:${TAG}"
SA="memdog-api@${PROJECT}.iam.gserviceaccount.com"
API_URL="${API_URL:-https://memdog-api-266276359448.us-central1.run.app}"

docker buildx build --platform linux/amd64 -t "$IMAGE" --push .

gcloud run deploy "$SERVICE" \
  --project "$PROJECT" --region "$REGION" \
  --image "$IMAGE" \
  --service-account "$SA" \
  --set-env-vars "MEMDOG_API_URL=${API_URL},MEMDOG_PROJECT_ID=${MEMDOG_PROJECT_ID},MEMDOG_PRODUCER_ID=${MEMDOG_PRODUCER_ID}" \
  --set-secrets "MEMDOG_API_KEY=memdog-demo-key:latest" \
  --min-instances 0 --max-instances 3 --cpu 1 --memory 512Mi \
  --quiet

gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" \
  --format="value(status.url)"
