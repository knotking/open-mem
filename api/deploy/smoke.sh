#!/usr/bin/env bash
# Exercise the milestone against a deployed instance.
#
#   ./deploy/smoke.sh https://memdog-api-xxxx.run.app <api_key> <producer_id> <project_id>
#
# This is the same sentence the test suite proves locally, run against real
# infrastructure: write -> the item is durable -> enrichment makes it findable
# -> the answer cites it -> a second tenant's credential cannot see it.
set -euo pipefail

URL="${1:?service url}"; KEY="${2:?api key}"; PRODUCER="${3:?producer id}"; PROJECT="${4:?project id}"
# X-API-Key rather than Authorization: when the service sits behind Cloud Run
# IAM, the platform owns the Authorization header and a request cannot carry
# two credentials. Both headers reach the same verifier.
AUTH="X-API-Key: ${KEY}"
JSON="content-type: application/json"

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }

say "health"
curl -sf "$URL/api/v1/health" | tee /dev/stderr | grep -q '"status":"ok"'

say "write"
WRITE=$(curl -s -X POST "$URL/api/v1/write" -H "$AUTH" -H "$JSON" \
  -H "Idempotency-Key: smoke-$(date +%s)" \
  -d "{\"producer_id\":\"$PRODUCER\",\"items\":[
        {\"external_id\":\"smoke-incident\",\"content\":{\"kind\":\"inline\",
         \"text\":\"The checkout service returned 502s for eleven minutes after a bad deploy.\n\nRollback completed at 14:02 UTC and error rates recovered.\"}},
        {\"external_id\":\"smoke-pending\",\"content\":{\"kind\":\"pending\",
         \"provider\":\"google-drive\",\"resource_id\":\"1AbC\"}}]}")
echo "$WRITE"
DATA_ID=$(echo "$WRITE" | python3 -c 'import json,sys;print(json.load(sys.stdin)["results"][0]["data_id"])')

say "read back (durable before enrichment)"
curl -sf "$URL/api/v1/data/$DATA_ID" -H "$AUTH" \
  | python3 -c 'import json,sys;d=json.load(sys.stdin);print("state",d["state"],"downloaded",d["is_downloaded"])'

say "retrieve (polling until searchable)"
for _ in $(seq 1 30); do
  FOUND=$(curl -s -X POST "$URL/api/v1/retrieve" -H "$AUTH" -H "$JSON" \
    -d "{\"query\":\"rollback recovered error rates\",\"filter\":{\"project_id\":\"$PROJECT\"}}")
  COUNT=$(echo "$FOUND" | python3 -c 'import json,sys;print(len(json.load(sys.stdin)["results"]))')
  [ "$COUNT" -gt 0 ] && break
  sleep 2
done
echo "$FOUND" | python3 -m json.tool

[ "$COUNT" -gt 0 ] || { echo "FAIL: nothing retrievable"; exit 1; }
say "PASS"
