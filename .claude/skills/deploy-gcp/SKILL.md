---
name: deploy-gcp
description: Deploy mem-dog to GCP (Cloud Run + private-IP Cloud SQL in memdog-dev-506718) — routine redeploys, from-scratch provisioning, and post-deploy verification. Invoke when asked to deploy, redeploy, ship, or release the API or UI; when a deploy fails; when standing the project up in a fresh GCP project; and whenever the deployment process itself changes, so this skill stays the record of how it is actually done.
---

# Deploying mem-dog to GCP

This skill is the **record of how mem-dog is actually deployed**, kept accurate
by being rewritten every time the process changes. It has one job beyond running
a deploy: a deploy that is done differently than what is written here is a
deploy that is only half finished — see [Keeping this skill true](#keeping-this-skill-true).

Ground truth lives in `api/deploy/cloudrun.sh` and `ui/deploy.sh`. Those scripts
are idempotent and they are the deploy. This file exists for what a script
cannot carry: what to do when it fails, what has to exist before it can work at
all, and what to check after.

## The target

**`memdog-dev-506718`** (project number `266276359448`), region `us-central1`,
reachable as `pagarwal@buildgeek.ai`.

| Piece | Name | Notes |
|---|---|---|
| API | Cloud Run `memdog-api` | `https://memdog-api-r5ifa3vgqq-uc.a.run.app` |
| UI | Cloud Run `memdog-sandbox` | `https://memdog-sandbox-r5ifa3vgqq-uc.a.run.app` |
| Database | Cloud SQL `memdog-spine` | PG16 + pgvector, **private IP `10.100.0.3` only** |
| Raw bytes | GCS `gs://memdog-spine-raw-dev` | |
| Images | Artifact Registry `memdog` | `us-central1-docker.pkg.dev/memdog-dev-506718/memdog` |
| Identity | `memdog-api@memdog-dev-506718.iam.gserviceaccount.com` | both services and all jobs |
| Jobs | `memdog-reconcile`, `memdog-crawl-tick`, `memdog-alert-tick`, `memdog-seed`, `memdog-bootstrap` | |
| Schedules | `memdog-reconcile-tick` every 10 min → `memdog-reconcile`; `memdog-alert-sweep` every 1 min → `memdog-alert-tick` | |
| Secrets | `memdog-db-password`, `memdog-master-key`, `memdog-demo-key`, `memdog-web-api-key`, `gemini-api-key` | |

**There is no GKE, no Kubernetes and no Supabase.** If a doc or an old memory
says otherwise it is describing `memdog-dev` (project `204556389124`), which is
the previous generation and whose owning account has a dead refresh token.

## Routine deploy

```bash
cd api && ./deploy/cloudrun.sh <tag>     # e.g. spine-13
cd ui  && ./deploy.sh <tag>              # e.g. ui-4, only if the UI changed
```

**Tags are descriptive, not monotonic.** The registry holds both — `spine-1`
through `spine-51` from the early days, then `spine-deletion-window`,
`spine-item-metadata`, `spine-open-models`, `spine-workday-crm`. Recent practice
is a short name for what the deploy contains, and it is the better convention:
`spine-52` tells a person reading `IMAGE_TAG` on a running service nothing about
what is on it.

Check what is deployed before picking a tag — the running service reports its
own as `IMAGE_TAG`, and the image tag is in the revision:

```bash
gcloud run services describe memdog-api --project memdog-dev-506718 \
  --region us-central1 --format='value(spec.template.spec.containers[0].image)'
```

Deploying the API also redeploys `memdog-reconcile`, `memdog-crawl-tick`,
`memdog-alert-tick` and `memdog-seed` onto the same image and the same
environment. That coupling is
deliberate and load-bearing: **the reconciler once drifted twenty tags behind
and lost `EMBED_ENGINE`, so it re-embedded with the old model, concluded nothing
was stale, and quietly repaired the corpus back toward the state it was supposed
to be leaving.** Anything that reads `current_generators` has to agree with the
service about what "current" means. Never deploy a job by hand.

- **A deploy succeeds and one endpoint 500s with `column "…" does not exist`.**
  Found 2026-08-30. **A migration is immutable once applied.** `schema_migrations`
  records the version and the runner skips anything already there, so *editing*
  an applied migration reaches only databases that have never seen it. The trap
  is that it is invisible locally: the suite drops the schema and re-migrates
  every run, so it always reads the edited file and passes, while production
  applied the original months or minutes ago and never looked again. **Never
  edit a migration that has been deployed — add the next number**, with
  `IF NOT EXISTS` so a database created from the edited version converges rather
  than failing.

**Database migrations need no step.** `app.py` runs `migrate()` on startup, so
a new `api/src/memdog/migrations/*.sql` applies itself the first time the new
revision serves. It follows that a migration that fails takes the revision down
with it — a deploy that ends in a revision that will not become ready is
usually a migration, not the app.

## Verify

```bash
URL=https://memdog-api-r5ifa3vgqq-uc.a.run.app
curl -sf $URL/api/v1/health        # {"status":"ok", ...}
cd api && ./deploy/smoke.sh $URL <api_key> <producer_id> <project_id>
```

`smoke.sh` proves the whole sentence against real infrastructure: write → the
item is durable → enrichment makes it findable → the answer cites it → a second
tenant's credential cannot see it. Run it after any deploy that touched write,
enrichment or retrieval.

The demo credential is in Secret Manager, never in a log:

```bash
gcloud secrets versions access latest --secret memdog-demo-key --project memdog-dev-506718
```

## When it fails

Each of these presents as a different bug than it is.

- **`/healthz` returns 404 on a perfectly healthy service.** Google Front End
  intercepts exactly that path and answers before the container sees it —
  verified against Google's own `cloudrun/hello` image, where every path
  returns 200 except that one. Health is **`/api/v1/health`**.
- **404 where you expected 403.** Cloud Run returns 404 when IAM rejects a
  request in some configurations, so an auth failure reads as a routing failure.
- **Two credentials on one request.** When the service sits behind Cloud Run IAM
  the platform owns `Authorization`, so the app's own key travels as
  `X-API-Key`. Both reach the same verifier.
- **`gcloud auth print-identity-token --audiences=...` refuses.** A user account
  cannot mint a custom-audience token; it needs
  `--impersonate-service-account=memdog-api@memdog-dev-506718.iam.gserviceaccount.com`
  plus `roles/iam.serviceAccountTokenCreator`.
- **`Reauthentication failed` from any gcloud command.** Ask Parag to run
  `gcloud auth login` and wait. Do not attempt it — it needs a browser.
- **The image builds but Cloud Run will not start it.** `--platform linux/amd64`
  is not optional; dev machines are arm64 and the failure is silent until deploy.
- **The scheduled reconcile never fires, with no error on the scheduler job.**
  The Cloud Scheduler service agent needs `roles/iam.serviceAccountTokenCreator`
  on `memdog-api@…`, because Scheduler impersonates it to mint the OAuth token.
- **`smoke.sh` ends in `FAIL: nothing retrievable`, and nothing is wrong.**
  Fixed 2026-08-30 by sending `options.enrich`; kept here because the symptom
  will recur wherever the flag is missing. The items are written and durable,
  the response says `"reason": "not_yet_enriched"`, and there is **no error in
  the logs at all** — that silence is the tell. Enrichment is optional by
  design, so a write that does not ask for it lands in `stored` and stays
  there; a script that then asserts retrievability is asserting a step it never
  requested. Enrich one item by hand to confirm a deployment is healthy:

  ```bash
  curl -X POST -H "X-API-Key: $KEY" -H 'content-type: application/json' \
       -d '{"embed":true,"summarize":true}' "$URL/api/v1/data/<data_id>/enrich"
  ```

  It returns `{"status":"requested"}` and reaches `enriched` in about a minute.
  A `Pending` item staying `stored` is **correct** — it waits on a fetch worker
  that does not exist, and it appears in `excluded` on a passing run.

- **A rotated secret does not reach the running service, or reaches it but not
  the jobs.** `--set-secrets NAME=secret:latest` resolves **at deploy time, not
  at run time**, so adding a secret version changes nothing until a new revision
  is created. Worse, the four deployments rotate independently: roll the service
  alone and `memdog-reconcile` keeps the old value, so the reconciler silently
  degrades while the service looks fine — the same drift the job-coupling rule
  above exists to prevent. Rotate all four together:

  ```bash
  printf '%s' "$NEW" | gcloud secrets versions add <secret> --project memdog-dev-506718 --data-file=-
  gcloud run services update memdog-api --region us-central1 \
    --update-secrets GEMINI_API_KEY=gemini-api-key:latest --quiet
  for J in memdog-reconcile memdog-crawl-tick memdog-seed; do
    gcloud run jobs update $J --region us-central1 \
      --update-secrets GEMINI_API_KEY=gemini-api-key:latest --quiet
  done
  ```

  Verify by enriching one item and reading `fallback_depth` — `0` means the
  model was actually reached. Never echo the secret; pipe it.

- **Enrichment succeeds but produces nothing useful: no entities, no edges, no
  graph.** Look at the artifact's `fields.fallback_depth` and
  `fallback_reason`:

  ```bash
  curl -H "X-API-Key: $KEY" "$URL/api/v1/data/<data_id>/artifacts"
  # depth=1  reason=["gemini: 429 Too Many Requests"]  → quota
  # depth=1  reason=["gemini: circuit open"]           → breaker tripped after repeated 429s
  ```

  `gemini-api-key` is against an exhausted quota, so extraction falls back to
  the local heuristic. `docs/graph.md` already names the consequence: *"with the
  extractor degraded to the local heuristic there are no entities and therefore
  no edges."* **The whole graph feature is dark while this holds**, and it looks
  like a broken graph rather than a billing problem. Lexical retrieval still
  works, which is why a smoke test passes through it.

  Two reasons distinguish transient from standing: a single `429` may be a
  burst, but `circuit open` means the client has stopped trying and will keep
  falling back until the breaker resets.

  **Ask the API which quota it means** — the answer names the tier outright, and
  is the difference between "wait" and "pay":

  ```bash
  curl -s -X POST "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.7-flash:generateContent" \
    -H "x-goog-api-key: $KEY" -H 'content-type: application/json' \
    -d '{"contents":[{"parts":[{"text":"hi"}]}]}'
  # quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier, limit: 20
  ```

  Twenty requests **per day** does not survive one seed run. Note the tier
  follows the **key's own project**, not this one: `generativelanguage` is not
  even enabled on `memdog-dev-506718`, so billing being enabled here proves
  nothing. Find the key's project (`gen-lang-client-*` when AI Studio made it)
  and check billing there.

## Known drift

None outstanding. `memdog-bootstrap` was the last of it — created by hand,
pinned twenty tags behind, and repurposed to run `grant-key` instead of a
bootstrap. It is in the job loop as of 30 Aug 2026, so it is redeployed with
everything else and cannot drift again. `refuse_if_occupied` makes it a no-op
on a deployment that already has a tenant.

## From scratch

Standing this up in an empty project is a different job with an order that
cannot be reshuffled — the peering range must exist before the database can be
created, and the database before the service can start. See
[`provision.md`](provision.md).

## Keeping this skill true

**Every change to how mem-dog is deployed lands here in the same session it is
discovered.** That is the point of the skill; a runbook that lags reality is
worse than none, because it is trusted.

Update this file when any of these happen — not later, not "once it settles":

- a step is added, removed or reordered in `cloudrun.sh` / `deploy.sh`
- a new env var, secret, service, job, bucket, IAM role or enabled API becomes
  required (name it, and say what breaks without it)
- a deploy fails for a reason not already listed under [When it fails](#when-it-fails)
  — add the symptom **and** the real cause, because the gap between them is the
  whole value
- a resource is renamed, a URL changes, or the target project moves
- something written here turns out to be wrong or obsolete — delete it rather
  than adding a caveat beside it

Write the symptom first and the cause second. Someone reaching for this file is
holding an error message, not a diagnosis.

Then:

1. Commit the skill change with the deploy work it came from.
2. Run the `changelog` skill — a new required env var or a new provisioning step
   is exactly the line someone deploying needs.
3. If the change is a decision rather than a mechanic (a new target project,
   dropping a component), also update the corresponding memory file under
   `project_gcp_target` / `reference_gcp_deployment_gotchas`, so a session that
   never opens this skill still gets the correction.
