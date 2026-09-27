"""A matter: the asserted / inferred distinction, which nothing else shows.

Use case 7. The mechanism on display is not retrieval — it is that *"this
document is in the matter"* and *"this document appears related"* are
categorically different claims. A system that collapses them is useless for
either purpose: you cannot produce an inferred set in discovery, and you cannot
rely on an asserted set that quietly includes guesses.

So the corpus is written so that the distinction is visible rather than
described. Two documents genuinely belong to the matter. Two more mention the
docket number in passing and are exactly the kind of thing an identifier match
would sweep in — one of them is about a different matter entirely, and one is a
newsletter. A reader can see which is which, which is the point.

**Every record here is invented.** The docket, the parties, the firm and the
addresses are reserved or `.example`, and the marker is in the body of each
record rather than beside it. A synthetic contract that reads as real is a
document somebody can misuse, and this one is written not to.
"""

from __future__ import annotations

from . import Corpus, Item, Question

DOCKET = "M-DEMO-2291"

CORPUS = Corpus(
    key="legal",
    title="A matter",
    blurb="Asserted vs inferred membership — what is in the matter, and what merely "
          "mentions it.",
    note="Synthetic matter — invented parties, a reserved docket, generated text. "
         "Not a real contract and not legal advice.",
    memory_key="matter-file",
    memory_type="matter",
    default_identifier=DOCKET,
    items=(
        Item(
            external_id="doc-msa-northwind",
            data_type="contract", source_type="upload", days_ago=290,
            tags=("asserted", "privileged"),
            text=f"""Master Services Agreement — Northwind Trading (demo) and Contoso Pacific (demo)
Docket {DOCKET}. Executed 4 November 2025.

3.1 Term. This agreement runs for twenty-four months from the effective date and
renews for successive twelve-month terms unless either party gives written
notice not less than ninety days before the end of the then-current term.

7.2 Limitation of liability. Neither party's aggregate liability shall exceed
the fees paid in the twelve months preceding the claim. This cap does not apply
to breaches of clause 9 (confidentiality) or to indemnified IP claims.

9.4 Confidentiality survives termination by three years.

11.1 Governing law: the courts of the demonstration jurisdiction.""",
        ),
        Item(
            external_id="doc-notice-renewal",
            data_type="correspondence", source_type="email", days_ago=44,
            tags=("asserted",),
            text=f"""From: counsel@northwind.example
To: legal@contoso-pacific.example
Subject: Notice under clause 3.1 — docket {DOCKET}
Date: 7 July 2026

We write to give notice under clause 3.1 of the Master Services Agreement dated
4 November 2025 that Northwind does not intend to renew the current term, which
ends 4 November 2027. Ninety days' notice is therefore satisfied with margin.

This notice is given without prejudice to the open question under clause 7.2
regarding the indemnity cap, which remains unresolved between us.""",
        ),
        Item(
            external_id="doc-unrelated-matter",
            data_type="correspondence", source_type="email", days_ago=30,
            tags=("inferred",),
            text=f"""From: paralegal@northwind.example
To: records@northwind.example
Subject: filing note — please check

Filed the Ridgeway bundle this morning. While reconciling the index I noticed
our reference for it was typed as {DOCKET}, which is the Contoso matter, not
Ridgeway. The Ridgeway docket is M-DEMO-2318. Correcting the index now.

Flagging because a search on the wrong docket will pull this thread into the
Contoso file, and it has nothing to do with it.""",
        ),
        Item(
            external_id="doc-newsletter",
            data_type="note", source_type="feed", days_ago=12,
            tags=("inferred",),
            text=f"""Demonstration Legal Weekly — issue 402

Renewal and notice clauses continue to generate the largest share of contested
correspondence in commercial disputes, according to figures compiled this
quarter. Practitioners cite ninety-day windows as the most frequently missed.

Matters referenced in this issue: {DOCKET}, M-DEMO-1180, M-DEMO-2044.

This is a synthetic newsletter written for a product demonstration.""",
        ),
    ),
    questions=(
        Question("What notice period does the agreement require, and was it met?",
                 "doc-notice-renewal"),
        Question("Is there a cap on liability, and what is excluded from it?",
                 "doc-msa-northwind"),
        Question("Which documents mention the docket without belonging to the matter?",
                 "doc-unrelated-matter"),
    ),
)
