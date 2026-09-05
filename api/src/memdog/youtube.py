"""Reading a YouTube video into text that the rest of the pipeline can use.

**Why this is not a transcript.**

The obvious design is to fetch the video's own captions and store them. That
route is closed: YouTube's `timedtext` endpoint now answers a bare request with
200 and zero bytes, and the official Data API only hands captions to the
account that owns the video. The second obvious design -- download the video and
send it to the transcription model -- runs into an 18 MB inline ceiling that a
minute of 720p already exceeds, and would need ffmpeg in the runtime image,
which `pyproject.toml` deliberately rules out.

What does work is that Gemini accepts a YouTube URL directly, as a `file_data`
part, and watches the video itself. No download, no scraping, no extra
dependency.

It will not, however, write the video out verbatim: asked for a full
transcript it stops with `finishReason: RECITATION` and returns nothing at all.
That is the right outcome rather than an obstacle -- storing somebody's whole
video as text is reproducing it, and it is not what the graph needs. What the
graph needs is what was said, what it was about, and what connects to what. So
this asks for a structured account in the model's own words, section by section
with timestamps, and gets several hundred words of exactly that.

The account is written into the record as if it had been fetched, so
classification, parsing, embedding, enrichment and graph extraction all run
afterwards with no knowledge that a video was involved.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urlparse

import httpx

from . import usage

log = logging.getLogger(__name__)

# 11 characters of base64url is the shape YouTube has used since the beginning.
# Matching it exactly is what stops `?v=` carrying something else entirely into
# a URL we then hand to a model.
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")

HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "youtu.be", "www.youtu.be",
    "youtube-nocookie.com", "www.youtube-nocookie.com",
}

# Long enough for a conference talk read end to end, short enough that a
# malformed answer is obvious rather than merely expensive.
MAX_OUTPUT_TOKENS = 8192
READ_TIMEOUT_SECONDS = 600.0
OEMBED_TIMEOUT_SECONDS = 20.0

PROMPT = (
    "Produce a detailed structured account of this video for a searchable "
    "knowledge base. Cover it section by section in the order it happens, with "
    "an approximate timestamp for each section, what is explained or claimed, "
    "the people, organizations, works and technical terms introduced, and how "
    "they relate to one another. Name the speakers where they can be "
    "identified. Where a short phrase is worth keeping exactly, quote it and "
    "attribute it.\n\n"
    "Write it in your own words as an account of the material. Do not "
    "transcribe the video verbatim and do not reproduce it at length: a "
    "summary that stands in for the original is not what is wanted here."
)


class NotAVideo(Exception):
    """The URL is not a YouTube video. Terminal -- retrying re-reads the same
    URL and reaches the same conclusion."""


class ReadUnavailable(Exception):
    """The read failed. `retryable` distinguishes a rate limit from a refusal,
    because the queue's backoff is the right home for the first and the wrong
    home for the second."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class Watched:
    video_id: str
    url: str
    title: str | None
    author: str | None
    account: str
    model_id: str


def video_id(url: str) -> str | None:
    """The video id, or None if this is not a YouTube video URL.

    Deliberately strict. A permissive parser here would let a URL on another
    host reach the model with a `file_uri` we did not intend, and "it looked
    like YouTube" is not a property worth guessing at.
    """
    if not url or not isinstance(url, str):
        return None
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or (parsed.hostname or "").lower() not in HOSTS:
        return None

    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    if host in ("youtu.be", "www.youtu.be"):
        candidate = path.lstrip("/").split("/")[0]
    elif path == "/watch":
        candidate = (parse_qs(parsed.query).get("v") or [""])[0]
    else:
        # /shorts/ID, /embed/ID, /live/ID -- the same id in a different route.
        parts = [p for p in path.split("/") if p]
        routed = len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v")
        candidate = parts[1] if routed else ""
    return candidate if VIDEO_ID.match(candidate or "") else None


def canonical(vid: str) -> str:
    """One URL shape for one video, so the same video pasted three ways is one
    `external_id` rather than three records."""
    return f"https://www.youtube.com/watch?v={vid}"


async def describe(vid: str) -> tuple[str | None, str | None]:
    """Title and channel, from the public oEmbed endpoint.

    Official, unauthenticated and cheap. Best-effort on purpose: a record whose
    title says "watch?v=aircAruvnKk" is worse than one titled properly, and
    neither is worth failing the whole read over.
    """
    try:
        async with httpx.AsyncClient(timeout=OEMBED_TIMEOUT_SECONDS) as client:
            response = await client.get(
                "https://www.youtube.com/oembed"
                f"?url={quote(canonical(vid), safe='')}&format=json"
            )
            if response.status_code != 200:
                return None, None
            data = response.json()
    except (httpx.HTTPError, ValueError):
        return None, None
    return data.get("title"), data.get("author_name")


class GeminiVideoReader:
    """Watches the video at a URL and writes an account of it.

    The video is never downloaded here. `file_data.file_uri` hands Gemini the
    URL and Gemini fetches it, which is what keeps this free of ffmpeg, yt-dlp
    and the 18 MB inline ceiling that governs `multimodal.py`.
    """

    def __init__(self, api_key: str, model_id: str) -> None:
        self.model_id = model_id
        self.enabled = True
        self._api_key = api_key
        self._base = "https://generativelanguage.googleapis.com/v1beta"

    async def read(self, vid: str) -> Watched:
        url = canonical(vid)
        title, author = await describe(vid)
        # Metered here for the reason `multimodal` meters in the engine: nothing
        # above opens a metered block, and watching a video is the most
        # expensive single call the platform makes -- roughly a hundred thousand
        # input tokens for twenty minutes of video.
        async with usage.meter("watch", "gemini", model_id=self.model_id):
            account = await self._watch(url)
        return Watched(video_id=vid, url=url, title=title, author=author,
                       account=account, model_id=self.model_id)

    async def _watch(self, url: str) -> str:
        body = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"file_data": {"file_uri": url}},
                    {"text": PROMPT},
                ],
            }],
            "generationConfig": {"temperature": 0,
                                 "maxOutputTokens": MAX_OUTPUT_TOKENS},
        }
        try:
            async with httpx.AsyncClient(timeout=READ_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{self._base}/models/{self.model_id}:generateContent",
                    headers={"x-goog-api-key": self._api_key},
                    json=body,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            detail = _message(exc.response)
            if exc.response.status_code == 429:
                raise ReadUnavailable(
                    f"the model is rate limiting: {detail or exc}", retryable=True
                ) from exc
            if exc.response.status_code in (500, 503):
                raise ReadUnavailable(
                    f"the model is temporarily unavailable: {detail or exc}",
                    retryable=True,
                ) from exc
            raise ReadUnavailable(
                f"the video could not be read ({exc.response.status_code}): "
                f"{detail or exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ReadUnavailable(f"reading the video failed: {exc}",
                                  retryable=True) from exc

        return _text_of(data)


def _message(response) -> str:
    """The provider's own sentence. `raise_for_status` keeps the status and
    throws away the body, which is the half that says what was wrong."""
    try:
        return str((response.json().get("error") or {}).get("message") or "")[:400]
    except (ValueError, AttributeError):
        return ""


def _text_of(data: dict) -> str:
    """The account, or a sentence saying why there is none.

    `RECITATION` is called out by name because it is not a failure of the
    request -- it is the model declining to reproduce the video, and a reader
    who sees "no content" would go looking for a bug instead of rewording a
    prompt.
    """
    candidates = data.get("candidates") or []
    if not candidates:
        raise ReadUnavailable("the model returned no answer for this video")
    candidate = candidates[0]
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if text:
        return text

    reason = candidate.get("finishReason") or "unknown"
    if reason == "RECITATION":
        raise ReadUnavailable(
            "the model declined to write this video out, because doing so "
            "would reproduce it rather than describe it"
        )
    raise ReadUnavailable(f"the model returned nothing for this video ({reason})")


def build_video_reader(settings):
    """Off unless there is a key and media interpretation is on.

    Same switch as `build_multimodal`: watching a video is media interpretation,
    and a deployment that has turned that off has not agreed to pay for this
    either.
    """
    if not settings.media_interpretation or not settings.gemini_api_key:
        return None
    # The multimodal model, never the transcription one. `build_multimodal`
    # makes the same distinction for the same reason: a transcription-only model
    # rejects frames, and this call is frames as much as audio.
    return GeminiVideoReader(settings.gemini_api_key, settings.multimodal_model)
