"""Turning a researcher's profile into a crawlable identity.

One failure dominates this file and it is not a crash. **Two researchers share
a name, the wrong OpenAlex author is chosen, and the corpus that comes back is
entirely coherent and about somebody else** -- every record real, every citation
checkable, the whole thing wrong. Nothing downstream can detect it, and nothing
about the result looks like an error.

So most of what is asserted here is a refusal: no titles to confirm against, not
enough overlap, a profile the model could not read. An empty answer is
recoverable; a plausible wrong one is not.
"""

from __future__ import annotations

import httpx
import pytest

from open_mem.crawlers import CrawlerConfig, _from_inverted_index, validate_config
from open_mem.scholar import (
    Author,
    Profile,
    ScholarError,
    profile_id,
    read_profile,
    resolve_author,
    works_crawler,
)
from open_mem.urlcontext import Read, UrlNotRead

pytestmark = pytest.mark.asyncio

PROFILE = "https://scholar.google.com/citations?user=qc6CJjYAAAAJ&hl=en"

READING = {
    "name": "Ada Lovelace",
    "affiliation": "Analytical Engine Institute",
    "titles": [
        "Notes on the Analytical Engine",
        "On the Bernoulli Number Algorithm",
        "Sketch of the Analytical Engine Invented by Charles Babbage",
    ],
}


class Reader:
    """Answers in the declared shape, as the real reader now does."""

    enabled = True
    model_id = "fake"

    def __init__(self, reading: dict | None = READING) -> None:
        self._reading = reading

    async def read_structured(self, url: str, *, schema: dict, prompt: str) -> dict:
        if self._reading is None:
            raise UrlNotRead("the page could not be retrieved")
        return dict(self._reading)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ------------------------------------------------------------------ the URL

@pytest.mark.parametrize("url", [
    "https://scholar.google.com/citations?user=qc6CJjYAAAAJ",
    "scholar.google.co.uk/citations?hl=en&user=qc6CJjYAAAAJ",
])
async def test_a_profile_url_is_recognised(url):
    assert profile_id(url) == "qc6CJjYAAAAJ"


@pytest.mark.parametrize("url", [
    "https://scholar.google.com/citations",          # no user
    "https://example.com/citations?user=abc12",      # not scholar
    "https://scholar.google.com/scholar?q=lovelace",  # a search, not a profile
])
async def test_anything_else_is_refused(url):
    with pytest.raises(ScholarError):
        profile_id(url)


# -------------------------------------------------------------- the reading

async def test_the_profile_is_read_into_an_identity():
    profile = await read_profile(Reader(), PROFILE)
    assert profile.name == "Ada Lovelace"
    assert profile.affiliation == "Analytical Engine Institute"
    assert len(profile.titles) == 3
    assert "Notes on the Analytical Engine" in profile.titles


async def test_a_reading_with_no_name_is_refused():
    """The shape can be right and the content empty -- which is what the prompt
    asks for when the page could not be read. An empty name is not a researcher
    called nothing; it is a page that was not read."""
    with pytest.raises(ScholarError) as caught:
        await read_profile(Reader(reading={"name": "", "titles": []}), PROFILE)
    assert caught.value.status == 502


async def test_the_profile_is_asked_for_a_shape_not_for_prose():
    """Asked for prose this returned markdown headings, and the parser took
    `### Overview` for a person's name. A model asked for a shape produces the
    shape; parsing its prose is a guess at a format never promised."""
    from open_mem.scholar import PROFILE_SCHEMA

    seen = {}

    class Recording(Reader):
        async def read_structured(self, url, *, schema, prompt):  # noqa: ANN001
            seen["schema"] = schema
            seen["prompt"] = prompt
            return dict(READING)

    await read_profile(Recording(), PROFILE)
    assert seen["schema"] is PROFILE_SCHEMA
    assert "titles" in seen["schema"]["required"]
    # Titles are emitted before anything unbounded, or a long affiliation
    # spends the budget they needed.
    order = seen["schema"]["propertyOrdering"]
    assert order.index("titles") == len(order) - 1
    assert "not answer from what you already know".replace("not ", "") in seen["prompt"].lower()


async def test_a_profile_that_could_not_be_read_produces_nothing():
    """Refused, never invented. Asked about a page it cannot reach a model
    answers anyway and fluently, and the prose does not say which happened."""
    with pytest.raises(ScholarError) as caught:
        await read_profile(Reader(reading=None), PROFILE)
    assert caught.value.status == 502


async def test_url_context_being_off_is_a_configuration_answer():
    class Off:
        enabled = False

    with pytest.raises(ScholarError) as caught:
        await read_profile(Off(), PROFILE)
    assert caught.value.status == 503


# ------------------------------------------------------------ the resolution

def _openalex(candidates, titles_by_author):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/authors":
            return httpx.Response(200, json={"results": candidates})
        author = request.url.params["filter"].split(":", 1)[1]
        return httpx.Response(200, json={
            "results": [{"display_name": t} for t in titles_by_author.get(author, [])]})
    return handler


A_WRONG = {"id": "https://openalex.org/A111", "display_name": "Ada Lovelace",
           "works_count": 900}
A_RIGHT = {"id": "https://openalex.org/A222", "display_name": "Ada Lovelace",
           "works_count": 12,
           # The current spelling. The singular `last_known_institution` is
           # still present on real records as null, so reading that one returns
           # None rather than raising and the affiliation just vanishes.
           "last_known_institutions": [{"display_name": "Analytical Engine Institute"}]}


async def test_the_author_confirmed_by_their_papers_wins():
    """Not the first hit. Search orders candidates by how prolific they are, and
    taking the top one is exactly how the wrong author of a common name is
    picked -- the busier stranger outranks the person you meant."""
    profile = await read_profile(Reader(), PROFILE)
    handler = _openalex([A_WRONG, A_RIGHT], {
        "A111": ["Something Else Entirely", "A Paper On Turbines"],
        "A222": ["Notes on the Analytical Engine",
                 "On the Bernoulli Number Algorithm"],
    })
    async with _client(handler) as client:
        author = await resolve_author(profile, client=client)

    assert author.author_id == "A222"
    assert len(author.matched_on) == 2
    assert author.affiliation == "Analytical Engine Institute"


async def test_one_shared_title_is_not_enough():
    """A single overlap is a co-authored paper, which is how a collaborator gets
    mistaken for the author."""
    profile = await read_profile(Reader(), PROFILE)
    handler = _openalex([A_WRONG], {
        "A111": ["Notes on the Analytical Engine", "A Paper On Turbines"]})
    async with _client(handler) as client:
        with pytest.raises(ScholarError) as caught:
            await resolve_author(profile, client=client)
    assert caught.value.status == 409
    assert "guessing" in str(caught.value)


async def test_a_profile_with_no_papers_cannot_be_confirmed():
    """There is nothing to check a name against, so nothing is chosen."""
    profile = Profile(url=PROFILE, name="Ada Lovelace", titles=[])
    with pytest.raises(ScholarError):
        await resolve_author(profile)


async def test_no_candidate_at_all_is_a_404():
    profile = await read_profile(Reader(), PROFILE)
    async with _client(_openalex([], {})) as client:
        with pytest.raises(ScholarError) as caught:
            await resolve_author(profile, client=client)
    assert caught.value.status == 404


# ------------------------------------------------------------- the crawler

async def test_the_generated_crawler_is_a_valid_one():
    """Configuration, not a strategy. If this stops parsing, the claim that
    `discover_http` already covers OpenAlex has stopped being true."""
    author = Author(author_id="A222", display_name="Ada Lovelace", works_count=12)
    config = CrawlerConfig(**works_crawler(author, memory_key="ada")["config"])
    validate_config(config)

    assert config.strategy == "http"
    assert config.pagination.type == "cursor"
    assert config.pagination.cursor_path == "meta.next_cursor"
    # The two lines that make a paper arrive as a paper rather than a row
    # about one, and an abstract arrive as prose rather than as a wall of JSON.
    assert config.extract.pending_path == "best_oa_location.pdf_url"
    assert config.extract.content_format == "inverted_index"


async def test_a_work_with_a_pdf_becomes_something_to_download():
    from open_mem.crawlers import _map_item

    author = Author(author_id="A222", display_name="Ada", works_count=1)
    config = CrawlerConfig(**works_crawler(author, memory_key="ada")["config"])
    found = _map_item(config, {
        "id": "https://openalex.org/W1", "display_name": "Notes",
        "best_oa_location": {"pdf_url": "https://arxiv.org/pdf/1234.pdf"},
    })
    assert found.pending["provider"] == "url"
    assert found.pending["resource_id"] == "https://arxiv.org/pdf/1234.pdf"


async def test_a_paywalled_work_is_still_a_record():
    """Most published work is not open access. A corpus that silently dropped it
    would answer questions about a body of work while missing most of it."""
    from open_mem.crawlers import _map_item

    author = Author(author_id="A222", display_name="Ada", works_count=1)
    config = CrawlerConfig(**works_crawler(author, memory_key="ada")["config"])
    found = _map_item(config, {
        "id": "https://openalex.org/W2", "display_name": "A Closed Paper",
        "best_oa_location": None,
        "abstract_inverted_index": {"An": [0], "important": [1], "result": [2]},
        "publication_year": 2019,
    })
    assert found.pending is None
    assert found.title == "A Closed Paper"
    assert found.text == "An important result"
    assert found.fields["year"] == 2019


# ------------------------------------------------------- the inverted index

async def test_an_inverted_index_becomes_the_sentence_it_was_made_from():
    assert _from_inverted_index(
        {"the": [0, 4], "cat": [1], "sat": [2], "on": [3], "mat": [5]}
    ) == "the cat sat on the mat"


async def test_a_gap_is_left_as_a_gap():
    """Positions can be sparse. Joining what is present is the honest
    reconstruction; inserting a guessed word would put a claim in an abstract
    that the source never made."""
    assert _from_inverted_index({"alpha": [0], "omega": [4]}) == "alpha omega"


@pytest.mark.parametrize("value", [None, {}, "not an index", {"w": "not a list"}])
async def test_nothing_to_rebuild_is_nothing(value):
    """None rather than an empty string, so the caller's existing "no content"
    path runs instead of a blank record being written."""
    assert _from_inverted_index(value) is None


async def test_a_work_with_no_abstract_is_its_title_not_its_json():
    """Found on real data: OpenAlex omits abstracts it may index but not
    redistribute, so some of the most-cited papers have none. The generic
    fallback dumped the whole API response as the record's text — a wall of
    metadata JSON that retrieval scores against and no reader can use, on
    exactly the records that were already thinnest."""
    from open_mem.crawlers import _map_item

    author = Author(author_id="A222", display_name="Ada", works_count=1)
    config = CrawlerConfig(**works_crawler(author, memory_key="ada")["config"])
    found = _map_item(config, {
        "id": "https://openalex.org/W3", "display_name": "Deep learning",
        "abstract_inverted_index": None, "publication_year": 2015,
        "primary_location": {"source": {"display_name": "Nature"}},
    })
    assert found.text == "Deep learning"
    assert "openalex.org" not in (found.text or "")
    # The rest of what is known about it is still on the record.
    assert found.fields["venue"] == "Nature"
    assert found.fields["year"] == 2015


async def test_the_affiliation_survives_the_field_rename():
    """OpenAlex moved it to `last_known_institutions`. The singular name is
    still on the record as null, so reading it returns None rather than raising
    — the affiliation silently disappears while everything else about the
    resolution looks correct."""
    from open_mem.scholar import _institution

    assert _institution({"last_known_institutions": [{"display_name": "Mila"}]}) == "Mila"
    assert _institution({"last_known_institution": {"display_name": "Older"}}) == "Older"
    # The shape a real record has: the old key present and empty.
    assert _institution({"last_known_institution": None,
                         "last_known_institutions": [{"display_name": "Mila"}]}) == "Mila"
    assert _institution({"last_known_institution": None}) is None


async def test_a_title_listed_twice_is_shown_once():
    """A profile can list one title for two works — "Deep learning" is both a
    Nature paper and a book. Repeating it as evidence reads as a bug in the
    matching rather than as two real works."""
    profile = Profile(url=PROFILE, name="Ada Lovelace", titles=[
        "Deep learning", "Deep learning", "On the Bernoulli Number Algorithm"])
    handler = _openalex([A_RIGHT], {
        "A222": ["Deep learning", "On the Bernoulli Number Algorithm"]})
    async with _client(handler) as client:
        author = await resolve_author(profile, client=client)
    assert author.matched_on == ["Deep learning", "On the Bernoulli Number Algorithm"]


async def test_a_downloadable_paper_is_marked_as_a_file():
    """So the fetch path knows it is a document rather than a page. A memory
    that reads pages with the model would otherwise hand a PDF's URL to the
    model and store the reading instead of the paper."""
    from open_mem.crawlers import _map_item

    author = Author(author_id="A222", display_name="Ada", works_count=1)
    config = CrawlerConfig(**works_crawler(author, memory_key="ada")["config"])
    found = _map_item(config, {
        "id": "https://openalex.org/W1", "display_name": "Notes",
        "best_oa_location": {"pdf_url": "https://arxiv.org/pdf/1234.pdf"}})
    assert found.pending["hints"] == {"kind": "file"}


async def test_papers_do_not_land_in_a_memory_that_never_enriches():
    """`default` does not enrich, so every paper would sit at `stored` and the
    corpus would be unsearchable while looking complete."""
    from open_mem.scholar import PAPERS_TYPE

    author = Author(author_id="A222", display_name="Ada", works_count=1)
    spec = works_crawler(author, memory_key="ada")
    assert spec["config"]["memory_type"] == PAPERS_TYPE != "default"
