"""The model catalog, and whether anything can actually serve it.

A card is a claim: *this model can do these things here*. The catalog and the
engine layer are written in different files by different reasoning, and nothing
connected them — so a card naming a provider with no builder would list fine,
assign fine, and resolve to `None`, at which point the request quietly falls
back to the deployment's default model. No error, a plausible answer, and the
org's chosen model never ran.

That is the same shape as every other defect this repository has found, which
is why it gets a guard rather than a code review.
"""

from __future__ import annotations

import pytest

from open_mem.engines import EngineRegistry
from open_mem.models import PURPOSES, SHIPPED_CARDS

# Which builder kind serves which purpose. `answer` is deliberately absent from
# PURPOSES: chat follows the org's extraction assignment so that
# `allowed_providers` is one decision and not two. It still needs a builder,
# which is exactly what this mapping exists to check.
KIND_FOR = {"extraction": "extraction", "classification": "extraction"}


def _builders() -> set[tuple[str, str]]:
    """The (kind, provider) pairs `_construct` knows how to build.

    Read by constructing each one, because a table of names kept beside the
    real table is a second thing to forget to update.
    """
    registry = EngineRegistry.__new__(EngineRegistry)
    registry._settings = type("S", (), {"ollama_url": "http://localhost:11434"})()
    pairs = set()
    for kind in ("extraction", "answer"):
        for provider in ("google", "gemini", "ollama", "anthropic", "openai", "local"):
            built = registry._construct(
                kind=kind, provider=provider, model_id="m",
                base_url=None, credential="k",
            )
            if built is not None:
                pairs.add((kind, provider))
    return pairs


def test_every_shipped_card_names_a_provider_something_can_build():
    """A card for a provider with no builder is a model you can select and
    assign, that then resolves to nothing and falls back to the default — with
    no error anywhere and an answer that looks fine."""
    builders = _builders()
    orphans = []
    for card in SHIPPED_CARDS:
        provider = card["provider"]
        if provider == "local":
            continue  # served by the built-in defaults, not the engine registry
        for capability in card["capabilities"]:
            kind = KIND_FOR.get(capability)
            if kind is None:
                continue  # vision and transcription go through the multimodal seam
            if (kind, provider) not in builders:
                orphans.append(f"{card['model_id']} claims {capability} "
                               f"but no ({kind}, {provider}) builder exists")
    assert not orphans, (
        "these cards name a provider the engine layer cannot construct, so "
        "assigning one silently falls back to the deployment default:\n  "
        + "\n  ".join(orphans)
    )


def test_a_card_never_declares_something_that_is_not_a_purpose():
    """`answer` reads like a capability and is not one. A card declaring it
    would pass review and could never be assigned, because the assignment gate
    checks the purpose against this exact list."""
    for card in SHIPPED_CARDS:
        for capability in card["capabilities"]:
            assert capability in PURPOSES, (
                f"{card['model_id']} declares {capability!r}, which is not in "
                f"PURPOSES {PURPOSES} — the assignment gate would refuse it"
            )


def test_an_open_model_can_answer_as_well_as_extract():
    """The asymmetry this file was written for. Ollama could extract and not
    answer, so an org that assigned Llama for extraction got its documents read
    by Llama and its questions answered by the deployment's default — with
    nothing saying so."""
    builders = _builders()
    assert ("extraction", "ollama") in builders
    assert ("answer", "ollama") in builders, (
        "an org assigning an open model for extraction gets the default "
        "answerer, and the fallback is silent"
    )


def test_the_catalog_offers_more_than_one_vendor():
    """Not a style rule. A catalog with one vendor in it means `hosting: local`
    is unreachable, and with it every regulated data type — which is enforced
    at three separate points and would simply never resolve."""
    providers = {c["provider"] for c in SHIPPED_CARDS}
    assert len(providers) >= 3, providers
    assert any(c["hosting"] == "local" and c["provider"] != "local"
               for c in SHIPPED_CARDS), (
        "nothing but the built-in stubs can serve a regulated data type"
    )


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_the_absence_of_a_frontier_provider_is_deliberate(provider):
    """Stated rather than left to be discovered. Neither has a builder, so a
    card for one would be the exact defect the first test guards against — the
    catalog does not list them, and this records why it does not."""
    assert (("extraction", provider) not in _builders()), (
        f"a {provider} builder now exists — add its cards to SHIPPED_CARDS and "
        "delete this test, which only documented the gap"
    )
    assert not [c for c in SHIPPED_CARDS if c["provider"] == provider]
