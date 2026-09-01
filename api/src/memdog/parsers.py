"""Format handlers -- turning bytes into text the spine can index.

Tiers, not a count. "60+ MIME types" does not mean sixty formats are equally
well handled, and pretending otherwise is how a support matrix becomes a lie:

- **A** — full extraction. Native parse, no model.
- **B** — text extraction, shallow structure.
- **C** — stored, metadata only. Versioned, tagged, findable by filename and
  context; not content-searchable. **Nothing is rejected.**

Everything here is deterministic. The formats that need a model -- OCR, image
captioning, audio and video transcription -- are Tier C for now and say so with
a reason, because they are the expensive tier by a wide margin and gating them
is a cost decision, not a technical one.

Three rules the handlers share:

**Never crash a worker.** A password-protected PDF, a corrupt archive and a
truncated spreadsheet are all ordinary inputs. They produce a `ParseFailed` with
a reason and are *not* retried, because retrying a file that will never parse is
a busy loop wearing a failure's clothes.

**Detect the encoding.** Latin-1, Windows-1252 and Shift-JIS are what real
exports contain. Without detection you embed mojibake, and mojibake retrieves
nothing while looking like it worked.

**Cap the output.** One 500k-row spreadsheet can consume a workspace's entire
embedding budget. Extraction stops at a documented ceiling and says it did.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import tarfile
import zipfile
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from typing import Callable

log = logging.getLogger(__name__)

# One document must not consume a workspace's budget. Both are deliberate,
# documented ceilings rather than incidental limits.
MAX_TEXT_CHARS = 2_000_000
MAX_ARCHIVE_MEMBERS = 200
MAX_SHEET_ROWS = 20_000

# A digital PDF and a scanned one are different products: cheap text extraction
# versus OCR through a vision model. Detect before routing or the cost model is
# off by an order of magnitude.
SCANNED_PDF_CHARS_PER_PAGE = 40


class ParseFailed(Exception):
    """Terminal. The file will never parse, so do not retry it."""

    def __init__(self, reason: str, *, code: str) -> None:
        super().__init__(reason)
        self.code = code


class NeedsModel(Exception):
    """Tier C by policy, not by failure.

    The bytes are fine and interpretable -- by a model this deployment has not
    been told it may spend money on. The item stays stored with the reason
    recorded, and interpretation can be added later without re-ingesting.
    """

    def __init__(self, capability: str) -> None:
        super().__init__(f"requires {capability}")
        self.capability = capability


@dataclass
class Parsed:
    text: str
    tier: str = "A"
    structure: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    truncated: bool = False

    def capped(self) -> "Parsed":
        # Postgres text columns cannot hold a NUL, and a handler that produces
        # one would fail at the very end of a long pipeline. Strip rather than
        # raise: the surrounding text is still worth indexing.
        if "\x00" in self.text:
            self.text = self.text.replace("\x00", "")
            self.warnings.append("null bytes removed before indexing")

        if len(self.text) > MAX_TEXT_CHARS:
            self.text = self.text[:MAX_TEXT_CHARS]
            self.truncated = True
            self.warnings.append(
                f"text truncated at {MAX_TEXT_CHARS} characters; the rest is stored but not indexed"
            )
        return self


def decode(payload: bytes) -> tuple[str, str]:
    """Bytes to text, with the encoding actually used.

    UTF-8 first because it is nearly always right, then detection, then a
    lossy Latin-1 floor -- which never raises, so a weird file degrades rather
    than failing.
    """
    try:
        return payload.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(payload).best()
        if best is not None:
            return str(best), best.encoding
    except Exception:  # noqa: BLE001 -- detection is best-effort by definition
        pass
    return payload.decode("latin-1", errors="replace"), "latin-1"


# ----------------------------------------------------------------- text & markup


def parse_text(payload: bytes, *, mime: str, name: str) -> Parsed:
    text, encoding = decode(payload)
    warnings = [] if encoding == "utf-8" else [f"decoded as {encoding}, not utf-8"]
    return Parsed(text=text, structure={"encoding": encoding}, warnings=warnings)


def parse_html(payload: bytes, *, mime: str, name: str) -> Parsed:
    from selectolax.parser import HTMLParser

    raw, encoding = decode(payload)
    tree = HTMLParser(raw)
    for tag in tree.css("script, style, noscript"):
        tag.decompose()
    body = tree.body or tree.root
    text = body.text(separator="\n", strip=True) if body else ""
    title = tree.css_first("title")
    headings = [h.text(strip=True) for h in tree.css("h1, h2, h3")][:50]
    return Parsed(
        text=text,
        structure={
            "encoding": encoding,
            "title": title.text(strip=True) if title else None,
            "headings": headings,
        },
    )


def parse_json(payload: bytes, *, mime: str, name: str) -> Parsed:
    raw, encoding = decode(payload)
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ParseFailed(f"invalid JSON: {exc}", code="malformed") from exc
    # Pretty-printed rather than raw: the indexer reads keys and values, and
    # one-line JSON chunks badly.
    return Parsed(
        text=json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True),
        structure={"encoding": encoding, "top_level": type(value).__name__},
    )


def parse_jsonl(payload: bytes, *, mime: str, name: str) -> Parsed:
    raw, encoding = decode(payload)
    lines, bad = [], 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            lines.append(json.dumps(json.loads(line), ensure_ascii=False, sort_keys=True))
        except ValueError:
            bad += 1
    warnings = [f"{bad} unparseable lines skipped"] if bad else []
    return Parsed(
        text="\n".join(lines),
        structure={"encoding": encoding, "records": len(lines)},
        warnings=warnings,
    )


def parse_yaml(payload: bytes, *, mime: str, name: str) -> Parsed:
    import yaml

    raw, encoding = decode(payload)
    try:
        # safe_load, never load: a YAML document is untrusted input and
        # full-fat YAML can construct arbitrary Python objects.
        value = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ParseFailed(f"invalid YAML: {exc}", code="malformed") from exc
    return Parsed(
        text=yaml.safe_dump(value, sort_keys=True, allow_unicode=True),
        structure={"encoding": encoding},
    )


def parse_delimited(payload: bytes, *, mime: str, name: str) -> Parsed:
    raw, encoding = decode(payload)
    delimiter = "\t" if name.lower().endswith((".tsv", ".tab")) or mime.endswith("tab-separated-values") else ","
    reader = csv.reader(io.StringIO(raw), delimiter=delimiter)
    rows, header, truncated = [], None, False
    for index, row in enumerate(reader):
        if index == 0:
            header = row
        if index >= MAX_SHEET_ROWS:
            truncated = True
            break
        rows.append(row)
    # Row-per-line with the header repeated as labels: a bare CSV grid chunks
    # into meaningless fragments, because the header scrolls out of the chunk.
    lines = []
    if header and len(rows) > 1:
        for row in rows[1:]:
            pairs = [f"{h}: {v}" for h, v in zip(header, row) if v not in ("", None)]
            if pairs:
                lines.append("; ".join(pairs))
    else:
        lines = [delimiter.join(r) for r in rows]
    parsed = Parsed(
        text="\n".join(lines),
        structure={"encoding": encoding, "columns": header or [], "rows": max(len(rows) - 1, 0)},
    )
    if truncated:
        parsed.truncated = True
        parsed.warnings.append(f"stopped at {MAX_SHEET_ROWS} rows")
    return parsed


def parse_xml(payload: bytes, *, mime: str, name: str) -> Parsed:
    from selectolax.parser import HTMLParser

    raw, encoding = decode(payload)
    # Deliberately not ElementTree: XML parsers are an XXE vector, and this
    # only needs the text content.
    text = HTMLParser(raw).text(separator="\n", strip=True)
    return Parsed(text=text, structure={"encoding": encoding})


# ------------------------------------------------------------------------- pdf


def parse_pdf(payload: bytes, *, mime: str, name: str) -> Parsed:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(payload))
        if reader.is_encrypted:
            # An empty-password PDF is common and legitimately openable.
            try:
                if reader.decrypt("") == 0:
                    raise ParseFailed("PDF is password protected", code="encrypted")
            except (NotImplementedError, PdfReadError) as exc:
                raise ParseFailed(f"PDF encryption unsupported: {exc}", code="encrypted") from exc
        pages = [(page.extract_text() or "") for page in reader.pages]
    except ParseFailed:
        raise
    except Exception as exc:  # noqa: BLE001 -- pypdf raises many shapes
        raise ParseFailed(f"unreadable PDF: {exc}", code="malformed") from exc

    text = "\n\n".join(p.strip() for p in pages if p.strip())
    if pages and len(text) / len(pages) < SCANNED_PDF_CHARS_PER_PAGE:
        # A scanned PDF is not a failed digital one. Routing it to OCR is a
        # different product with a different cost, so it is named rather than
        # returned as a near-empty success.
        raise NeedsModel("ocr")
    return Parsed(
        text=text,
        structure={"pages": len(pages), "chars_per_page": round(len(text) / max(len(pages), 1))},
    )


# ---------------------------------------------------------------------- office


def _zip_guard(payload: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            if any(info.flag_bits & 0x1 for info in archive.infolist()):
                raise ParseFailed("archive is password protected", code="encrypted")
    except zipfile.BadZipFile as exc:
        raise ParseFailed(f"not a readable archive: {exc}", code="malformed") from exc


def parse_docx(payload: bytes, *, mime: str, name: str) -> Parsed:
    import docx

    _zip_guard(payload)
    try:
        document = docx.Document(io.BytesIO(payload))
    except Exception as exc:  # noqa: BLE001
        raise ParseFailed(f"unreadable docx: {exc}", code="malformed") from exc
    blocks = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                blocks.append(" | ".join(cells))
    headings = [p.text for p in document.paragraphs if p.style.name.startswith("Heading")][:50]
    return Parsed(text="\n\n".join(blocks), structure={"headings": headings})


def parse_xlsx(payload: bytes, *, mime: str, name: str) -> Parsed:
    import openpyxl

    _zip_guard(payload)
    try:
        book = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ParseFailed(f"unreadable spreadsheet: {exc}", code="malformed") from exc

    lines, truncated, sheets = [], False, []
    total = 0
    for sheet in book.worksheets:
        sheets.append(sheet.title)
        lines.append(f"# {sheet.title}")
        header: list[str] | None = None
        for index, row in enumerate(sheet.iter_rows(values_only=True)):
            if total >= MAX_SHEET_ROWS:
                truncated = True
                break
            values = ["" if v is None else str(v) for v in row]
            if not any(values):
                continue
            if index == 0:
                header = values
                continue
            total += 1
            if header:
                pairs = [f"{h}: {v}" for h, v in zip(header, values) if v]
                lines.append("; ".join(pairs))
            else:
                lines.append(" | ".join(values))
        if truncated:
            break
    book.close()
    parsed = Parsed(text="\n".join(lines), structure={"sheets": sheets, "rows": total})
    if truncated:
        parsed.truncated = True
        parsed.warnings.append(f"stopped at {MAX_SHEET_ROWS} rows across sheets")
    return parsed


def parse_pptx(payload: bytes, *, mime: str, name: str) -> Parsed:
    from pptx import Presentation

    _zip_guard(payload)
    try:
        deck = Presentation(io.BytesIO(payload))
    except Exception as exc:  # noqa: BLE001
        raise ParseFailed(f"unreadable presentation: {exc}", code="malformed") from exc
    blocks = []
    for number, slide in enumerate(deck.slides, start=1):
        parts = [
            shape.text_frame.text.strip()
            for shape in slide.shapes
            if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip()
        ]
        if parts:
            blocks.append(f"# Slide {number}\n" + "\n".join(parts))
    return Parsed(text="\n\n".join(blocks), structure={"slides": len(deck.slides)})


def parse_opendocument(payload: bytes, *, mime: str, name: str) -> Parsed:
    """odt / ods -- a zip with content.xml. No dependency needed for text."""
    from selectolax.parser import HTMLParser

    _zip_guard(payload)
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            content = archive.read("content.xml")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise ParseFailed(f"unreadable OpenDocument file: {exc}", code="malformed") from exc
    raw, _ = decode(content)
    return Parsed(text=HTMLParser(raw).text(separator="\n", strip=True), tier="B")


# -------------------------------------------------------------------- email


def parse_email(payload: bytes, *, mime: str, name: str) -> Parsed:
    message = message_from_bytes(payload, policy=policy.default)
    headers = {
        key: str(message.get(key, ""))
        for key in ("From", "To", "Cc", "Subject", "Date", "Message-ID", "In-Reply-To")
        if message.get(key)
    }
    body_part = message.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_part is not None:
        content = body_part.get_content()
        if body_part.get_content_type() == "text/html":
            from selectolax.parser import HTMLParser

            body = HTMLParser(content).text(separator="\n", strip=True)
        else:
            body = content
    # Attachments are processed separately and referenced by name only; this
    # must not speculate about contents it cannot see.
    attachments = [
        part.get_filename()
        for part in message.iter_attachments()
        if part.get_filename()
    ]
    header_text = "\n".join(f"{k}: {v}" for k, v in headers.items())
    return Parsed(
        text=f"{header_text}\n\n{body}".strip(),
        structure={"headers": headers, "attachments": attachments},
    )


def parse_mbox(payload: bytes, *, mime: str, name: str) -> Parsed:
    """A Takeout mbox is routinely multi-GB with tens of thousands of messages.

    Properly it fans out into one item per message -- that is bulk ingestion,
    and it belongs with the bulk slice. Here it is summarised and capped, and
    says so, rather than pretending one item can represent fifty thousand.
    """
    separator = re.compile(rb"^From ", re.MULTILINE)
    chunks = [c for c in separator.split(payload) if c.strip()]
    kept = chunks[:50]
    parts = []
    for chunk in kept:
        try:
            parts.append(parse_email(b"From " + chunk, mime="message/rfc822", name=name).text)
        except Exception:  # noqa: BLE001 -- one bad message must not lose the rest
            continue
    parsed = Parsed(
        text="\n\n---\n\n".join(parts),
        structure={"messages": len(chunks), "indexed": len(kept)},
    )
    if len(chunks) > len(kept):
        parsed.truncated = True
        parsed.warnings.append(
            f"mbox holds {len(chunks)} messages; {len(kept)} indexed. "
            "Per-message fan-out belongs to bulk ingestion"
        )
    return parsed


# ------------------------------------------------------- calendar & contacts


def _unfold(raw: str) -> list[str]:
    """RFC 5545 line folding: a continuation starts with a space or tab."""
    lines: list[str] = []
    for line in raw.splitlines():
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def parse_transcript(payload: bytes, *, mime: str, name: str) -> Parsed:
    """WebVTT and SRT, as speaker-attributed text rather than timed cues.

    A meeting transcript arrives as thousands of two-second cues, and indexing
    them as cues is the wrong unit twice over: a sentence is split across three
    of them, so no chunk contains a whole thought, and the timestamps outnumber
    the words. So consecutive cues from one speaker are joined into a turn, and
    the timestamps are dropped from the text and kept in `structure` -- a
    citation wants an offset into what was said, not a clock reading.

    Speaker attribution comes from two conventions and neither is guaranteed:
    WebVTT's `<v Name>` voice span, and the `Name:` prefix every meeting tool
    writes. When neither is present the text is still worth having, and the
    turns are simply unattributed rather than guessed at.
    """
    raw, _ = decode(payload)
    lines = [line.strip() for line in raw.replace("\r\n", "\n").split("\n")]

    turns: list[tuple[str | None, list[str]]] = []
    speakers: list[str] = []
    cues = 0
    for line in lines:
        if not line or line == "WEBVTT" or line.startswith("NOTE "):
            continue
        # A cue's timing line, or the bare number SRT puts above it. Both are
        # structure rather than speech.
        if "-->" in line:
            cues += 1
            continue
        if line.isdigit():
            continue
        if line.startswith("STYLE") or line.startswith("REGION"):
            continue

        speaker, text = _voice(line)
        if text == "":
            continue
        if speaker and speaker not in speakers:
            speakers.append(speaker)
        # One turn per speaker, not one per cue: consecutive cues from the same
        # person are one thing said, and splitting them is what makes a
        # transcript unsearchable.
        if turns and turns[-1][0] == speaker:
            turns[-1][1].append(text)
        else:
            turns.append((speaker, [text]))

    body = "\n\n".join(
        (f"{speaker}: " if speaker else "") + " ".join(parts) for speaker, parts in turns)
    return Parsed(
        text=body,
        structure={
            "kind": "transcript",
            "cues": cues,
            "turns": len(turns),
            # The attendee list as the transcript itself reports it. Not the
            # ACL -- these are display names, and a name is not a principal.
            # Resolving them is the caller's job precisely because getting it
            # wrong would widen visibility.
            "speakers": speakers,
        },
        warnings=[] if speakers else ["no speaker attribution found in this transcript"],
    )


def _voice(line: str) -> tuple[str | None, str]:
    """`<v Dana>text` or `Dana: text`, and neither is guaranteed."""
    if line.startswith("<v ") and ">" in line:
        name, _, rest = line[3:].partition(">")
        return name.strip(), _strip_tags(rest).strip()
    head, sep, rest = line.partition(":")
    # A speaker label, not a sentence that happens to contain a colon.
    #
    # Length alone is not enough -- "One thing was clear: it shipped" passes any
    # reasonable character limit. Word count is the signal that works: a label
    # is a name, and names are one to three words. The trade is deliberate and
    # runs the safe way. A four-word name is read as unattributed speech, which
    # **loses** attribution; the alternative misreads half a sentence as a
    # speaker, which **invents** it, and invented attribution in a transcript is
    # a quote put in somebody's mouth.
    #
    # None of this applies to `<v Name>`, which is unambiguous and is what the
    # providers actually emit -- this is the fallback for the tools that do not.
    words = head.split()
    if (sep and words and len(words) <= 3 and len(head) <= 48
            and not head.endswith((".", "?", "!", ","))):
        return head.strip(), _strip_tags(rest).strip()
    return None, _strip_tags(line).strip()


def _strip_tags(text: str) -> str:
    import re as _re

    return _re.sub(r"<[^>]+>", "", text)


def parse_ics(payload: bytes, *, mime: str, name: str) -> Parsed:
    """Highest value per unit cost in the whole format list.

    It maps directly onto a canonical Event with no model involved, which is
    why it is worth prioritising well above its apparent importance.
    """
    raw, _ = decode(payload)
    events, current = [], None
    for line in _unfold(raw):
        if line.startswith("BEGIN:VEVENT"):
            current = {}
        elif line.startswith("END:VEVENT"):
            if current is not None:
                events.append(current)
            current = None
        elif current is not None and ":" in line:
            key, _, value = line.partition(":")
            current[key.split(";")[0].upper()] = value.strip()

    lines = []
    for event in events:
        lines.append(
            "\n".join(
                f"{label}: {event[key]}"
                for key, label in (
                    ("SUMMARY", "Event"), ("DTSTART", "Starts"), ("DTEND", "Ends"),
                    ("LOCATION", "Location"), ("ORGANIZER", "Organizer"),
                    ("ATTENDEE", "Attendee"), ("DESCRIPTION", "Description"),
                )
                if event.get(key)
            )
        )
    return Parsed(
        text="\n\n".join(lines),
        structure={"events": len(events), "target_type": "Event"},
    )


def parse_vcf(payload: bytes, *, mime: str, name: str) -> Parsed:
    raw, _ = decode(payload)
    cards, current = [], None
    for line in _unfold(raw):
        upper = line.upper()
        if upper.startswith("BEGIN:VCARD"):
            current = {}
        elif upper.startswith("END:VCARD"):
            if current is not None:
                cards.append(current)
            current = None
        elif current is not None and ":" in line:
            key, _, value = line.partition(":")
            current.setdefault(key.split(";")[0].upper(), value.strip())

    lines = []
    for card in cards:
        lines.append(
            "\n".join(
                f"{label}: {card[key]}"
                for key, label in (
                    ("FN", "Name"), ("ORG", "Organisation"), ("TITLE", "Title"),
                    ("EMAIL", "Email"), ("TEL", "Phone"), ("ADR", "Address"),
                )
                if card.get(key)
            )
        )
    return Parsed(
        text="\n\n".join(lines),
        structure={"contacts": len(cards), "target_type": "Person"},
    )


# ----------------------------------------------------------------- archives


def parse_archive(payload: bytes, *, mime: str, name: str) -> Parsed:
    """Expand and recurse, capped.

    Properly, each member becomes its own item with its own provenance -- that
    is bulk ingestion. Here the members' text is extracted inline and capped,
    and the listing is always recorded so nothing is invisible.
    """
    members: list[tuple[str, bytes]] = []
    try:
        if mime in ("application/zip", "application/x-zip-compressed") or name.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                if any(info.flag_bits & 0x1 for info in archive.infolist()):
                    raise ParseFailed("archive is password protected", code="encrypted")
                for info in archive.infolist()[:MAX_ARCHIVE_MEMBERS]:
                    if info.is_dir() or info.file_size > 20_000_000:
                        continue
                    members.append((info.filename, archive.read(info)))
        else:
            with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
                for info in archive.getmembers()[:MAX_ARCHIVE_MEMBERS]:
                    if not info.isfile() or info.size > 20_000_000:
                        continue
                    handle = archive.extractfile(info)
                    if handle is not None:
                        members.append((info.name, handle.read()))
    except ParseFailed:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ParseFailed(f"unreadable archive: {exc}", code="malformed") from exc

    listing = [f"{n} ({len(b)} bytes)" for n, b in members]
    blocks = [f"Archive contents:\n" + "\n".join(listing)]
    for member_name, member_bytes in members:
        handler = for_file(member_name, sniff_bytes(member_bytes))
        if handler is None:
            continue
        try:
            blocks.append(f"## {member_name}\n{handler(member_bytes, mime='', name=member_name).text}")
        except (ParseFailed, NeedsModel):
            continue  # a member that cannot be read is listed, not fatal
    return Parsed(text="\n\n".join(blocks), structure={"members": len(members)})


def sniff_bytes(payload: bytes) -> str:
    import filetype

    guess = filetype.guess(payload[:8192])
    return guess.mime if guess else ""


# ------------------------------------------------------------------ registry

Handler = Callable[..., Parsed]

# Tier C: interpretable, but only by a model this deployment has not been told
# it may spend money on. Named individually so the reason is specific.
NEEDS_MODEL = {
    "image": "vision",
    "audio": "transcription",
    "video": "transcription",
}

BY_MIME: dict[str, Handler] = {
    "text/plain": parse_text,
    "text/markdown": parse_text,
    "text/html": parse_html,
    "text/vtt": parse_transcript,
    "application/x-subrip": parse_transcript,
    "text/csv": parse_delimited,
    "text/tab-separated-values": parse_delimited,
    "application/json": parse_json,
    "application/xml": parse_xml,
    "text/xml": parse_xml,
    "application/pdf": parse_pdf,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": parse_docx,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": parse_xlsx,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": parse_pptx,
    "application/vnd.oasis.opendocument.text": parse_opendocument,
    "application/vnd.oasis.opendocument.spreadsheet": parse_opendocument,
    "message/rfc822": parse_email,
    "application/zip": parse_archive,
    "application/x-tar": parse_archive,
    "application/gzip": parse_archive,
}

BY_EXTENSION: dict[str, Handler] = {
    ".txt": parse_text, ".md": parse_text, ".rst": parse_text, ".log": parse_text,
    ".tex": parse_text, ".rtf": parse_text,
    ".html": parse_html, ".htm": parse_html, ".svg": parse_xml,
    ".csv": parse_delimited, ".tsv": parse_delimited,
    ".json": parse_json, ".geojson": parse_json,
    ".jsonl": parse_jsonl, ".ndjson": parse_jsonl,
    ".yaml": parse_yaml, ".yml": parse_yaml,
    ".xml": parse_xml, ".gpx": parse_xml, ".kml": parse_xml,
    ".pdf": parse_pdf,
    ".docx": parse_docx, ".xlsx": parse_xlsx, ".pptx": parse_pptx,
    ".odt": parse_opendocument, ".ods": parse_opendocument,
    ".eml": parse_email, ".mbox": parse_mbox,
    ".vtt": parse_transcript, ".srt": parse_transcript,
    ".ics": parse_ics, ".ical": parse_ics, ".vcf": parse_vcf, ".vcard": parse_vcf,
    ".zip": parse_archive, ".tar": parse_archive, ".gz": parse_archive, ".tgz": parse_archive,
}

# Code is text with a language label. Structure-aware parsing is a later slice;
# indexing the source is most of the value and costs nothing.
CODE_EXTENSIONS = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".rs": "rust", ".java": "java", ".swift": "swift", ".sql": "sql",
    ".sh": "shell", ".tf": "terraform", ".rb": "ruby", ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cs": "csharp", ".php": "php", ".kt": "kotlin", ".scala": "scala",
    ".css": "css", ".toml": "toml", ".ini": "ini", ".env": "dotenv",
}

# Macro-enabled Office is a malware vector precisely because colleagues
# download each other's uploads. Stored and never executed or expanded.
BLOCKED_EXTENSIONS = {".docm", ".xlsm", ".pptm", ".dotm", ".xlam"}


def _code_handler(language: str) -> Handler:
    def handler(payload: bytes, *, mime: str, name: str) -> Parsed:
        parsed = parse_text(payload, mime=mime, name=name)
        parsed.structure["language"] = language
        return parsed

    return handler


# MIME types that identify the *encoding* rather than the format. A CSV, an
# ICS calendar, a YAML file and a JSON Lines export are all literally
# text/plain, so sniffing says "these are characters" and stops -- which is
# true and useless. For these, the extension carries the format.
GENERIC_MIMES = {"text/plain", "application/octet-stream", "application/zip", ""}


def for_file(name: str, mime: str) -> Handler | None:
    """Resolve a handler. Extension wins only where MIME is silent or generic.

    MIME stays authoritative for *routing away from danger* -- it is sniffed
    from the bytes, and a caller-declared type is an injection vector. But
    `text/plain` on a `.ics` is MIME telling you the encoding, not the format,
    and treating that as the final word means a calendar is indexed as a wall
    of `BEGIN:VEVENT` instead of a canonical Event.

    The asymmetry is deliberate: a *specific* MIME overrides the name, and a
    *generic* one defers to it. A `.docx` extension can never override a
    sniffed `application/pdf`, but it can override `application/octet-stream`.
    """
    lowered = name.lower()
    extension = lowered[lowered.rfind(".") :] if "." in lowered else ""

    if extension in BLOCKED_EXTENSIONS:
        return None
    if mime and mime not in GENERIC_MIMES and mime in BY_MIME:
        return BY_MIME[mime]
    if extension in CODE_EXTENSIONS:
        return _code_handler(CODE_EXTENSIONS[extension])
    if extension in BY_EXTENSION:
        return BY_EXTENSION[extension]
    if mime in BY_MIME:
        return BY_MIME[mime]          # generic, but it is all we have
    if mime.startswith("text/"):
        return parse_text
    return None


def parse(payload: bytes, *, mime: str, name: str) -> Parsed:
    """Extract text, or say precisely why not.

    Raises `NeedsModel` for Tier C by policy and `ParseFailed` for files that
    will never parse. Both are terminal; neither is retried.
    """
    family = mime.split("/")[0] if mime else ""
    if family in NEEDS_MODEL:
        raise NeedsModel(NEEDS_MODEL[family])

    handler = for_file(name, mime)
    if handler is None:
        raise ParseFailed(
            f"no handler for {mime or 'unknown type'} ({name or 'unnamed'})",
            code="unsupported",
        )
    return handler(payload, mime=mime, name=name).capped()


def supported_formats() -> set[str]:
    """Every format a handler exists for, counted rather than asserted.

    The landing page quotes this number. A number someone typed into copy is
    wrong within a month and wrong in the flattering direction.
    """
    return {e.lstrip(".") for e in BY_EXTENSION} | set(BY_MIME)
