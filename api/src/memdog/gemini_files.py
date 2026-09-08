"""Putting bytes where a model can reach them, when they are too big to send.

`generateContent` takes media inline as base64, which is what
`multimodal.GeminiMultimodal` does today and what almost every item needs. That
request has a hard ceiling around 20 MB, and above it there is exactly one way
through: upload the file first, then reference it by URI in the same
`generateContent` call the small path already makes.

**This module does the upload and nothing else.** No prompts, no modality
routing, no decision about whether the large path should be taken at all --
those stay in `multimodal.py`, where the small path's versions of them already
live. What is here is the part that is genuinely new: a resumable handshake, a
file that is not usable the moment it exists, and a handle that expires.

Three things about the Files API that shape everything below.

**A file is not ready when the upload returns.** It arrives `PROCESSING` and
becomes `ACTIVE` some time later -- video takes the longest, which is precisely
the case this module exists for. Calling `generateContent` against a processing
file fails, so the wait is not an optimisation; it is part of the upload.

**A file expires.** The provider deletes it after about 48 hours. So a
`file_uri` is never written to a row: the durable copy is the bytes in our own
blob store, and anything that needs to interpret them again uploads them again.
Storing the URI would produce a reference that works in testing and is gone by
the time a reprocess runs.

**A file that is not deleted still counts.** There is a per-project storage
quota, and an interpretation that fails halfway would otherwise leave its input
behind to fill it. Deletion is in a `finally`, and it never masks the error that
sent us there.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

# How long to wait for a file to leave PROCESSING, and how often to ask. A large
# video is the slow case by a wide margin; the bound exists so that a file the
# provider never finishes is a named timeout rather than a worker held open.
POLL_SECONDS = 2.0
MAX_WAIT_SECONDS = 600.0


class FileUploadError(Exception):
    """The upload, the wait, or the delete failed in a way that is terminal.

    Separate from the transient errors `multimodal` raises as `RuntimeError`:
    those are worth another attempt, and these describe an input or a state that
    a retry would reproduce exactly.
    """


class FileNotReady(FileUploadError):
    """Still `PROCESSING` when the wait ran out, or `FAILED` outright.

    Its own type because the two are worth telling apart from a bad request: the
    bytes were accepted and the provider could not make them usable, which is a
    statement about the file rather than about the request.
    """


@dataclass
class FileHandle:
    """What the provider gave back. Live for about 48 hours -- never stored."""

    name: str          # `files/abc123` -- the id used to poll and to delete
    uri: str           # what goes in a `file_data.file_uri` part
    mime_type: str
    state: str = "PROCESSING"


class GeminiFiles:
    """The resumable upload, the wait, and the delete."""

    def __init__(self, api_key: str, base: str | None = None,
                 timeout: float = 600.0) -> None:
        self._api_key = api_key
        # Overridable so the simulator in `tools/fake_gemini_files.py` can stand
        # in. The default is the real host.
        self._base = (base or "https://generativelanguage.googleapis.com").rstrip("/")
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._api_key}

    async def upload(self, payload: bytes, *, mime: str,
                     display_name: str) -> FileHandle:
        """Two requests: ask where to put it, then put it there.

        The start request carries only the metadata, which is what makes this
        resumable -- the provider allocates a URL for the bytes and the bytes go
        to that URL rather than to the API host.
        """
        start_headers = {
            **self._headers(),
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(len(payload)),
            "X-Goog-Upload-Header-Content-Type": mime or "application/octet-stream",
            "content-type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                start = await client.post(
                    f"{self._base}/upload/v1beta/files",
                    headers=start_headers,
                    json={"file": {"display_name": display_name}},
                )
                start.raise_for_status()
                # The header, not the body. The body of a start request is
                # empty, and reading a URL out of it would be reading nothing.
                upload_url = (start.headers.get("x-goog-upload-url")
                              or start.headers.get("X-Goog-Upload-URL"))
                if not upload_url:
                    raise FileUploadError(
                        "the upload was started and the provider named no URL to "
                        "send the bytes to"
                    )

                sent = await client.post(
                    upload_url,
                    headers={
                        **self._headers(),
                        "Content-Length": str(len(payload)),
                        "X-Goog-Upload-Offset": "0",
                        # `upload, finalize` in one command: the whole payload is
                        # already in memory, so chunking it would add a failure
                        # mode without removing one.
                        "X-Goog-Upload-Command": "upload, finalize",
                    },
                    content=payload,
                )
                sent.raise_for_status()
                body = sent.json()
        except httpx.HTTPStatusError as exc:
            raise FileUploadError(
                f"the upload was refused ({exc.response.status_code}): "
                f"{_message(exc.response)}"
            ) from exc
        except httpx.HTTPError as exc:
            # Transient, and named as such: the caller turns this into the kind
            # of error the queue retries.
            raise RuntimeError(f"the upload could not be completed: {exc}") from exc

        return _handle(body)

    async def await_active(self, handle: FileHandle, *,
                           max_wait: float = MAX_WAIT_SECONDS) -> FileHandle:
        """Poll until the file is usable, or say why it never will be."""
        if handle.state == "ACTIVE":
            return handle
        waited = 0.0
        current = handle
        while waited < max_wait:
            if current.state == "ACTIVE":
                return current
            if current.state == "FAILED":
                raise FileNotReady(
                    f"the provider could not process {current.name}; the bytes "
                    "were accepted and are not usable"
                )
            await asyncio.sleep(POLL_SECONDS)
            waited += POLL_SECONDS
            current = await self.get(current.name)
        raise FileNotReady(
            f"{current.name} was still {current.state.lower()} after "
            f"{int(max_wait)}s"
        )

    async def get(self, name: str) -> FileHandle:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    f"{self._base}/v1beta/{name}", headers=self._headers())
                response.raise_for_status()
                return _handle(response.json())
        except httpx.HTTPStatusError as exc:
            raise FileUploadError(
                f"could not read {name} ({exc.response.status_code}): "
                f"{_message(exc.response)}"
            ) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"could not read {name}: {exc}") from exc

    async def delete(self, name: str) -> None:
        """Best effort, and deliberately so.

        This runs in a `finally`, often while an exception is on its way up. A
        delete that fails must not replace the reason we are here -- the file
        expires on its own in a couple of days either way, so the worst case of
        swallowing this is quota held slightly longer, and the worst case of
        raising it is losing the error that actually matters.
        """
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.delete(
                    f"{self._base}/v1beta/{name}", headers=self._headers())
                if response.status_code not in (200, 204, 404):
                    log.warning("could not delete %s: %s", name, response.status_code)
        except Exception as exc:                      # noqa: BLE001 -- see above
            log.warning("could not delete %s: %s", name, exc)


def _handle(body: dict) -> FileHandle:
    file = body.get("file") if isinstance(body.get("file"), dict) else body
    name = file.get("name") or ""
    uri = file.get("uri") or ""
    if not name or not uri:
        raise FileUploadError(
            "the provider accepted the upload and returned no file to point at")
    return FileHandle(
        name=name, uri=uri,
        mime_type=file.get("mimeType") or file.get("mime_type") or "",
        state=file.get("state") or "PROCESSING",
    )


def _message(response) -> str:
    """The provider's own sentence, or nothing.

    The same shape as `multimodal._provider_message`, and for the same reason:
    `raise_for_status` keeps the status and throws away the half that says what
    was actually wrong.
    """
    try:
        payload = response.json()
    except Exception:                                 # noqa: BLE001
        return (response.text or "").strip()[:400]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:400]
    return str(payload)[:400]
