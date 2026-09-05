"""The shipped extraction prompts.

Data, not code. Each is a row in the `generators` registry, hashed into
`generator_version` -- so changing one is a versioning event that makes every
artifact it produced detectably stale, exactly like changing a model.

Each prompt is the shared skeleton plus a type-specific block. The blocks are
short on purpose: **the schema already specifies the shape**, so the prompt only
has to say what is *interesting* about this type.
"""

from __future__ import annotations

# Every block ends up appended to the same injection-defended skeleton in
# extraction.py. A per-agent variation of that skeleton would be a per-agent
# hole, so the variation lives here and the defence does not.

CHAT_MESSAGE = """Extract from a chat or instant message. Messages are short, contextual and often
elliptical -- they reference earlier turns you cannot see.

title:       the topic in a few words, not the message text verbatim
description: name the channel or medium if evident -- "A direct message about..."
keywords:    the subject matter, never the participants' names

Do not reconstruct missing context. If a message refers to "it" or "that",
record the reference as written rather than resolving it.

Emphasise commitments ("I'll send it Friday"), decisions, and requests directed
at a person. These are the durable content of a conversation; pleasantries are not.

If the message carries no extractable content -- an acknowledgement, a reaction --
set summary to null and return empty arrays. Still produce a title.
An empty extraction is a correct extraction."""

EMAIL_MESSAGE = """Extract from an email.

title:       the subject line if it is meaningful; otherwise generate one.
             Strip "Re:", "Fwd:" and ticket-number prefixes.
description: "An email from X to Y regarding..." -- one sentence.

Distinguish the NEW content of this message from quoted history beneath it.
Extract only the new content, but note whether quoted material was present.

Signature blocks, disclaimers and footers are NOT content. Do not extract
entities that appear only in a signature or legal footer.

Attachments are processed separately. Reference them by name only; do not
speculate about contents you cannot see.

Emphasise commitments and deadlines. Email is where obligations are created."""

DOCUMENT = """Extract from a document.

title:       the document's own title if present. If generating one, describe the
             document, not its subject -- "Q3 security review" not "Security".
keywords:    include domain terms a specialist would search, not only common words.

Preserve structure: if there are sections or headings, reflect that in the summary
rather than flattening it.

Extract claims the document ASSERTS. Distinguish these from claims it quotes,
cites or attributes to others -- set the attributed source when the document is
not speaking in its own voice.

Tables are data, not prose. Extract their subject and shape; do not transcribe
cell values into claims.

Record the document's own date, marked "document date"."""

TRANSCRIPT = """Extract from a transcript of spoken conversation.

title:       what the meeting was ABOUT, not its calendar name. "Renewal pricing
             objection" beats "Weekly sync".
description: "A call between X and Y in which..."

Speech is disfluent; ignore filler, repetition and false starts.

Attribute every intent to a speaker. An unattributed commitment is nearly useless
-- if the speaker cannot be determined, set the actor to null rather than guessing.

Distinguish a decision ("we're going with option B") from a proposal that was not
resolved ("what if we did B?"). The second is a question, not a decision.

Timestamps in the transcript are positions in the recording, not dates that were
discussed. Do not record them as dates."""

STRUCTURED_RECORD = """This record has already been normalized into typed fields.

title:       build from the structured fields -- "INV-2291 - Acme - $12,400".
             Deterministic and useful beats descriptive and invented.
description: name the record type and its state -- "An open support ticket..."

Do NOT re-extract what the structured fields already carry. You will duplicate
them less accurately.

Extract only from free text: descriptions, comments, notes. Your job is what the
schema could not capture -- the reason behind a status, the sentiment of a comment
thread, the commitment buried in a note.

If the free text adds nothing beyond the structured fields, return core fields
only and leave the standard arrays empty."""

CODE_OR_CONFIG = """Extract from source code or configuration.

title:       "path/to/file -- what it does"
keywords:    language, framework, and the systems it touches.

Describe purpose and interface, not implementation.

Do NOT extract secrets, credentials, tokens or keys, even where present. If
credential-shaped material is found, record its presence as "contains
credential material" without reproducing the value.

Identifiers are entities only when they name a system, service or component --
not for every variable."""

IMAGE = """Extract from a description of an image, or from text transcribed out of one.

title:       what the image shows, in a few words.
keywords:    visible subjects and any transcribed text worth searching.

Do not infer context that the description does not state. A description saying
"two people at a table" does not establish who they are or why they met."""

GENERIC = """Extract from a record of unknown type.

title:       describe what this record is, in a few words. Always produce one --
             a list view cannot render a row without it.

Extract only what is plainly present. Where the type is unclear, a sparse,
correct extraction is better than a rich, speculative one."""


SPREADSHEET = """Extract from a spreadsheet or tabular export.

A table is not prose and must not be summarised as though it were. What a
reader needs is its shape.

title:       what the table is OF -- "Q3 pipeline by region", not "Spreadsheet".
description: name the columns and say how many rows, in one sentence.
summary:     what the data covers -- the range of dates, the categories present,
             the magnitudes involved. Not a narration of individual rows.
keywords:    column names and the domain terms in them.

Never invent totals or averages you have not been given. A number stated
confidently and wrongly is worse than an absent one, because a reader cannot
tell the two apart from the summary.

If several sheets are present, say so and describe each briefly. If the first
row is clearly a header, treat it as one rather than extracting it as data."""

PRESENTATION = """Extract from a slide deck.

A deck is an argument delivered in fragments, and the argument is the
interesting part -- no single slide contains it.

title:       the deck's own title slide if present.
description: who it was for and what it argued, where that is evident.
summary:     the through-line across the slides, not a slide-by-slide list.

Speaker notes, where present, are usually more substantive than the slide text.
Weight them accordingly.

Bullet fragments are not sentences. Do not pad them into sentences that claim
more than the slide did."""

CALENDAR = """Extract from a calendar event or invitation.

This type maps almost perfectly onto structured fields, so prefer precision
over prose.

title:       the event's own summary line.
description: "A meeting on <date> with N attendees", or the equivalent.
keywords:    the subject matter, never the attendees' names.

Record the organiser and attendee count in fields. Do not list attendees by
name in keywords -- an attendee list is personal data and belongs in the entity
layer where it is governed, not in a free-text field where it is not.

A recurring event describes a series. Say so, rather than describing one
instance as though it were the whole thing.

If the event has nothing beyond a title, that is a complete extraction. Do not
speculate about its purpose."""

CONTACT = """Extract from a contact record.

This is personal data about an identifiable person, and it is almost entirely
structured. Extract exactly what is present.

title:       the person's or organization's name as written.
description: role and affiliation if stated -- nothing inferred.

Do not enrich. Do not guess a company from an email domain, a location from a
phone prefix, or anything at all from a name. The value of a contact record is
that it is what someone entered; a plausible invention is indistinguishable
from it afterwards and cannot be separated out again."""

LOG = """Extract from a log file or diagnostic output.

Logs are overwhelmingly repetitive, and nearly all the information is in the
small part that differs.

title:       what produced it, and over what period.
description: the time span covered and roughly how many lines.
summary:     the errors and anomalies, the distinct failure modes, and whether
             they cluster in time. Not a description of normal operation.

Report the shape of the failures rather than every instance: "142 connection
timeouts to the payments host, all between 02:10 and 02:40" beats reproducing
any one of them.

If nothing anomalous is present, say so plainly. A quiet log is a real and
useful finding."""

CONFIG = """Extract from a configuration or infrastructure-definition file.

title:       what it configures.
description: the system or tool, and the environment where evident.
keywords:    service, resource and component names.

**Never reproduce a secret.** Keys, tokens, passwords and connection strings
must be reported as present and redacted. Naming the variable is useful; naming
its value leaks it into the summary, and from there into embeddings and answers,
where it cannot be recalled.

Describe what the configuration does, not its syntax."""

AUDIO = """Extract from an audio recording, working from its transcript.

title:       what the recording is about, never "Audio recording".
description: the kind of audio -- a call, a voice note, a meeting, a broadcast --
             and its approximate length if known.
summary:     what was discussed and decided.

Speaker labels are frequently wrong or missing in automatic transcription.
Attribute statements only where the transcript is explicit, and prefer "one
speaker said" to guessing which.

Transcription errors cluster in names, numbers and technical terms. Where a term
is clearly garbled, do not confidently normalise it into something plausible --
a wrong name recorded confidently is worse than an uncertain one, because
nothing downstream can tell it was a guess.

Emphasise decisions, commitments and action items. Speech is where things get
agreed, and where they are least likely to be written down anywhere else."""

VIDEO = """Extract from a video recording.

Two channels carry meaning and they are not the same: what is said, and what is
shown. A screen-share of a dashboard and a conversation about it are different
content.

title:       what the video is about.
description: its kind -- a meeting, a screen recording, a demo, a clip -- and length.
summary:     the spoken content first, then anything visually distinct that the
             speech does not already cover.

If it is primarily a screen recording, what is on screen may matter more than the
narration. Say which one you drew from.

Do not narrate the visuals shot by shot. As with audio, treat speaker attribution
and garbled terms with suspicion."""

ARCHIVE = """Extract from an archive or container file.

The archive itself has almost no content. Its members do, and they are processed
separately.

title:       what the archive appears to contain, from its name and its listing.
description: how many members, and of what kinds.
summary:     the structure -- top-level folders, the dominant file types.

Do not speculate about the contents of members whose names are all you have seen.
Listing them is useful; imagining what is inside them is not."""

GEO = """Extract from geospatial data -- a track, a route, or a set of features.

title:       what the data describes -- a journey, an area, a set of locations.
description: the kind of geometry, and how many features or points.
summary:     the extent covered, the time span if the points are timestamped, and
             anything distinctive about the shape of the data.

Location data is sensitive. Describe the extent -- a city, a region -- rather than
reproducing precise coordinates in free text, where they escape the governance
that applies to the structured fields.

Do not infer a purpose. A track between two points is a track between two points."""


WEB_PAGE = """Extract from a web page, and judge it.

A web page is not a document somebody handed you. It was published, and it was
optimised for something -- ranking, converting, persuading, or occasionally
informing. Say what it is and how much of it is worth anything.

title:       the page's own headline, not the browser title with the site name
             bolted on. "Postgres index bloat" not "Postgres index bloat | Acme
             Blog | Acme".
keywords:    domain terms a specialist would search, not the site's own tag soup.

Ignore the furniture. Navigation, footers, cookie banners, newsletter prompts,
share buttons, related-article rails, comment threads and "you may also like"
blocks are not the page. Nothing in them belongs in the summary, and a claim
made in a comment is not a claim the page makes.

**First decide whether this is content at all.** An error page, a login wall, a
paywall stub, a consent interstitial and a parked domain all return HTTP 200 and
all produce fluent text. Say so plainly when that is what arrived, and do not
summarise the apology as though it were an article.

Then fill `quality`, which is the point of reading a page rather than a file:

page_kind        what it IS, structurally.
purpose          what it is trying to get the reader to do or believe. Every
                 page wants something; "inform" is an answer, not a default.
substance        could a reader get this anywhere, or is something here that
                 only this page has? Original reporting, primary data, direct
                 experience and specific numbers are substance. Restating the
                 question five ways is not.
evidence         does it name sources, link them, quantify, or simply assert?
                 Confidence is not evidence: a page with no sources and no
                 hedging is `asserted`, however authoritative it sounds.
authorship       the byline and any stated credentials. "unattributed" when
                 there is none -- which is itself the finding.
dated            publication and update dates the page states. Say "undated"
                 rather than inferring one; a rebuild date is not a write date.
commercial       how the page is monetised, where that shapes what it says.
reliability      up to five specific reasons to trust or doubt this page.
                 Specific: "cites the 2024 CVE by number" or "every statistic
                 is unsourced", never "seems reliable".
missing          up to five questions the page raises and does not answer.
retrieval_value  whether this is worth keeping in a searchable corpus, and the
                 one field a person will actually filter on.
verdict          one short paragraph: what this is, whether to keep it, what
                 for.

Length is not substance. A four-thousand-word page that says one thing slowly
is `thin`, and saying so is more useful than a polite summary of the padding.

Extract claims the page ASSERTS, and keep them separate from what it quotes or
attributes. A page reporting somebody else's study is not the study."""


# data_type -> prompt. The cascade produces the data_type; this maps it onto the
# agent that handles it, which is the whole reason those are separate fields.
BY_DATA_TYPE: dict[str, str] = {
    "chat_message": CHAT_MESSAGE,
    "message_email": EMAIL_MESSAGE,
    "document_pdf": DOCUMENT,
    "document_text": DOCUMENT,
    # A fetched page is not a document that happens to be HTML. It has an
    # agenda, furniture, and a real chance of not being content at all, and
    # those are different questions from the ones a PDF raises.
    "document_html": WEB_PAGE,
    "transcript": TRANSCRIPT,
    "structured_json": STRUCTURED_RECORD,
    "structured_csv": STRUCTURED_RECORD,
    "sensor_gps": STRUCTURED_RECORD,
    "image": IMAGE,
    "code": CODE_OR_CONFIG,
    "binary_blob": GENERIC,
    # Types whose interesting questions genuinely differ from a document's.
    # The test for adding one is not "is this a distinct file format" -- it is
    # "would a reader ask something different of it". A .docx asks the same
    # questions as a PDF and shares its prompt; a spreadsheet does not.
    "document_office": DOCUMENT,
    "document_markup": DOCUMENT,
    "spreadsheet": SPREADSHEET,
    "presentation": PRESENTATION,
    "calendar": CALENDAR,
    "contact": CONTACT,
    "log": LOG,
    "config": CONFIG,
    "audio": AUDIO,
    "video": VIDEO,
    "archive": ARCHIVE,
    "geo": GEO,
}

NAMES: dict[str, str] = {
    id(v): k for k, v in {
        "chat_message": CHAT_MESSAGE, "email_message": EMAIL_MESSAGE,
        "document": DOCUMENT, "transcript": TRANSCRIPT,
        "structured_record": STRUCTURED_RECORD, "code_or_config": CODE_OR_CONFIG,
        "image": IMAGE, "generic": GENERIC, "web_page": WEB_PAGE,
        "spreadsheet": SPREADSHEET, "presentation": PRESENTATION,
        "calendar": CALENDAR, "contact": CONTACT, "log": LOG,
        "config": CONFIG, "audio": AUDIO, "video": VIDEO,
        "archive": ARCHIVE, "geo": GEO,
    }.items()
}


def for_data_type(data_type: str | None) -> tuple[str, str]:
    """Returns (prompt_name, prompt). Falls back to the generic block, never to
    nothing -- an unhandled type still has to produce a renderable envelope."""
    prompt = BY_DATA_TYPE.get(data_type or "", GENERIC)
    return NAMES.get(id(prompt), "generic"), prompt


def registry() -> list[dict]:
    """The data-type to prompt mapping, as data.

    Served to the console so its list cannot drift from the register the way a
    hardcoded one did -- it showed six types while twenty-four were routed,
    which is exactly the kind of divergence that is invisible until someone
    looks for a type that is missing.
    """
    from .classify import _EXTENSION_MAP, _MIME_MAP, _SOURCE_TYPE_MAP

    extensions: dict[str, list[str]] = {}
    for extension, data_type in _EXTENSION_MAP.items():
        extensions.setdefault(data_type, []).append("." + extension)
    mimes: dict[str, list[str]] = {}
    for mime, data_type in _MIME_MAP.items():
        mimes.setdefault(data_type, []).append(mime)

    shared: dict[int, int] = {}
    for prompt in BY_DATA_TYPE.values():
        shared[id(prompt)] = shared.get(id(prompt), 0) + 1

    rows = []
    for data_type in sorted(set(BY_DATA_TYPE) | set(_EXTENSION_MAP.values())
                            | set(_MIME_MAP.values()) | set(_SOURCE_TYPE_MAP.values())):
        name, prompt = for_data_type(data_type)
        rows.append({
            "data_type": data_type,
            "prompt": name,
            # Sharing a prompt is a decision, not an omission: a .docx asks a
            # PDF's questions. Saying which are shared makes that visible.
            "shared": shared.get(id(prompt), 0) > 1,
            "is_default": prompt is GENERIC and data_type not in BY_DATA_TYPE,
            "extensions": sorted(extensions.get(data_type, [])),
            "mime_types": sorted(mimes.get(data_type, [])),
            "excerpt": prompt.strip().split("\n")[0],
        })
    return rows
