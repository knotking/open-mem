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
| Jobs | `memdog-reconcile`, `memdog-crawl-tick`, `memdog-seed`, `memdog-bootstrap` | |
| Schedule | `memdog-reconcile-tick` | every 10 min → `memdog-reconcile` |
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

Deploying the API also redeploys `memdog-reconcile`, `memdog-crawl-tick` and
`memdog-seed` onto the same image and the same environment. That coupling is
deliberate and load-bearing: **the reconciler once drifted twenty tags behind
and lost `EMBED_ENGINE`, so it re-embedded with the old model, concluded nothing
was stale, and quietly repaired the corpus back toward the state it was supposed
to be leaving.** Anything that reads `current_generators` has to agree with the
service about what "current" means. Never deploy a job by hand.

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
  Found 2026-08-30. The items are written and durable; the retrieve response
  says so — `"reason": "not_yet_enriched"`, `"state": "stored"` — and there is
  **no error in the logs at all**, which is the tell. `smoke.sh` sends no
  `options.enrich`, so the write falls back to the project's
  `enrich_by_default`, and on `prj_01M12VFWRTE5YCWAQFFXQSEN2C` that is off.
  Enrichment is optional by design, so the script asserts a step it never asked
  for. Confirm the deployment is fine by enriching one item by hand:

  ```bash
  curl -X POST -H "X-API-Key: $KEY" -H 'content-type: application/json' \
       -d '{"embed":true,"summarize":true}' "$URL/api/v1/data/<data_id>/enrich"
  ```

  It returns `{"status":"requested"}`; the item reaches `enriched` in about a
  minute and is then retrievable. **`smoke.sh` needs the flag added** — until
  then it fails on any project that has not opted into enrichment, which reads
  as a broken deploy.

## Known drift

`memdog-bootstrap` is **not** managed by `cloudrun.sh`. It was created by hand,
is pinned to `spine-12`, and currently runs `grant-key mdk_01M12VFWTA …` rather
than a bootstrap. It is stale by construction. Either fold it into the script's
job loop or treat it as a one-shot to re-point before each use — but do not
assume it runs current code.

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
