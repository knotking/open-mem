"""Crawlers -- discovering data that does not announce itself.

Every other ingestion path waits to be told: a webhook fires, a user uploads,
an SDK calls. Most data does nothing of the kind. A documentation site has four
hundred pages and no webhook; an API has three years of history nobody will
re-save.

Two decisions shape everything here.

**A backfill and a poll are two schedules of the same thing.** So there is one
worker with several discovery strategies, not a class per case.

**The crawler discovers and emits; it does not fetch and does not enrich.**
Every item goes out through `POST /api/v1/write` exactly as an external
producer's would -- records as `Inline`, files as `Pending` for the fetch
worker to materialise. That is what keeps a managed crawler from having powers
an external one lacks, and it means nothing downstream can tell where an item
came from.

Three strategies are implemented, and they are the three that work without a
credentialled connector: `http` (a templated REST request, which subsumes
enumerate/query/search for most APIs), `feed` (RSS, Atom, sitemap) and
`traverse` (link-following, with the obligations that carries). The
provider-specific strategies in the design -- `tree` over a Drive folder,
`enumerate` over Salesforce -- need OAuth connections that do not exist yet.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import asyncpg
import httpx
import jmespath
from pydantic import BaseModel, Field, field_validator

from .fetching import FetchError, validate_url
from .ids import new_id
from .telemetry import record, span


class CrawlerError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ config

class Limits(BaseModel):
    """Every one of these has stopped a real runaway somewhere.

    A `traverse` crawler with a loose pattern will ingest the public internet
    on the customer's inference budget, and it will do it enthusiastically.
    """

    max_items: int = Field(default=1000, ge=1, le=1_000_000)
    max_depth: int = Field(default=2, ge=0, le=10)
    max_bytes_per_item: int = Field(default=5_000_000, ge=1024)
    rate_per_sec: float = Field(default=2.0, gt=0, le=50)
    wall_clock_seconds: int = Field(default=1800, ge=10, le=86_400)


class Politeness(BaseModel):
    """Traverse only. Ignoring robots is both rude and the fastest way to be
    blocked, so honouring it is not configurable off."""

    user_agent: str = "mem-dog-crawler/1.0 (+https://mem-dog.dev/crawler)"
    # Floor, not the whole story: a host's own crawl-delay wins when longer.
    min_delay_seconds: float = Field(default=0.5, ge=0, le=60)


class Pagination(BaseModel):
    type: Literal["none", "cursor", "offset", "page",
                  "link_header", "next_url"] = "none"
    # For `cursor`, where the next cursor is in the body. For `next_url`, where
    # the next *URL* is -- `nextRecordsUrl`, `@odata.nextLink`, `paging.next`.
    cursor_path: str | None = None
    cursor_param: str | None = None
    page_param: str | None = None
    size_param: str | None = None
    page_size: int = Field(default=100, ge=1, le=1000)
    max_pages: int = Field(default=20, ge=1, le=1000)
    # A JMESPath predicate over the page body. When it is true, stop.
    stop_when: str | None = None


class Extract(BaseModel):
    """Where the items are, and which field of an item is what.

    Paths are JMESPath, so `data[*]` and `attachments[*].download_url` mean
    what they look like they mean.
    """

    items_path: str = "@"
    id_path: str | None = None
    version_path: str | None = None
    title_path: str | None = None
    content_path: str | None = None
    url_path: str | None = None


class Transform(BaseModel):
    target: str
    expr: str


class HttpRequest(BaseModel):
    method: Literal["GET", "POST"] = "GET"
    url: str
    query: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] | None = None


class Tree(BaseModel):
    """Where a folder recursion starts, and on which API.

    Two named APIs rather than a template, because folder recursion is the one
    thing whose shape genuinely differs between them: Drive asks for children
    with a query (`'<id>' in parents`), Graph asks for them with a path
    (`/items/<id>/children`), and a folder is marked by a mime type in one and a
    facet in the other. Everything else about these sources the `http` strategy
    already covers.

    Closed, so a name that does not resolve is a 422 at save rather than a
    crawler that fails at 3am.
    """

    api: Literal["google_drive", "microsoft_graph"]
    # Drive: the folder id from the folder's URL.
    # Graph: the drive to walk -- `sites/<site-id>/drive` or `users/<upn>/drive`.
    root: str
    # An allowlist of mime prefixes. Empty means every file, which is usually
    # not what anyone wants of a shared drive.
    include_mime: list[str] = Field(default_factory=list)


class CrawlerConfig(BaseModel):
    """Declarative and stored. Nothing here is code.

    Expressions are JMESPath -- no side effects, no I/O, no loops -- and
    template variables come from a fixed documented set. A config cannot hang a
    worker or reach the network in a way the strategy did not already declare.
    """

    name: str
    strategy: Literal["http", "feed", "traverse", "tree"]

    # http
    request: HttpRequest | None = None
    pagination: Pagination = Field(default_factory=Pagination)
    extract: Extract = Field(default_factory=Extract)
    transform: list[Transform] = Field(default_factory=list)
    filter_include: str | None = None

    # tree
    tree: Tree | None = None

    # feed / traverse
    seeds: list[str] = Field(default_factory=list)
    # An allowlist, never a blocklist. Link traversal escapes any blocklist
    # eventually -- it only takes one page linking outward.
    allow_hosts: list[str] = Field(default_factory=list)
    allow_path_prefix: str | None = None

    incremental: Literal["none", "watermark", "etag"] = "none"
    watermark_param: str | None = None
    # Which placeholder in the request names the unit a cursor is kept for --
    # "channel", "repo", "project". One crawler over forty channels needs forty
    # cursors, or a busy one drags the quiet ones past their own history.
    scope_param: str | None = None

    limits: Limits = Field(default_factory=Limits)
    politeness: Politeness = Field(default_factory=Politeness)

    # Off by default, and deliberately so: a crawler is the one producer that
    # can discover fifty thousand records unattended, and enriching all of them
    # is a model call per chunk on data nobody has asked a question about yet.
    # Turning it on is a decision someone makes after seeing a dry run's count.
    enrich: bool = False

    memory_key: str | None = None
    memory_type: str = "default"
    tags: list[str] = Field(default_factory=list)

    @field_validator("seeds")
    @classmethod
    def _seeds_are_fetchable(cls, seeds: list[str]) -> list[str]:
        # Validated at configuration time rather than at first run: a crawler
        # that fails at 3am because a seed was never fetchable is a worse way
        # to learn it than a 422 at the moment of saving.
        for seed in seeds:
            validate_url(seed)
        return seed_list(seeds)


def seed_list(seeds: list[str]) -> list[str]:
    return [s.strip() for s in seeds if s.strip()]


def validate_config(config: CrawlerConfig) -> None:
    """What must hold before a config can be stored at all."""
    if config.strategy == "http":
        if config.request is None:
            raise CrawlerError("an http crawler needs a request", status=422)
        validate_url(config.request.url)
    elif config.strategy == "tree":
        if config.tree is None:
            raise CrawlerError(
                "a tree crawler needs `tree` -- which API, and the folder or "
                "drive to walk", status=422,
            )
        if not config.tree.root.strip():
            raise CrawlerError("a tree crawler needs a root to walk from",
                               status=422)
    else:
        if not config.seeds:
            raise CrawlerError(f"a {config.strategy} crawler needs at least one seed",
                               status=422)
    if config.strategy == "traverse" and not (config.allow_hosts or config.allow_path_prefix):
        # Refusing here is the whole point. A traverse with no bound is a
        # crawler of the internet, and it will not stop on its own.
        raise CrawlerError(
            "a traverse crawler must declare allow_hosts or allow_path_prefix; "
            "an unbounded link crawl will not stop on its own",
            status=422,
        )
    for expression in [config.filter_include, config.pagination.stop_when,
                       *[t.expr for t in config.transform]]:
        if expression:
            try:
                jmespath.compile(expression)
            except Exception as exc:
                raise CrawlerError(f"invalid expression {expression!r}: {exc}",
                                   status=422) from exc


def fingerprint(config: CrawlerConfig) -> str:
    """What a dry run approved. Change any of it and the approval lapses."""
    material = config.model_dump_json(exclude={"name", "tags"})
    return hashlib.sha256(material.encode()).hexdigest()[:16]


# --------------------------------------------------------------- discovery

@dataclass(frozen=True)
class Auth:
    """What a connection adds to a request.

    Passed in already resolved rather than as a connection id, so a strategy
    never reaches the database and cannot decrypt anything on its own. The
    secret exists in this object and in the request it produces, and nowhere a
    config or a log can reach it.
    """

    headers: dict[str, str] = field(default_factory=dict)
    query: dict[str, str] = field(default_factory=dict)


@dataclass
class Discovered:
    """One thing found. Not yet written, not yet fetched."""

    external_id: str
    title: str | None = None
    text: str | None = None
    url: str | None = None
    version: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    depth: int = 0
    # Set when the discovery found a *reference* rather than the content: a
    # listing returns a file's name and id, and the bytes are a second request
    # that needs the same credential. Resolved by the fetch worker, which is
    # where every other download already happens -- so the byte cap, the blob
    # store and the parse pipeline are the ones already in use.
    pending: dict[str, Any] | None = None

    def version_hash(self) -> str:
        """Whichever change signal the source offered, reduced to one column.

        Falling back to the content hash matters: a source with no version
        field and no etag is exactly the one where re-enriching everything
        nightly is the default failure.
        """
        material = self.version or self.text or self.url or self.external_id
        return hashlib.sha256((material or "").encode()).hexdigest()[:32]


class RateLimited(CrawlerError):
    """The source said to come back later, and said when.

    Separate from `CrawlerError` because it is not a failure of the crawl: the
    run did nothing wrong, the credential is simply out of allowance, and the
    two must not be reported the same way -- one clears on its own.
    """

    def __init__(self, message: str, *, retry_after: int) -> None:
        super().__init__(message, status=429)
        self.retry_after = retry_after


def _retry_after(headers) -> int:
    """Seconds to wait, from whichever header the source chose to use."""
    raw = headers.get("retry-after") or headers.get("x-ratelimit-reset-after")
    try:
        return max(1, int(float(raw)))
    except (TypeError, ValueError):
        # A date-formatted Retry-After, or none at all. A minute is short enough
        # to recover promptly and long enough not to hammer a limiter.
        return 60


class Budget:
    """The stop conditions, in one place so every strategy obeys the same ones.

    A strategy that checked limits itself would be a strategy that forgot one.
    """

    def __init__(self, limits: Limits) -> None:
        self.limits = limits
        self.started = time.monotonic()
        self.items = 0

    def exhausted(self) -> str | None:
        if self.items >= self.limits.max_items:
            return f"reached max_items ({self.limits.max_items})"
        if time.monotonic() - self.started > self.limits.wall_clock_seconds:
            return f"reached wall clock ({self.limits.wall_clock_seconds}s)"
        return None

    def take(self) -> None:
        self.items += 1


class Throttle:
    """Rate limiting that is actually per host.

    A global rate would let one slow host block a fast one, and would let many
    fast pages hammer a single origin. The politeness obligation is per origin,
    so the bookkeeping is too.
    """

    def __init__(self, rate_per_sec: float) -> None:
        self._interval = 1.0 / rate_per_sec
        self._last: dict[str, float] = {}

    async def wait(self, url: str, extra_delay: float = 0.0) -> None:
        host = (urlparse(url).hostname or "").lower()
        interval = max(self._interval, extra_delay)
        previous = self._last.get(host)
        now = time.monotonic()
        if previous is not None:
            gap = now - previous
            if gap < interval:
                await asyncio.sleep(interval - gap)
        self._last[host] = time.monotonic()


async def _get(client: httpx.AsyncClient, url: str, *, headers: dict[str, str],
               max_bytes: int) -> httpx.Response:
    """Every outbound request re-validates, including redirect targets.

    The crawler is the one component whose whole job is following URLs it was
    given, so it is the one that most needs the SSRF check the fetch worker
    already has.
    """
    validate_url(url)
    response = await client.get(url, headers=headers, follow_redirects=False)
    hops = 0
    while response.status_code in (301, 302, 303, 307, 308) and hops < 3:
        location = response.headers.get("location")
        if not location:
            break
        url = validate_url(str(response.url.join(location)))
        response = await client.get(url, headers=headers, follow_redirects=False)
        hops += 1
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise FetchError(f"{declared} bytes exceeds the {max_bytes} limit")
    return response


def _render(template: str, variables: dict[str, str]) -> str:
    """Template variables come from a fixed set and are substituted, never
    evaluated. `{{ watermark }}` is a lookup, not an expression."""
    def replace(match: re.Match) -> str:
        return str(variables.get(match.group(1).strip(), ""))
    return re.sub(r"\{\{([^}]+)\}\}", replace, template)


def _search(path: str | None, payload: Any) -> Any:
    if not path:
        return None
    try:
        return jmespath.search(path, payload)
    except Exception:
        return None


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


async def discover_http(
    config: CrawlerConfig, *, watermark: str | None, budget: Budget,
    throttle: Throttle, checkpoint: dict, auth: Auth | None = None,
) -> list[Discovered]:
    """A templated request with declared pagination.

    This is the strategy that stops "we need to pull from X" being adapter
    work. It covers enumerate, query and search for most REST APIs, because
    those differ in pagination shape and field names, not in kind.
    """
    assert config.request is not None
    request = config.request
    # `epoch` is what makes an incremental clause safe on the first run.
    #
    # A template carrying `WHERE LastModifiedDate > {{ watermark }}` renders to
    # `> ` before anything has been crawled, and a source answers that with a
    # syntax error -- so the clause would work on every run except the one that
    # matters, and the connector would look broken on the day it was set up.
    # `{{ watermark|epoch }}` is not expression syntax; it is a second variable
    # a template picks instead.
    variables = {
        "watermark": watermark or "",
        "watermark_or_epoch": watermark or "1970-01-01T00:00:00Z",
        "now": _now_iso(),
    }
    found: list[Discovered] = []
    query = {k: _render(v, variables) for k, v in request.query.items()}
    if config.incremental == "watermark" and config.watermark_param and watermark:
        query[config.watermark_param] = watermark
    headers = {k: _render(v, variables) for k, v in request.headers.items()}
    headers.setdefault("User-Agent", config.politeness.user_agent)
    # The credential goes on last and cannot be overridden by the config. A
    # template that could set `Authorization` would be a place to put a secret
    # in the clear, which is the thing the connection exists to prevent.
    if auth is not None:
        headers.update(auth.headers)
        query.update(auth.query)

    page = int(checkpoint.get("page", 0))
    cursor = checkpoint.get("cursor")
    url = _render(request.url, variables)

    async with httpx.AsyncClient(timeout=30.0) as client:
        while page < config.pagination.max_pages:
            if budget.exhausted():
                break
            call_query = dict(query)
            pagination = config.pagination
            if pagination.type == "cursor" and cursor and pagination.cursor_param:
                call_query[pagination.cursor_param] = cursor
            elif pagination.type == "offset" and pagination.page_param:
                call_query[pagination.page_param] = str(page * pagination.page_size)
            elif pagination.type == "page" and pagination.page_param:
                call_query[pagination.page_param] = str(page + 1)
            if pagination.size_param:
                call_query[pagination.size_param] = str(pagination.page_size)

            await throttle.wait(url)
            target = str(httpx.URL(url, params=call_query)) if call_query else url
            response = await _get(client, target, headers=headers,
                                  max_bytes=config.limits.max_bytes_per_item)
            if response.status_code == 401 or response.status_code == 403:
                # Distinguished from other failures upstream: an auth failure
                # must fail the run rather than end it quietly as complete,
                # because a quiet completion advances the watermark.
                raise CrawlerError(f"source returned {response.status_code}", status=401)
            if response.status_code == 429:
                # Surfaced as its own error so the caller can record it against
                # the credential rather than the crawler. `Retry-After` is the
                # source saying exactly when it will answer again; inventing a
                # shorter backoff is how a cooling token becomes a banned one.
                raise RateLimited(
                    f"{response.request.url.host} refused: rate limited",
                    retry_after=_retry_after(response.headers),
                )
            if response.status_code >= 400:
                raise CrawlerError(f"source returned {response.status_code}", status=502)

            try:
                payload = response.json()
            except ValueError as exc:
                raise CrawlerError("source did not return JSON", status=502) from exc

            items = _search(config.extract.items_path, payload)
            if items is None:
                items = []
            if isinstance(items, dict):
                items = [items]
            if not isinstance(items, list):
                items = [items]

            for item in items:
                if budget.exhausted():
                    break
                if config.filter_include and not _search(config.filter_include, item):
                    continue
                found.append(_map_item(config, item))
                budget.take()

            page += 1
            checkpoint["page"] = page
            if not items:
                break
            if pagination.stop_when and _search(pagination.stop_when, payload):
                break
            if pagination.type == "cursor":
                cursor = _search(pagination.cursor_path, payload)
                checkpoint["cursor"] = cursor
                if not cursor:
                    break
            elif pagination.type == "next_url":
                # The source hands back the whole next URL rather than a token
                # to put in a parameter. Salesforce does this (`nextRecordsUrl`,
                # and as a *path*), so does Graph (`@odata.nextLink`, absolute).
                # Templating it was impossible, so those entries simply pulled
                # one page and said so in a note -- a crawl that reports a
                # plausible number of items and silently omits the rest.
                nxt = _as_text(_search(pagination.cursor_path, payload))
                if not nxt:
                    break
                url, call_query = _next_url(url, nxt), {}
                # The query went into the URL the source gave us; sending it
                # again would re-apply `q=` on top of a continuation that
                # already encodes it.
                query = {}
            elif pagination.type == "link_header":
                link = response.headers.get("link", "")
                match = re.search(r'<([^>]+)>;\s*rel="next"', link)
                if not match:
                    break
                url, call_query = _next_url(url, match.group(1)), {}
            elif pagination.type == "none":
                break

    return found


def _next_url(current: str, candidate: str) -> str:
    """Resolve a next-page URL that came out of a response, and refuse a move.

    This value is **untrusted**: it arrives in a body or a `Link` header from
    the source being crawled. Following it blindly would let any source it is
    pointed at redirect an authenticated crawler -- carrying the connection's
    credential in its headers -- at a host of the source's choosing. So the
    resolved URL must stay on the origin the run started against; a source that
    genuinely pages across hosts is a source this cannot crawl, which is the
    right way round.
    """
    resolved = urljoin(current, candidate)
    here, there = urlparse(current), urlparse(resolved)
    if (there.scheme, there.hostname, there.port) != (here.scheme, here.hostname, here.port):
        raise CrawlerError(
            f"source paged to a different origin ({there.scheme}://{there.netloc}); "
            "refusing to follow it with the connection's credential",
            status=502,
        )
    return validate_url(resolved)


def _map_item(config: CrawlerConfig, item: Any) -> Discovered:
    extract = config.extract
    external_id = _as_text(_search(extract.id_path, item)) if extract.id_path else None
    if not external_id:
        # Deterministic, so a re-crawl of a source with no id still upserts
        # rather than duplicating.
        external_id = hashlib.sha256(
            json.dumps(item, sort_keys=True, default=str).encode()
        ).hexdigest()[:24]
    fields: dict[str, Any] = {}
    for transform in config.transform:
        fields[transform.target] = _search(transform.expr, item)
    text = _as_text(_search(extract.content_path, item)) if extract.content_path else None
    if text is None:
        text = json.dumps(item, ensure_ascii=False, indent=2, default=str)
    return Discovered(
        external_id=external_id,
        title=_as_text(_search(extract.title_path, item)),
        text=text,
        url=_as_text(_search(extract.url_path, item)),
        version=_as_text(_search(extract.version_path, item)),
        fields=fields,
    )


_ENTRY = re.compile(r"<(?:item|entry)[\s>].*?</(?:item|entry)>", re.S | re.I)
_URL_ENTRY = re.compile(r"<url>.*?</url>", re.S | re.I)


def _tag(block: str, *names: str) -> str | None:
    for name in names:
        match = re.search(rf"<{name}[^>]*>(.*?)</{name}>", block, re.S | re.I)
        if match:
            value = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", match.group(1), flags=re.S)
            return re.sub(r"<[^>]+>", "", value).strip()
        # Atom links carry the URL in an attribute rather than a body.
        match = re.search(rf'<{name}[^>]*href="([^"]+)"', block, re.I)
        if match:
            return match.group(1).strip()
    return None


async def discover_feed(
    config: CrawlerConfig, *, watermark: str | None, budget: Budget,
    throttle: Throttle, checkpoint: dict, auth: Auth | None = None,
) -> list[Discovered]:
    """RSS, Atom and sitemaps -- an index someone already maintains.

    Parsed with regex rather than an XML parser deliberately: feeds in the wild
    are frequently not well-formed, and a strict parser turns a slightly broken
    feed into a crawler that reports zero items forever. An XML parser would
    also be an XXE surface pointed at untrusted documents.
    """
    found: list[Discovered] = []
    headers = {"User-Agent": config.politeness.user_agent}
    async with httpx.AsyncClient(timeout=30.0) as client:
        for seed in config.seeds:
            if budget.exhausted():
                break
            await throttle.wait(seed)
            response = await _get(client, seed, headers=headers,
                                  max_bytes=config.limits.max_bytes_per_item)
            if response.status_code >= 400:
                raise CrawlerError(f"feed returned {response.status_code}", status=502)
            body = response.text
            blocks = _ENTRY.findall(body) or _URL_ENTRY.findall(body)
            for block in blocks:
                if budget.exhausted():
                    break
                link = _tag(block, "link", "loc", "guid", "id")
                if not link:
                    continue
                title = _tag(block, "title")
                summary = _tag(block, "description", "summary", "content")
                published = _tag(block, "pubDate", "updated", "lastmod", "published")
                if config.incremental == "watermark" and watermark and published:
                    # A feed is ordered by recency, so anything at or before
                    # the watermark has been seen.
                    if published <= watermark:
                        continue
                found.append(Discovered(
                    external_id=link,
                    title=title,
                    text="\n\n".join(p for p in [title, summary] if p) or link,
                    url=link,
                    version=published,
                ))
                budget.take()
    return found


_HREF = re.compile(r'href=["\']([^"\'#]+)', re.I)

# A page is a document. Stylesheets, scripts, fonts and images are linked from
# every page on a site, and a `text/` prefix check waves `text/css` straight
# through -- which spends the crawl budget on stylesheets and stores them as
# records. Both ends are checked: the extension before fetching, so the budget
# is never spent at all, and the content type after, because a URL's extension
# is a hint rather than a fact.
DOCUMENT_TYPES = ("text/html", "application/xhtml", "text/plain", "text/markdown")
ASSET_SUFFIXES = (
    ".css", ".js", ".mjs", ".map", ".ico", ".png", ".jpg", ".jpeg", ".gif",
    ".svg", ".webp", ".avif", ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp4", ".webm", ".mp3", ".wav", ".zip", ".gz", ".tgz", ".pdf",
)


def looks_like_an_asset(url: str) -> bool:
    path = (urlparse(url).path or "").lower()
    return path.endswith(ASSET_SUFFIXES)


class Robots:
    """robots.txt, cached per origin for the run.

    Honouring it is not configurable. It is an obligation, and separately it is
    the cheapest way to not get blocked.
    """

    def __init__(self, user_agent: str) -> None:
        self.user_agent = user_agent
        self._parsers: dict[str, RobotFileParser | None] = {}
        self._delays: dict[str, float] = {}

    async def load(self, client: httpx.AsyncClient, url: str) -> None:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._parsers:
            return
        parser = RobotFileParser()
        try:
            response = await client.get(f"{origin}/robots.txt",
                                        headers={"User-Agent": self.user_agent},
                                        timeout=10.0)
            if response.status_code == 200:
                parser.parse(response.text.splitlines())
            else:
                # No robots.txt means no restriction, which is the standard
                # reading -- not an excuse to skip the lookup.
                parser.parse([])
        except httpx.HTTPError:
            # Unreachable robots.txt is treated as disallowing nothing but is
            # still rate-limited by the default delay. Refusing to crawl on a
            # transient robots failure would be its own kind of wrong.
            parser.parse([])
        self._parsers[origin] = parser
        delay = None
        try:
            delay = parser.crawl_delay(self.user_agent)
        except Exception:
            delay = None
        self._delays[origin] = float(delay or 0.0)

    def allows(self, url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        parser = self._parsers.get(origin)
        if parser is None:
            return True
        return parser.can_fetch(self.user_agent, url)

    def delay(self, url: str) -> float:
        parsed = urlparse(url)
        return self._delays.get(f"{parsed.scheme}://{parsed.netloc}", 0.0)


def in_scope(config: CrawlerConfig, url: str) -> bool:
    """An allowlist. A blocklist would be escaped by the first outbound link."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if config.allow_hosts and host not in {h.lower() for h in config.allow_hosts}:
        return False
    if config.allow_path_prefix and not parsed.path.startswith(config.allow_path_prefix):
        return False
    return True


async def discover_traverse(
    config: CrawlerConfig, *, watermark: str | None, budget: Budget,
    throttle: Throttle, checkpoint: dict, auth: Auth | None = None,
) -> list[Discovered]:
    """Breadth-first link following, inside a declared allowlist.

    Breadth-first rather than depth-first so that a max_depth cut leaves a
    complete shallow crawl instead of one deep arbitrary path.
    """
    from selectolax.parser import HTMLParser

    robots = Robots(config.politeness.user_agent)
    headers = {"User-Agent": config.politeness.user_agent}
    seen: set[str] = set(checkpoint.get("visited") or [])
    queue: list[tuple[str, int]] = [(s, 0) for s in config.seeds if s not in seen]
    found: list[Discovered] = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        while queue:
            if budget.exhausted():
                break
            url, depth = queue.pop(0)
            if url in seen or not in_scope(config, url) or looks_like_an_asset(url):
                continue
            seen.add(url)
            try:
                await robots.load(client, url)
            except FetchError:
                continue
            if not robots.allows(url):
                # Counted, not silent. "The crawler found nothing" and "the
                # site told us not to look" are different problems with
                # different fixes, and they are indistinguishable in a bare
                # discovery count.
                record("crawl_robots_denied", 1, host=(urlparse(url).hostname or ""))
                continue
            await throttle.wait(url, extra_delay=max(
                robots.delay(url), config.politeness.min_delay_seconds))
            try:
                response = await _get(client, url, headers=headers,
                                      max_bytes=config.limits.max_bytes_per_item)
            except (FetchError, httpx.HTTPError):
                # One unreachable page does not fail a crawl of four hundred.
                continue
            if response.status_code >= 400:
                continue
            content_type = (response.headers.get("content-type") or "").split(";")[0].strip()
            if content_type and not content_type.startswith(DOCUMENT_TYPES):
                continue

            body = response.text
            tree = HTMLParser(body)
            title_node = tree.css_first("title")
            text = tree.body.text(separator="\n", strip=True) if tree.body else body
            found.append(Discovered(
                external_id=url,
                title=title_node.text(strip=True) if title_node else None,
                text=text,
                url=url,
                version=response.headers.get("etag")
                or response.headers.get("last-modified"),
                depth=depth,
            ))
            budget.take()
            checkpoint["visited"] = sorted(seen)

            if depth < config.limits.max_depth:
                for href in _HREF.findall(body):
                    link = urljoin(url, href)
                    if (link not in seen and in_scope(config, link)
                            and not looks_like_an_asset(link)):
                        queue.append((link, depth + 1))
    return found


# --------------------------------------------------------------- tree

# A folder is a folder in two different vocabularies.
DRIVE_FOLDER = "application/vnd.google-apps.folder"


def _permitted(mime: str | None, prefixes: list[str]) -> bool:
    if not prefixes:
        return True
    return any((mime or "").startswith(p) for p in prefixes)


async def discover_tree(
    config: CrawlerConfig, *, watermark: str | None, budget: Budget,
    throttle: Throttle, checkpoint: dict, auth: Auth | None = None,
) -> list[Discovered]:
    """Walk a document library, breadth-first, and reference what it holds.

    This is the strategy the Drive and SharePoint catalog entries had to admit
    they were missing: listing one folder is one request, and a shared drive is
    a tree. It is bounded by `max_depth` like `traverse` is, and by the same
    budget as everything else -- a drive nobody has looked at in three years is
    exactly where an unbounded walk finds forty thousand files.

    **It discovers references, not documents.** A listing returns a name, an id
    and a modified time; the bytes are a second request per file. Emitting them
    as `Pending` puts that request in the fetch worker, where the byte cap, the
    blob store and the parse pipeline already are -- rather than downloading a
    hundred PDFs inside a discovery pass that is holding a run open.
    """
    assert config.tree is not None
    tree = config.tree
    if auth is None:
        # Neither API has an anonymous mode. Saying so beats a 401 that reads
        # as the source's problem.
        raise CrawlerError(
            f"a {tree.api} crawler needs a connection; neither Drive nor Graph "
            "answers without one", status=401,
        )

    found: list[Discovered] = []
    headers = {"User-Agent": config.politeness.user_agent, "Accept": "application/json"}
    headers.update(auth.headers)

    # Breadth-first so a shallow, wide drive is not exhausted by one deep
    # branch when the budget runs out.
    queue: deque[tuple[str, int]] = deque([(tree.root, 0)])
    seen_folders: set[str] = set(checkpoint.get("folders") or [])

    async with httpx.AsyncClient(timeout=30.0) as client:
        while queue:
            if budget.exhausted():
                break
            folder, depth = queue.popleft()
            if folder in seen_folders:
                continue          # a shortcut in Drive can make the tree a graph
            seen_folders.add(folder)

            async for entry in _children(client, tree, folder, headers=headers,
                                         auth=auth, throttle=throttle,
                                         config=config):
                if budget.exhausted():
                    break
                child_id, name, mime, url, modified, is_folder, ref = entry
                if is_folder:
                    if depth < config.limits.max_depth:
                        queue.append((child_id, depth + 1))
                    continue
                if not _permitted(mime, tree.include_mime):
                    continue
                if config.incremental == "watermark" and watermark and modified:
                    if modified <= watermark:
                        continue
                found.append(Discovered(
                    external_id=f"{tree.api}:{child_id}",
                    title=name,
                    url=url,
                    version=modified,
                    depth=depth,
                    pending={"provider": tree.api, "resource_id": ref,
                             "hints": {"name": name, "mime_type": mime}},
                ))
                budget.take()
            checkpoint["folders"] = sorted(seen_folders)

    return found


async def _children(client, tree: Tree, folder: str, *, headers, auth,
                    throttle, config):
    """One folder's entries, paged, normalised across the two APIs.

    A generator so the budget is checked between pages rather than after the
    whole folder has been read -- a folder with nine thousand files in it should
    cost one page, not nine thousand records.
    """
    if tree.api == "google_drive":
        url = "https://www.googleapis.com/drive/v3/files"
        params = {
            "q": f"'{folder}' in parents and trashed = false",
            "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,webViewLink)",
            "pageSize": "100",
        }
    else:
        base = tree.root.strip("/")
        # The root of the walk is the drive itself; anything deeper is an item
        # in it. Both are paths under the same drive, which is what makes the
        # item's download URL constructible later.
        path = f"{base}/root/children" if folder == tree.root \
            else f"{base}/items/{folder}/children"
        url = f"https://graph.microsoft.com/v1.0/{path}"
        params = {"$top": "100", "$select":
                  "id,name,file,folder,webUrl,lastModifiedDateTime,parentReference"}
    params.update(auth.query)

    while url:
        await throttle.wait(url)
        target = str(httpx.URL(url, params=params)) if params else url
        response = await _get(client, target, headers=headers,
                              max_bytes=config.limits.max_bytes_per_item)
        if response.status_code in (401, 403):
            # Fails the run rather than ending it quietly: a quiet completion
            # advances the watermark past files that were never read.
            raise CrawlerError(f"source returned {response.status_code}", status=401)
        if response.status_code == 404:
            # A folder that has been deleted mid-walk is not a failed crawl.
            return
        if response.status_code >= 400:
            raise CrawlerError(f"source returned {response.status_code}", status=502)
        try:
            payload = response.json()
        except ValueError as exc:
            raise CrawlerError("source did not return JSON", status=502) from exc

        if tree.api == "google_drive":
            for f in payload.get("files") or []:
                mime = f.get("mimeType")
                yield (f.get("id") or "", f.get("name"), mime,
                       f.get("webViewLink"), f.get("modifiedTime"),
                       mime == DRIVE_FOLDER, f.get("id") or "")
            token = payload.get("nextPageToken")
            if not token:
                return
            params = {**params, "pageToken": token}
        else:
            for f in payload.get("value") or []:
                drive = (f.get("parentReference") or {}).get("driveId") or ""
                item = f.get("id") or ""
                yield (item, f.get("name"), (f.get("file") or {}).get("mimeType"),
                       f.get("webUrl"), f.get("lastModifiedDateTime"),
                       f.get("folder") is not None,
                       f"drives/{drive}/items/{item}" if drive else "")
            url, params = payload.get("@odata.nextLink"), {}
            if not url:
                return


STRATEGIES = {
    "http": discover_http,
    "feed": discover_feed,
    "traverse": discover_traverse,
    "tree": discover_tree,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def discover(
    config: CrawlerConfig, *, watermark: str | None, checkpoint: dict,
    auth: "Auth | None" = None,
) -> tuple[list[Discovered], Budget, str | None]:
    budget = Budget(config.limits)
    throttle = Throttle(config.limits.rate_per_sec)
    with span("crawl.discover", strategy=config.strategy,
              incremental=config.incremental,
              authenticated=auth is not None) as current:
        found = await STRATEGIES[config.strategy](
            config, watermark=watermark, budget=budget,
            throttle=throttle, checkpoint=checkpoint, auth=auth,
        )
        current.set_attribute("discovered", len(found))
    return found, budget, budget.exhausted()


def next_watermark(config: CrawlerConfig, found: list[Discovered],
                   current: str | None) -> str | None:
    """The high-water mark across what was actually discovered.

    Taking the max of what was seen -- rather than 'now' -- means a source
    whose clock differs from ours does not cause a silent gap.
    """
    if config.incremental != "watermark":
        return current
    versions = [d.version for d in found if d.version]
    if not versions:
        return current
    highest = max(versions)
    return max(highest, current) if current else highest


