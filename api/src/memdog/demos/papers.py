"""One researcher's published papers — the corpus that is not invented.

Every other entry in the gallery is written here as an invention. This one is a
real person's publication record, resolved from a public Google Scholar profile
the way `POST /api/v1/crawlers/from-scholar` resolves one: the profile names a
researcher, the researcher is matched in OpenAlex against papers the profile
actually lists, and the works come from OpenAlex rather than from Scholar.

**It fetches rather than carries a copy.** A hundred abstracts pasted into this
repository would be a second, ageing copy of somebody's record, and nothing here
would ever update it. The list is pulled at seed time from a public API with no
key, so what the demo answers from is what OpenAlex holds today.

**And it carries no synthetic marker.** The other three stamp *"generated, not
real"* into every record because they are. Stamping it across a real
researcher's abstracts would be the same failure in the other direction — a
false claim about work that exists — so `synthetic=False`, and the note under
the chat says whose work it is and where it came from instead.

Titles, abstracts, venues, years and DOIs only. The open-access PDFs are not
downloaded: the demo answers from what OpenAlex publishes about each paper, and
that is a metadata record rather than a redistribution of the papers.
"""

from __future__ import annotations

import httpx

from . import Corpus, Item, Question

# Resolved from https://scholar.google.com/citations?user=UBXqggoAAAAJ — the
# author id is recorded rather than the profile URL because it is the thing
# that is stable and the thing the match was confirmed against.
AUTHOR_ID = "A5104682589"
AUTHOR_NAME = "Manindra Agrawal"
AFFILIATION = "Indian Institute of Technology Kanpur"

OPENALEX = "https://api.openalex.org/works"
# OpenAlex asks for a contact in the query so heavy callers can be reached
# rather than blocked. Theirs is the polite pool.
MAILTO = "demo@memdog.dev"


def _abstract(inverted: dict | None) -> str:
    """OpenAlex ships `{"word": [positions]}`, not the abstract.

    An inverted index rather than text, because a publisher may license the
    index and not the prose. Reconstructing it is what the crawler's
    `content_format: inverted_index` does; this is the same operation, done
    here because this corpus does not go through a crawler.
    """
    if not inverted:
        return ""
    positions: list[tuple[int, str]] = []
    for word, spots in inverted.items():
        for spot in spots:
            positions.append((spot, word))
    return " ".join(word for _, word in sorted(positions))


async def fetch() -> list[Item]:
    """The author's works, as records. One page of a hundred is the corpus."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(OPENALEX, params={
            "filter": f"author.id:{AUTHOR_ID}",
            "per-page": "100",
            "mailto": MAILTO,
        })
        response.raise_for_status()
        works = response.json().get("results") or []

    items: list[Item] = []
    for work in works:
        title = (work.get("display_name") or "").strip()
        if not title:
            # A work with no title cannot be cited and cannot be recognised in
            # an answer, so it is not worth the tokens.
            continue
        year = work.get("publication_year")
        venue = (((work.get("primary_location") or {}).get("source") or {})
                 .get("display_name") or "")
        authors = [
            (a.get("author") or {}).get("display_name", "")
            for a in (work.get("authorships") or [])
        ]
        abstract = _abstract(work.get("abstract_inverted_index"))

        # Written as a record a reader recognises rather than as a JSON blob:
        # the answer quotes this text back, so it is what somebody will read.
        body = [title]
        if authors:
            body.append(", ".join(a for a in authors if a))
        if venue or year:
            body.append(" · ".join(str(p) for p in (venue, year) if p))
        if work.get("doi"):
            body.append(str(work["doi"]))
        if abstract:
            body.append("")
            body.append(abstract)

        items.append(Item(
            external_id=work["id"],
            text="\n".join(body),
            data_type="paper",
            source_type="openalex",
            # OpenAlex gives a year, not a date. Dating everything relative to
            # the reference point would put a 2004 paper in the last fortnight,
            # so age is derived from the publication year instead.
            days_ago=max(0, (2026 - int(year)) * 365) if year else 0,
            tags=("paper", f"openalex:{AUTHOR_ID}"),
            identifiers=(work["id"],),
        ))
    return items


CORPUS = Corpus(
    key="papers",
    title="A researcher's papers",
    blurb="100 works from one Google Scholar profile — the only corpus here that "
          "is real rather than invented.",
    note=f"{AUTHOR_NAME} ({AFFILIATION}), resolved from a public Google Scholar "
         "profile and fetched from OpenAlex. Real published work — titles, "
         "abstracts, venues and DOIs as OpenAlex holds them.",
    memory_key=f"papers-{AUTHOR_ID}",
    memory_type="papers",
    default_identifier=f"openalex:{AUTHOR_ID}",
    synthetic=False,
    items=(),
    fetch=fetch,
    questions=(
        # Named after its authors rather than after its title. "What is the
        # main result of PRIMES is in P?" is the question a person would ask,
        # and it retrieves the *errata* ahead of the paper -- two near-identical
        # short records, one of which has the abstract. A sample question that
        # depends on the embedder being good is a demo that breaks quietly on a
        # deployment configured differently.
        Question("What deterministic polynomial-time algorithm did Agrawal, "
                 "Kayal and Saxena give for primality?",
                 "https://openalex.org/W2159341052"),
        Question("What does this work say about arithmetic circuits and depth four?",
                 "https://openalex.org/W1530841022"),
        Question("What is polynomial identity testing, in this author's work?",
                 "https://openalex.org/W2618153707"),
    ),
)
