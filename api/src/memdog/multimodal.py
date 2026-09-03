"""Interpreting the formats that need a model.

Images, audio, video and scanned PDFs. These are Tier A in the format promise
and the expensive tier in the cost model, and both facts are true at once.

The engine is a seam like every other: one Protocol, a Gemini implementation
that speaks every modality, and a null implementation that declines clearly.
Swapping in a local Whisper or a vision model later is an implementation, not a
change to the pipeline.

**Cost is enforced, not documented.** Ten hours of uploaded video, transcribed
and keyframe-captioned on someone's own API key, is a large unbudgeted bill. So
media interpretation is opt-in per deployment, every item is bounded by size,
and what was spent is recorded on the row rather than inferred from an invoice.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from . import usage

log = logging.getLogger(__name__)

# Inline payloads have a hard ceiling at the provider. Beyond it a resumable
# upload is required, which belongs with the uploads slice -- so the boundary is
# named and refused rather than silently truncated into a wrong answer.
MAX_INLINE_BYTES = 18 * 1024 * 1024


class MediaDisabled(Exception):
    """Interpretation is off for this deployment. Not a failure -- a policy."""


class MediaTooLarge(Exception):
    """Beyond the inline ceiling. Terminal until resumable upload exists."""


class QuotaExhausted(Exception):
    """The provider is rate limited or out of quota.

    Neither a failure nor a retry-in-a-moment: a daily quota does not reset
    within a backoff window, so burning five attempts in two seconds wastes the
    little quota that remains and buries the real reason in a dead letter.
    """


@dataclass
class Interpreted:
    text: str
    modality: str
    model_id: str
    tokens: int = 0
    # What the provider says it actually ran, and the id of this specific call.
    # The model_id is what we asked for; this is what answered.
    model_version: str | None = None
    response_id: str | None = None
    structure: dict = field(default_factory=dict)


class MultimodalEngine(Protocol):
    model_id: str
    enabled: bool

    async def interpret(self, payload: bytes, *, mime: str, modality: str) -> Interpreted: ...


# One instruction per modality. They are deliberately extractive: a caption that
# speculates is worse than a short one, because it becomes indexed text that the
# corpus cannot distinguish from something actually present in the source.
PROMPTS = {
    "image": (
        "Describe this image factually for a search index. State what is visibly "
        "present: objects, people (without identifying them), setting, and any "
        "visible text transcribed verbatim. Do not speculate about context, "
        "intent, or anything not visible. If the image is primarily a document, "
        "transcribe its text in full and say so."
    ),
    "audio": (
        "Transcribe this audio verbatim. Label distinct speakers as Speaker 1, "
        "Speaker 2 and so on where they are distinguishable. Do not summarise, "
        "translate, or correct grammar. If a passage is inaudible, mark it "
        "[inaudible] rather than guessing."
    ),
    "video": (
        "Transcribe the speech in this video verbatim, labelling distinct "
        "speakers where distinguishable. Then, separately, describe what is "
        "visually shown, including any on-screen text transcribed verbatim. Do "
        "not speculate about anything not shown or said."
    ),
    "ocr": (
        "Transcribe all text in this document verbatim, preserving reading order "
        "and page breaks. Mark each page as '--- page N ---'. Do not summarise, "
        "reorder, or correct the text. If a page has no legible text, say "
        "'[no legible text]'."
    ),
}


class NullMultimodal:
    """Media interpretation is off. Says so, specifically."""

    model_id = "none"
    enabled = False

    async def interpret(self, payload: bytes, *, mime: str, modality: str) -> Interpreted:
        raise MediaDisabled(modality)


class GeminiMultimodal:
    """One provider, every modality.

    Gemini takes image, audio, video and PDF bytes directly, which is why it is
    the practical choice here: the alternative is a transcription service, a
    vision model and an OCR pipeline, each with its own deployment story, to
    cover one row of the format table.
    """

    def __init__(self, api_key: str, model_id: str, per_modality: dict[str, str] | None = None) -> None:
        self.model_id = model_id
        self.enabled = True
        self._api_key = api_key
        # Assignment per (purpose, data type) rather than one model for
        # everything. Today they resolve to the same model; the seam is here
        # because a purpose-built transcriber is cheaper than a general model
        # for transcription, and that swap should be configuration.
        self._per_modality = per_modality or {}
        self._base = "https://generativelanguage.googleapis.com/v1beta"

    def model_for(self, modality: str) -> str:
        return self._per_modality.get(modality, self.model_id)

    async def interpret(self, payload: bytes, *, mime: str, modality: str) -> Interpreted:
        if len(payload) > MAX_INLINE_BYTES:
            raise MediaTooLarge(
                f"{len(payload)} bytes exceeds the {MAX_INLINE_BYTES} inline ceiling; "
                "resumable upload is required"
            )
        prompt = PROMPTS.get(modality, PROMPTS["image"])
        model = self.model_for(modality)
        # Metered here rather than at the call site: this engine is not in a
        # routing chain, so nothing above it opens a metered block, and media is
        # the most expensive per-item call the platform makes.
        async with usage.meter("interpret", "gemini", model_id=model):
            return await self._interpret(payload, prompt, model, mime, modality)

    async def _interpret(
        self, payload: bytes, prompt: str, model: str, mime: str, modality: str
    ) -> Interpreted:
        body = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": mime or "application/octet-stream",
                                "data": base64.b64encode(payload).decode(),
                            }
                        },
                    ],
                }
            ],
            # Deterministic: this is transcription and description, not writing.
            "generationConfig": {"temperature": 0, "maxOutputTokens": 8192},
        }
        try:
            async with httpx.AsyncClient(timeout=600.0) as client:
                response = await client.post(
                    f"{self._base}/models/{model}:generateContent",
                    headers={"x-goog-api-key": self._api_key},
                    json=body,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            # `raise_for_status` names the status and the URL and throws the
            # body away, which is the half that says what was actually wrong.
            # A video sent to an audio-only model recorded "Client error '400
            # Bad Request'" on the row and cost an afternoon; the provider had
            # said "Image input modality is not enabled for this model" in the
            # body all along, and the console already renders that reason.
            detail = _provider_message(exc.response)
            if exc.response.status_code == 429:
                raise QuotaExhausted(detail or str(exc)) from exc
            if exc.response.status_code in (500, 503):
                # Rate limits and capacity spikes are transient; the queue's
                # backoff is the right place to handle them.
                raise RuntimeError(
                    f"multimodal temporarily unavailable: {detail or exc}") from exc
            raise MediaDisabled(
                f"multimodal rejected the request ({exc.response.status_code}): "
                f"{detail or exc}"
            ) from exc
        except httpx.HTTPError as exc:
            # Transient. Unlike a parse failure this is worth retrying, so it
            # propagates to the queue rather than being recorded on the row.
            raise RuntimeError(f"multimodal request failed: {exc}") from exc

        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise MediaDisabled(f"model returned nothing ({reason})")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "\n".join(p.get("text", "") for p in parts).strip()
        meta = data.get("usageMetadata") or {}
        usage.observe(
            tokens_in=meta.get("promptTokenCount", 0),
            tokens_out=meta.get("candidatesTokenCount", 0),
            tokens_cached=meta.get("cachedContentTokenCount", 0),
            modality=modality,
        )
        return Interpreted(
            text=text,
            modality=modality,
            model_id=model,
            tokens=meta.get("totalTokenCount", 0),
            model_version=data.get("modelVersion"),
            response_id=data.get("responseId"),
            structure={
                "prompt_tokens": meta.get("promptTokenCount", 0),
                "output_tokens": meta.get("candidatesTokenCount", 0),
            },
        )


def _provider_message(response) -> str:
    """The provider's own sentence, or nothing.

    Never raises: this runs while handling an error, and a failure to parse an
    error body must not replace the error being reported.
    """
    try:
        payload = response.json()
    except Exception:
        return (response.text or "").strip()[:400]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:400]
    return str(payload)[:400]


def modality_for(mime: str) -> str | None:
    family = mime.split("/")[0] if mime else ""
    if family in ("image", "audio", "video"):
        return family
    return None


def build_multimodal(settings) -> MultimodalEngine:
    if not settings.media_interpretation:
        return NullMultimodal()
    if not settings.gemini_api_key:
        log.warning("MEDIA_INTERPRETATION is on but GEMINI_API_KEY is unset; media stays stored")
        return NullMultimodal()
    per_modality = {}
    if settings.transcribe_model:
        # Audio only. A transcription model is audio-only by construction, and
        # video carries frames -- pointing `video` here made every recording
        # fail with "Image input modality is not enabled for this model" while
        # images, which use the multimodal model, worked fine. Video therefore
        # falls through to `multimodal_model`, which handles both modalities.
        per_modality["audio"] = settings.transcribe_model
    return GeminiMultimodal(
        settings.gemini_api_key, settings.multimodal_model, per_modality
    )
