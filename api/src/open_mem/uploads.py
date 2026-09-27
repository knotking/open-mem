"""Upload sessions.

`POST /uploads` grants a capability rather than writing data: permission to put
bytes at one specific key, for a bounded time. The *completion* is an ordinary
write with a `Stored` content ref, which is why uploads do not need a second
admission path -- they reuse the one every producer already goes through.

The local variant has no signer, so it issues a URL pointing at its own upload
endpoint with a signed token. That is deliberate: the alternative is running
MinIO for S3 parity, and the contract is what matters, not the container count.
GCS swaps in a real signed URL behind the same two calls.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import asyncpg

from .ids import new_id

DEFAULT_TTL_SECONDS = 3600


class UploadError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Session:
    upload_id: str
    url: str
    token: str
    storage_key: str
    expires_at: datetime


async def create_session(
    pool: asyncpg.Pool,
    *,
    producer_id: str,
    principal,
    external_id: str,
    mime_type: str | None,
    size_bytes: int | None,
    base_url: str,
    max_bytes: int,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> Session:
    producer = await pool.fetchrow(
        """
        SELECT producer_id, org_id, project_id, user_id, status
        FROM producers WHERE producer_id = $1
        """,
        producer_id,
    )
    if producer is None or producer["org_id"] != principal.org_id:
        raise UploadError("unknown producer", status=404)
    if producer["status"] != "enabled":
        raise UploadError("producer is not enabled", status=403)
    # Refuse before any bytes move: rejecting an upload is cheap, and
    # discovering the size limit after a 500 MB transfer is not.
    if size_bytes is not None and size_bytes > max_bytes:
        raise UploadError(
            f"{size_bytes} bytes exceeds the {max_bytes} limit for this deployment",
            status=413,
        )

    upload_id = new_id("upl")
    token = f"{upload_id}.{secrets.token_urlsafe(32)}"
    storage_key = f"{producer['org_id']}/{producer['project_id']}/{upload_id}/raw/pending"
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

    await pool.execute(
        """
        INSERT INTO upload_sessions (upload_id, org_id, project_id, producer_id, user_id,
            external_id, storage_key, mime_type, size_bytes, token_hash, expires_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """,
        upload_id, producer["org_id"], producer["project_id"], producer_id,
        principal.user_id, external_id, storage_key, mime_type, size_bytes,
        hashlib.sha256(token.encode()).digest(), expires_at,
    )
    return Session(
        upload_id=upload_id,
        url=f"{base_url}/api/v1/uploads/{upload_id}/bytes",
        token=token,
        storage_key=storage_key,
        expires_at=expires_at,
    )


async def authorise(pool: asyncpg.Pool, upload_id: str, token: str) -> asyncpg.Record:
    row = await pool.fetchrow(
        "SELECT * FROM upload_sessions WHERE upload_id = $1", upload_id
    )
    if row is None:
        raise UploadError("unknown upload session", status=404)
    if not hmac.compare_digest(bytes(row["token_hash"]), hashlib.sha256(token.encode()).digest()):
        raise UploadError("invalid upload token", status=403)
    if row["status"] != "pending":
        # Not an error worth retrying: a completed session is spent.
        raise UploadError(f"upload session is {row['status']}", status=409)
    if row["expires_at"] <= datetime.now(timezone.utc):
        await pool.execute(
            "UPDATE upload_sessions SET status = 'expired' WHERE upload_id = $1", upload_id
        )
        raise UploadError("upload session has expired", status=410)
    return row


async def complete(
    pool: asyncpg.Pool, upload_id: str, *, storage_ref: str, checksum: str, received: int
) -> asyncpg.Record:
    await pool.execute(
        """
        UPDATE upload_sessions
        SET status = 'completed', completed_at = now(), received_bytes = $2, checksum = $3,
            storage_key = $4
        WHERE upload_id = $1
        """,
        upload_id, received, checksum, storage_ref,
    )
    return await pool.fetchrow("SELECT * FROM upload_sessions WHERE upload_id = $1", upload_id)
