"""Point at a researcher, get a crawlable identity.

A person has a Scholar profile URL. What is needed to ingest their work is an
OpenAlex author id. This is the bridge, and it is two steps because each one is
good at something the other is not.

**Scholar is the entry point.** It is the URL somebody actually has, and its
page carries the things that identify *which* researcher is meant: the
affiliation, the co-authors, and the exact titles they claim. It is rendered by
JavaScript, so it is read with URL Context -- the model fetches it from Google's
own infrastructure rather than from this deployment, which is why this works at
all. A reading is accepted only when the retrieval status says the page was
actually retrieved; asked about a page it could not reach, a model answers
anyway and fluently, and the text does not say which happened.

**OpenAlex is the resolver.** It has a real API, no key, stable cursor
pagination, and it knows where the open-access PDF of each work lives. Scholar's
rendered page gives you the first twenty titles; OpenAlex gives you all of them.

**The failure that matters is the name collision.** Two researchers share a
name, the wrong author id is chosen, and the corpus that comes back is entirely
coherent and about the wrong person -- every record real, every citation
checkable, the whole thing wrong. Nothing downstream can detect that. So a match
is confirmed against titles the profile actually listed, and an unconfirmed one
is refused rather than guessed: an empty result is recoverable and a plausible
wrong one is not.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

OPENALEX = "https://api.openalex.org"
# OpenAlex asks callers to identify themselves and gives the polite pool faster,
# more reliable service for it. Cheap manners with a real payoff.
MAILTO = "open-mem@buildgeek.ai"
RESOLVE_TIMEOUT_SECONDS = 30.0

# `scholar.google.com/citations?user=<id>`, and the id is what identifies the
# profile. Matched rather than parsed loosely: this string is put into a prompt
# and into an HTTP request, and being generous here buys nothing.
SCHOLAR_PROFILE = re.compile(
    r"^(?:https?://)?scholar\.google\.[a-z.]{2,6}/citations\?(?:.*&)?user=([A-Za-z0-9_-]{5,32})"
)

# How many of the profile's titles have to turn up in a candidate's work for the
# match to be believed. Two, not one: a single shared title is a co-authored
# paper, which is exactly how the wrong author of a common name gets picked.
CONFIRMING_TITLES = 2


class ScholarError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Profile:
    """What the profile page said, before anything has been resolved."""

    url: str
    name: str
    affiliation: str | None = None
    titles: list[str] = field(default_factory=list)


@dataclass
class Author:
    """A resolved OpenAlex author, and what the resolution rested on."""

    author_id: str
    display_name: str
    works_count: int
    affiliation: str | None = None
    orcid: str | None = None
    # The titles that confirmed it. Recorded because "we matched on the name"
    # and "we matched on the name and three of their papers" are different
    # degrees of confidence, and only one of them survives a common surname.
    matched_on: list[str] = field(default_factory=list)


def profile_id(url: str) -> str:
    match = SCHOLAR_PROFILE.match(url.strip())
    if match is None:
        raise ScholarError(
            "not a Google Scholar profile URL -- expected "
            "https://scholar.google.com/citations?user=...")
    return match.group(1)


def _normalise(title: str) -> str:
    """Titles are compared on their words, not their punctuation.

    Two records of one paper differ by a colon, a subtitle, an en dash, or a
    trailing period far more often than they differ in substance.
    """
    return " ".join(re.findall(r"[a-z0-9]+", title.lower()))


PROFILE_SCHEMA = {
    "type": "object",
    "required": ["name", "titles"],
    # Order matters for the same reason it does in `extraction.py`: a
    # schema-constrained model emits in this order and the output budget is
    # finite, so the titles must not sit behind anything unbounded.
    "propertyOrdering": ["name", "affiliation", "titles"],
    "properties": {
        "name": {"type": "string",
                 "description": "The researcher's full name exactly as the "
                                "profile shows it. Empty if the page could not "
                                "be read."},
        "affiliation": {"type": "string",
                        "description": "Their institution as shown, or empty."},
        "titles": {"type": "array", "items": {"type": "string", "maxLength": 300},
                   "description": "Publication titles listed on the profile, "
                                  "verbatim, without years, venues or citation "
                                  "counts."},
    },
}

PROFILE_PROMPT = (
    "Read this Google Scholar profile and report the researcher's name, their "
    "affiliation, and the titles of the publications listed on it. Titles "
    "verbatim as shown, without years, venues or citation counts.\n\n"
    "If you cannot retrieve the page, return an empty name and no titles. Do "
    "not answer from what you already know about this person: a list assembled "
    "from memory is indistinguishable from one that was read, and it is worse "
    "than no list at all.\n\nURL: "
)


async def read_profile(reader, url: str) -> Profile:
    """Read the profile page into a shape, not into prose.

    Asked for prose this returned markdown -- `### Overview and Metadata`, then
    `* **Document:** ...` bullets -- and the parser looking for `Name:` lines
    took a heading as the researcher's name and resolved against `'### Overview'`.
    That is not a regex to improve. A model asked for a shape produces the
    shape, and every parse of a model's prose is a guess at a format that was
    never promised.
    """
    from .urlcontext import UrlNotRead

    profile_id(url)  # refuses early, before a model call is paid for
    if not getattr(reader, "enabled", False):
        raise ScholarError(
            "reading a Scholar profile needs URL Context, which is off on this "
            "deployment", status=503)
    try:
        read = await reader.read_structured(
            url, schema=PROFILE_SCHEMA, prompt=f"{PROFILE_PROMPT}{url}")
    except UrlNotRead as exc:
        # Refused, never invented. A profile the model could not reach produces
        # nothing rather than a plausible researcher.
        raise ScholarError(f"the profile could not be read: {exc}", status=502) from exc

    name = (read.get("name") or "").strip()
    if not name:
        raise ScholarError(
            "the profile was read but no researcher name could be found on it",
            status=502)
    titles = [t.strip() for t in (read.get("titles") or []) if t and t.strip()]
    return Profile(url=url, name=name,
                   affiliation=(read.get("affiliation") or "").strip() or None,
                   titles=titles)


async def resolve_author(profile: Profile, *, client: httpx.AsyncClient | None = None
                         ) -> Author:
    """Find the OpenAlex author this profile is, or refuse.

    Searching by name returns candidates ordered by how prolific they are, and
    taking the first is how a corpus about the wrong person gets built. So each
    candidate's own works are checked against the titles the profile listed, and
    the first candidate confirmed by `CONFIRMING_TITLES` of them wins.
    """
    if not profile.titles:
        raise ScholarError(
            "the profile listed no papers, so there is nothing to confirm a "
            "match against -- refusing to pick an author by name alone")

    owns = httpx.AsyncClient(timeout=RESOLVE_TIMEOUT_SECONDS) if client is None else client
    try:
        candidates = await _search_authors(owns, profile.name)
        if not candidates:
            raise ScholarError(f"no OpenAlex author matches {profile.name!r}", status=404)

        wanted = {_normalise(t) for t in profile.titles}
        for candidate in candidates[:5]:
            # The bare id, not the full `https://openalex.org/A222` the search
            # returns. A filter given the URL matches nothing and returns an
            # empty page, which is indistinguishable from an author who happens
            # to share no titles -- so every candidate would be rejected and the
            # whole resolution would refuse, always, for the right-looking
            # reason.
            short = candidate["id"].rsplit("/", 1)[-1]
            titles = await _author_titles(owns, short)
            matched = sorted(wanted & titles)
            if len(matched) >= CONFIRMING_TITLES:
                return Author(
                    author_id=short,
                    display_name=candidate.get("display_name") or profile.name,
                    works_count=int(candidate.get("works_count") or 0),
                    affiliation=_institution(candidate) or profile.affiliation,
                    orcid=candidate.get("orcid"),
                    matched_on=_unique(
                        t for t in profile.titles if _normalise(t) in matched),
                )
        raise ScholarError(
            f"found {len(candidates)} authors named {profile.name!r} and could "
            f"confirm none of them against the papers on the profile. Refusing "
            f"rather than guessing: the wrong author produces a corpus that is "
            f"entirely coherent and about somebody else.",
            status=409)
    finally:
        if client is None:
            await owns.aclose()


def _institution(candidate: dict) -> str | None:
    """Where OpenAlex currently keeps the affiliation.

    It is `last_known_institutions`, a list. The singular
    `last_known_institution` was the older spelling and is still *present* on
    the record as null -- so reading it returns None rather than raising, and
    the affiliation silently disappears while everything else about the
    resolution looks right. Both are tried, newest first.
    """
    plural = candidate.get("last_known_institutions") or []
    if plural and isinstance(plural, list):
        name = (plural[0] or {}).get("display_name")
        if name:
            return name
    return (candidate.get("last_known_institution") or {}).get("display_name")


def _unique(titles) -> list[str]:
    """First occurrence wins, order kept.

    A profile can list one title for two works -- "Deep learning" is both a
    Nature paper and a book -- and repeating it as evidence reads as a bug in
    the matching rather than as two real works.
    """
    seen, out = set(), []
    for title in titles:
        key = _normalise(title)
        if key not in seen:
            seen.add(key)
            out.append(title)
    return out


async def _search_authors(client: httpx.AsyncClient, name: str) -> list[dict]:
    response = await client.get(
        f"{OPENALEX}/authors",
        params={"search": name, "per-page": 10, "mailto": MAILTO})
    response.raise_for_status()
    return response.json().get("results") or []


async def _author_titles(client: httpx.AsyncClient, author_id: str) -> set[str]:
    """One page of a candidate's most-cited work, normalised for comparison.

    Most-cited rather than most-recent: a Scholar profile lists what it lists in
    citation order too, so this is the overlap most likely to exist if the two
    are the same person.
    """
    response = await client.get(
        f"{OPENALEX}/works",
        params={"filter": f"author.id:{author_id}", "per-page": 50,
                "sort": "cited_by_count:desc", "select": "display_name",
                "mailto": MAILTO})
    response.raise_for_status()
    return {
        _normalise(work.get("display_name") or "")
        for work in (response.json().get("results") or [])
        if work.get("display_name")
    }


# The memory type a researcher's papers land in.
#
# Not `default`: papers are ingested to be asked questions about, and `default`
# does not enrich, so every paper would sit at `stored` and the corpus would be
# unsearchable while looking complete. Created with `enrich` on and `url_reader`
# left at `fetch` -- the PDFs are documents to download, not pages to read.
PAPERS_TYPE = "papers"


def works_crawler(author: Author, *, memory_key: str, memory_type: str = PAPERS_TYPE
                  ) -> dict:
    """The crawler config that pulls this author's papers.

    Configuration rather than a strategy: `discover_http` already does templated
    requests with cursor pagination and JMESPath extraction, which is exactly
    OpenAlex's shape. The only thing that had to be built was `pending_path`,
    so an open-access PDF becomes a file the fetch worker downloads rather than
    a URL sitting inertly in a metadata record.

    A work with no open-access PDF resolves `pending_path` to nothing and lands
    as its title, abstract and metadata. That is not a degraded case to be
    hidden: most published work is not open access, and a corpus that silently
    omitted it would answer questions about a body of work while missing most
    of it.
    """
    return {
        "name": f"papers-{author.author_id}",
        "strategy": "http",
        "config": {
            "name": f"{author.display_name} — papers",
            "strategy": "http",
            "memory_key": memory_key,
            "memory_type": memory_type,
            "tags": ["paper", f"openalex:{author.author_id}"],
            "incremental": "watermark",
            "request": {
                "method": "GET",
                "url": f"{OPENALEX}/works",
                "query": {
                    "filter": f"author.id:{author.author_id}",
                    "per-page": "100",
                    "mailto": MAILTO,
                },
            },
            "pagination": {
                "type": "cursor",
                "cursor_path": "meta.next_cursor",
                "cursor_param": "cursor",
                "page_size": 100,
                "max_pages": 50,
            },
            "extract": {
                "items_path": "results",
                "id_path": "id",
                "title_path": "display_name",
                "version_path": "updated_date",
                # Reconstructed from `{"word": [positions]}`; OpenAlex ships an
                # inverted index rather than the abstract itself.
                "content_path": "abstract_inverted_index",
                "content_format": "inverted_index",
                "url_path": "doi",
                # The whole reason a paper arrives as a paper rather than as a
                # row about one.
                "pending_path": "best_oa_location.pdf_url",
            },
            "transform": [
                {"target": "year", "expr": "publication_year"},
                {"target": "venue", "expr": "primary_location.source.display_name"},
                {"target": "doi", "expr": "doi"},
                {"target": "citations", "expr": "cited_by_count"},
                # So "which of these do we only have the abstract of" is a
                # question the corpus can answer about itself.
                {"target": "open_access", "expr": "open_access.is_oa"},
                {"target": "authors", "expr": "authorships[*].author.display_name"},
            ],
        },
    }
