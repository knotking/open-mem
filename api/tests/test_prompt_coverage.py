"""Every format we parse must reach a prompt that was written for it.

This is the gap that was invisible: the parsers grew to 59 formats while the
classifier still mapped 8 MIME types, so a spreadsheet, a calendar, an audio
file and a Terraform config all arrived at the generic prompt -- or at the
document prompt, which narrates a table as though it were prose.

Nothing failed. The extraction succeeded every time, and was simply worse than
it needed to be, which is the kind of defect that survives indefinitely.
"""

from __future__ import annotations

import pytest

from open_mem.classify import _EXTENSION_MAP, _MIME_MAP, classify
from open_mem.prompts import BY_DATA_TYPE, GENERIC, for_data_type

# The families that must never share the document prompt, and why. Each earns
# its own because a reader asks something different of it -- not because it is
# a distinct file format.
DISTINCT = {
    "spreadsheet": "a table has a shape, not a narrative",
    "presentation": "a deck is an argument spread across fragments",
    "calendar": "an event is almost entirely structured fields",
    "contact": "personal data that must not be enriched or guessed at",
    "log": "the information is in the small part that differs",
    "config": "must never reproduce a secret",
    "audio": "speaker labels and garbled terms need suspicion",
    "video": "what is said and what is shown are different channels",
    "archive": "the container has no content; its members do",
    "geo": "coordinates are sensitive and belong in fields",
}


@pytest.mark.parametrize("data_type", sorted(DISTINCT))
def test_each_distinct_type_has_its_own_prompt(data_type):
    name, prompt = for_data_type(data_type)
    assert prompt is not GENERIC, f"{data_type} falls back to generic: {DISTINCT[data_type]}"
    assert name == data_type


def test_every_classifier_output_has_a_prompt():
    """The two registers have to agree. A classifier that emits a type nothing
    maps means silent degradation to the generic prompt -- an extraction that
    succeeds and is quietly worse."""
    produced = set(_MIME_MAP.values()) | set(_EXTENSION_MAP.values())
    missing = sorted(t for t in produced if t not in BY_DATA_TYPE)
    assert missing == [], f"classifier emits types with no prompt: {missing}"


def test_media_reaches_a_media_prompt():
    """The concrete regression: audio and video had no mapping at all, so an
    mp3 was classified `binary_blob` and extracted with the generic prompt
    after being transcribed -- the one case where speaker attribution and
    transcription errors most need to be warned about."""
    for name, expected in [("standup.mp3", "audio"), ("demo.mov", "video"),
                           ("call.m4a", "audio"), ("recording.webm", "video")]:
        data_type, _ = classify(explicit_data_type=None, source_type=None,
                                mime_type=None, external_id=name)
        assert data_type == expected, name
        assert for_data_type(data_type)[1] is not GENERIC


def test_a_spreadsheet_is_not_treated_as_a_document():
    for name in ["q3.xlsx", "budget.ods", "export.tsv"]:
        data_type, _ = classify(explicit_data_type=None, source_type=None,
                                mime_type=None, external_id=name)
        assert data_type == "spreadsheet", name
    assert for_data_type("spreadsheet")[1] is not for_data_type("document_pdf")[1]


def test_the_config_prompt_forbids_reproducing_secrets():
    """A config file is the one type where the summary itself can leak. The
    value would land in the summary, then the embedding, then an answer, where
    it cannot be recalled."""
    _, prompt = for_data_type("config")
    assert "secret" in prompt.lower()
    assert "redact" in prompt.lower()


def test_the_contact_prompt_forbids_enrichment():
    _, prompt = for_data_type("contact")
    assert "do not enrich" in prompt.lower()


def test_sniffed_bytes_still_outrank_an_extension():
    """Extension is the weakest signal and must not overtake the sniffed type;
    otherwise renaming a file changes which agent reads it."""
    data_type, layer = classify(
        explicit_data_type=None, source_type=None,
        mime_type="application/pdf", external_id="actually.mp3",
    )
    assert data_type == "document_pdf"
    assert layer == 5, "the MIME layer must resolve before the extension layer"


def test_an_unknown_extension_still_lands_somewhere_renderable():
    data_type, _ = classify(explicit_data_type=None, source_type=None,
                            mime_type=None, external_id="mystery.qqq")
    assert data_type == "binary_blob"
    assert for_data_type(data_type)[1] is GENERIC
