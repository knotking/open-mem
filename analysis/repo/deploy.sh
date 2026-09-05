#!/usr/bin/env bash
# Deploy the repo analysis job to Cloud Run.
#
# A Job, not a service. It is started by name with per-execution env overrides
# by `repos.RepoAnalysisWorker`, runs once per snapshot, and exits. There is no
# URL and nothing to route to it.
set -euo pipefail

PROJECT="${PROJECT:-memdog-dev-506718}"
REGION="${REGION:-us-central1}"
JOB="${JOB:-memdog-repo-analysis}"
TAG="${1:-repo-analysis-1}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/memdog/memdog-repo-analysis:${TAG}"
SA="memdog-api@${PROJECT}.iam.gserviceaccount.com"
API_URL="${API_URL:-https://memdog-api-r5ifa3vgqq-uc.a.run.app}"
PRODUCER_ID="${PRODUCER_ID:?set PRODUCER_ID to the producer this job writes through}"

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

step "Building ${IMAGE}"
# Build and push as two steps, for the reason recorded in agents/openclaw:
# `buildx --push` on the default driver uploads every layer and then fails with
# "No such image", which reads as a broken build after a successful push.
if [ "${SKIP_BUILD:-0}" != "1" ]; then
  docker build --platform linux/amd64 -t "$IMAGE" .
  docker push "$IMAGE"
fi

step "Deploying job ${JOB}"
# `--max-retries 0`: a failed analysis is recorded on the snapshot with its
# reason, and retrying a clone that failed because the repository is gone would
# spend the same minutes to reach the same conclusion. Capacity failures are the
# queue's business, not the job runner's.
#
# Memory is sized for the clone: Cloud Run's filesystem is memory, so the repo
# size guard in `repos.py` and this number are the same constraint seen twice.
gcloud run jobs deploy "$JOB" \
  --project "$PROJECT" --region "$REGION" \
  --image "$IMAGE" \
  --service-account "$SA" \
  --max-retries 0 \
  --task-timeout 3600s \
  --memory 4Gi --cpu 2 \
  --set-env-vars "MEMDOG_API_URL=${API_URL},MEMDOG_PRODUCER_ID=${PRODUCER_ID},IMAGE_TAG=${TAG}" \
  --set-secrets "MEMDOG_API_KEY=memdog-repo-analysis-key:latest"

step "Granting the API permission to start it"
# The API's service account starts executions; it does not need to be able to
# change the job. `run.invoker` on the job itself is the whole grant.
gcloud run jobs add-iam-policy-binding "$JOB" \
  --project "$PROJECT" --region "$REGION" \
  --member "serviceAccount:${SA}" \
  --role roles/run.invoker >/dev/null

cat <<EOF

Deployed. Point the API at it:

  REPO_ANALYSIS_JOB=projects/${PROJECT}/locations/${REGION}/jobs/${JOB}

Until that is set, a requested snapshot is recorded and immediately marked
failed with that as its reason -- deliberately, so it never sits pending and
reads as still running.
EOF
