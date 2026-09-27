"""The predicate registry -- one vocabulary, shared by every template.

Predicates were defined twice: `graph.PREDICATES` for what the store accepts
and `extraction.RELATION_PREDICATES` for what the model is offered. Two lists
of the same twelve strings, and nothing failed if they drifted -- a predicate
added to the extractor and not the store is written by the model, accepted by
the schema, and rejected by a CHECK constraint at the end of enrichment.

The larger reason to centralise is templates. A template declares which
relationships are worth looking for in a class of document, and the tempting
design is to let each carry its own vocabulary. That produces N disconnected
subgraphs and no path from a design document to the meeting that discussed it
-- which is the entire reason to keep a graph. **So templates select from this
registry and may propose additions to it; they never mint private names.**

Two properties are recorded here that the bare tuple could not hold:

**Domain and range.** `teaches` runs person -> topic. An edge saying a city
teaches a doctrine is now refused at write time rather than found a quarter
later by someone reading a bad answer. `related_to` accepts anything, which is
exactly why it accumulates: an untyped edge is an unfalsifiable one.

**Confidence class.** A `part_of` read off a table of contents is near-certain;
a `leads_to` recovered from the shape of an argument is a reading, and a
defensible one to disagree with. Both currently arrive at 0.5 and the number
means nothing. Structural or interpretive is knowable in advance for a class of
document, which is what makes it a property of the vocabulary rather than a
judgement at read time.
"""

from __future__ import annotations

from dataclasses import dataclass

# Every entity type the resolver can produce (`entities.TYPES`). Spelled out
# rather than imported so a predicate's range cannot silently widen when a type
# is added -- a new node type should require deciding which edges may touch it.
ANY = frozenset({"person", "organization", "location", "product", "event",
                 "topic", "other"})
AGENT = frozenset({"person", "organization"})
IDEA = frozenset({"topic", "event", "other"})


@dataclass(frozen=True)
class Predicate:
    name: str
    domain: frozenset[str]
    range: frozenset[str]
    gloss: str
    # At most one open value at a time. A new fact on one of these closes the
    # previous one; everything else accumulates. Default is multi-valued and
    # single-valued is opt-in, because getting this backwards is the expensive
    # error -- see `graph.SINGLE_VALUED`.
    single_valued: bool = False
    # "structural" -- stated plainly and cheap to verify.
    # "interpretive" -- a defensible reading of what the text argues.
    confidence: str = "structural"
    # Whether A->B->C licenses A->C. Almost never true: `part_of` genuinely is,
    # `leads_to` is not, because each step in a causal chain carries its own
    # conditions and collapsing them is how a graph starts producing confident
    # nonsense.
    transitive: bool = False


def _p(name, domain, rng, gloss, **kw) -> Predicate:
    return Predicate(name=name, domain=domain, range=rng, gloss=gloss, **kw)


# The twelve that shipped, now typed. Types were chosen to refuse what is
# plainly wrong rather than to be maximally precise: `owns` is left wide open
# because anything can own anything, while `works_for` is not, because a
# doctrine does not work for a city.
REGISTRY: dict[str, Predicate] = {p.name: p for p in (
    _p("works_for", AGENT, AGENT, "employed by, or engaged by"),
    _p("member_of", AGENT, frozenset({"organization", "event", "other"}),
       "belongs to a group"),
    _p("reports_to", frozenset({"person"}), AGENT, "reports to", single_valued=True),
    _p("collaborates_with", AGENT, AGENT, "works alongside"),
    _p("located_in", ANY, frozenset({"location"}), "is situated in",
       single_valued=True, transitive=True),
    _p("part_of", ANY, ANY, "is a component of", transitive=True),
    _p("owns", ANY, ANY, "holds or possesses"),
    _p("produces", ANY, ANY, "makes or emits"),
    _p("uses", ANY, ANY, "depends on or employs"),
    _p("attended", frozenset({"person", "organization"}),
       frozenset({"event"}), "was present at"),
    _p("about", ANY, IDEA, "concerns the subject of"),
    _p("related_to", ANY, ANY, "connected, in a way the text did not specify"),

    # Added for templates. None is scripture-specific: an argument, an incident
    # report and a research paper all need to say who claimed what, what leads
    # to what, and what is set against what.
    _p("teaches", AGENT, IDEA, "puts forward, or instructs in",
       confidence="interpretive"),
    _p("leads_to", ANY, ANY, "brings about, according to the text",
       confidence="interpretive"),
    _p("contrasts_with", ANY, ANY, "is set against, as an alternative or opposite",
       confidence="interpretive"),
    _p("caused_by", ANY, ANY, "was brought about by", confidence="interpretive"),
    _p("mitigated_by", ANY, ANY, "was limited or stopped by",
       confidence="interpretive"),
)}

NAMES: tuple[str, ...] = tuple(REGISTRY)
SINGLE_VALUED = frozenset(p.name for p in REGISTRY.values() if p.single_valued)
INTERPRETIVE = frozenset(
    p.name for p in REGISTRY.values() if p.confidence == "interpretive")


def permits(predicate: str, subject_type: str | None, object_type: str | None) -> bool:
    """Whether this predicate may join these two entity types.

    An unknown type passes. The resolver's types come from a model, and
    refusing an edge because a node was typed `other` would throw away a good
    claim over a bad guess about one of its endpoints -- the wrong trade, since
    the type is the least reliable thing on the row.
    """
    spec = REGISTRY.get(predicate)
    if spec is None:
        return False
    if subject_type and subject_type not in spec.domain:
        return False
    if object_type and object_type not in spec.range:
        return False
    return True


def describe() -> list[dict]:
    """The vocabulary, for `GET /api/v1/graph/predicates` and the console."""
    return [
        {"predicate": p.name, "gloss": p.gloss,
         "domain": sorted(p.domain), "range": sorted(p.range),
         "single_valued": p.single_valued, "confidence": p.confidence,
         "transitive": p.transitive}
        for p in REGISTRY.values()
    ]
