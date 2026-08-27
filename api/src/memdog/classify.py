"""MIME sniffing and the classification cascade.

Two rules, and the first one is the original routing defect stated as policy:
**`mime_type` is server-detected and outranks `source_type`.** A client-declared
type chooses which agent runs, which makes it an injection vector.

The two documents disagree about how that ordering is realised: schema.md says
MIME outranks `source_type`, while the cascade in workers.md places `source_type`
at layer 2, *above* the MIME registry at layer 5. Taken literally the cascade
lets a lying `source_type` win, which is the defect the rule exists to prevent.
Resolved here in the way that satisfies both: `source_type` keeps its layer-2
position, but a hint that contradicts the sniffed MIME is discarded.

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
}


def sniff_mime(payload: bytes | None, text: str | None, declared: str | None) -> str | None:
    """Bytes first, structure second, and the caller's word last."""
    if payload:
        guess = filetype.guess(payload[:8192])
        if guess is not None:
            return guess.mime
        try:
            payload.decode("utf-8")
        except UnicodeDecodeError:
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


def classify(
    *,
    explicit_data_type: str | None,
    source_type: str | None,
    mime_type: str | None,
    payload_keys: list[str] | None = None,
    external_id: str | None = None,
) -> tuple[str, int]:
    """Returns (data_type, layer_that_resolved_it)."""
    if explicit_data_type:  # layer 3 short-circuits everything above it
        return explicit_data_type, 3
    if source_type and source_type.lower() in _SOURCE_TYPE_MAP:
        claimed = _SOURCE_TYPE_MAP[source_type.lower()]
        sniffed = _MIME_MAP.get(mime_type or "")
        # Layer 2 is where `source_type` lives, but a hint that *contradicts*
        # the sniffed bytes is discarded rather than obeyed. This is the point
        # at which "mime_type outranks source_type" is actually enforced: a
        # caller declaring `pdf` over JSON bytes must not route to the PDF
        # agent. Where the two agree, or where nothing was sniffed, the hint
        # still resolves the item cheaply.
        if sniffed is None or sniffed == claimed:
            return claimed, 2
    if payload_keys:
        keys = {k.lower() for k in payload_keys}
        if {"latitude", "longitude"} <= keys:
            return "sensor_gps", 4
    if mime_type and mime_type in _MIME_MAP:
        return _MIME_MAP[mime_type], 5
    if external_id and "." in external_id:
        ext = external_id.rsplit(".", 1)[-1].lower()
        if ext in ("csv", "json", "pdf", "html"):
            return _SOURCE_TYPE_MAP.get(ext, f"structured_{ext}"), 6
    return "binary_blob", 0
