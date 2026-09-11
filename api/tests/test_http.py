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


async def test_the_range_endpoint_binds_its_from_and_to(client, tenant, pool):
    """`from` is a Python keyword, so the query parameter reaches the handler
    only through an alias — and a mis-bound alias does not raise. It silently
    leaves the window unset, so the range answers about the whole timeline while
    the caller believes it answered about a week. That is the one thing here the
    service-level tests cannot catch, because they call the function directly.
    """
    auth = {"Authorization": f"Bearer {tenant.api_key}"}

    typed = await client.post(
        f"/api/v1/projects/{tenant.project_id}/memory-types", headers=auth,
        json={"name": "vendor_feed", "checkpoints": True})
    assert typed.status_code == 200, typed.text

    await client.post(
        "/api/v1/write", headers={**auth, "Idempotency-Key": "range-1"},
        json={"producer_id": tenant.producer_id,
              "items": [{"external_id": "feed-1",
                         "memory": {"key": "acme-feed", "type": "vendor_feed"},
                         "content": {"kind": "inline", "text": "Status: green"}}]})
    memory_id = await pool.fetchval(
        "SELECT memory_id FROM memories WHERE project_id = $1 AND memory_key = $2",
        tenant.project_id, "acme-feed")
    assert memory_id, "the write should have created the timeline memory"

    whole = await client.get(f"/api/v1/memories/{memory_id}/changes", headers=auth)
    assert whole.status_code == 200, whole.text
    # Never inferred: a consumer holding a composed answer and a net one must be
    # able to tell them apart.
    assert whole.json()["basis"] == "composed"

    # The alias bound if — and only if — the value reached the position parser.
    refused = await client.get(
        f"/api/v1/memories/{memory_id}/changes?from=not-a-position", headers=auth)
    assert refused.status_code == 400, refused.text
    assert "position" in refused.json()["detail"]

    assert (await client.get(
        f"/api/v1/memories/{memory_id}/changes?to=1", headers=auth)
    ).status_code == 200

    # `net` binds too. Asserted through the refusal that needs no model: a net
    # comparison without a `from` has only one description and says so, which it
    # can only do if the flag reached the handler.
    netted = await client.get(
        f"/api/v1/memories/{memory_id}/changes?net=true", headers=auth)
    assert netted.status_code == 400, netted.text
    assert "starting point" in netted.json()["detail"]


async def test_a_completed_upload_keeps_its_subject_and_its_date(client, tenant, pool):
    """An uploaded item must be able to say everything an inline one can.

    `access`, `template` and `options` were added here once for this reason.
    `case`, `identifiers` and `event_time` were not, so a document big enough to
    need an upload session landed **detached from the subject it was filed
    under and dated when it arrived**, while the identical file sent inline
    attached and kept its date. Nothing raises: the write succeeds, the record
    is real, and it is simply missing from the patient's timeline -- which is
    the failure a case exists to prevent.

    Asserted through the timeline rather than the response, because the response
    is 207 either way.
    """
    auth = {"Authorization": f"Bearer {tenant.api_key}"}

    session = await client.post(
        "/api/v1/uploads", headers=auth,
        json={"producer_id": tenant.producer_id, "external_id": "discharge.txt",
              "mime_type": "text/plain", "size": 21})
    assert session.status_code == 200, session.text
    upload_id, token = session.json()["upload_id"], session.json()["token"]

    put = await client.put(
        f"/api/v1/uploads/{upload_id}/bytes",
        headers={"X-Upload-Token": token, "content-type": "text/plain"},
        content=b"Discharged, improving",
    )
    assert put.status_code == 200, put.text

    finished = await client.post(
        f"/api/v1/uploads/{upload_id}/complete", headers=auth,
        json={"external_id": "discharge.txt",
              "case": {"case_type": "patient", "external_id": "MRN-A12345"},
              "identifiers": ["ACC-99821"],
              # Back-dated on purpose: an `event_time` that is silently dropped
              # is invisible until somebody reads a history in the wrong order.
              "event_time": "2019-04-02T09:00:00+00:00"},
    )
    assert finished.status_code == 207, finished.text

    case_id = await pool.fetchval(
        "SELECT case_id FROM cases WHERE project_id = $1 AND external_id = $2",
        tenant.project_id, "MRN-A12345")
    assert case_id, "the completion should have declared the subject it named"

    timeline = await client.get(f"/api/v1/cases/{case_id}/timeline", headers=auth)
    assert timeline.status_code == 200, timeline.text
    entries = timeline.json()["entries"]
    assert len(entries) == 1, "the uploaded document is a member of the case it named"
    assert entries[0]["basis"] == "asserted"
    assert entries[0]["event_time"].startswith("2019-04-02")

    identifiers = await pool.fetchval(
        "SELECT identifiers FROM data_items WHERE data_id = $1", entries[0]["data_id"])
    assert identifiers == ["ACC-99821"]
