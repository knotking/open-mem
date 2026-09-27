"""Upload sessions.

`POST /uploads` grants a capability rather than writing data. The completion is
an ordinary write with a Stored ref, which is the whole reason uploads need no
second admission path.
"""

from __future__ import annotations

import pytest

from open_mem.uploads import UploadError, authorise, complete, create_session

pytestmark = pytest.mark.asyncio


async def _session(pool, tenant, principal, **kw):
    return await create_session(
        pool,
        producer_id=kw.pop("producer_id", tenant.producer_id),
        principal=principal,
        external_id=kw.pop("external_id", "video.mp4"),
        mime_type=kw.pop("mime_type", "video/mp4"),
        size_bytes=kw.pop("size_bytes", 1024),
        base_url="http://test",
        max_bytes=kw.pop("max_bytes", 10_000_000),
    )


async def test_a_session_grants_one_key_for_a_bounded_time(pool, tenant, principal_for):
    actor = await principal_for(tenant.api_key)
    session = await _session(pool, tenant, actor)
    assert session.url.endswith(f"/api/v1/uploads/{session.upload_id}/bytes")
    assert session.storage_key.startswith(f"{tenant.org_id}/{tenant.project_id}/")

    row = await pool.fetchrow(
        "SELECT status, token_hash FROM upload_sessions WHERE upload_id = $1", session.upload_id
    )
    assert row["status"] == "pending"
    # The token is a write primitive for that key, so it is hashed at rest
    # exactly like an API key -- one database read must not yield upload access.
    assert session.token.encode() not in bytes(row["token_hash"])


async def test_the_size_is_refused_before_any_bytes_move(pool, tenant, principal_for):
    """Discovering the limit after a 500 MB transfer is not a limit."""
    actor = await principal_for(tenant.api_key)
    with pytest.raises(UploadError) as exc:
        await _session(pool, tenant, actor, size_bytes=20_000_000, max_bytes=10_000_000)
    assert exc.value.status == 413


async def test_a_wrong_token_is_refused(pool, tenant, principal_for):
    actor = await principal_for(tenant.api_key)
    session = await _session(pool, tenant, actor)
    with pytest.raises(UploadError) as exc:
        await authorise(pool, session.upload_id, "not-the-token")
    assert exc.value.status == 403


async def test_a_session_is_spent_once_completed(pool, tenant, principal_for):
    """Replaying a completed upload URL must not overwrite the object."""
    actor = await principal_for(tenant.api_key)
    session = await _session(pool, tenant, actor)
    await authorise(pool, session.upload_id, session.token)
    await complete(pool, session.upload_id, storage_ref="file://x", checksum="sha256:x", received=10)

    with pytest.raises(UploadError) as exc:
        await authorise(pool, session.upload_id, session.token)
    assert exc.value.status == 409


async def test_an_expired_session_is_refused_and_marked(pool, tenant, principal_for):
    actor = await principal_for(tenant.api_key)
    session = await _session(pool, tenant, actor)
    await pool.execute(
        "UPDATE upload_sessions SET expires_at = now() - interval '1 minute' WHERE upload_id = $1",
        session.upload_id,
    )
    with pytest.raises(UploadError) as exc:
        await authorise(pool, session.upload_id, session.token)
    assert exc.value.status == 410
    assert await pool.fetchval(
        "SELECT status FROM upload_sessions WHERE upload_id = $1", session.upload_id
    ) == "expired"


async def test_a_disabled_producer_cannot_be_uploaded_to(pool, tenant, principal_for):
    """Admission control is one place, and it applies before the capability is
    granted rather than after the bytes arrive."""
    actor = await principal_for(tenant.api_key)
    await pool.execute("UPDATE producers SET status = 'disabled' WHERE producer_id = $1",
                       tenant.producer_id)
    with pytest.raises(UploadError) as exc:
        await _session(pool, tenant, actor)
    assert exc.value.status == 403


async def test_another_orgs_producer_is_not_addressable(pool, tenant, other_tenant, principal_for):
    intruder = await principal_for(other_tenant.api_key)
    with pytest.raises(UploadError) as exc:
        await _session(pool, tenant, intruder)
    # Same answer as missing: a producer id is not an oracle for what exists
    # in another organization.
    assert exc.value.status == 404
