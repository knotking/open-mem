"""W2 -- resolving a `Pending` reference into bytes.

A crawler or a webhook can say "there is a file at this URL" without carrying
the file. That is what makes an external producer first-class: it writes a
`Pending` ref through the public API and the platform fetches it, rather than
needing an internal queue it cannot reach.

**Most of this module is a security boundary.** Fetching a caller-supplied URL
from inside the deployment is server-side request forgery by construction: the
request originates from a host that can reach the VPC, the database's private
IP, and -- on GCP -- a metadata server that hands credentials to anything that
asks. So the URL is validated after DNS resolution and again after every
redirect, because a name that resolved publicly a moment ago can resolve to
169.254.169.254 on the next lookup.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

MAX_REDIRECTS = 3
FETCH_TIMEOUT_SECONDS = 120

# Blocked by address below in any case; named separately because they are the
# specific thing an SSRF is usually reaching for.
METADATA_HOSTS = {"metadata.google.internal", "metadata", "169.254.169.254"}
ALLOWED_SCHEMES = {"http", "https"}


class FetchError(Exception):
    """Terminal unless marked retryable: a URL refused now is refused
    identically on every attempt."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class Fetched:
    payload: bytes
    mime_type: str | None
    final_url: str
    redirects: int


def _address_is_private(host: str) -> bool:
    """Resolve and check *every* address the name maps to.

    Every one, not the first: a hostname with one public and one private record
    would otherwise pass the check and then connect to the private address.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise FetchError(f"cannot resolve {host}", retryable=True) from exc

    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local      # 169.254.0.0/16 -- the metadata range
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            return True
    return False


def validate_url(url: str) -> str:
    """Refuse anything that could reach inside. Re-run after every redirect."""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        # file://, gopher:// and friends are how an SSRF becomes a file read.
        raise FetchError(f"scheme {parsed.scheme!r} is not fetchable")
    if not parsed.hostname:
        raise FetchError("no host in URL")
    host = parsed.hostname.lower().rstrip(".")
    if host in METADATA_HOSTS:
        raise FetchError("refusing to fetch cloud metadata")
    if _address_is_private(host):
        raise FetchError(
            f"{host} resolves to a private or link-local address; fetching it "
            "would reach inside the deployment"
        )
    return url


async def fetch_url(url: str, *, max_bytes: int) -> Fetched:
    """Stream the download, enforcing the cap against bytes that actually arrive.

    `Content-Length` is the sender's claim, not a fact, so the cap is checked
    against both -- and the transfer is abandoned the moment it is exceeded
    rather than after buffering the whole thing.
    """
    validate_url(url)
    redirects, current = 0, url

    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=False) as client:
        while True:
            try:
                async with client.stream("GET", current) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location or redirects >= MAX_REDIRECTS:
                            raise FetchError("too many redirects")
                        redirects += 1
                        # Re-validated every hop: an open redirect to the
                        # metadata address is the standard way past a check
                        # that only ran once.
                        current = validate_url(str(response.url.join(location)))
                        continue

                    if response.status_code >= 500:
                        raise FetchError(f"upstream returned {response.status_code}",
                                         retryable=True)
                    if response.status_code >= 400:
                        raise FetchError(f"upstream returned {response.status_code}")

                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > max_bytes:
                        raise FetchError(f"{declared} bytes exceeds the {max_bytes} limit")

                    chunks, total = [], 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise FetchError(
                                f"stopped after {total} bytes; the limit is {max_bytes}"
                            )
                        chunks.append(chunk)

                    return Fetched(
                        payload=b"".join(chunks),
                        mime_type=(response.headers.get("content-type") or "").split(";")[0]
                        or None,
                        final_url=str(response.url),
                        redirects=redirects,
                    )
            except FetchError:
                raise
            except httpx.HTTPError as exc:
                raise FetchError(f"fetch failed: {exc}", retryable=True) from exc


class FetchWorker:
    """Turns a `Pending` item into a `Stored` one.

    Afterwards the row is indistinguishable from one an upload produced, which
    is the entire point of the content contract: enrichment cannot tell how the
    bytes arrived.
    """

    def __init__(self, pool, blobs, settings, queue=None) -> None:
        self._pool = pool
        self._blobs = blobs
        self._settings = settings
        self._queue = queue

    def register(self, queue, topic: str = "fetch") -> None:
        queue.subscribe(topic, self.handle)

    async def handle(self, message) -> None:
        from .events import dispatch_pending, mark_consumed, mark_deferred, mark_failed
        from .telemetry import continue_trace

        event_id = message.body.get("event_id")
        data_id = message.body["data_id"]
        with continue_trace("fetch", message.headers, data_id=data_id):
            try:
                await self.fetch(data_id)
            except FetchError as exc:
                if exc.retryable and event_id:
                    await mark_deferred(self._pool, event_id, str(exc))
                    return
                # Terminal. Record the reason on the row so the item explains
                # itself; retrying a refused URL never succeeds.
                await self._pool.execute(
                    """
                    UPDATE data_items
                    SET parse_status = 'unsupported',
                        parse_detail = jsonb_build_object('reason', $2::text, 'stage', 'fetch')
                    WHERE data_id = $1
                    """,
                    data_id, str(exc),
                )
                if event_id:
                    await mark_failed(self._pool, event_id, str(exc))
                return

        if event_id:
            await mark_consumed(self._pool, event_id)
            if self._queue is not None:
                # Consuming this releases anything gated on it -- an enrichment
                # request for a pending item names the fetch as its cause.
                await dispatch_pending(self._pool, self._queue)

    async def fetch(self, data_id: str) -> None:
        from .classify import classify, sniff_mime
        from .workers import record_version

        row = await self._pool.fetchrow(
            """
            SELECT pending_ref, org_id, project_id, deleted_at
            FROM data_items WHERE data_id = $1
            """,
            data_id,
        )
        if row is None or row["deleted_at"] is not None or row["pending_ref"] is None:
            return  # already fetched, or gone -- idempotent by construction

        ref = dict(row["pending_ref"])
        if ref.get("provider") != "url":
            # Every other provider needs a credential from its connection, which
            # is connector work. Say which, rather than failing opaquely.
            raise FetchError(f"no fetcher for provider {ref.get('provider')!r}")

        result = await fetch_url(
            ref.get("resource_id", ""), max_bytes=self._settings.max_upload_bytes
        )
        # The server sniffs; the sender's Content-Type is a hint like any other.
        mime = sniff_mime(result.payload, None, result.mime_type)
        storage_ref, checksum = await self._blobs.put(
            org_id=row["org_id"], project_id=row["project_id"], data_id=data_id,
            kind="raw", payload=result.payload, mime_type=mime,
        )
        data_type, layer = classify(
            explicit_data_type=None, source_type=None, mime_type=mime,
            external_id=result.final_url,
        )

        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                UPDATE data_items
                SET storage_ref = $2, pending_ref = NULL, mime_type = $3,
                    size_bytes = $4, checksum = $5, data_type = $6,
                    classified_by_layer = $7, updated_at = now()
                WHERE data_id = $1
                """,
                data_id, storage_ref, mime, len(result.payload), checksum, data_type, layer,
            )
            await record_version(
                conn, data_id, source="fetch", content_text=None, mime_type=mime,
                detail={"url": result.final_url, "redirects": result.redirects,
                        "bytes": len(result.payload)},
            )
        log.info("fetched %s (%d bytes) for %s", result.final_url, len(result.payload), data_id)
