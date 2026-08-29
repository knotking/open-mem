"""`python -m memdog bootstrap` -- create a tenant and print a credential."""

from __future__ import annotations

import asyncio
import sys

from .bootstrap import AlreadyBootstrapped, bootstrap_tenant, refuse_if_occupied
from .seed import QUESTIONS
from .config import load_settings
from .inference import build_embedder
from . import usage
from .db import create_pool, migrate


async def _bootstrap(email: str, scope: str) -> int:
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


async def _bootstrap_to_secret(email: str, scope: str, project: str, secret: str) -> int:
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
    enrich = EnrichWorker(pool, build_extractor(settings), settings)
    await enrich.ensure_generator()
    enrich.register(queue)

    swept = await reconcile(
        pool, queue, embed_generator=embed.generator_version,
        enrich_generator=enrich.generator_version, grace_seconds=grace,
    )
    print(f"re-enqueued: parse={swept.parse} embed={swept.embed} "
          f"enrich={swept.enrich} events={swept.events}")
    if swept.total:
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
    enrich = EnrichWorker(pool, build_extractor(settings), settings)
    await enrich.ensure_generator()
    enrich.register(queue)
    EventWorker(pool, queue, embed_worker=embed, enrich_worker=enrich).register(queue)

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
        print("usage: python -m memdog seed --demo [--reset]", file=sys.stderr)
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
        "crawl-tick", "seed",
        "grant-key", "add-member",
    ):
        print("usage: python -m memdog bootstrap [email] [personal|shared]",
              file=sys.stderr)
        print("       python -m memdog smoke <url> <key> <producer_id> <project_id>",
              file=sys.stderr)
        print("       python -m memdog revoke-key <prefix>", file=sys.stderr)
        print("       python -m memdog reconcile [grace_seconds]", file=sys.stderr)
        print("       python -m memdog crawl-tick [max_crawlers]", file=sys.stderr)
        print("       python -m memdog seed --demo [--reset]", file=sys.stderr)
        print("       python -m memdog bootstrap-to-secret <email> <scope> "
              "<project> <secret_name>   # for jobs: stdout is Cloud Logging",
              file=sys.stderr)
        return 2
    if sys.argv[1] == "seed":
        flags = set(sys.argv[2:])
        return asyncio.run(
            _seed(reset="--reset" in flags, demo="--demo" in flags)
        )
    if sys.argv[1] == "smoke":
        return asyncio.run(_smoke(*sys.argv[2:6]))
    if sys.argv[1] == "crawl-tick":
        asyncio.run(_crawl_tick(int(sys.argv[2]) if len(sys.argv) > 2 else 5))
        return
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
    scope = sys.argv[3] if len(sys.argv) > 3 else "personal"
    return asyncio.run(_bootstrap(email, scope))


if __name__ == "__main__":
    raise SystemExit(main())
