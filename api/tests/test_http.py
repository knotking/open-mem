"""The endpoints themselves.

The service functions are tested directly elsewhere; this checks the things only
the HTTP layer can get wrong -- the credential header, the status codes, and the
fact that a batch write is always 207 rather than sometimes 200.
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client(pool, tenant):
    from memdog.app import app

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_endpoints(client, tenant, pool):
    auth = {"Authorization": f"Bearer {tenant.api_key}"}

    health = await client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    assert (await client.get("/api/v1/data/data_x")).status_code == 401
    assert (await client.get("/api/v1/data/data_x", headers={"Authorization": "Bearer mdk_nope.x"})
            ).status_code == 401

    written = await client.post(
        "/api/v1/write",
        headers={**auth, "Idempotency-Key": "http-1"},
        json={
            "producer_id": tenant.producer_id,
            "items": [{
                "external_id": "http-item-1",
                "content": {"kind": "inline", "text": "Kubernetes pods were evicted under memory pressure."},
            }],
            # Enrichment is opt-in: recording is cheap, spending is not.
            "options": {"enrich": True},
        },
    )
    # Always 207 -- batch is not a separate verb, so it is not a separate status.
    assert written.status_code == 207
    body = written.json()
    assert body["accepted"] == 1
    data_id = body["results"][0]["data_id"]

    fetched = await client.get(f"/api/v1/data/{data_id}", headers=auth)
    assert fetched.status_code == 200
    assert fetched.json()["is_downloaded"] is True

    # Enrichment is asynchronous; wait for the in-process worker to catch up.
    from memdog.app import app as live_app

    await live_app.state.queue.drain()

    found = await client.post(
        "/api/v1/retrieve",
        headers=auth,
        json={"query": "pods evicted memory", "filter": {"project_id": tenant.project_id}},
    )
    assert found.status_code == 200
    payload = found.json()
    assert [c["data_id"] for c in payload["results"]] == [data_id]
    assert payload["model_id"] == live_app.state.embedder.model_id


async def test_a_permission_failure_is_never_a_500(client, tenant):
    """`_control` translates AuthError for the control plane, but ten handlers
    call `actor.require()` in their own body and are wrapped by nothing — so a
    credential lacking a capability escaped as `500 Internal Server Error`.

    That is worse than an unhelpful status. It tells whoever is looking that the
    server is broken, when the truth is that their key cannot do this, and those
    two send you to completely different places.

    Asserted on `/platform/health` because ADMIN is the one capability an
    ordinary tenant key is guaranteed not to hold.
    """
    response = await client.get(
        "/api/v1/platform/health",
        headers={"Authorization": f"Bearer {tenant.api_key}"},
    )
    assert response.status_code == 403, response.text
    assert "admin" in response.json()["detail"].lower()
