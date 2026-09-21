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
reachable as either `pagarwal@buildgeek.ai` or `pagarwal@homegeek.ai` — both
have full access to Cloud Run, Cloud SQL and Secret Manager here.

| Piece | Name | Notes |
|---|---|---|
| API | Cloud Run `memdog-api` | `https://memdog-api-r5ifa3vgqq-uc.a.run.app` |
| UI | Cloud Run `memdog-sandbox` | `https://memdog-sandbox-r5ifa3vgqq-uc.a.run.app` |
| Database | Cloud SQL `memdog-spine` | PG16 + pgvector, **private IP `10.100.0.3` only** |
| Raw bytes | GCS `gs://memdog-spine-raw-dev` | |
| Images | Artifact Registry `memdog` | `us-central1-docker.pkg.dev/memdog-dev-506718/memdog` |
| Identity | `memdog-api@memdog-dev-506718.iam.gserviceaccount.com` | both services and all jobs |
| Jobs | `memdog-reconcile`, `memdog-crawl-tick`, `memdog-alert-tick`, `memdog-seed`, `memdog-bootstrap` | |
| Repo analysis | Cloud Run Job `memdog-repo-analysis` | **Not on the API image** — own Dockerfile, own tag, own deploy. Off unless `REPO_ANALYSIS_JOB` is set. |
| Schedules | `memdog-reconcile-tick` every 10 min → `memdog-reconcile`; `memdog-alert-sweep` every 1 min → `memdog-alert-tick` | |
| Secrets | `memdog-db-password`, `memdog-master-key`, `memdog-demo-key`, `memdog-web-api-key`, `gemini-api-key` | |
| Drive reader | `memdog-drive-key` | **Optional.** A service-account JSON key, mounted as `DRIVE_SERVICE_ACCOUNT`. Absent, Drive folders report themselves unconfigured — see [Connecting a Drive folder](#connecting-a-drive-folder). |
| Console sign-in | `owner@memdog.dev` (owner), `demo@memdog.dev` (admin) | Identity Platform; passwords in `memdog-owner-password` / `memdog-demo-password` |

**There is no GKE, no Kubernetes and no Supabase.** If a doc or an old memory
says otherwise it is describing `memdog-dev` (project `204556389124`), which is
the previous generation and whose owning account has a dead refresh token.

## Routine deploy

```bash
cd api && ./deploy/cloudrun.sh <tag>     # e.g. spine-13

cd ui && MEMDOG_PROJECT_ID=<prj_...> MEMDOG_PRODUCER_ID=<key_...> \
         API_URL=https://memdog-api-r5ifa3vgqq-uc.a.run.app \
         ./deploy.sh <tag>               # e.g. ui-4, only if the UI changed
```

**The UI deploy has no defaults for those two and refuses without them**, by
design — they name the org whose data the console shows, and a wrong guess is
the "404 unknown producer for signed-in users only" failure described below.
`API_URL` defaults to the `...-266276359448...` spelling of the same service;
passing the `r5ifa3vgqq` one keeps it consistent with what is already set. Read
the current values off the running service rather than remembering them:

```bash
gcloud run services describe memdog-sandbox --project memdog-dev-506718 \
  --region us-central1 --format='value(spec.template.spec.containers[0].env)' \
  | tr ';' '\n' | grep -E 'MEMDOG_(PROJECT|PRODUCER)_ID'
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

- **`failed to build: ... proxyconnect tcp: dial tcp 192.168.65.1:3128: i/o
  timeout`.** Seen twice on 2026-09-08. Docker Desktop's proxy times out pushing
  a layer to Artifact Registry. It is **transient — re-run the same command and
  it goes through**, and it is not the expired-login failure below: that one
  reports a credential error with an empty `out:`, this one names a socket.

  **The trap is not the timeout, it is how it reports.** `./deploy.sh <tag> |
  tail -4` exits 0 even when the build failed, because the pipeline's status is
  `tail`'s. A deploy that failed then looks like one that worked while the old
  revision keeps serving — the same false success the `ui/deploy.sh` note at the
  end of this file describes. Redirect instead of piping, and read the script's
  own exit code:

  ```bash
  ./deploy.sh <tag> > /tmp/deploy.log 2>&1; echo "EXIT=$?"; tail -3 /tmp/deploy.log
  ```

  Confirm against the served tag regardless — that is the only fact that
  settles it:

  ```bash
  gcloud run services describe memdog-sandbox --project memdog-dev-506718 \
    --region us-central1 --format='value(spec.template.spec.containers[0].image)'
  ```

- **A deploy succeeds and the public demo gallery goes dark.** Found 2026-09-08.
  `PUBLIC_DEMOS` is the gallery registry, it is JSON, and JSON is full of
  commas — which is exactly the character `--set-env-vars` splits on. So it was
  never in that list, which meant it lived *only* on the running service, and
  `--set-env-vars` replaces the whole set: **every deploy silently dropped it.**
  This is the `REPO_ANALYSIS_JOB` trap above, one variable over, and it had been
  happening unnoticed because nothing errors — the landing page simply has no
  gallery, which reads as a UI regression rather than as a deploy that lost a
  variable.

  `cloudrun.sh` now writes the whole environment to a **file** (`--env-vars-file`;
  JSON is valid YAML, so there is no delimiter to collide with and nothing to
  quote) and defaults `PUBLIC_DEMOS` to *whatever the service already has*
  rather than to empty — it is data a human produced with `seed-demos`, not a
  toggle with a sensible constant, so the only correct default is what is
  already true. `PUBLIC_DEMOS=''` clears it deliberately.

  **`seed-demos` is now a much bigger run than it was, and seeding one corpus
  is the normal way to use it.** The gallery gained the Gita, which is 701
  records against roughly a hundred for everything else combined, and every one
  is enriched — so a full run is on the order of seven hundred model calls where
  it used to be about a hundred.

  Seed one corpus, not all of them:

  ```bash
  gcloud run jobs execute memdog-seed --project memdog-dev-506718 --region us-central1 \
    --args="-m,memdog,seed-demos,--only=gita" --wait
  ```

  **`--only` exists because seeding is destructive.** `seed_corpus` deletes the
  project of its own name before writing, so a full run takes every published
  corpus down and builds it back. That is fine on an empty deployment and a poor
  trade on one where four corpora already work — a run that exhausts its quota
  partway stops at a `DemoSeedError` and what it leaves behind is the corpora it
  had already deleted.

  A filtered run prints **only the entries it seeded**, and `PUBLIC_DEMOS` is
  the whole gallery, so the output has to be **merged** into the existing value
  rather than pasted over it. Setting it to a filtered run's output unpublishes
  everything not named. The seeder says so on the way out; it is repeated here
  because it is the step that loses corpora.

  Three things sized for a hundred records that seven hundred broke, all fixed
  and all worth recognising if they come back:

  - **`--task-timeout` is now per job**, 3 hours for `memdog-seed` against 30
    minutes for the ticks. Enrichment is one worker per topic, so it is serial —
    a model call per record, one after the next. Under the shared thirty the
    task is killed partway and leaves a project written and half-enriched.
  - **The in-process drain matches it**, `DEMO_SEED_DRAIN_SECONDS`, default two
    hours. It raises `TimeoutError` rather than returning early, so when it was
    ten minutes the seed died on what reads as a broken queue. Raise it and the
    task timeout together or the job is killed inside the drain.
  - **The seeder waits out `429`.** It writes through the public verb on purpose,
    so it meets the same `6000 credits/minute` limit any client does — which a
    hundred records never reach and seven hundred reach in the first few batches.
    It reads `Retry-After` and retries. Raising the seeder's quota instead was
    rejected: it would seed by a route no client has.

  Against the free tier described under [When it fails](#when-it-fails) a seed
  this size does not merely run slowly — it falls back to the local heuristic
  partway through and publishes a corpus with **no entities and therefore no
  graph**, which is the one thing that corpus exists to show. Check the key's
  quota before seeding, and re-read `claims` on `GET /api/v1/public/demos`
  afterwards: a Gita entry reporting few or no claims is that failure, not a
  quiet corpus.

  **The sample questions rest entirely on the vector arm.** `websearch_to_tsquery`
  ANDs every word of a query, so a natural-language question almost never
  matches lexically — measured on the seeded Gita, the lexical arm returned
  nothing for all five. A corpus whose questions depend on the embedder is the
  failure `papers.py` names, so validate them against the configured embedder
  before spending a seed on them, not after.

  The gcloud `^delim^` escape was rejected on purpose: it works until a blurb
  contains the delimiter — an email address, a percentage — which trades a
  certain bug for a latent one.

  **The general move is worth keeping: diff the live environment against what
  the script sets, before deploying.** It is one command and it is how this was
  found.

  ```bash
  gcloud run services describe memdog-api --project memdog-dev-506718 \
    --region us-central1 --format='value(spec.template.spec.containers[0].env)' \
    | tr ';' '\n' | grep -oE "'name': '[A-Z_]+'"
  ```

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

### Large media is off unless the deploy says otherwise

`cloudrun.sh` passes `LARGE_MEDIA=${LARGE_MEDIA:-false}` and
`MAX_LARGE_MEDIA_BYTES=${MAX_LARGE_MEDIA_BYTES:-268435456}` to the service **and
to every job**, for the reason the job-coupling rule above exists: the parse
worker runs in both, and a service that can interpret a 200 MB video while the
reconciler cannot would repair the corpus back toward `needs_model`.

Without `LARGE_MEDIA=true`, audio, video and documents over about 18 MB are
recorded as `needs_model` with the inline ceiling as the reason — which is the
correct default, because this is the most expensive thing the platform can be
asked to do. Nothing is lost; the bytes are stored and the item can be
reprocessed once it is switched on.

```bash
cd api && LARGE_MEDIA=true ./deploy/cloudrun.sh <tag>
```

Two things to know before turning it on:

- **`--set-env-vars` replaces the whole set.** Deploying afterwards without
  `LARGE_MEDIA=true` in the environment silently turns it back off, and the
  symptom is large media quietly returning to `needs_model`. This is the same
  trap that made the reconciler drift.
- **An org can still decline it, and by default an org has said nothing.** The
  `large_media` setting resolves org-first with this env var as the platform
  default, and the platform value is seeded into `settings` at startup so
  `GET /settings/effective` reports what is actually happening rather than a
  default the runtime is ignoring.

`large_media_graph` is a setting only, off by default, and needs no deploy: a
multi-hour transcript otherwise puts hundreds of low-precision entities into a
graph every other record in the project resolves against.

**The provider limits in `multimodal.MODEL_LIMITS` have never been checked
against a live account.** They are written from published documentation and
carry the date they were read. An item refused as beyond the model is refused on
the strength of that table, so a stale number is a wrong answer rather than a
slow one — worth one manual run against a real key.

### Connecting a Drive folder

`DRIVE_SERVICE_ACCOUNT` holds one service-account JSON key — the deployment's own
Drive reader — so that connecting a folder is *sharing it with an address*
rather than every tenant creating a service account and pasting its key.

**It is optional and `cloudrun.sh` treats it that way.** The secret is added to
`--set-secrets` only when it exists, because `--set-secrets` fails outright on a
secret that does not — and a deployment without a Drive reader is not broken.
`GET /api/v1/drive/share-address` answers `configured: false` and the console
says the feature is not switched on, which is a different sentence from a panel
that failed to load.

To switch it on, create the key in **Google Cloud → IAM → Service Accounts** (a
plain account, no roles needed on this project — its power comes entirely from
what people share with it), enable the Drive API on the key's own project, then:

```bash
gcloud secrets create memdog-drive-key --project memdog-dev-506718 --replication-policy automatic
gcloud secrets versions add memdog-drive-key --project memdog-dev-506718 --data-file=key.json
gcloud secrets add-iam-policy-binding memdog-drive-key --project memdog-dev-506718 \
  --member serviceAccount:memdog-api@memdog-dev-506718.iam.gserviceaccount.com \
  --role roles/secretmanager.secretAccessor
cd api && ./deploy/cloudrun.sh <tag>     # the secret only binds on a new revision
rm key.json
```

Then verify the address the console will show, and share a folder with exactly
that address:

```bash
curl -sf -H "X-API-Key: $KEY" $URL/api/v1/drive/share-address
```

- **A connected folder crawls nothing and reports zero, with no error.** That is
  the folder never having been shared with the address, and it is the only
  failure mode this feature has that does not announce itself — which is why the
  crawler is created disabled and the dry run is the step that answers it. Zero
  is the diagnosis, not a bug.
- **A second project connecting the same folder is refused with 409.** Deliberate:
  one identity reads every folder shared with it, and a folder id is in a URL and
  is not a secret, so the first project to connect a folder owns it. Without that
  refusal, knowing an id would be enough to read another tenant's documents.

### The repo analysis job

**It is not on the API image and is not in the job loop.** Every other job runs
`memdog` and is redeployed with the API precisely so it cannot drift. This one
carries `git`, `graphifyy` and 37 tree-sitter grammars — the reason it exists at
all is to keep that out of the API image — so it has its own Dockerfile, its own
tag, and its own deploy:

```bash
cd analysis/repo && PRODUCER_ID=<prd_...> ./deploy.sh <tag>   # e.g. repo-analysis-1
```

**It is off until the API is told its name.** Deploying the job does nothing on
its own; `REPO_ANALYSIS_JOB` has to name it, fully qualified:

```bash
gcloud run services update memdog-api --project memdog-dev-506718 --region us-central1 \
  --update-env-vars REPO_ANALYSIS_JOB=projects/memdog-dev-506718/locations/us-central1/jobs/memdog-repo-analysis
```

Empty is the correct default — the job clones arbitrary public repositories and
spends four model calls per snapshot, so it is switched on deliberately. **A
snapshot requested with it unset is not lost and does not hang**: it is recorded
and immediately marked `failed` with that as its reason, because a snapshot left
`pending` reads as one still running.

It needs its own write credential (`memdog-repo-analysis-key`) and a producer to
write through, because it reaches mem-dog only through the public write API — it
holds no database credential and has no privileged path. `deploy.sh` grants the
API's service account `roles/run.invoker` on the job, which is the whole
permission needed to start an execution.

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

## Signing in to the console

Two Identity Platform accounts exist on the project. Neither password was
recorded when the accounts were made, so both were reset on 1 Sep 2026 and are
now in Secret Manager — that is the only copy:

```bash
gcloud secrets versions access latest --secret memdog-owner-password --project memdog-dev-506718
gcloud secrets versions access latest --secret memdog-demo-password  --project memdog-dev-506718
```

Three things have to line up or sign-in fails in a way that does not name the
cause, because two of them are deliberately quiet:

1. **The Identity Platform account exists and `emailVerified` is true.** The
   verifier refuses an unverified address with 403 — an unverified email proves
   nothing, and the address is what an admin invites against.
2. **The address is admitted.** Registration is `invite_only` by default, which
   admits an address holding a live invite *or* one that is already a `users`
   row. `python -m memdog add-member <email> <role>` creates that row.
3. **A membership exists.** Auto-provisioning creates a user and an identity but
   **never a membership**, so a brand-new account authenticates cleanly and then
   gets `403 this account is not a member of any organization`.

   **Deleting an account's *data* also removes its membership.** Found
   2026-09-06. `POST /api/v1/users/{id}/deletion` is offboarding, not a data
   wipe: `account.py` says so outright — *"revocation is immediate and
   unconditional either way: keys, producers and connections stop working
   before any data question is settled"* — and membership goes with them. So
   "drop the demo account's data" and "the demo account can no longer sign in"
   are the same operation, and the second half is not mentioned in the
   response, which reports only counts of data deleted and retained.

   The tell is that the account signs in fine and fails immediately after, with
   the message above — identical to a brand-new account that was never added.
   Put it back with the ordinary endpoint, which attaches to the **caller's**
   org rather than the oldest one:

   ```bash
   curl -X POST -H "X-API-Key: $KEY" -H 'content-type: application/json' \
     -d '{"email":"demo@memdog.dev","role":"admin"}' \
     "$URL/api/v1/organizations/members"
   ```

   It restores the same `user_id`, so everything the account already owned is
   still its own. Prefer this to `python -m memdog add-member`, which attaches
   to *"the first organization"* (`ORDER BY created_at LIMIT 1`) and is the
   cause of the wrong-org failure described below.

   **To wipe an account's data without locking it out**, delete through
   `POST /api/v1/deletions` with a selector instead, which touches records and
   nothing else.
4. **The bootstrap connection is `shared`, not `personal`.** Found 2026-09-04.
   A personal connection binds its producer to the user who bootstrapped it, and
   the write path refuses everyone else with *"this producer is bound to another
   user's personal connection"*. Because the console sends the signed-in user's
   identity rather than a service credential, `personal` breaks Add data for
   every account except the bootstrap owner — while leaving reads working, so it
   presents as "adding data is broken" rather than as a permissions choice.

   It is fixed forward with
   `PATCH /api/v1/connections/{id} {"scope":"shared"}`, which applies to future
   writes only. `cloudrun.sh` now passes `shared`; a deployment genuinely meant
   for one person should pass `personal` deliberately.

5. **The membership is in the org the console is configured for.** Found
   2026-09-03. `MEMDOG_PRODUCER_ID` and `MEMDOG_PROJECT_ID` are baked in at
   deploy time and name one org; the console serves whoever signs in. When those
   disagree, every write fails with `404 unknown producer` — *for signed-in
   users only*.

   The trap is that it cannot be reproduced with curl. `lib/api.ts` sends the
   **signed-in user's** identity and falls back to the service key only when
   sign-in is not configured, so an unauthenticated reproduction always takes
   the service key, which is in the producer's org and always works. Hours went
   into testing the one path that cannot fail.

   `add-member` makes this easy to cause: it attaches to *"the first
   organization"* (`ORDER BY created_at LIMIT 1`), which is the oldest org and
   not necessarily the configured one. Checking that `/api/v1/projects` returns
   200 does not catch it — the account is a member of *an* org, just the wrong
   one. Check the project id matches `MEMDOG_PROJECT_ID`.

   Moving somebody between orgs is not one call. Resolution takes the **oldest**
   membership, so the old row has to be deleted, not merely outranked — and
   `remove_member` refuses self-removal, so it needs a third admin who is not
   being moved. An existing `users` row with no way to sign in can be given an
   Identity Platform account (create, then set `emailVerified`) to become that
   admin.

Reset a password without touching the rest (the body goes in a file — a password
in a command line is a password in the process table):

```bash
gcloud secrets versions access latest --secret memdog-web-api-key --project memdog-dev-506718  # for testing sign-in
curl -X POST "https://identitytoolkit.googleapis.com/v1/projects/memdog-dev-506718/accounts:update" \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "x-goog-user-project: memdog-dev-506718" -H 'content-type: application/json' \
  -d @body.json    # {"localId": "...", "password": "...", "emailVerified": true}
```

- **Any `identitytoolkit` call fails with "requires a quota project".** User
  credentials carry no quota project of their own; add
  `-H "x-goog-user-project: memdog-dev-506718"`. It reads as a permissions
  problem and is not one.

> **`pytest` destroys the local database.** The suite drops the schema and
> re-migrates on every run against the same Postgres the local console uses, so
> a seeded demo tenant does not survive it. Re-seed afterwards. Found the hard
> way, mid-session, having wiped a corpus somebody was demoing from.

Rehearse the inbound path against the deployment without waiting for a provider
— this is the only thing that exercises the HTTP layer of `/webhooks/`:

```bash
# a throwaway producer, its own signing secret, then all nine providers
curl -X POST -H "X-API-Key: $KEY" -H 'content-type: application/json' \
  -d '{"project_id":"<prj>","type":"webhook"}' $URL/api/v1/producers
curl -X POST -H "X-API-Key: $KEY" -H 'content-type: application/json' \
  -d @secret.json $URL/api/v1/producers/<whk>/signing-secret
curl -X PATCH -H "X-API-Key: $KEY" -H 'content-type: application/json' \
  -d '{"inbound_auth":"signature","mapping":{"provider":"twilio"}}' \
  $URL/api/v1/producers/<whk>/inbound
cd api && python -m tools.fake_inbound twilio $URL/webhooks/<whk> "$SECRET"
```

Tear down afterwards: the items it writes are real. Disable the producer
(`PATCH .../producers/<whk>` with `{"status":"disabled"}`) and delete the items
it wrote — the project listing does not return `producer_id`, so match on each
item's detail rather than on a time window.

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
- **Twilio webhooks are refused with `signature verification failed`, and the
  secret is correct.** Found 1 Sep 2026. Cloud Run terminates TLS and forwards
  to the container over plain HTTP, so `request.url` reconstructs as `http://`
  while Twilio signed the `https://` address configured in their console.
  Twilio is the only provider that signs the **URL** rather than the body, so
  it is the only one affected — every other provider keeps working, which makes
  this look like a Twilio-specific credential problem. It is not: it is the
  proxy. `_public_url()` now honours `X-Forwarded-Proto`. The general lesson is
  the one worth keeping: **anything that signs or compares a URL is wrong by
  default behind Cloud Run** until the forwarded scheme is applied.
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
- **The image build ends in `error getting credentials - err: exit status 1`,
  with an empty `out:`.** That is the same expired login wearing a Docker
  costume: `buildx` asks the gcloud credential helper for a registry token, the
  helper fails silently, and the message names neither gcloud nor the registry.
  `gcloud auth configure-docker` will say the helper is *already registered
  correctly*, which is true and beside the point. Confirm with
  `gcloud auth print-access-token` — if that fails, it is the login, not Docker.
  Same fix, same constraint: it needs a browser.
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

  **A `401` is a different problem from a `429` and reads identically.** Found
  2026-09-05: `fallback_reason` was
  `gemini: EmbeddingUnavailable: Client error '401 Unauthorized'`. Quota
  exhaustion is worth waiting out; an expired or revoked credential never
  resolves itself, and every model call falls back silently in the meantime.
  The corpus keeps ingesting, artifacts keep being written, and every summary
  is the first few lines of its own input — which reads as a bad model rather
  than a dead credential.

  **Do not judge the key by its prefix.** Keys here are `AQ.A…`, not the
  `AIza…` older AI Studio issued, and both are valid — an hour was spent
  concluding from the prefix that the key was "not an API key at all" when the
  format was fine and the credential had simply stopped working. The only test
  that means anything is asking the model:

  ```bash
  GK=$(gcloud secrets versions access latest --secret gemini-api-key --project memdog-dev-506718)
  curl -s -X POST "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.7-flash:generateContent" \
    -H "x-goog-api-key: $GK" -H 'content-type: application/json' \
    -d '{"contents":[{"parts":[{"text":"hi"}]}]}' | head -5
  ```

  **A new secret version is not a rotation.** `--set-secrets NAME=secret:latest`
  resolves at *deploy* time, so adding version 4 changes nothing until the
  service and every job are rolled — see the rotation block above. Confirm the
  fix by enriching one item and reading `fallback_depth`: `0` means the model
  was actually reached.

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
