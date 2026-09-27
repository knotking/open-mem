"""`python -m open_mem bootstrap` -- create a tenant and print a credential."""

from __future__ import annotations

import asyncio
import os
import sys

from .bootstrap import AlreadyBootstrapped, bootstrap_tenant, refuse_if_occupied
from .seed import QUESTIONS
from .config import load_settings
from .inference import build_embedder
from . import usage
from .db import create_pool, migrate


async def _bootstrap(email: str, scope: str | None) -> int:
    settings = load_settings()
    pool = await create_pool(settings)
    await migrate(pool, settings)
    try:
        await refuse_if_occupied(pool)
    except AlreadyBootstrapped as exc:
        await pool.close()
        print(f"bootstrap refused: {exc}", file=sys.stderr)
        return 1
    tenant = await bootstrap_tenant(pool, email=email, connection_scope=scope)
    await pool.close()
    print(f"org_id      {tenant.org_id}")
    print(f"project_id  {tenant.project_id}")
    print(f"user_id     {tenant.user_id}")
    print(f"producer_id {tenant.producer_id}")
    print(f"api_key     {tenant.api_key}    # shown once, never again")
    return 0


async def _store_secret(project: str, name: str, value: str) -> None:
    """Put a freshly issued credential in Secret Manager, not on stdout.

    Printing a key is right for a human at a terminal and wrong for a job: a
    Cloud Run Job's stdout is Cloud Logging, which is a durable, widely-readable
    store. Same command, different blast radius.

    Uses the REST API with the metadata server's token rather than adding a
    client library for one call.
    """
    import base64

    import httpx

    async with httpx.AsyncClient(timeout=30.0) as c:
        token = (
            await c.get(
                "http://metadata.google.internal/computeMetadata/v1/"
                "instance/service-accounts/default/token",
                headers={"Metadata-Flavor": "Google"},
            )
        ).json()["access_token"]
        auth = {"Authorization": f"Bearer {token}"}
        base = "https://secretmanager.googleapis.com/v1"
        created = await c.post(
            f"{base}/projects/{project}/secrets?secretId={name}",
            headers=auth,
            json={"replication": {"automatic": {}}},
        )
        if created.status_code not in (200, 409):
            created.raise_for_status()
        added = await c.post(
            f"{base}/projects/{project}/secrets/{name}:addVersion",
            headers=auth,
            json={"payload": {"data": base64.b64encode(value.encode()).decode()}},
        )
        added.raise_for_status()
    print(f"credential written to Secret Manager as {name} (not printed here)")


async def _grant_key(prefix: str, capabilities: str) -> None:
    """Adjust an existing key's capabilities.

    Deliberately a CLI rather than an endpoint: the API refuses to issue a key
    with capabilities the calling credential does not hold, which is correct
    and leaves exactly one bootstrap problem -- the first key. This solves that
    from inside the deployment rather than by weakening the rule.
    """
    settings = load_settings()
    pool = await create_pool(settings)
    caps = [c.strip() for c in capabilities.split(",") if c.strip()]
    updated = await pool.execute(
        "UPDATE api_keys SET capabilities = $2 WHERE prefix = $1", prefix, caps
    )
    await pool.close()
    print(f"grant {prefix} -> {caps}: {updated}")


async def _add_member(email: str, role: str) -> None:
    """Attach a person to the first organization. For the initial admin only."""
    from .ids import new_id

    settings = load_settings()
    pool = await create_pool(settings)
    org_id = await pool.fetchval("SELECT org_id FROM organizations ORDER BY created_at LIMIT 1")
    async with pool.acquire() as conn, conn.transaction():
        user_id = await conn.fetchval("SELECT user_id FROM users WHERE email = $1", email)
        if user_id is None:
            user_id = new_id("usr")
            await conn.execute(
                "INSERT INTO users (user_id, email, display_name) VALUES ($1, $2, $2)",
                user_id, email,
            )
        await conn.execute(
            """
            INSERT INTO memberships (user_id, org_id, role) VALUES ($1, $2, $3)
            ON CONFLICT (user_id, org_id) DO UPDATE SET role = EXCLUDED.role
            """,
            user_id, org_id, role,
        )
    await pool.close()
    print(f"member {email} ({role}) in {org_id} as {user_id}")


async def _revoke_key(prefix: str) -> None:
    settings = load_settings()
    pool = await create_pool(settings)
    updated = await pool.execute(
        "UPDATE api_keys SET revoked_at = now() WHERE prefix = $1 AND revoked_at IS NULL",
        prefix,
    )
    await pool.close()
    print(f"revoke {prefix}: {updated}")


async def _bootstrap_to_secret(
    email: str, scope: str | None, project: str, secret: str
) -> int:
    settings = load_settings()
    pool = await create_pool(settings)
    await migrate(pool, settings)
    try:
        await refuse_if_occupied(pool)
    except AlreadyBootstrapped as exc:
        await pool.close()
        print(f"bootstrap refused: {exc}", file=sys.stderr)
        return 1
    tenant = await bootstrap_tenant(pool, email=email, connection_scope=scope)
    await pool.close()
    print(f"org_id      {tenant.org_id}")
    print(f"project_id  {tenant.project_id}")
    print(f"user_id     {tenant.user_id}")
    print(f"producer_id {tenant.producer_id}")
    await _store_secret(project, secret, tenant.api_key)
    return 0


async def _standing_in(pool) -> dict:
    """Standing queries ride the alert sweep rather than adding a job.

    They are due at most once a minute and evaluate a bounded window, so a
    per-minute pass costs one indexed lookup that usually returns nothing --
    the same argument compaction made for riding it.
    """
    from .standing import tick as standing_tick

    return await standing_tick(pool)


async def _alert_tick(limit: int) -> None:
    """The floor under the async consumer.

    Cloud Run scales to zero and the queue is in-process, so a window in flight
    dies with its instance. The rows are the record of outstanding work, and
    this is what re-derives it.
    """
    import json as jsonlib

    from .alerts import tick
    from .compaction import tick as compaction_tick
    from .crypto import Envelope
    from .extraction import build_extractor

    settings = load_settings()
    pool = await create_pool(settings)
    try:
        # The envelope is what lets the sweep sign a delivery. Without it the
        # tick would evaluate and then quietly deliver nothing, which is the
        # failure that looks most like success.
        # Standing queries first, and the order is load-bearing: they queue
        # deliveries, and `tick` is what sends everything owed. Evaluated after
        # it, a match found this minute would sit until the next one -- a
        # minute of latency that would look like the sweep being slow rather
        # than like two steps in the wrong order.
        standing = await _standing_in(pool)
        result = await tick(pool, limit=limit,
                            envelope=Envelope.from_settings(settings))
        result["standing"] = standing
        # Deadlines, on the same pass. In a state graph with no topological
        # order a deadline is the only thing that can say an instance is stuck,
        # so a clock that does not run makes `stuck` unobservable rather than
        # merely late.
        from .workflows import tick as workflow_tick

        result["workflows"] = await workflow_tick(pool)
        # Compaction jobs ride the same sweep rather than adding a fourth job.
        # They are due at most daily, so a per-minute pass costs one indexed
        # lookup that usually returns nothing.
        result["compaction"] = await compaction_tick(
            pool, extractor=build_extractor(settings))
        print(jsonlib.dumps(result, default=str))
    finally:
        await pool.close()


async def _reconcile(grace: int) -> None:
    """One sweep, then exit. Cloud Scheduler drives this as a job.

    It builds its own worker set rather than talking to the running service:
    the point is that the *rows* are the record of outstanding work, so a
    process that has never seen the original request can pick it all up.
    """
    from .blobs import build_blob_store
    from .extraction import build_extractor
    from .multimodal import build_multimodal
    from .queue import InProcessQueue
    from .memories import sweep_all
    from .reconcile import reconcile
    from .workers import EmbedWorker, EnrichWorker, ParseWorker

    settings = load_settings()
    pool = await create_pool(settings)
    # Scheduled sweeps are exactly the spend nobody is watching: nobody is
    # waiting on the response, so an unmetered reconcile is a bill with no
    # request behind it. The meter is configured here for the same reason the
    # service configures it at startup.
    usage.configure(pool)
    queue = InProcessQueue()
    # The parse tier must be subscribed here too, or the sweep publishes work
    # that nothing consumes -- which looks exactly like the sweep doing nothing.
    ParseWorker(
        pool, build_blob_store(settings), queue=queue,
        multimodal=build_multimodal(settings),
    ).register(queue)
    from .workers import EventWorker

    embed = EmbedWorker(pool, build_embedder(settings), settings, queue=queue)
    await embed.ensure_generator()
    embed.register(queue, "embed")
    extractor = build_extractor(settings)
    enrich = EnrichWorker(pool, extractor, settings)
    await enrich.ensure_generator()
    enrich.register(queue)
    # The delete tier, for the same reason the parse tier is here: expiry
    # tombstones through the ordinary cascade, and a tombstone whose reclamation
    # message nothing consumes leaves chunks, embeddings and blobs behind for a
    # record the platform has already promised is gone.
    from .blobs import build_blob_store as _blobs
    from .deletion import DeleteWorker

    DeleteWorker(pool, _blobs(settings)).register(queue)

    # The checkpoint tier, for the same reason the parse tier is above: the
    # sweep publishes stalled checkpoints, and a topic nothing consumes turns
    # the recovery into a no-op that reports a number.
    EventWorker(pool, queue, embed_worker=embed, enrich_worker=enrich,
                extractor=extractor).register(queue)

    swept = await reconcile(
        pool, queue, embed_generator=embed.generator_version,
        enrich_generator=enrich.generator_version, grace_seconds=grace,
    )
    print(f"re-enqueued: parse={swept.parse} embed={swept.embed} "
          f"enrich={swept.enrich} events={swept.events} "
          f"checkpoints={swept.checkpoints}")

    # Retention, on the sweep that already runs on a schedule. `purge_events`
    # existed, was tested, and was called by nothing -- so the raw table grew
    # without limit while the retention story read as implemented.
    if settings.usage_retention_days:
        dropped = await usage.purge_events(
            pool, older_than_days=settings.usage_retention_days
        )
        print(f"usage events purged: {dropped} "
              f"(older than {settings.usage_retention_days} days)")

    # Expiry, on the same schedule and for the same reason `purge_events` is
    # here. `ttl_seconds` and `on_expiry` were storable, editable and displayed
    # while **nothing swept**, so a conversation memory with a one-hour TTL was
    # still there a year later and the retention story read as implemented.
    expired = await sweep_all(pool, queue)
    print(f"expired: considered={expired['considered']} deleted={expired['deleted']} "
          f"archived={expired['archived']} refiled={expired['refiled']} "
          f"across {expired['scopes']} owner-project scopes")
    # Unconditionally, now that the sweep publishes too: gating the drain on
    # the reconciler's own count would exit while an expiry deletion was still
    # queued, leaving a tombstoned record whose blobs were never reclaimed --
    # and nothing would look wrong, which is the failure this file keeps
    # meeting.
    await queue.drain(timeout=600)
    await queue.close()
    await pool.close()
    print("reconcile complete")


async def _crawl_tick(limit: int) -> None:
    """One scheduler pass, then exit. Cloud Scheduler drives this as a job.

    Scheduling lives outside the request path deliberately: a crawl can run for
    hours, and a Cloud Run instance that scales to zero between requests is the
    wrong place to hold one. The advisory lock inside `tick` means running this
    on several instances is safe rather than merely unlikely to overlap.
    """
    from .blobs import build_blob_store
    from .crawling import CrawlWorker, tick
    from .extraction import build_extractor
    from .multimodal import build_multimodal
    from .queue import InProcessQueue
    from .workers import EmbedWorker, EnrichWorker, EventWorker, ParseWorker

    settings = load_settings()
    pool = await create_pool(settings)
    usage.configure(pool)
    queue = InProcessQueue()
    blobs = build_blob_store(settings)
    ParseWorker(pool, blobs, queue=queue, multimodal=build_multimodal(settings)).register(queue)
    embed = EmbedWorker(pool, build_embedder(settings), settings, queue=queue)
    await embed.ensure_generator()
    embed.register(queue, "embed")
    extractor = build_extractor(settings)
    enrich = EnrichWorker(pool, extractor, settings)
    await enrich.ensure_generator()
    enrich.register(queue)
    EventWorker(pool, queue, embed_worker=embed, enrich_worker=enrich,
                extractor=extractor).register(queue)

    result = await tick(pool, CrawlWorker(pool, queue, blobs, settings), limit=limit)
    if result.get("skipped_lock"):
        print("another scheduler holds the lock")
    for run in result.get("runs", []):
        print(f"{run['run_id']}: {run['status']} discovered={run['discovered']} "
              f"emitted={run['emitted']} skipped={run['skipped']} failed={run['failed']}"
              + (f" ({run['reason']})" if run.get("reason") else ""))
    if not result.get("runs"):
        print(f"nothing due (skipped={len(result.get('skipped', []))}, "
              f"reaped={result.get('reaped', 0)})")
    await queue.drain(timeout=900)
    await queue.close()
    await pool.close()


async def _smoke(url: str, key: str, producer_id: str, project_id: str) -> int:
    """The milestone, run against a deployed service from inside the project.

    Where the platform in front of the service owns `Authorization` -- Cloud Run
    IAM does -- the identity token goes there and the API credential goes in
    `X-API-Key`. Both reach the same verifier.
    """
    import httpx

    headers = {"X-API-Key": key}
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            token = (
                await c.get(
                    "http://metadata.google.internal/computeMetadata/v1/"
                    f"instance/service-accounts/default/identity?audience={url}",
                    headers={"Metadata-Flavor": "Google"},
                )
            ).text
        headers["Authorization"] = f"Bearer {token}"
        print("identity token: acquired from metadata server")
    except httpx.HTTPError:
        print("identity token: unavailable (not on GCP) -- continuing unauthenticated")

    text = (
        "The checkout service returned 502s for eleven minutes after a bad deploy.\n\n"
        "Rollback completed at 14:02 UTC and error rates recovered."
    )
    async with httpx.AsyncClient(base_url=url, headers=headers, timeout=60.0) as c:
        health = await c.get("/api/v1/health")
        print("healthz:", health.status_code, health.text[:200])
        health.raise_for_status()

        written = await c.post(
            "/api/v1/write",
            headers={"Idempotency-Key": "smoke-1"},
            json={
                "producer_id": producer_id,
                "items": [
                    {"external_id": "smoke-incident",
                     "content": {"kind": "inline", "text": text}},
                    {"external_id": "smoke-pending",
                     "content": {"kind": "pending", "provider": "google-drive",
                                 "resource_id": "1AbC"}},
                    {"external_id": "smoke-bytes",
                     "content": {"kind": "inline",
                                 "bytes_b64": "iVBORw0KGgoAAAANSUhEUg=="}},
                ],
            },
        )
        print("write:", written.status_code, written.text[:400])
        written.raise_for_status()
        results = written.json()["results"]
        data_id = results[0]["data_id"]

        item = (await c.get(f"/api/v1/data/{data_id}")).json()
        print("read:", item["state"], "downloaded:", item["is_downloaded"])

        stored = (await c.get(f"/api/v1/data/{results[2]['data_id']}")).json()
        print("bytes item storage_ref:", stored.get("storage_ref"))

        found = None
        for _ in range(30):
            found = (await c.post("/api/v1/retrieve", json={
                "query": "rollback recovered error rates",
                "filter": {"project_id": project_id},
            })).json()
            if found["results"]:
                break
            await asyncio.sleep(2)
        print("retrieve:", len(found["results"]), "hit(s), model", found["model_id"])
        if not found["results"]:
            print("FAIL: nothing retrievable")
            return 1

        top = found["results"][0]
        print("  cited:", top["data_id"], "span", top["span_start"], "-", top["span_end"],
              "matched_by", top["matched_by"], "state", top["state"])

        arts = (await c.get(f"/api/v1/data/{data_id}/artifacts")).json()["artifacts"]
        print("artifacts:", len(arts))
        if arts:
            print("  title:", arts[0]["title"][:80])
            print("  keywords:", arts[0]["keywords"][:5])
            print("  model:", arts[0]["model_id"], "acl:", arts[0]["access_level"])

    print("PASS")
    return 0


async def _seed_demos(only: frozenset[str] | None = None) -> int:
    """Seed the gallery corpora and print the registry they become.

    In-process like `seed --demo`, and for the same reason: every request goes
    through the real routing, the real credential check and the real write verb,
    so what is proved here is what an external client would get.

    It prints `PUBLIC_DEMOS` rather than writing it anywhere. The registry is
    deployment configuration -- the one thing deciding what an unauthenticated
    visitor can reach -- and a seeder that could edit it would be a seeder that
    could publish a corpus nobody chose to publish.

    **`--only` exists because seeding is destructive.** `seed_corpus` deletes
    the project of the same name before writing, so a full run takes every
    published corpus down and builds it back -- which is fine the first time and
    is a poor trade when one corpus is being added to four that already work.
    The failure that motivates it is concrete: enrichment is a model call per
    record, a run that exhausts its quota partway stops at a `DemoSeedError`,
    and what is left behind is the corpora it had already deleted. Naming one
    corpus keeps the blast radius to the corpus being changed.

    The printed registry then covers **only what was seeded**, which is the
    whole of the caller's job to reconcile: `PUBLIC_DEMOS` is the full list, so
    a filtered run's output has to be merged into it rather than pasted over it.
    Said out loud below rather than left to be noticed.
    """
    import json

    import httpx

    from .app import app
    from .demos import CORPORA
    from .demos.seeding import DemoSeedError, seed_corpus

    chosen = dict(CORPORA)
    if only is not None:
        unknown = sorted(only - set(CORPORA))
        if unknown:
            # Refused rather than ignored, for the reason `graph_templates`
            # refuses an unknown template: a typo that silently seeds nothing
            # looks exactly like a run that succeeded.
            print(f"unknown corpus: {', '.join(unknown)} — known corpora are "
                  f"{', '.join(sorted(CORPORA))}", file=sys.stderr)
            return 2
        chosen = {k: v for k, v in CORPORA.items() if k in only}

    async with app.router.lifespan_context(app):
        pool = app.state.pool
        # **Not the oldest organization.** `add-member` picks that way and the
        # deploy runbook records what it costs -- the oldest org is not
        # necessarily the one anything is configured for. Here the requirement
        # is concrete: a corpus has to be written through a *shared* connection
        # or its records land private and the demo answers nothing to anyone
        # but the seeder. So the org is chosen by having one.
        org_id = sys.argv[2] if len(sys.argv) > 2 else await pool.fetchval(
            "SELECT org_id FROM connections WHERE scope = 'shared'"
            " ORDER BY created_at DESC LIMIT 1")
        if org_id is None:
            print("no organization with a shared connection — run "
                  "`bootstrap <email> shared` or `seed --demo` first, or name an "
                  "org: `seed-demos <org_id>`", file=sys.stderr)
            return 2
        print(f"seeding into {org_id}")
        owner = await pool.fetchval(
            "SELECT user_id FROM memberships WHERE org_id = $1"
            " ORDER BY created_at LIMIT 1", org_id)
        if owner is None:
            print(f"no member in {org_id} to act as", file=sys.stderr)
            return 2

        # The seeder speaks the public API, so it needs a credential like any
        # other client. Minted here and revoked in the `finally` rather than
        # reusing an existing key, which cannot be read back anyway.
        from .auth import CONFIG_WRITE, DATA_READ, DATA_WRITE, issue_key

        token = await issue_key(
            pool, user_id=owner, org_id=org_id, project_id=None,
            name="demo-gallery-seeder",
            capabilities=[DATA_READ, DATA_WRITE, CONFIG_WRITE],
        )

        # **Ten minutes was sized for a hundred records and this gallery now
        # holds seven hundred.** Enrichment is one worker per topic, so it is
        # serial by construction -- a model call per record, one after the next
        # -- and `queue.drain` raises `TimeoutError` rather than returning
        # early. Under the old number the scripture corpus could not finish: it
        # would write every record, enrich part of it, and die on a timeout that
        # reads as a broken queue rather than as a deadline set for a smaller
        # gallery.
        #
        # Overridable because the honest bound is the *job's* task timeout, and
        # that is deployment configuration rather than something this file can
        # know. Raise both together or the job is killed mid-enrichment, which
        # leaves a published corpus half-read.
        drain_seconds = float(os.environ.get("DEMO_SEED_DRAIN_SECONDS") or 7200)

        async def drain() -> None:
            await app.state.queue.drain(timeout=drain_seconds)

        entries = []
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://seed", timeout=180.0
            ) as client:
                for corpus in chosen.values():
                    entry = await seed_corpus(
                        pool, client, corpus,
                        org_id=org_id, admin_key=token, drain=drain)
                    print(f"  {corpus.key:9} {entry.pop('_written'):4} records, "
                          f"{entry.pop('_questions')}/{len(corpus.questions)} questions answered")
                    entries.append(entry)
        except DemoSeedError as exc:
            print(f"seeding failed: {exc}", file=sys.stderr)
            return 1
        finally:
            await pool.execute(
                "UPDATE api_keys SET revoked_at = now()"
                " WHERE org_id = $1 AND name = 'demo-gallery-seeder'", org_id)

    print()
    if only is not None:
        # The difference between this and the line below is the whole hazard of
        # a filtered run: `PUBLIC_DEMOS` is the entire gallery, so pasting a
        # partial registry over it unpublishes everything not named here.
        print(f"Seeded {len(entries)} of {len(CORPORA)} corpora. These entries "
              "must be MERGED into the existing PUBLIC_DEMOS —")
        print("setting it to just this would unpublish every corpus not listed:")
        print(json.dumps(entries))
    else:
        print("Set this on the API, and nothing is public until you do:")
        print(f"PUBLIC_DEMOS={json.dumps(json.dumps(entries))}")
    return 0


async def _seed(*, reset: bool, demo: bool) -> int:
    """Create the demo tenant, through the running application.

    The app is mounted in-process rather than reached over a socket, so this
    needs no deployed service -- but it is emphatically not a fixture path:
    every request goes through the real routing, the real credential check, the
    real admission control and the real write verb. The only thing missing
    compared to an external client is the network.

    `seed --demo` is explicit and has no default. A production install is not
    seeded by accident.
    """
    import httpx

    from .app import app
    from .seed import SeedError, reset_demo, seed_demo

    if not demo:
        print("usage: python -m open_mem seed --demo [--reset]", file=sys.stderr)
        return 2

    async with app.router.lifespan_context(app):
        async def drain() -> None:
            # Synchronous enrichment for the single-domain seed: the point is to
            # finish with a corpus that answers questions, and a seed that
            # returned before it was ready could not verify itself.
            await app.state.queue.drain(timeout=600)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://seed", timeout=120.0
        ) as client:
            try:
                if reset:
                    removed = await reset_demo(app.state.pool, client, drain=drain)
                    if removed["org_id"]:
                        print(f"purged {removed['purged']} records and the demo org "
                              f"{removed['org_id']} through the ordinary cascade "
                              f"({removed['users_removed']} users removed)")
                result = await seed_demo(app.state.pool, client, drain=drain)
            except SeedError as exc:
                # A failing seed is the useful case: it names the step that
                # broke, which is the whole reason this runs the real path.
                print(f"seed failed: {exc}", file=sys.stderr)
                return 1

    print()
    print(f"org         {result.org_id}")
    print(f"project     {result.project_id}")
    print(f"records     {result.written} written, {result.enriched} enriched")
    print(f"case        {result.case_members['asserted']} asserted, "
          f"{result.case_members['inferred']} inferred members")
    print(f"normalized  {result.normalization['projected']} projected, "
          f"{result.normalization['failed']} failed with a reason")
    print(f"questions   {result.questions_passed}/{len(QUESTIONS)} answered by the corpus")
    print(f"acl         the private record is hidden from the second member: "
          f"{result.private_item_hidden}")
    print()
    # Shown once and never again. A known demo credential present in every
    # deployment is a shipped default password, which is the failure mode that
    # appears in breach write-ups more reliably than any other.
    print("credentials below are shown once and are not recoverable:")
    print(f"  {result.admin_email:<38} {result.admin_key}")
    print(f"  {result.member_email:<38} {result.member_key}")
    return 0


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in (
        "bootstrap", "smoke", "revoke-key", "bootstrap-to-secret", "reconcile",
        "crawl-tick", "seed", "seed-demos", "alert-tick",
        "grant-key", "add-member",
    ):
        print("usage: python -m open_mem bootstrap [email] [personal|shared] "
              "(default: no connection)",
              file=sys.stderr)
        print("       python -m open_mem smoke <url> <key> <producer_id> <project_id>",
              file=sys.stderr)
        print("       python -m open_mem revoke-key <prefix>", file=sys.stderr)
        print("       python -m open_mem reconcile [grace_seconds]", file=sys.stderr)
        print("       python -m open_mem crawl-tick [max_crawlers]", file=sys.stderr)
        print("       python -m open_mem alert-tick [max_alerts]", file=sys.stderr)
        print("       python -m open_mem seed --demo [--reset]", file=sys.stderr)
        print("       python -m open_mem seed-demos [org_id] [--only=key,key]"
              "   # gallery corpora; prints the PUBLIC_DEMOS to set",
              file=sys.stderr)
        print("       python -m open_mem bootstrap-to-secret <email> <scope> "
              "<project> <secret_name>   # for jobs: stdout is Cloud Logging",
              file=sys.stderr)
        return 2
    if sys.argv[1] == "seed":
        flags = set(sys.argv[2:])
        return asyncio.run(
            _seed(reset="--reset" in flags, demo="--demo" in flags)
        )
    if sys.argv[1] == "seed-demos":
        # The positional argument is an org id and the flag is a filter, so the
        # flag has to be pulled out before the positional is read -- otherwise
        # `seed-demos --only=gita` seeds into an organization called
        # "--only=gita", which resolves to nothing and reports no shared
        # connection rather than a bad argument.
        rest = [a for a in sys.argv[2:] if not a.startswith("--")]
        picked = [a for a in sys.argv[2:] if a.startswith("--only=")]
        only = None
        if picked:
            only = frozenset(
                k.strip() for k in picked[-1].split("=", 1)[1].split(",") if k.strip()
            )
        sys.argv = [sys.argv[0], sys.argv[1], *rest]
        return asyncio.run(_seed_demos(only))

    if sys.argv[1] == "smoke":
        return asyncio.run(_smoke(*sys.argv[2:6]))
    if sys.argv[1] == "crawl-tick":
        asyncio.run(_crawl_tick(int(sys.argv[2]) if len(sys.argv) > 2 else 5))
        return
    if sys.argv[1] == "alert-tick":
        asyncio.run(_alert_tick(int(sys.argv[2]) if len(sys.argv) > 2 else 20))
        return 0
    if sys.argv[1] == "reconcile":
        asyncio.run(_reconcile(int(sys.argv[2]) if len(sys.argv) > 2 else 300))
        return 0
    if sys.argv[1] == "grant-key":
        asyncio.run(_grant_key(sys.argv[2], sys.argv[3]))
        return 0
    if sys.argv[1] == "add-member":
        asyncio.run(_add_member(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "admin"))
        return 0
    if sys.argv[1] == "revoke-key":
        asyncio.run(_revoke_key(sys.argv[2]))
        return 0
    if sys.argv[1] == "bootstrap-to-secret":
        return asyncio.run(_bootstrap_to_secret(*sys.argv[2:6]))
    email = sys.argv[2] if len(sys.argv) > 2 else "owner@example.com"
    # No connection unless one is asked for. A bootstrap key is the operator's
    # own credential, not a credential to somebody else's system -- and a
    # connection's scope is now a ceiling on what its producer may publish, so
    # the old default silently capped every write made with this key at
    # `private`. `personal` and `shared` are still accepted, for standing up a
    # tenant that really does write on somebody's behalf.
    scope = sys.argv[3] if len(sys.argv) > 3 else None
    return asyncio.run(_bootstrap(email, scope))


if __name__ == "__main__":
    raise SystemExit(main())
