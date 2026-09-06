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
import re
import socket
from dataclasses import dataclass
from urllib.parse import quote, urlparse

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


async def fetch_url(url: str, *, max_bytes: int,
                    headers: dict[str, str] | None = None) -> Fetched:
    """Stream the download, enforcing the cap against bytes that actually arrive.

    `Content-Length` is the sender's claim, not a fact, so the cap is checked
    against both -- and the transfer is abandoned the moment it is exceeded
    rather than after buffering the whole thing.
    """
    validate_url(url)
    redirects, current = 0, url
    # A credential belongs to the host it was issued for. Both Drive and Graph
    # answer a download with a redirect to a pre-signed CDN URL, and forwarding
    # the Authorization header there hands an access token to a host that never
    # needed one -- the classic way a token ends up in somebody else's logs.
    origin = urlparse(url).hostname
    sending = dict(headers or {})

    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=False) as client:
        while True:
            try:
                async with client.stream("GET", current, headers=sending) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location or redirects >= MAX_REDIRECTS:
                            raise FetchError("too many redirects")
                        redirects += 1
                        # Re-validated every hop: an open redirect to the
                        # metadata address is the standard way past a check
                        # that only ran once.
                        current = validate_url(str(response.url.join(location)))
                        if urlparse(current).hostname != origin:
                            sending = {k: v for k, v in sending.items()
                                       if k.lower() != "authorization"}
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


# Google's own document formats have no bytes to download -- `?alt=media` on a
# Doc returns a 403. They are exported, and the export format is the decision
# about what gets indexed: plain text for a document, CSV for a sheet.
GOOGLE_EXPORTS = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
    "application/vnd.google-apps.script": "application/vnd.google-apps.script+json",
}

# A resource id reaches here from a listing, and is about to be put in a URL.
# Anything with a slash or a dot-segment in it could address a different
# resource on the same host -- which is a path traversal against an API rather
# than a filesystem, and just as effective.
_DRIVE_ID = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
_GRAPH_PATH = re.compile(r"^drives/[A-Za-z0-9_.!-]{1,128}/items/[A-Za-z0-9_.!-]{1,128}$")


def download_url(provider: str, resource_id: str, hints: dict) -> str:
    """Where the bytes for this reference actually live.

    Constructed here rather than stored on the row: a stored URL is one more
    thing that can be tampered with between the listing and the fetch, and the
    host is not a choice -- it follows from the provider.
    """
    if provider == "google_drive":
        if not _DRIVE_ID.match(resource_id):
            raise FetchError(f"{resource_id!r} is not a Drive file id")
        export = GOOGLE_EXPORTS.get(hints.get("mime_type") or "")
        if export:
            return (f"https://www.googleapis.com/drive/v3/files/{resource_id}"
                    f"/export?mimeType={quote(export)}")
        if (hints.get("mime_type") or "").startswith("application/vnd.google-apps."):
            # A folder, a form, a shortcut. There is nothing to download, and
            # saying which beats a 403 from Drive.
            raise FetchError(
                f"{hints['mime_type']} has no downloadable content"
            )
        return (f"https://www.googleapis.com/drive/v3/files/{resource_id}"
                "?alt=media&supportsAllDrives=true")
    if provider == "microsoft_graph":
        if not _GRAPH_PATH.match(resource_id):
            raise FetchError(
                f"{resource_id!r} is not a Graph item path "
                "(drives/<drive-id>/items/<item-id>)"
            )
        return f"https://graph.microsoft.com/v1.0/{resource_id}/content"
    raise FetchError(f"no fetcher for provider {provider!r}")


class FetchWorker:
    """Turns a `Pending` item into a `Stored` one.

    Afterwards the row is indistinguishable from one an upload produced, which
    is the entire point of the content contract: enrichment cannot tell how the
    bytes arrived.
    """

    def __init__(self, pool, blobs, settings, queue=None, envelope=None) -> None:
        from .youtube import build_video_reader

        self._pool = pool
        self._blobs = blobs
        self._settings = settings
        self._queue = queue
        # A video is not downloaded and then read; it is read where it lives.
        # None when media interpretation is off, which makes a YouTube
        # reference a configuration error rather than a silent no-op.
        self._video = build_video_reader(settings)
        from .urlcontext import build_url_reader

        self._url_reader = build_url_reader(settings)
        # Only a reference that names a connection needs this. Absent, such a
        # reference fails as a configuration problem rather than being fetched
        # unauthenticated and reported as the source refusing us.
        self._envelope = envelope

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

    async def _credential(self, connection_id, org_id: str, provider: str
                          ) -> dict[str, str]:
        """The headers this provider's download needs, resolved at the moment
        of use -- the same call the crawler made to list the folder."""
        from .connections import ConnectionError_, authorize

        if not connection_id:
            raise FetchError(
                f"a {provider} reference names no connection, and neither "
                "Drive nor Graph answers without one"
            )
        if self._envelope is None:
            raise FetchError(
                "this reference authenticates through a connection and the "
                "deployment has no encryption configured"
            )
        try:
            headers, query = await authorize(
                self._pool, self._envelope, connection_id, org_id
            )
        except ConnectionError_ as exc:
            # Retryable when the token endpoint was unreachable rather than
            # refusing: a 502 now is a 200 in five minutes, and marking the row
            # unsupported would make an outage permanent.
            raise FetchError(str(exc), retryable=exc.status >= 500) from exc
        if query:
            raise FetchError(
                "this connection presents its credential in the query string, "
                "which a file download would put in the source's access log"
            )
        return headers

    async def _watch(self, url: str) -> Fetched:
        """A YouTube reference, read into text and returned as if downloaded.

        Returning `Fetched` is the whole trick: everything after this point --
        the blob write, classification, parsing, embedding, enrichment and
        graph extraction -- runs unchanged and never learns a video was
        involved. What it stores is an account of the video rather than the
        video, which is what the model will produce and the only thing the
        graph has any use for.

        The title and channel are put at the top rather than left to the
        enricher to invent: they are facts, they are free, and a record headed
        `watch?v=aircAruvnKk` is one nobody recognises in a list.
        """
        from .youtube import NotAVideo, ReadUnavailable, video_id

        vid = video_id(url)
        if vid is None:
            raise FetchError(f"not a YouTube video URL: {url[:200]}")
        if self._video is None:
            raise FetchError(
                "reading a video needs media interpretation, which is off on "
                "this deployment"
            )
        try:
            watched = await self._video.read(vid)
        except ReadUnavailable as exc:
            raise FetchError(str(exc), retryable=exc.retryable) from exc
        except NotAVideo as exc:
            raise FetchError(str(exc)) from exc

        heading = watched.title or watched.url
        if watched.author:
            heading = f"{heading} — {watched.author}"
        document = f"{heading}\n{watched.url}\n\n{watched.account}\n"
        return Fetched(payload=document.encode("utf-8"),
                       mime_type="text/plain; charset=utf-8",
                       final_url=watched.url, redirects=0)

    async def _read_page(self, url: str,
                         failure: "FetchError | None" = None) -> Fetched:
        """A page read by the model rather than downloaded.

        Returns `Fetched` for the same reason `_watch` does: everything after
        this -- the blob write, classification, parsing, embedding, enrichment
        -- runs unchanged. What is stored is an account of the page, headed with
        the URL and marked as an account, because a record that reads like the
        page while being a model's summary of it is the one outcome worth
        avoiding here.

        Reached two ways, and **the header has to say which**, because they are
        different claims about the same record. As a fallback, the bytes were
        asked for and could not be had. As the chosen reader, they were never
        requested -- and a header saying they "were not retrievable" would be a
        plain untruth sitting inside the corpus, in the one sentence that exists
        to keep an account from being mistaken for the page.

        `failure` is the fetch error to re-raise when this is the fallback and
        the reading fails too. "403 from the site" is the fact somebody needs,
        and replacing it with "the model could not read it either" hides the
        cause behind the fallback. When this *is* the primary reader there is no
        prior failure, and `UrlNotRead` propagates so the caller can fall
        through to an ordinary GET.
        """
        from .urlcontext import UrlNotRead

        try:
            page = await self._url_reader.read(url)
        except UrlNotRead as exc:
            log.info("url_context could not read %s: %s", url, exc)
            raise (failure or exc) from exc

        log.info("url_context read %s (%d tool tokens)", url, page.tool_tokens)
        why = (
            "The page's own bytes were not retrievable by this deployment, so "
            "this is a reading of the page rather than the page itself."
            if failure is not None else
            "This memory reads pages with the model rather than downloading "
            "them, so this is a reading of the page rather than the page itself."
        )
        header = f"{url}\n\n[Account of this page produced by a model with URL Context. {why}]\n\n"
        return Fetched(payload=(header + page.account + "\n").encode("utf-8"),
                       mime_type="text/plain; charset=utf-8",
                       final_url=url, redirects=0)

    async def _reads_with_model(self, data_id: str) -> bool:
        """Whether any memory holding this record asks for the model reader.

        `bool_or` rather than "the first memory's type": a record can be in
        several memories, and if one of them says its pages are read by the
        model then that is the answer. The alternative makes a record's
        treatment depend on which container happens to sort first, which is a
        difference nobody can see and nobody chose.
        """
        return bool(await self._pool.fetchval(
            """
            SELECT bool_or(t.url_reader = 'context')
            FROM memory_members mm
            JOIN memories m ON m.memory_id = mm.memory_id
            JOIN memory_types t ON t.project_id = m.project_id AND t.name = m.type
            WHERE mm.data_id = $1 AND m.deleted_at IS NULL
            """,
            data_id,
        ))

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
        provider = ref.get("provider")
        hints = dict(ref.get("hints") or {})

        if provider == "youtube":
            result = await self._watch(ref.get("resource_id", ""))
        elif provider == "url":
            target, headers = ref.get("resource_id", ""), None
        else:
            target = download_url(provider, ref.get("resource_id", ""), hints)
            headers = await self._credential(ref.get("connection_id"),
                                             row["org_id"], provider)

        if provider != "youtube":
            # **The model first, when the memory asks for it.**
            #
            # Only a plain web URL. A connector download is authenticated and
            # private; handing that URL to a third-party model would ask it to
            # fetch something it cannot reach and should not be asked to.
            #
            # The inversion exists because the fallback below cannot fire for
            # the pages that need it most: a JavaScript-rendered page answers
            # 200 with an empty shell, so the GET *succeeds*, and the record
            # lands with no text and nothing reporting a problem.
            #
            # The GET stays underneath as the backup, so a refusal costs a
            # slower path rather than an empty record.
            # A file the source named for download is never read by the model,
            # whatever the memory asks for. "Read pages with the model" is about
            # pages: handing a PDF's URL to a model and storing its reading
            # throws away the document to keep a summary of it, and the summary
            # cannot be re-parsed, re-chunked or quoted from.
            read_first = (
                provider == "url"
                and hints.get("kind") != "file"
                and self._url_reader.enabled
                and await self._reads_with_model(data_id)
            )
            result = None
            if read_first:
                from .urlcontext import UrlNotRead

                try:
                    result = await self._read_page(target)
                except UrlNotRead as exc:
                    log.info("falling back to a GET for %s: %s", target, exc)

            if result is None:
                try:
                    result = await fetch_url(
                        target, max_bytes=self._settings.max_upload_bytes,
                        headers=headers)
                except FetchError as exc:
                    # The ordinary fallback, unchanged: a plain web URL whose
                    # GET failed, read by the model instead. Skipped when the
                    # model already tried and refused -- asking twice cannot
                    # produce a different answer and would bill for finding out.
                    if provider != "url" or not self._url_reader.enabled or read_first:
                        raise
                    result = await self._read_page(target, exc)
        # The server sniffs; the sender's Content-Type is a hint like any other.
        mime = sniff_mime(result.payload, None, result.mime_type)
        storage_ref, checksum = await self._blobs.put(
            org_id=row["org_id"], project_id=row["project_id"], data_id=data_id,
            kind="raw", payload=result.payload, mime_type=mime,
        )
        data_type, layer = classify(
            explicit_data_type="transcript" if provider == "youtube" else None,
            source_type=None, mime_type=mime, external_id=result.final_url,
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
