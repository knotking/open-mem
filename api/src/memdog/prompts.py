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


# data_type -> prompt. The cascade produces the data_type; this maps it onto the
# agent that handles it, which is the whole reason those are separate fields.
BY_DATA_TYPE: dict[str, str] = {
    "chat_message": CHAT_MESSAGE,
    "message_email": EMAIL_MESSAGE,
    "document_pdf": DOCUMENT,
    "document_text": DOCUMENT,
    "document_html": DOCUMENT,
    "transcript": TRANSCRIPT,
    "structured_json": STRUCTURED_RECORD,
    "structured_csv": STRUCTURED_RECORD,
    "sensor_gps": STRUCTURED_RECORD,
    "image": IMAGE,
    "code": CODE_OR_CONFIG,
    "binary_blob": GENERIC,
}

NAMES: dict[str, str] = {
    id(v): k for k, v in {
        "chat_message": CHAT_MESSAGE, "email_message": EMAIL_MESSAGE,
        "document": DOCUMENT, "transcript": TRANSCRIPT,
        "structured_record": STRUCTURED_RECORD, "code_or_config": CODE_OR_CONFIG,
        "image": IMAGE, "generic": GENERIC,
    }.items()
}


def for_data_type(data_type: str | None) -> tuple[str, str]:
    """Returns (prompt_name, prompt). Falls back to the generic block, never to
    nothing -- an unhandled type still has to produce a renderable envelope."""
    prompt = BY_DATA_TYPE.get(data_type or "", GENERIC)
    return NAMES.get(id(prompt), "generic"), prompt
