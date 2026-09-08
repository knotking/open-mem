"""A recurring meeting: the same question asked four times, and what moved.

Use case 12. What this corpus shows that the others do not is *change* — four
instances of one weekly review, where the interesting answer is never "what does
it say" but "what is different since last time". A pile of transcripts answers
from all four at once; a sequence answers from the last one and can say what
moved.

The corpus is written so the movement is real and traceable: one date slips
twice and then holds, an owner changes, and a risk that is raised in week one
is closed in week four. A reader can check the answer against the records, which
is the only kind of answer worth demonstrating.

**This is also the corpus that shows the gap.** The catalog specifies a
`continues` link from each instance to the previous one, and nothing in the
system reads that relation — see `docs/use-cases/README.md`. The records carry
the sequence in their own text and identifiers, so the corpus is honest about
what it demonstrates today rather than implying an edge that does nothing.
"""

from __future__ import annotations

from . import Corpus, Item, Question

SERIES = "SERIES-DEMO-PLATFORM-REVIEW"


def _instance(n: int, days_ago: int, body: str) -> Item:
    return Item(
        external_id=f"review-week-{n}",
        data_type="meeting", source_type="transcript", days_ago=days_ago,
        tags=(f"week-{n}",), identifiers=(SERIES,),
        text=f"Platform review — week {n} of the series\n\n{body.strip()}",
    )


CORPUS = Corpus(
    key="meetings",
    title="A weekly review",
    blurb="Four instances of one recurring meeting — what changed since last time, "
          "not what was said.",
    note="Synthetic transcripts — invented people at reserved .example addresses, "
         "generated for a product demonstration.",
    memory_key="platform-review",
    memory_type="session",
    default_identifier=SERIES,
    items=(
        _instance(1, 28, """
Present: Dana Whitfield, Priya Raman, Sam Okonjo.

Migration cutover is scheduled for 12 September. Priya owns it.

Priya raised a risk: the replica lag under load has not been measured, and the
cutover plan assumes it is under thirty seconds. Nobody has tested that. Marked
open.

Dana asked whether the rollback path had been exercised. It has not. Also open.
"""),
        _instance(2, 21, """
Present: Dana Whitfield, Priya Raman.

Cutover moves from 12 September to 19 September. The reason is the replica lag
work, which is not finished — measurement now shows lag reaching four minutes
under the load we expect, well outside the thirty seconds the plan assumed.

Rollback path: still not exercised. Priya will schedule it.

Sam absent.
"""),
        _instance(3, 14, """
Present: Dana Whitfield, Priya Raman, Sam Okonjo.

Cutover moves again, from 19 September to 3 October. Lag is now measured at
under twenty seconds after the index change, so that risk is closed. The new
delay is not technical — the change window collides with the quarter close.

Ownership of the cutover passes from Priya to Sam, because Priya is on the
compliance audit through October.

Rollback path exercised on Tuesday. It worked, and took eleven minutes.
"""),
        _instance(4, 7, """
Present: Dana Whitfield, Sam Okonjo.

Cutover holds at 3 October. No change this week, which is the first week that
has been true.

All risks raised in week one are now closed: replica lag measured and mitigated,
rollback exercised. Sam owns the cutover.

Dana noted that the date moved twice before it settled, and asked that the next
series start with the change window checked against the finance calendar.
"""),
    ),
    questions=(
        Question("When is the cutover now, and how many times has the date moved?",
                 "review-week-4"),
        Question("Why did the cutover slip from 19 September?", "review-week-3"),
        Question("Who owns the cutover, and did that change?", "review-week-3"),
        Question("What was the replica lag risk, and was it closed?", "review-week-2"),
    ),
)
