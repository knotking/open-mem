"""`python -m memdog bootstrap` -- create a tenant and print a credential."""

from __future__ import annotations

import asyncio
import sys

from .bootstrap import bootstrap_tenant
from .config import load_settings
from .db import create_pool, migrate


async def _bootstrap(email: str, scope: str) -> None:
    settings = load_settings()
    pool = await create_pool(settings)
    await migrate(pool, settings)
    tenant = await bootstrap_tenant(pool, email=email, connection_scope=scope)
    await pool.close()
    print(f"org_id      {tenant.org_id}")
    print(f"project_id  {tenant.project_id}")
    print(f"user_id     {tenant.user_id}")
    print(f"producer_id {tenant.producer_id}")
    print(f"api_key     {tenant.api_key}    # shown once, never again")


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


async def _revoke_key(prefix: str) -> None:
    settings = load_settings()
    pool = await create_pool(settings)
    updated = await pool.execute(
        "UPDATE api_keys SET revoked_at = now() WHERE prefix = $1 AND revoked_at IS NULL",
        prefix,
    )
    await pool.close()
    print(f"revoke {prefix}: {updated}")


async def _bootstrap_to_secret(email: str, scope: str, project: str, secret: str) -> None:
    settings = load_settings()
    pool = await create_pool(settings)
    await migrate(pool, settings)
    tenant = await bootstrap_tenant(pool, email=email, connection_scope=scope)
    await pool.close()
    print(f"org_id      {tenant.org_id}")
    print(f"project_id  {tenant.project_id}")
    print(f"user_id     {tenant.user_id}")
    print(f"producer_id {tenant.producer_id}")
    await _store_secret(project, secret, tenant.api_key)


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


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in (
        "bootstrap", "smoke", "revoke-key", "bootstrap-to-secret"
    ):
        print("usage: python -m memdog bootstrap [email] [personal|shared]",
              file=sys.stderr)
        print("       python -m memdog smoke <url> <key> <producer_id> <project_id>",
              file=sys.stderr)
        print("       python -m memdog revoke-key <prefix>", file=sys.stderr)
        print("       python -m memdog bootstrap-to-secret <email> <scope> "
              "<project> <secret_name>   # for jobs: stdout is Cloud Logging",
              file=sys.stderr)
        return 2
    if sys.argv[1] == "smoke":
        return asyncio.run(_smoke(*sys.argv[2:6]))
    if sys.argv[1] == "revoke-key":
        asyncio.run(_revoke_key(sys.argv[2]))
        return 0
    if sys.argv[1] == "bootstrap-to-secret":
        asyncio.run(_bootstrap_to_secret(*sys.argv[2:6]))
        return 0
    email = sys.argv[2] if len(sys.argv) > 2 else "owner@example.com"
    scope = sys.argv[3] if len(sys.argv) > 3 else "personal"
    asyncio.run(_bootstrap(email, scope))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
