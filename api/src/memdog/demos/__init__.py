"""Corpora for the public gallery: content, kept out of the code that loads it.

`seed.py` builds one demo tenant and proves it works, and its verification is
deeply about that domain — a case summary, a normalization projection, an ACL
check on one private record. Those are the sales demo's own subject, not a
sequence three other corpora can inherit, so this is a second and much smaller
seeder rather than a generalisation of that one.

**What every corpus here owes.**

*Questions that name the record answering them.* Taken from `seed.py`, where the
rule is already written: *"a demo corpus with no questions attached is a corpus;
the questions are what make it a demonstration"*. Here they are also the
acceptance test — a corpus whose questions stop finding their records fails the
seed instead of quietly becoming a demonstration of nothing.

*A mechanism it alone shows.* Four corpora that each demonstrate "chat over
documents" teach one thing four times. The `blurb` is where an entry says what
it shows that its neighbours do not, and an entry that cannot answer that should
not be in the gallery.

*Unmistakably synthetic content.* `seed.py` sets the discipline and the reason:
reserved names, `.example` domains, identifier ranges documented as fake,
because *"a demo record that looks real may be treated as real"*. That is
routine for a sales pipeline and sharp for a legal matter — a synthetic contract
that reads as real is a document somebody can misuse — so every record carries
the marker in its own text, not only in metadata.

**Nothing clinical.** Use case 8 is absent on purpose. Fake medical records are
the highest-risk synthetic data in the catalog and no demo value justifies
authoring them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

# In the content itself rather than only in metadata, because the failure this
# guards against is a screenshot. Same string `seed.py` uses.
MARKER = "[DEMO — synthetic data, generated, not real]"

# Everything is dated relative to one point, so a corpus reads as a coherent
# few months whenever it is seeded rather than as a pile that all arrived the
# day somebody ran this.
REFERENCE = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Question:
    """A question, and the record that must answer it.

    `must_find` is an `external_id`. Naming it is what turns a sample question
    into an assertion: if the corpus stops answering, the seed says so.
    """

    question: str
    must_find: str


@dataclass(frozen=True)
class Item:
    external_id: str
    text: str
    data_type: str
    source_type: str
    days_ago: int
    tags: tuple[str, ...] = ()
    identifiers: tuple[str, ...] = ()
    # Off for a corpus whose whole point is that it never reaches a model.
    enrich: bool = True
    # Which graph schema to read this record under. `None` is open-domain
    # extraction, which stays the default for the same reason it is the
    # platform's: a template is an assertion about what a document is *for*,
    # and a corpus that cannot make that assertion should not pretend to.
    template: str | None = None

    def payload(self, *, default_identifier: str, synthetic: bool = True) -> dict:
        # **The marker is a claim, so it is only made where it is true.** Three
        # of these corpora are inventions and say so in every record. One is a
        # real researcher's published work, and stamping "generated, not real"
        # across somebody's actual abstracts would be a false statement about
        # their research -- the mirror of the failure the marker exists to
        # prevent.
        body = f"{MARKER}\n\n{self.text.strip()}" if synthetic else self.text.strip()
        return {
            "external_id": self.external_id,
            "content": {"kind": "inline", "text": body},
            "data_type": self.data_type,
            "source_type": self.source_type,
            "event_time": (REFERENCE - timedelta(days=self.days_ago)).isoformat(),
            "tags": [*self.tags, "demo"],
            "identifiers": list(self.identifiers) or [default_identifier],
            # Omitted rather than sent as null when there is none, so the four
            # corpora that predate templates produce byte-identical payloads
            # and a re-seed of one of them is not a silent change of schema.
            **({"template": self.template} if self.template else {}),
        }


@dataclass(frozen=True)
class Corpus:
    key: str
    title: str
    # What this one shows that the others do not. Rendered on the gallery card.
    blurb: str
    # Provenance and the synthetic-data disclosure, shown under the chat.
    note: str
    memory_key: str
    memory_type: str
    default_identifier: str
    items: tuple[Item, ...]
    questions: tuple[Question, ...]
    ttl_seconds: int | None = None
    on_expiry: str = "keep_members"
    # Whether records in this corpus are enriched at all. False is not a saving
    # here, it is the demonstration.
    enrich: bool = True
    scopes: dict = field(default_factory=dict)
    # Whether the content is invented. Decides the marker, and nothing else.
    synthetic: bool = True
    # A corpus whose content is real and lives somewhere else fetches it at seed
    # time instead of carrying a copy. The three synthetic corpora are written
    # here because they are inventions; a hundred real papers are somebody's
    # published record, and a stale copy of that in this repository would be a
    # second source of truth nobody updates.
    fetch: object | None = None

    async def load(self) -> tuple["Item", ...]:
        if self.fetch is None:
            return self.items
        return tuple(await self.fetch())

    def registry_entry(self, project_id: str, memory_id: str) -> dict:
        """The `PUBLIC_DEMOS` entry this corpus becomes once it is seeded."""
        return {
            "key": self.key,
            "title": self.title,
            "blurb": self.blurb,
            "note": self.note,
            "project_id": project_id,
            "memory_id": memory_id,
            "questions": [q.question for q in self.questions],
        }


from .gita import CORPUS as GITA              # noqa: E402
from .legal import CORPUS as LEGAL            # noqa: E402
from .meetings import CORPUS as MEETINGS      # noqa: E402
from .papers import CORPUS as PAPERS          # noqa: E402
from .sensors import CORPUS as SENSORS        # noqa: E402

# Order is the gallery's order, and the gallery's order is what a visitor is
# offered first. The Gita leads because it is the only entry with something to
# look at as well as something to ask, and a page whose opening move is a second
# chat box teaches the same thing the four before it taught.
CORPORA: dict[str, Corpus] = {
    c.key: c for c in (GITA, LEGAL, MEETINGS, SENSORS, PAPERS)
}
