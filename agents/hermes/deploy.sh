#!/usr/bin/env bash
# Deploy Hermes Agent (community edition) to Cloud Run, pointed at mem-dog.
set -euo pipefail

PROJECT="${PROJECT:-memdog-dev-506718}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-memdog-hermes}"
TAG="${1:-hermes-1}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/memdog/memdog-hermes:${TAG}"
SA="memdog-api@${PROJECT}.iam.gserviceaccount.com"
API_URL="${API_URL:-https://memdog-api-r5ifa3vgqq-uc.a.run.app}"

: "${MEMDOG_PROJECT_ID:?set MEMDOG_PROJECT_ID (the project the agent works over)}"

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

step "Building ${IMAGE}"
# --platform is not optional: Cloud Run is amd64 and dev machines are not.
#
# Build and push as two steps rather than `buildx --push`. With the default
# `docker` driver selected, buildx uploads every layer and *then* fails with
# "No such image" looking for it in the daemon store -- so the push has already
# succeeded by the time it reports failure, which reads as a broken build and
# invites a pointless 8-minute re-push. Plain build + push has no such gap, and
# this image is a trivial FROM+COPY that needs none of buildx's machinery.
#
# SKIP_BUILD=1 deploys the tag already in the registry.
if [ "${SKIP_BUILD:-0}" != "1" ]; then
  docker build --platform linux/amd64 -t "$IMAGE" .
  docker push "$IMAGE"
else
  echo "    SKIP_BUILD=1 -- using the ${TAG} already in the registry"
fi

step "Deploying ${SERVICE}"
# --allow-unauthenticated is forced by a collision, not by a preference for
# public services. Cloud Run IAM claims the `Authorization` header, and Hermes'
# API server needs that same header for its own bearer key -- one request cannot
# carry two bearer tokens. So IAM-private would make the agent unreachable by
# anything that could also authenticate to it, and API_SERVER_KEY is the whole
# of the auth. It must be strong; the upstream security audit says the same,
# because this endpoint dispatches agent execution.
#
# 4Gi and 2 CPU: the image is 2.7GB with Node, Python and a supervision tree,
# and s6 has to bring up the gateway before Cloud Run's startup probe gives up.
gcloud run deploy "$SERVICE" \
  --project "$PROJECT" --region "$REGION" \
  --image "$IMAGE" \
  --service-account "$SA" \
  --set-env-vars "MEMDOG_API_URL=${API_URL},MEMDOG_PROJECT_ID=${MEMDOG_PROJECT_ID},HERMES_MODEL=${HERMES_MODEL:-hermes-4-405b},HERMES_PROVIDER=${HERMES_PROVIDER:-nous-api},HERMES_TOOLSETS=${HERMES_TOOLSETS:-todo},IMAGE_TAG=${TAG}" \
  --set-secrets "MEMDOG_API_KEY=memdog-demo-key:latest,MODEL_API_KEY=${MODEL_SECRET:-hermes-api-key}:latest,API_SERVER_KEY=hermes-server-key:latest" \
  --allow-unauthenticated \
  --min-instances 0 --max-instances 2 \
  --cpu 2 --memory 4Gi --timeout 900 \
  --quiet

step "Done"
gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" \
  --format="value(status.url)"
