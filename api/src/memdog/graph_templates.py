"""Premeditated templates -- deciding the shape of the graph before reading.

Extraction is routed by `data_type`, derived from the bytes: a .docx is a
`document`, a .wav is `audio`. That is the right default and it has a ceiling
built into it -- **the bytes cannot tell you what the document is for.** A
novel, a design document and a contract are one `data_type` and are interesting
for entirely different reasons.

For the summary that ceiling costs a bland paragraph. For the *graph* it costs
the graph. Seven of the twelve shipped predicates are org-chart shaped, so
anything that is not a workplace document collapses into `related_to`, which
asserts only that two words occurred near each other. The graph fills up and
says nothing.

A template declares the shape first: **these relationships may exist in this
kind of document; find them.** Premeditation is the load-bearing word -- the
judgement about what matters is made once, by a person, for a class of
documents, rather than per document by a model at read time.

Three things follow, and the first is the one that matters:

**Absence becomes evidence.** Under open extraction a missing edge cannot be
read: *the model did not find it*, *it is not in the document* and *nothing was
looking for it* are indistinguishable, so every gap is meaningless and the
graph can only be browsed. When the schema is declared you know what was
sought, and an empty slot is a result.

**The offered vocabulary shrinks.** A template narrows the enum the model may
answer with, which turns open-ended extraction into slot-filling -- a markedly
easier task, and an easier one to review.

**Being wrong is recoverable.** The template is hashed into
`generator_version`, so editing one makes exactly its edges detectably stale and
`/reprocess` rebuilds them. Two templates can be run over one text and their
graphs diffed, which is the only honest way to find out whether a template is
any good.

A template is a hint, not a promise. `scripture` on a novel must degrade to a
plain document graph rather than force teachings out of prose containing none.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from .predicates import REGISTRY


@dataclass(frozen=True)
class Template:
    name: str
    # What this template exists to answer. Not decoration: reviewing a template
    # means checking that every predicate below serves one of these. A
    # predicate answering no listed question is what turns a vocabulary into an
    # ontology nobody fills.
    questions: tuple[str, ...]
    # Selected from the global registry, never invented here.
    predicates: tuple[str, ...]
    # The prose that goes into the prompt. The "look for" line is the point of
    # a template; everything else is scaffolding.
    look_for: str
    # What this template gets wrong if nobody says otherwise.
    traps: tuple[str, ...] = ()
    # How a claim from this kind of document should be cited. A chunk id is the
    # wrong provenance for scripture, where the whole commentary tradition
    # addresses "BG 2.47".
    citation_unit: str = "chunk"
    description: str = ""

    def block(self) -> str:
        """The instruction block appended to the system prompt.

        Kept short on purpose. The first version spelled out every predicate's
        gloss, every trap, and a paragraph of caveats on top of an already long
        per-type block -- and the model answered it by writing several thousand
        words of compliance filler into `summary` ("faithfully, cleanly,
        seamlessly, accurately...") until the output budget was gone. Adding
        instructions past a certain density stops steering the model and starts
        giving it something to perform.

        So: what this is, what to look for, the predicate names alone, and the
        traps. The glosses live in the schema enum the model is already reading;
        repeating them here bought nothing and cost the envelope.
        """
        lines = [
            f"This content has been declared a {self.name}.",
            self.look_for.strip(),
            "",
            "Record only these relationships: " + ", ".join(self.predicates) + ".",
        ]
        if self.traps:
            lines.extend(["", *(f"- {t}" for t in self.traps)])
        lines.extend(["", "If the content is not this kind of thing, extract it "
                          "plainly and return no relations. An empty graph is "
                          "a correct graph."])
        return "\n".join(lines)

    def digest(self) -> str:
        """Stable hash, folded into `generator_version`.

        Covers the predicates as well as the prose: narrowing the offered set
        changes what the model can say, so it changes the artifact as surely as
        editing a sentence does.
        """
        payload = json.dumps({
            "name": self.name, "look_for": self.look_for,
            "predicates": list(self.predicates), "traps": list(self.traps),
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:12]


TEMPLATES: dict[str, Template] = {t.name: t for t in (
    Template(
        name="scripture",
        description="A religious or philosophical text: argument, not narrative.",
        questions=(
            "who teaches what, and inside whose narration?",
            "what does the text claim leads to what?",
            "what does it set against what?",
        ),
        predicates=("teaches", "leads_to", "contrasts_with", "part_of",
                    "about", "member_of", "located_in"),
        look_for=(
            "This is a religious or philosophical text. What is worth keeping "
            "is the argument, not the plot: who puts a doctrine forward, what "
            "the text says follows from what, and which ideas it sets against "
            "each other. A chapter list is not a summary."
        ),
        traps=(
            "Epithets are one person: use one spelling per figure throughout.",
            "The narrator is not the speaker: attribute a teaching to whoever "
            "said it, not to whoever reported it.",
            "A chain is ordered: 'A leads to B leads to C' is two directed "
            "relations, not three related things.",
        ),
        citation_unit="chapter:verse",
    ),
    Template(
        name="design-doc",
        description="A proposal: the reasoning is the part that ages well.",
        questions=(
            "what was proposed, and what does it depend on?",
            "what alternatives were set against it?",
            "what is claimed to follow from the choice?",
        ),
        predicates=("contrasts_with", "leads_to", "uses", "part_of", "about",
                    "produces", "related_to"),
        look_for=(
            "This is a design or proposal document. Record the approach and "
            "what it depends on, the alternatives it was weighed against, and "
            "the consequences it claims. The rejected alternatives are the "
            "part that ages well; a document that records only the proposal "
            "has lost its reasoning."
        ),
        traps=(
            "An alternative that was considered and rejected still belongs in "
            "the graph, set against the chosen approach.",
            "A dependency the document merely mentions is not one it uses.",
        ),
        citation_unit="section",
    ),
    Template(
        name="incident",
        description="A postmortem or outage report: what caused what.",
        questions=(
            "what failed, and what caused it?",
            "what stopped or limited it?",
            "which systems and people were involved?",
        ),
        predicates=("caused_by", "mitigated_by", "leads_to", "part_of",
                    "uses", "about", "attended"),
        look_for=(
            "This is an incident or postmortem report. Record the causal "
            "chain the document asserts -- what failed, what brought it "
            "about, and what limited or ended it. Causation must be what the "
            "document claims, not what seems likely from the sequence."
        ),
        traps=(
            "Order is not cause. Two things in a timeline are not related "
            "unless the document says one brought about the other.",
            "A contributing factor and a root cause are both caused_by; do "
            "not silently promote one to the other.",
        ),
        citation_unit="section",
    ),
)}


class UnknownTemplate(Exception):
    """Raised rather than ignored.

    Ignoring a typo silently produces a generic extraction the caller believes
    is specialised -- and believes it about the *graph*, where a missing
    predicate is invisible. Refusing costs a write and names the alternatives,
    which are discoverable at `GET /api/v1/templates`.
    """


def get(name: str | None) -> Template | None:
    if not name:
        return None
    template = TEMPLATES.get(name.strip().lower())
    if template is None:
        raise UnknownTemplate(
            f"unknown template '{name}' -- valid templates are "
            f"{', '.join(sorted(TEMPLATES))}"
        )
    return template


def predicates_for(name: str | None) -> tuple[str, ...]:
    """The predicates a template offers, or the whole registry without one."""
    template = get(name)
    if template is None:
        return tuple(REGISTRY)
    return template.predicates


def registry() -> list[dict]:
    """For `GET /api/v1/templates`, so no client hardcodes a list that drifts."""
    return [
        {"template": t.name, "description": t.description,
         "questions": list(t.questions), "predicates": list(t.predicates),
         "citation_unit": t.citation_unit, "digest": t.digest()}
        for t in TEMPLATES.values()
    ]
