"""Judging a fetched page, and the schema that keeps the judgement bounded.

A page is not a file. It was published, it wants something, and it has a real
chance of not being content at all -- an error page, a login wall and a parked
domain all arrive as HTTP 200 with fluent text. These tests pin the two things
that make that judgement usable: it is asked only of pages, and it can never
grow large enough to cost the graph.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memdog import prompts                                    # noqa: E402
from memdog.classify import classify, sniff_mime               # noqa: E402
from memdog.extraction import (                               # noqa: E402
    JUDGED, PAGE_KINDS, RETRIEVAL_VALUE, _gemini_schema, envelope_schema,
)


def test_a_page_is_judged_and_a_document_is_not():
    """The block costs output tokens on every extraction that carries it, so it
    is asked of the type it was written for and no other. A PDF fetched from a
    URL is still a PDF."""
    assert "document_html" in JUDGED
    for kind in ("document_pdf", "document_text", "spreadsheet", "transcript"):
        assert kind not in JUDGED

    assert "quality" not in envelope_schema()["properties"]
    assert "quality" in envelope_schema(quality=True)["properties"]
    assert "quality" not in _gemini_schema()["properties"]
    assert "quality" in _gemini_schema(quality=True)["properties"]


def test_the_judgement_never_outranks_the_graph():
    """Property order is load-bearing: whatever is last is what gets dropped
    when something earlier runs long, and the graph is the part that cannot be
    reconstructed without paying for the call again."""
    order = _gemini_schema(quality=True)["propertyOrdering"]
    assert order.index("entities") < order.index("quality")
    assert order.index("relations") < order.index("quality")
    assert order.index("quality") < order.index("summary")
    assert order[-1] == "summary", "summary must stay the field that pays"


def test_the_ordering_still_matches_the_properties_when_unjudged():
    """The two halves of the Gemini dialect drifting apart is how a field ends
    up ordered but never emitted."""
    for judged in (False, True):
        schema = _gemini_schema(quality=judged)
        assert set(schema["propertyOrdering"]) <= set(schema["properties"])


def test_the_verdict_is_enumerated_so_it_can_be_filtered():
    """A paragraph about a page's quality can be read but not counted, and the
    question people have is "which of these four hundred pages are marketing
    with no sources"."""
    quality = _gemini_schema(quality=True)["properties"]["quality"]
    assert quality["properties"]["retrieval_value"]["enum"] == RETRIEVAL_VALUE
    assert quality["properties"]["page_kind"]["enum"] == PAGE_KINDS
    # The values that exist because a page can fail to be content at all.
    assert "not_content" in RETRIEVAL_VALUE
    assert "error_or_empty" in PAGE_KINDS and "login_or_paywall" in PAGE_KINDS


def test_the_fields_worth_having_are_required():
    """An optional property may simply be absent -- that is how the graph came
    back empty once. The verdict and its axes are forced."""
    required = _gemini_schema(quality=True)["properties"]["quality"]["required"]
    for field in ("page_kind", "substance", "evidence", "retrieval_value", "verdict"):
        assert field in required


def test_a_page_gets_the_page_prompt_and_not_the_document_one():
    name, block = prompts.for_data_type("document_html")
    assert name == "web_page"
    assert block is prompts.WEB_PAGE
    assert prompts.for_data_type("document_pdf")[1] is prompts.DOCUMENT


def test_the_page_prompt_names_the_traps_that_make_a_page_look_good():
    """Each of these is a specific way a page reads as better than it is, and a
    prompt that omits them gets a polite summary of the padding."""
    block = prompts.WEB_PAGE
    for trap in ("Navigation", "HTTP 200", "Confidence is not evidence",
                 "Length is not substance", "unattributed"):
        assert trap in block, f"the page prompt no longer warns about {trap!r}"


# -- reaching the page prompt at all ----------------------------------------


def test_a_fetched_page_is_recognised_as_html_from_its_bytes():
    """`filetype` knows containers and magic numbers, not markup, so a page
    guessed as nothing, decoded cleanly and came back `text/plain`. That made
    `document_html` unreachable through the fetch path: every page pulled from a
    URL was typed `document_text` and read with the document prompt.
    """
    page = b"<!DOCTYPE html><html><head><title>x</title></head><body>hi</body></html>"
    assert sniff_mime(page, None, None) == "text/html"
    assert classify(explicit_data_type=None, source_type=None,
                    mime_type="text/html", external_id=None)[0] == "document_html"


def test_markup_is_recognised_past_the_things_real_pages_start_with():
    """A BOM, an XML declaration or a licence comment before `<html` is
    ordinary, and the strict prefix test alone missed all three."""
    for opening in (
        b"\xef\xbb\xbf<!doctype html><html><body>x</body></html>",
        b"\n\n   <html><body>x</body></html>",
        b"<?xml version='1.0'?><html xmlns='http://www.w3.org/1999/xhtml'><body>x</body></html>",
        b"<!-- generated, do not edit -->\n<html><body>x</body></html>",
    ):
        assert sniff_mime(opening, None, None) == "text/html", opening[:30]


def test_prose_that_merely_mentions_markup_is_not_markup():
    """The look is at the head of the document, not a search of the whole
    thing: a page that discusses `<html>` three screens down is writing about
    markup, not made of it."""
    prose = b"A note on markup.\n\n" + b"padding. " * 200 + b"<html> is the root element."
    assert sniff_mime(prose, None, None) == "text/plain"
    assert sniff_mime(b"just some words", None, None) == "text/plain"
