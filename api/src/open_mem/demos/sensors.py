"""A sensor fleet: the corpus that never reaches a model.

Use case 6, and the one entry in the gallery whose demonstration is a *negative*
— nothing here is embedded, nothing is enriched, and no question about it costs
a model call. Every other app in the gallery is a reason to believe the platform
is an expensive wrapper around an LLM. This one is the reason to believe it is
not: admission control decides `embed` and `enrich` per producer, so a fleet
writing millions of readings a day is a facet range query and a bill of nothing.

The readings are deliberately shaped so the interesting answer is a range and
not a search. One freezer drifts above its threshold across a weekend and comes
back; a second is fine throughout; a third loses its sensor entirely, which is
a different failure from being warm and must not read the same.

**The readings alone cannot be asked anything, and that is the finding.** The
first version of this corpus was `enrich=False` throughout, and the seed refused
it: sixty-three records, zero chunks, none of the questions answerable. Nothing
unenriched is retrievable at all, so a chat gallery cannot host a corpus that
never reaches a model — which the card had cheerfully claimed it could.

The fix is the use case as the catalog actually writes it: *"tracing for raw
readings; derived aggregates land in a durable memory... aggregation is a
scheduled job, not an agent."* The readings stay unenriched and unsearchable,
and a handful of daily digests — the output of that job, not of a model reading
each row — are what the questions land on. Sixty-three records cost nothing;
three of them are what anybody reads.
"""

from __future__ import annotations

from . import Corpus, Item, Question

FLEET = "FLEET-DEMO-COLDCHAIN"


def _reading(unit: str, day: int, hour: int, celsius: float | None, *, note: str = "") -> Item:
    """One reading. `None` is a sensor that reported nothing, which is not zero
    and not warm — the two are different faults and a corpus that renders them
    the same teaches the wrong lesson."""
    value = "no reading" if celsius is None else f"{celsius:.1f}"
    return Item(
        external_id=f"{unit}-d{day}-h{hour:02d}",
        data_type="reading", source_type="sensor",
        days_ago=day, tags=(unit,), identifiers=(FLEET,), enrich=False,
        text=(f"unit={unit} metric=air_temp_c value={value} "
              f"threshold_c=-18.0 hour={hour:02d}:00"
              + (f"\nnote: {note}" if note else "")),
    )


def _series() -> tuple[Item, ...]:
    items: list[Item] = []
    # FRZ-01 drifts warm across one weekend and recovers. The event the whole
    # use case exists to notice.
    warm = {9: -17.2, 8: -15.4, 7: -14.9}
    for day in range(12, 5, -1):
        for hour in (2, 10, 18):
            if day in warm and hour == 10:
                items.append(_reading("FRZ-01", day, hour, warm[day],
                                      note="above threshold"))
            else:
                items.append(_reading("FRZ-01", day, hour, -19.4))
    # FRZ-02 is uneventful, so "which one went warm" has a wrong answer available.
    for day in range(12, 5, -1):
        for hour in (2, 10, 18):
            items.append(_reading("FRZ-02", day, hour, -20.1))
    # FRZ-03 stops reporting. Silence is its own fault and reads differently
    # from a warm cabinet.
    for day in range(12, 5, -1):
        for hour in (2, 10, 18):
            items.append(_reading("FRZ-03", day, hour,
                                  None if day <= 9 else -19.8,
                                  note="sensor offline" if day <= 9 else ""))
    return tuple(items)


def _digest(unit: str, day: int, text: str) -> Item:
    """What a nightly aggregation job wrote, not what a model read.

    Enriched, because these are the records a question is meant to land on --
    three of them against sixty-three readings, which is the ratio the whole
    use case is about.
    """
    return Item(
        external_id=f"digest-{unit}-d{day}",
        data_type="report", source_type="job", days_ago=day,
        tags=("digest", unit), identifiers=(FLEET,), enrich=True,
        text=text.strip(),
    )


DIGESTS = (
    _digest("FRZ-01", 7, """
Cold-chain daily digest — unit FRZ-01
Threshold -18.0 C. Readings above threshold on three consecutive days: 9 days
ago at -17.2 C, 8 days ago at -15.4 C, and 7 days ago at -14.9 C, each on the
10:00 reading. The unit returned to -19.4 C afterwards and has held since.
No other unit in the fleet exceeded threshold in this window.
"""),
    _digest("FRZ-02", 7, """
Cold-chain daily digest — unit FRZ-02
Threshold -18.0 C. No readings above threshold in this window. The unit held
between -20.1 C and -20.1 C across every reading. Nothing to report.
"""),
    _digest("FRZ-03", 7, """
Cold-chain daily digest — unit FRZ-03
Threshold -18.0 C. This unit stopped reporting nine days ago and has returned no
readings since; the sensor is offline. **This is not the same as running warm.**
The last reading before the outage was -19.8 C, within threshold. A unit that
reports nothing is a unit nobody is measuring, and it is the failure that hides.
"""),
)


CORPUS = Corpus(
    key="sensors",
    title="A sensor fleet",
    blurb="63 raw readings that never reach a model, and the 3 derived digests "
          "that do.",
    note="Synthetic telemetry — invented units on a reserved fleet id, generated "
         "for a product demonstration.",
    memory_key="cold-chain",
    memory_type="tracing",
    default_identifier=FLEET,
    ttl_seconds=259_200,
    on_expiry="orphan_delete",
    enrich=False,
    items=_series() + DIGESTS,
    questions=(
        Question("Which unit went above -18C, and on which days?", "digest-FRZ-01-d7"),
        Question("Which unit stopped reporting altogether?", "digest-FRZ-03-d7"),
    ),
)
