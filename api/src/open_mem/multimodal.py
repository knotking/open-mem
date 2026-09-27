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

# Inline payloads have a hard ceiling at the provider. Beyond it the bytes go
# through the Files API instead -- see `gemini_files` -- which is opt-in per
# deployment because it exists to let much more expensive inputs through.
MAX_INLINE_BYTES = 18 * 1024 * 1024

# **The second ceiling, and the reason there are two.**
#
# The Files API lifts how much can be *sent* -- roughly 18 MB to roughly 2 GB.
# It does not lift how much the model can *understand*, which is a duration for
# video and audio and a page count for a document. A five-hour recording is
# comfortably under the transport bound and comfortably beyond the model's.
#
# Conflating the two would reproduce, one level up, the failure this codebase
# has already been bitten by twice: an input accepted, truncated, and reported
# with a plausible number and no error. So the model bound is checked first and
# separately, nothing is uploaded when it is exceeded, and the refusal names
# which of the two ceilings was hit.
#
# These are the provider's published limits and they move. **Last checked
# against the documentation on 2026-09-07 and never against a live account** --
# an item refused here is refused on the strength of this table, so a stale
# number is a wrong answer rather than a slow one.
MODEL_LIMITS = {
    # Roughly an hour of video at default resolution for a 2M-context model.
    "video": {"seconds": 3600, "label": "about an hour of video"},
    # Audio is cheaper per second than video by a wide margin.
    "audio": {"seconds": 34200, "label": "about nine and a half hours of audio"},
    # Documents are bounded by pages rather than by time.
    "ocr": {"pages": 1000, "label": "about a thousand pages"},
}

# Bytes are what we have at this point in the pipeline; duration and page count
# are what the limits are written in. Converting between them exactly would mean
# decoding the media, which is a dependency this module has deliberately avoided
# -- so these are conservative floors used only to refuse the obviously
# impossible, and the provider remains the authority on everything below them.
#
# Deliberately generous: refusing something the model would have accepted is a
# worse failure than sending something it rejects, because the first is
# invisible and the second comes back with a reason.
BYTES_PER_SECOND = {
    "video": 60 * 1024,      # a low-bitrate hour is ~200 MB; this allows ~3.5 GB
    "audio": 4 * 1024,       # a 32 kbps hour is ~14 MB; this allows ~140 MB
}


class MediaDisabled(Exception):
    """Interpretation is off for this deployment. Not a failure -- a policy."""


class MediaTooLarge(Exception):
    """Beyond the inline ceiling, and the large-media path is not switched on.

    Still terminal, and still the right answer for a deployment that has not
    agreed to pay for the expensive path -- which is why this did not become a
    routing decision inside the exception. Turning `large_media` on is what
    turns this into an upload.
    """


class MediaBeyondModel(Exception):
    """Past what the model can take, however the bytes get there.

    The distinction from `MediaTooLarge` is the whole point of having two: that
    one is solved by switching something on, and this one cannot be solved at
    all. Telling somebody to enable a setting that will not help them is worse
    than telling them nothing.
    """


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

    async def interpret(self, payload: bytes, *, mime: str, modality: str,
                        model: str | None = None) -> Interpreted: ...


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

    async def interpret(self, payload: bytes, *, mime: str, modality: str,
                        model: str | None = None) -> Interpreted:
        raise MediaDisabled(modality)


class GeminiMultimodal:
    """One provider, every modality.

    Gemini takes image, audio, video and PDF bytes directly, which is why it is
    the practical choice here: the alternative is a transcription service, a
    vision model and an OCR pipeline, each with its own deployment story, to
    cover one row of the format table.
    """

    def __init__(self, api_key: str, model_id: str,
                 per_modality: dict[str, str] | None = None, *,
                 large_media: bool = False, files=None,
                 max_large_bytes: int = 0) -> None:
        self.model_id = model_id
        self.enabled = True
        self._api_key = api_key
        # Off unless a deployment has said otherwise. The org setting can turn
        # it on per call -- see `interpret` -- because the deployment default
        # and an org's own policy are different questions.
        self.large_media = large_media
        self._files = files
        self._max_large_bytes = max_large_bytes
        # Assignment per (purpose, data type) rather than one model for
        # everything. Today they resolve to the same model; the seam is here
        # because a purpose-built transcriber is cheaper than a general model
        # for transcription, and that swap should be configuration.
        self._per_modality = per_modality or {}
        self._base = "https://generativelanguage.googleapis.com/v1beta"

    def model_for(self, modality: str) -> str:
        return self._per_modality.get(modality, self.model_id)

    async def interpret(self, payload: bytes, *, mime: str, modality: str,
                        model: str | None = None,
                        large_media: bool | None = None) -> Interpreted:
        """`model` overrides the per-modality assignment for this call only.

        It exists for one caller: the retry that follows an empty answer from a
        purpose-built transcriber, which needs to ask the general model the same
        question before concluding the recording was silent.

        `large_media` overrides the deployment default for this call, because
        the org's own setting decides and it is resolved per item.
        """
        prompt = PROMPTS.get(modality, PROMPTS["image"])
        model = model or self.model_for(modality)
        allowed = self.large_media if large_media is None else large_media

        # **Checked before anything is sent anywhere.** Past this bound no
        # transport helps, so uploading first would spend a large upload to
        # arrive at the same refusal.
        beyond = beyond_model(payload, modality)
        if beyond:
            raise MediaBeyondModel(beyond)

        if len(payload) <= MAX_INLINE_BYTES:
            # Metered here rather than at the call site: this engine is not in a
            # routing chain, so nothing above it opens a metered block, and media
            # is the most expensive per-item call the platform makes.
            async with usage.meter("interpret", "gemini", model_id=model):
                return await self._interpret(payload, prompt, model, mime, modality)

        if not allowed or self._files is None:
            raise MediaTooLarge(
                f"{len(payload)} bytes exceeds the {MAX_INLINE_BYTES} inline "
                "ceiling; enable large media to send it through file upload"
            )
        if self._max_large_bytes and len(payload) > self._max_large_bytes:
            # A deployment bound, not the provider's. Named separately so the
            # sentence says which one refused, and raised as `MediaTooLarge`
            # because -- unlike the model ceiling -- raising the setting fixes it.
            raise MediaTooLarge(
                f"{len(payload)} bytes exceeds the {self._max_large_bytes} byte "
                "limit this deployment allows for large media"
            )
        async with usage.meter("interpret", "gemini", model_id=model):
            return await self._interpret_large(payload, prompt, model, mime, modality)

    async def _interpret(
        self, payload: bytes, prompt: str, model: str, mime: str, modality: str
    ) -> Interpreted:
        return await self._generate(
            [
                {"text": prompt},
                {
                    "inline_data": {
                        "mime_type": mime or "application/octet-stream",
                        "data": base64.b64encode(payload).decode(),
                    }
                },
            ],
            model, modality,
        )

    async def _interpret_large(
        self, payload: bytes, prompt: str, model: str, mime: str, modality: str
    ) -> Interpreted:
        """Upload, wait for it to be usable, ask the same question, tidy up.

        **The prompt is the one the inline path uses.** A transcript should not
        change character with the size of its input, and giving the large path
        its own wording would make two corpora out of one.
        """
        handle = await self._files.upload(
            payload, mime=mime or "application/octet-stream",
            display_name=f"{modality}-{len(payload)}",
        )
        try:
            handle = await self._files.await_active(handle)
            result = await self._generate(
                [
                    {"text": prompt},
                    {"file_data": {"mime_type": handle.mime_type or mime,
                                   "file_uri": handle.uri}},
                ],
                model, modality,
            )
        finally:
            # In a `finally` because a file left behind counts against a project
            # quota that nothing else will free until it expires. `delete` never
            # raises, so this cannot displace the error on its way up.
            await self._files.delete(handle.name)
        # The one thing that distinguishes this from an inline interpretation
        # afterwards. Read by the enrich worker, which does not build a graph
        # from a transcript this long unless it has been asked to.
        result.structure["via"] = "files_api"
        result.structure["bytes"] = len(payload)
        return result

    async def _generate(
        self, parts: list[dict], model: str, modality: str
    ) -> Interpreted:
        """One `generateContent` call, however the media got there.

        Shared rather than duplicated because the interesting half of this
        function is the error handling, and two copies of it would drift.
        """
        body = {
            "contents": [{"role": "user", "parts": parts}],
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


def beyond_model(payload: bytes, modality: str) -> str | None:
    """The sentence to refuse with, or None if it is worth trying.

    Deliberately one-sided. Bytes are a poor proxy for duration -- a codec
    choice moves it by an order of magnitude -- so this only catches inputs that
    could not be within the limit under any encoding, and lets the provider be
    the authority on everything else. An input wrongly refused here never
    reaches a model that would have accepted it, and nothing reports that; an
    input wrongly sent comes back with the provider's own reason.

    Documents are absent on purpose: a page count cannot be estimated from the
    size of a PDF at all, so the provider decides.
    """
    per_second = BYTES_PER_SECOND.get(modality)
    limit = MODEL_LIMITS.get(modality)
    if not per_second or not limit:
        return None
    ceiling = per_second * limit["seconds"]
    if len(payload) <= ceiling:
        return None
    return (
        f"{len(payload)} bytes is beyond what the model can take for "
        f"{modality} -- the limit is {limit['label']}, and no upload path "
        "changes that"
    )


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
    files = None
    if settings.large_media:
        # Built only when the deployment has switched it on, so a deployment
        # that has not cannot accidentally acquire the expensive path through an
        # org setting alone -- the deployment supplies the key and pays the bill.
        from .gemini_files import GeminiFiles

        files = GeminiFiles(settings.gemini_api_key)
    return GeminiMultimodal(
        settings.gemini_api_key, settings.multimodal_model, per_modality,
        large_media=settings.large_media, files=files,
        max_large_bytes=settings.max_large_media_bytes,
    )
