"""Extraction across a whole document, not just its first window.

Embedding has always chunked; extraction never did. A record's *text* was
therefore fully searchable while its *understanding* -- title, keywords,
entities, relations -- described only as much as fitted in one model call.

On a page those are the same thing. On a book they are not: a 232,000-character
Bhagavad Gita came back titled "Summary of Bhagavad Gita Chapters 1 through 16",
with fifteen entities and six edges for eighteen chapters. The model named its
own truncation point and the artifact was stored as a success.
"""

from __future__ import annotations

import pytest

from memdog.extraction import (
    Envelope, extract_long, merge_envelopes, split_windows,
)

pytestmark = pytest.mark.asyncio


class _Recording:
    """An extractor that records the windows it was handed."""

    model_id = "fake"

    def __init__(self, window: int) -> None:
        self.max_input_chars = window
        self.seen: list[str] = []

    async def extract(self, text, *, data_type, prompt=None, template=None):
        self.seen.append(text)
        n = len(self.seen)
        return Envelope(
            title=f"part {n}",
            summary=f"summary {n}",
            keywords=[f"k{n}"],
            entities=[{"name": f"Person{n}", "type": "person"}],
            relations=[{"subject": f"Person{n}", "predicate": "teaches",
                        "object": f"Topic{n}"}],
        )


# ------------------------------------------------------------------ splitting


def test_a_short_document_is_one_window_and_is_not_split():
    assert split_windows("short", 100) == ["short"]


def test_windows_prefer_a_paragraph_boundary():
    """A hard slice lands mid-sentence, and a model handed half a sentence at
    each edge invents the other half — the one failure a graph cannot tolerate,
    because a hallucinated edge becomes a traversable path."""
    text = "alpha. " * 10 + "\n\n" + "beta. " * 30
    windows = split_windows(text, 100)
    assert len(windows) > 1
    # The first cut fell on the blank line rather than through a word.
    assert windows[0].rstrip().endswith(".")


def test_splitting_covers_the_whole_text_and_loses_nothing():
    text = "".join(f"paragraph {i}.\n\n" for i in range(400))
    windows = split_windows(text, 500)
    assert "".join(windows) == text


def test_a_document_with_no_boundaries_still_makes_progress():
    """Without the fallback the search for a break point finds nothing and the
    loop degenerates to one character at a time."""
    windows = split_windows("x" * 5000, 500)
    assert len(windows) == 10
    assert all(len(w) == 500 for w in windows)


# ------------------------------------------------------------------- merging


def test_the_graph_is_cumulative_and_the_title_is_not():
    """The rule is deliberately asymmetric. A title names the whole document
    and the opening is the best single guess at it; entities and relations are
    claims the text makes, and every window makes more of them."""
    parts = [
        Envelope(title="first", description="d", summary="s1",
                 keywords=["a"], entities=[{"name": "Krishna", "type": "person"}],
                 relations=[{"subject": "Krishna", "predicate": "teaches",
                             "object": "karma yoga"}]),
        Envelope(title="second", summary="s2", keywords=["b"],
                 entities=[{"name": "Arjuna", "type": "person"}],
                 relations=[{"subject": "Arjuna", "predicate": "attended",
                             "object": "Kurukshetra"}]),
    ]
    merged = merge_envelopes(parts, read=2, total=2)
    assert merged.title == "first"
    assert merged.description == "d"
    assert [e["name"] for e in merged.entities] == ["Krishna", "Arjuna"]
    assert len(merged.relations) == 2
    assert merged.keywords == ["a", "b"]
    # A book's summary is genuinely a sequence; one paragraph about its opening
    # is not a summary of it.
    assert merged.summary == "s1\n\ns2"


def test_the_same_claim_in_two_windows_is_one_relation_at_the_better_confidence():
    parts = [
        Envelope(title="t", entities=[{"name": "Krishna", "type": "person"}],
                 relations=[{"subject": "Krishna", "predicate": "teaches",
                             "object": "karma yoga", "confidence": 0.4}]),
        Envelope(title="t", entities=[{"name": "krishna", "type": "person"}],
                 relations=[{"subject": "Krishna", "predicate": "teaches",
                             "object": "karma yoga", "confidence": 0.9}]),
    ]
    merged = merge_envelopes(parts, read=2, total=2)
    assert len(merged.relations) == 1
    assert merged.relations[0]["confidence"] == 0.9
    # Case is not a different person.
    assert len(merged.entities) == 1


def test_skipped_windows_are_named_rather_than_silently_dropped():
    """"We read 12 of 19" is a fact somebody can act on. An envelope that simply
    describes less is not."""
    merged = merge_envelopes([Envelope(title="t")], read=12, total=19)
    assert merged.fields["windows_read"] == 12
    assert merged.fields["windows_total"] == 19
    assert merged.fields["windows_skipped"] == 7


def test_a_single_window_does_not_claim_to_have_skipped_anything():
    merged = merge_envelopes([Envelope(title="t")], read=1, total=1)
    assert "windows_skipped" not in merged.fields


# ------------------------------------------------------------------ end to end


async def test_a_long_document_is_read_past_its_first_window():
    """The bug, stated as a test: eighteen chapters must not produce the graph
    of one."""
    extractor = _Recording(window=100)
    text = "".join(f"chapter {i} says something.\n\n" for i in range(40))
    envelope = await extract_long(extractor, text, data_type="document")

    assert len(extractor.seen) > 1, "the document was read in one window"
    assert "".join(extractor.seen) == text, "some of the document was never read"
    # Every window contributed to the graph.
    assert len(envelope.entities) == len(extractor.seen)
    assert len(envelope.relations) == len(extractor.seen)


async def test_a_short_document_makes_exactly_one_call():
    """Windowing must not cost an extra call on the ordinary case."""
    extractor = _Recording(window=10_000)
    envelope = await extract_long(extractor, "a short note", data_type="note")
    assert len(extractor.seen) == 1
    assert envelope.title == "part 1"
    # And it is the plain envelope, not a merged one asserting window counts.
    assert "windows_total" not in envelope.fields


async def test_the_window_budget_is_a_cap_and_the_remainder_is_reported():
    """Each window is a model call. Without a cap one upload of a large corpus
    quietly becomes hundreds of calls billed to a project that asked to 'add
    data'."""
    extractor = _Recording(window=100)
    text = "".join(f"paragraph {i}.\n\n" for i in range(200))
    envelope = await extract_long(extractor, text, data_type="document",
                                  max_windows=3)
    assert len(extractor.seen) == 3
    assert envelope.fields["windows_read"] == 3
    assert envelope.fields["windows_skipped"] > 0


async def test_the_extraction_window_applies_even_without_a_declared_limit():
    """The window that matters is behavioural, not technical.

    An extractor that declares no context limit still gets the extraction
    window, because the constraint is what a model does when handed a wall of
    text -- not what it can hold. Measured on one document, same prompt, same
    template: 200,000 characters produced zero entities and zero keywords while
    40,000 produced twelve entities and six edges. The model was not running out
    of room; it was answering a different question.
    """
    from memdog.extraction import EXTRACT_WINDOW

    class _Unbounded(_Recording):
        def __init__(self):
            super().__init__(window=0)

    extractor = _Unbounded()
    await extract_long(extractor, "x" * (EXTRACT_WINDOW * 3), data_type="document")
    assert len(extractor.seen) == 3


async def test_the_narrower_of_the_model_limit_and_the_window_wins():
    """A model whose context is smaller than the extraction window must not be
    handed the window: the point of the limit is that the call would fail."""
    extractor = _Recording(window=1_000)
    await extract_long(extractor, "x" * 5_000, data_type="document")
    assert len(extractor.seen) == 5
    assert all(len(w) <= 1_000 for w in extractor.seen)


def test_the_chain_reports_the_narrowest_window_of_its_steps():
    """The chain is what the worker holds — every concrete extractor sits
    behind it — so a window declared only on the concrete classes is a window
    nothing can see.

    That is not hypothetical: the first cut declared `max_input_chars` on
    GeminiExtractor and OllamaExtractor only. `getattr(chain, ...)` returned 0,
    `split_windows` treated a 232,000-character book as one window, and the
    deployed behaviour was identical to before the fix — the tests passed and
    the artifact carried no `windows_read`, which is the only reason it was
    caught rather than believed.

    The narrowest, not the primary's: a fallback is chosen when the primary
    fails, and handing it a window its own context cannot hold turns a degraded
    answer into no answer.
    """
    from memdog.extraction import (
        ChainedExtractor, GeminiExtractor, LocalHeuristicExtractor, OLLAMA_WINDOW,
        OllamaExtractor,
    )
    from memdog.routing import Chain, Step

    gemini = GeminiExtractor("key", "gemini-3.7-flash")
    ollama = OllamaExtractor("llama", "http://localhost:11434")
    local = LocalHeuristicExtractor()

    chain = ChainedExtractor(Chain("extract", [
        Step(name="gemini", model_id="g", call=gemini.extract),
        Step(name="ollama", model_id="o", call=ollama.extract),
        Step(name="local", model_id="l", call=local.extract),
    ]))
    assert chain.max_input_chars == OLLAMA_WINDOW

    # A chain of only unbounded extractors declares no window rather than zero
    # meaning "one character".
    unbounded = ChainedExtractor(Chain("extract", [
        Step(name="local", model_id="l", call=local.extract)]))
    assert unbounded.max_input_chars == 0
