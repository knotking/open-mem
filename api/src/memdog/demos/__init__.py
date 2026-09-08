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

    def payload(self, *, default_identifier: str) -> dict:
        return {
            "external_id": self.external_id,
            "content": {"kind": "inline", "text": f"{MARKER}\n\n{self.text.strip()}"},
            "data_type": self.data_type,
            "source_type": self.source_type,
            "event_time": (REFERENCE - timedelta(days=self.days_ago)).isoformat(),
            "tags": [*self.tags, "demo"],
            "identifiers": list(self.identifiers) or [default_identifier],
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


from .legal import CORPUS as LEGAL            # noqa: E402
from .meetings import CORPUS as MEETINGS      # noqa: E402
from .sensors import CORPUS as SENSORS        # noqa: E402

CORPORA: dict[str, Corpus] = {c.key: c for c in (LEGAL, MEETINGS, SENSORS)}
