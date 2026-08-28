"""MIME sniffing and the classification cascade.

Two rules, and the first one is the original routing defect stated as policy:
**`mime_type` is server-detected and outranks `source_type`.** A client-declared
type chooses which agent runs, which makes it an injection vector.

Position in the cascade is cost order, not authority. `source_type` is checked
at layer 3 because it is free, not because it wins: MIME is sniffed before the
cascade runs, and a hint that contradicts the bytes is discarded rather than
obeyed. Explicit caller intent is layer 1 -- it short-circuits, so it cannot sit
below the layers it short-circuits.

Layers 1-6 are deterministic. Layer 7 -- the model -- is not part of the spine;
what matters now is that `classified_by_layer` is recorded from row one, so the
"most traffic never reaches a model" claim is measured rather than asserted.
"""

from __future__ import annotations

import filetype

_SOURCE_TYPE_MAP = {
    "pdf": "document_pdf",
    "email": "message_email",
    "chat": "chat_message",
    "html": "document_html",
    "csv": "structured_csv",
    "calendar": "calendar",
    "contact": "contact",
    "audio": "audio",
    "video": "video",
    "transcript": "transcript",
    "log": "log",
    "spreadsheet": "spreadsheet",
    "presentation": "presentation",
}

_MIME_MAP = {
    "application/pdf": "document_pdf",
    "application/json": "structured_json",
    "text/csv": "structured_csv",
    "text/html": "document_html",
    "text/plain": "document_text",
    "image/jpeg": "image",
    "image/png": "image",
    "image/heic": "image",
    "image/gif": "image",
    "image/webp": "image",
    "image/tiff": "image",
    # Office. Distinct types rather than one "document", because a spreadsheet
    # and a slide deck raise genuinely different questions -- and routing them
    # all to the document prompt is what made a table get narrated as prose.
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        "document_office",
    "application/vnd.oasis.opendocument.text": "document_office",
    "application/rtf": "document_office",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "spreadsheet",
    "application/vnd.oasis.opendocument.spreadsheet": "spreadsheet",
    "text/tab-separated-values": "spreadsheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation":
        "presentation",
    "text/calendar": "calendar",
    "text/vcard": "contact",
    "text/markdown": "document_markup",
    "application/xml": "structured_json",
    "message/rfc822": "message_email",
    # Media. These had no mapping at all, so an mp3 classified as binary_blob
    # and was extracted with the generic prompt after being transcribed.
    "audio/mpeg": "audio",
    "audio/mp4": "audio",
    "audio/wav": "audio",
    "audio/x-wav": "audio",
    "audio/ogg": "audio",
    "audio/flac": "audio",
    "audio/aac": "audio",
    "audio/opus": "audio",
    "audio/amr": "audio",
    "video/mp4": "video",
    "video/quicktime": "video",
    "video/webm": "video",
    "video/x-matroska": "video",
    "application/zip": "archive",
    "application/x-tar": "archive",
    "application/gzip": "archive",
}

# Extension is a weaker signal than sniffed bytes and is only consulted when
# sniffing produced nothing -- but it is the only signal that distinguishes a
# Terraform file from a shell script, since both are text/plain on the wire.
_EXTENSION_MAP = {
    "pdf": "document_pdf", "html": "document_html", "htm": "document_html",
    "txt": "document_text", "rst": "document_markup", "tex": "document_markup",
    "md": "document_markup",
    "docx": "document_office", "docm": "document_office", "dotm": "document_office",
    "odt": "document_office", "rtf": "document_office",
    "xlsx": "spreadsheet", "xlsm": "spreadsheet", "xlam": "spreadsheet",
    "ods": "spreadsheet", "csv": "structured_csv", "tsv": "spreadsheet",
    "pptx": "presentation", "pptm": "presentation",
    "ics": "calendar", "ical": "calendar",
    "vcf": "contact", "vcard": "contact",
    "eml": "message_email", "mbox": "message_email",
    "json": "structured_json", "jsonl": "structured_json",
    "ndjson": "structured_json", "xml": "structured_json",
    "gpx": "geo", "kml": "geo", "geojson": "geo",
    "log": "log",
    "yaml": "config", "yml": "config", "toml": "config", "ini": "config",
    "env": "config", "tf": "config",
    "py": "code", "js": "code", "ts": "code", "tsx": "code", "go": "code",
    "rb": "code", "rs": "code", "java": "code", "kt": "code", "swift": "code",
    "c": "code", "h": "code", "cpp": "code", "cs": "code", "php": "code",
    "scala": "code", "sh": "code", "sql": "code", "css": "code",
    "zip": "archive", "tar": "archive", "gz": "archive", "tgz": "archive",
    "mp3": "audio", "wav": "audio", "m4a": "audio", "ogg": "audio",
    "flac": "audio", "aac": "audio", "opus": "audio", "amr": "audio",
    "mp4": "video", "mov": "video", "webm": "video", "mkv": "video",
    "jpg": "image", "jpeg": "image", "png": "image", "gif": "image",
    "webp": "image", "heic": "image", "tiff": "image", "svg": "image",
}


def sniff_mime(payload: bytes | None, text: str | None, declared: str | None) -> str | None:
    """Bytes first, structure second, and the caller's word last."""
    if payload:
        guess = filetype.guess(payload[:8192])
        if guess is not None:
            return guess.mime
        try:
            decoded = payload.decode("utf-8")
        except UnicodeDecodeError:
            return "application/octet-stream"
        # Decoding cleanly is not the same as being text. Control bytes are
        # valid UTF-8, so a DICOM header or a thumbnail can "decode" perfectly
        # and still be binary -- and a NUL cannot even be stored in a text
        # column, so this is the difference between a wrong answer and a
        # crashing worker.
        if _looks_binary(decoded):
            return "application/octet-stream"
        return "text/plain"
    if text is not None:
        stripped = text.lstrip()
        if stripped[:1] in "{[" and stripped[-1:] in "}]":
            return "application/json"
        if stripped[:5].lower() in ("<!doc", "<html"):
            return "text/html"
        return "text/plain"
    return declared


# Tab, newline and carriage return are the only control characters that appear
# in real text.
_TEXTUAL_CONTROLS = {0x09, 0x0A, 0x0D}


def _looks_binary(text: str, sample: int = 4096) -> bool:
    head = text[:sample]
    if not head:
        return False
    if "\x00" in head:
        return True
    controls = sum(1 for ch in head if ord(ch) < 0x20 and ord(ch) not in _TEXTUAL_CONTROLS)
    # A third is generous: real text with any control characters at all is rare.
    return controls / len(head) > 0.3


def classify(
    *,
    explicit_data_type: str | None,
    source_type: str | None,
    mime_type: str | None,
    payload_keys: list[str] | None = None,
    external_id: str | None = None,
) -> tuple[str, int]:
    """Returns (data_type, layer_that_resolved_it)."""
    if explicit_data_type:  # layer 1 -- explicit caller intent short-circuits
        return explicit_data_type, 1
    if source_type and source_type.lower() in _SOURCE_TYPE_MAP:  # layer 3
        claimed = _SOURCE_TYPE_MAP[source_type.lower()]
        sniffed = _MIME_MAP.get(mime_type or "")
        # Layer 2 is where `source_type` lives, but a hint that *contradicts*
        # the sniffed bytes is discarded rather than obeyed. This is the point
        # at which "mime_type outranks source_type" is actually enforced: a
        # caller declaring `pdf` over JSON bytes must not route to the PDF
        # agent. Where the two agree, or where nothing was sniffed, the hint
        # still resolves the item cheaply.
        if sniffed is None or sniffed == claimed:
            return claimed, 3
    if payload_keys:
        keys = {k.lower() for k in payload_keys}
        if {"latitude", "longitude"} <= keys:
            return "sensor_gps", 4
    if mime_type and mime_type in _MIME_MAP:
        return _MIME_MAP[mime_type], 5
    if external_id and "." in external_id:
        ext = external_id.rsplit(".", 1)[-1].lower()
        if ext in _EXTENSION_MAP:
            return _EXTENSION_MAP[ext], 6
    return "binary_blob", 0
