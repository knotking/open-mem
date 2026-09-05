"""Reading a page with Gemini's URL Context.

Almost every test here is about one thing: **the model answers whether or not
it reached the page.** The first live probe of this API returned a confident,
correct-sounding paragraph about `example.com` alongside
`URL_RETRIEVAL_STATUS_ERROR` for that exact URL. Nothing in the text said which
had happened.

So the text is never evidence of retrieval, and the tests are written against
the metadata rather than against the prose.
"""

from __future__ import annotations

import pytest

from memdog.config import Settings
from memdog.urlcontext import (
    GeminiUrlReader,
    NullUrlReader,
    UrlNotRead,
    build_url_reader,
)

pytestmark = pytest.mark.asyncio


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _Client:
    """Stands in for httpx's async client. Records what was sent."""

    sent: dict = {}

    def __init__(self, *a, **k) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        _Client.sent = json or {}
        return _Response(_Client.payload)


def _candidate(text: str, metadata: list[dict] | None) -> dict:
    candidate: dict = {"content": {"parts": [{"text": text}]}}
    if metadata is not None:
        candidate["urlContextMetadata"] = {"urlMetadata": metadata}
    return {"candidates": [candidate],
            "usageMetadata": {"totalTokenCount": 10, "toolUsePromptTokenCount": 900}}


def _reader(monkeypatch, payload: dict) -> GeminiUrlReader:
    import memdog.urlcontext as mod

    _Client.payload = payload
    monkeypatch.setattr(mod.httpx, "AsyncClient", _Client)
    return GeminiUrlReader("k", "gemini-3.7-flash")


SUCCESS = [{"retrievedUrl": "https://example.org/a",
            "urlRetrievalStatus": "URL_RETRIEVAL_STATUS_SUCCESS"}]
FAILURE = [{"retrievedUrl": "https://example.org/a",
            "urlRetrievalStatus": "URL_RETRIEVAL_STATUS_ERROR"}]


async def test_a_page_that_was_retrieved_is_read(monkeypatch):
    reader = _reader(monkeypatch, _candidate("An account of the page.", SUCCESS))
    page = await reader.read("https://example.org/a")
    assert page.account == "An account of the page."
    assert page.retrieval == SUCCESS
    # The page's own bytes are billed separately, and that is the number that
    # scales with the page rather than the prompt.
    assert page.tool_tokens == 900


async def test_an_answer_about_a_page_that_was_not_retrieved_is_refused(monkeypatch):
    """The failure this module exists for.

    The model produces fluent prose about a URL it could not reach, and the
    prose is indistinguishable from a real reading. Only the status separates
    them, so a non-success status refuses the answer outright rather than
    storing a recollection as a retrieval.
    """
    reader = _reader(monkeypatch, _candidate(
        "Example.com is a reserved domain maintained by IANA.", FAILURE))
    with pytest.raises(UrlNotRead) as caught:
        await reader.read("https://example.org/a")
    assert "not retrieved" in str(caught.value)
    assert "URL_RETRIEVAL_STATUS_ERROR" in str(caught.value)


async def test_an_answer_with_no_retrieval_metadata_at_all_is_refused(monkeypatch):
    """Silence is not success.

    A response carrying no `urlContextMetadata` cannot be shown to have read
    anything, and treating an absent field as a pass is how the one check that
    matters gets skipped in exactly the case it was written for.
    """
    reader = _reader(monkeypatch, _candidate("Confident prose.", None))
    with pytest.raises(UrlNotRead) as caught:
        await reader.read("https://example.org/a")
    assert "without reporting any retrieval" in str(caught.value)


async def test_a_retrieved_page_the_model_said_nothing_about_is_refused(monkeypatch):
    reader = _reader(monkeypatch, _candidate("   ", SUCCESS))
    with pytest.raises(UrlNotRead):
        await reader.read("https://example.org/a")


async def test_one_url_succeeding_is_enough(monkeypatch):
    """A redirect reports both the asked-for and the landed-on URL, and only
    one of them needs to have worked."""
    both = FAILURE + [{"retrievedUrl": "https://example.org/b",
                       "urlRetrievalStatus": "URL_RETRIEVAL_STATUS_SUCCESS"}]
    reader = _reader(monkeypatch, _candidate("Read it.", both))
    assert (await reader.read("https://example.org/a")).account == "Read it."


async def test_the_tool_is_declared_the_way_the_api_expects(monkeypatch):
    """`tools: [{"url_context": {}}]`, with the URLs in the prompt text.

    Verified against the live API rather than the docs, which describe a newer
    request shape than `:generateContent` accepts. An empty tool object looks
    like an omission and is the contract.
    """
    reader = _reader(monkeypatch, _candidate("ok", SUCCESS))
    await reader.read("https://example.org/a")
    assert _Client.sent["tools"] == [{"url_context": {}}]
    prompt = _Client.sent["contents"][0]["parts"][0]["text"]
    assert "https://example.org/a" in prompt
    # And the instruction that does the other half of the work.
    assert "do not answer from what you already know" in prompt


async def test_it_is_off_unless_switched_on_and_given_a_key():
    """Off by default, because a page that fetches normally costs an HTTP GET
    and this costs a model call carrying the whole page as input."""
    assert isinstance(build_url_reader(Settings()), NullUrlReader)
    assert isinstance(build_url_reader(Settings(url_context=True)), NullUrlReader)
    reader = build_url_reader(Settings(url_context=True, gemini_api_key="k"))
    assert isinstance(reader, GeminiUrlReader)


async def test_the_disabled_reader_says_why():
    with pytest.raises(UrlNotRead) as caught:
        await NullUrlReader().read("https://example.org/a")
    assert "not enabled" in str(caught.value)
