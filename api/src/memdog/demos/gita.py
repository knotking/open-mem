"""The Bhagavad Gita — the corpus that is worth *looking at*, not only asking.

Every other entry in the gallery is answered by reading. This one is the first
that is answered by reading *and* by seeing the shape of what was read, and the
difference is the whole reason it is here.

**Why this text and not another.** `graph_templates.scripture` was written
before any corpus existed to exercise it, down to its citation unit — *"a chunk
id is the wrong provenance for scripture, where the whole commentary tradition
addresses 'BG 2.47'"*. The template offers `teaches`, `leads_to` and
`contrasts_with`, and those three predicates are exactly what this text is made
of: somebody puts a doctrine forward, the text says one thing follows from
another, and it sets paths against each other. A novel would fill `part_of` and
little else. This is the document the template was shaped for, so it is the
honest test of whether premeditating a graph schema was worth doing.

**What it demonstrates that its neighbours do not.** The legal, meetings and
sensor corpora each show a *retrieval* property. This one shows an *extraction*
property: that a declared schema turns 701 short passages into a graph somebody
can read, where open-domain extraction over the same text would produce seven
hundred `related_to` edges asserting that two words occurred near each other.
The chain at BG 2.62–63 — sense objects to attraction to desire to anger to
delusion to ruin — is the canonical case: a template records it as five
directed `leads_to` edges, and open extraction records it as a cloud.

**One verse, one record.** The unit is chosen by the citation, not by
convenience. A chapter-sized record would answer "what is chapter 2 about" and
would be unable to cite BG 2.47, which is the reference a reader already knows
and the only one they can check. 701 records is also what makes the graph worth
drawing: an edge asserted by nine separate verses is a different claim from one
asserted once, and `evidence` can only count that if the verses are separate.

**Provenance, and the copyright care `papers.py` already argues for.** The
Sanskrit is roughly two thousand years old and belongs to nobody. The English
does not automatically: a translation is its own copyrightable work, and
shipping a public, unauthenticated demo built on one somebody still owns would
be exactly the failure the synthetic marker exists to prevent, pointed the other
way. Of the five English translations the source dataset carries, Shri Purohit
Swami's (1935) is the one with a clean public-domain case — the translator died
in 1941, so life-plus-seventy expired in 2012, and India's life-plus-sixty
expired in 2002. The other four are living-estate works of the mid-twentieth
century and are not used here. `synthetic=False` for the same reason it is false
in `papers.py`: stamping *"generated, not real"* across the Bhagavad Gita would
be a false statement about a real text.

**It fetches rather than carries a copy** — but not for `papers.py`'s reason.
That corpus fetches because a researcher's publication record changes and a copy
in this repository would age. This text has not changed in two millennia. What
would age is the *assembly* — the verse keying, the transliteration scheme, the
alignment between translation and sloka — which is the dataset's work and not
ours, and half a megabyte of it pasted into this repository would be a second
copy nobody reconciles.
"""

from __future__ import annotations

import asyncio

import httpx

from . import Corpus, Item, Question

# The `gita/gita` dataset: the Sanskrit, a transliteration, and several
# translations, all keyed by chapter and verse.
#
# **Pinned to a commit, not to `main`.** The repository publishes no tags, and
# an unpinned raw URL means the corpus can change under a re-seed -- which would
# show up as sample questions failing for a reason nobody can find anywhere in
# this repository. This is the commit the data files were last written in.
COMMIT = "c6fce39595445768876ddbb8d1268a9c935e1d2b"
DATA = f"https://raw.githubusercontent.com/gita/gita/{COMMIT}/data"

# See the module docstring. This is the one of the five with a clean
# public-domain case, and the choice is load-bearing rather than aesthetic.
TRANSLATOR = "Shri Purohit Swami"
TRANSLATION_YEAR = 1935


async def _json(client: httpx.AsyncClient, name: str):
    """One file. A failure raises, which fails the seed -- the alternative is a
    corpus that is quietly missing its translations or its chapter titles."""
    response = await client.get(f"{DATA}/{name}")
    response.raise_for_status()
    return response.json()


async def fetch() -> list[Item]:
    """701 verses, each a record that can be cited as the tradition cites it."""
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        chapters, verses, translations = await asyncio.gather(
            _json(client, "chapters.json"),
            _json(client, "verse.json"),
            _json(client, "translation.json"),
        )

    # Keyed by the dataset's global verse id, which is what the translation rows
    # join on -- `verse_id` counts straight through all eighteen chapters, so it
    # is not a verse *number* and must not be used as one.
    english = {
        row["verse_id"]: (row.get("description") or "").strip()
        for row in translations
        if row.get("authorName") == TRANSLATOR
    }
    if not english:
        # Named rather than silently producing a corpus of Sanskrit nobody can
        # read. A dataset that renames or drops this translator is a copyright
        # decision, not a formatting one, and it has to be made deliberately.
        raise RuntimeError(
            f"the source dataset no longer carries {TRANSLATOR}'s translation — "
            "pick a replacement whose public-domain case is as clean, do not "
            "silently substitute one of the others")

    titles = {
        int(c["chapter_number"]): (
            (c.get("name_transliterated") or "").strip(),
            (c.get("name_meaning") or "").strip(),
        )
        for c in chapters
    }

    items: list[Item] = []
    for verse in sorted(verses, key=lambda v: v["verse_order"]):
        chapter, number = int(verse["chapter_number"]), int(verse["verse_number"])
        translated = english.get(verse["id"], "")
        if not translated:
            continue
        sanskrit = (verse.get("text") or "").strip()
        roman = (verse.get("transliteration") or "").strip()
        name, meaning = titles.get(chapter, ("", ""))

        # The English first, deliberately. It is what the embedder indexes, what
        # a citation quotes back and what a visitor reads; the Sanskrit and the
        # transliteration are below it as the thing being translated rather than
        # as a header the model has to wade through to reach the argument.
        body = [f"Bhagavad Gita {chapter}.{number}"]
        if name:
            body.append(f"Chapter {chapter} — {name}"
                        + (f" ({meaning})" if meaning else ""))
        body += ["", translated]
        if roman:
            body += ["", roman]
        if sanskrit:
            body += ["", sanskrit]

        items.append(Item(
            # The reference the tradition already uses, and the citation unit
            # the template declares. An opaque id here would make every answer
            # uncheckable by the one reader most likely to check it.
            external_id=f"BG {chapter}.{number}",
            text="\n".join(body),
            data_type="scripture",
            source_type="gita-dataset",
            # **Every verse carries the same date, and that is the honest
            # answer.** `event_time` becomes `valid_from` on every fact drawn
            # from the record, so spreading the verses across a fake calendar
            # would put a temporal claim into the graph -- "this became true
            # before that" -- about a text with no chronology at all. The order
            # is real and is carried where it belongs: in the reference.
            days_ago=0,
            tags=("scripture", f"chapter:{chapter}"),
            identifiers=("bhagavad-gita",),
            # The reason this corpus exists. Without it the same 701 records
            # produce a graph of `related_to`, which is the null result the
            # template was written to avoid.
            template="scripture",
        ))
    return items


CORPUS = Corpus(
    key="gita",
    title="The Bhagavad Gita",
    blurb="701 verses read under a declared graph schema — the only corpus here "
          "you can look at as well as ask. Who teaches what, and what the text "
          "says leads to what.",
    note=f"The Bhagavad Gita. Sanskrit text in the public domain; English "
         f"translation by {TRANSLATOR} ({TRANSLATION_YEAR}), also public domain. "
         f"Verse keying, transliteration and alignment from the open gita/gita "
         f"dataset. Real text, not generated — the graph above it is extracted "
         f"by a model under the `scripture` template and is a reading, not the "
         f"text's own claim.",
    memory_key="bhagavad-gita",
    memory_type="scripture",
    default_identifier="bhagavad-gita",
    # A scripture does not expire, which is the contrast with the sensor
    # corpus's tracing memory rather than an oversight.
    ttl_seconds=None,
    synthetic=False,
    items=(),
    fetch=fetch,
    questions=(
        # Each names the verse that must answer it, and each is phrased the way
        # somebody would actually ask rather than in the words of the verse --
        # a question that merely quotes its own answer proves the lexical arm
        # works and nothing else. They are also chosen to land on the three
        # predicates the template offers: a teaching, a causal chain, and a
        # contrast.
        Question("What does the Gita say about the right to work but not to "
                 "its fruits?", "BG 2.47"),
        Question("What chain does the text say begins with dwelling on the "
                 "objects of sense and ends in ruin?", "BG 2.62"),
        Question("Is it better to do your own duty badly or someone else's "
                 "well?", "BG 3.35"),
        Question("What does Krishna say he does whenever spirituality decays?",
                 "BG 4.7"),
        Question("What are the three qualities of nature that bind the spirit?",
                 "BG 14.5"),
    ),
)
