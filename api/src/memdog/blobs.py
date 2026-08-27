"""The blob store holds bytes and nothing else.

All metadata lives in Postgres (FR-SCH-1). The path scheme is the same in every
variant -- `{org}/{project}/{data_id}/{kind}/{hash}.{ext}` -- so a ref written
locally is readable as a GCS object path with only the scheme changed. `kind` is
`raw` / `text` / `derived`: it classifies the artifact, not the content, and is
deliberately not a database field.
"""

from __future__ import annotations

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
