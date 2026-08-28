"""The blob store holds bytes and nothing else.

All metadata lives in Postgres (FR-SCH-1). The path scheme is the same in every
variant -- `{org}/{project}/{data_id}/{kind}/{hash}.{ext}` -- so a ref written
locally is readable as a GCS object path with only the scheme changed. `kind` is
`raw` / `text` / `derived`: it classifies the artifact, not the content, and is
deliberately not a database field.
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
from pathlib import Path
from typing import Protocol


class BlobStore(Protocol):
    async def put(self, *, org_id: str, project_id: str, data_id: str, kind: str,
                  payload: bytes, mime_type: str | None) -> tuple[str, str]: ...
    async def get(self, ref: str) -> bytes: ...


class FilesystemBlobStore:
    """The local variant. GCS swaps in behind the same two methods."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, ref: str) -> Path:
        if not ref.startswith("file://"):
            raise ValueError(f"not a filesystem ref: {ref}")
        return self._root / ref.removeprefix("file://")

    async def put(self, *, org_id: str, project_id: str, data_id: str, kind: str,
                  payload: bytes, mime_type: str | None) -> tuple[str, str]:
        digest = hashlib.sha256(payload).hexdigest()
        ext = (mimetypes.guess_extension(mime_type) if mime_type else None) or ".bin"
        key = f"{org_id}/{project_id}/{data_id}/{kind}/{digest}{ext}"
        path = self._root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return f"file://{key}", f"sha256:{digest}"

    async def get(self, ref: str) -> bytes:
        return self._path(ref).read_bytes()


class GCSBlobStore:
    """The cloud variant, behind the same two methods.

    Two notes on what this is and is not:

    **The client is synchronous**, so calls are handed to a worker thread rather
    than blocking the event loop. That is honest for the sizes the spine writes
    (inline payloads). Streaming large objects is a different code path and
    belongs with uploads, where `/tmp` being *memory* on Cloud Run makes
    streaming mandatory rather than merely correct.

    **The key scheme is identical to the filesystem store's.** A ref written by
    one is readable by the other with only the scheme changed, which is what
    makes the local variant a faithful rehearsal rather than an approximation.
    """

    def __init__(self, bucket: str) -> None:
        from google.cloud import storage  # imported lazily: local runs need no GCP deps

        self._bucket_name = bucket
        self._client = storage.Client()
        self._bucket = self._client.bucket(bucket)

    async def put(self, *, org_id: str, project_id: str, data_id: str, kind: str,
                  payload: bytes, mime_type: str | None) -> tuple[str, str]:
        digest = hashlib.sha256(payload).hexdigest()
        ext = (mimetypes.guess_extension(mime_type) if mime_type else None) or ".bin"
        key = f"{org_id}/{project_id}/{data_id}/{kind}/{digest}{ext}"

        def _upload() -> None:
            self._bucket.blob(key).upload_from_string(
                payload, content_type=mime_type or "application/octet-stream"
            )

        await asyncio.to_thread(_upload)
        return f"gs://{self._bucket_name}/{key}", f"sha256:{digest}"

    async def get(self, ref: str) -> bytes:
        prefix = f"gs://{self._bucket_name}/"
        if not ref.startswith(prefix):
            raise ValueError(f"ref does not belong to this bucket: {ref}")
        key = ref.removeprefix(prefix)
        return await asyncio.to_thread(self._bucket.blob(key).download_as_bytes)


def build_blob_store(settings) -> BlobStore:
    if settings.raw_bucket:
        return GCSBlobStore(settings.raw_bucket)
    return FilesystemBlobStore(Path(settings.blob_root))
