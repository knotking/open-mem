"""Reading a page the fetcher cannot get, by asking the model to read it.

**This is a fallback, not a replacement for fetching, and the order matters.**
An HTTP GET returns the bytes somebody published: they are stored, versioned,
re-parseable, and a claim made about them can be checked against them later. URL
Context returns a *model's reading* of a page and no bytes at all. Preferring it
would trade an artifact for an opinion about an artifact, which is the wrong
direction for a record store.

So it runs only where the fetcher has already failed or come back with nothing
usable -- a bot wall, a 403, a page whose content arrives by JavaScript and
whose HTML is an empty shell. Those are the pages that currently land as a
stored record with no text, and something is better than nothing there.

**The retrieval status is the whole safety property.** Asked about a URL it
could not reach, the model answers anyway, fluently, from training -- the first
probe of this API produced a confident and correct-sounding paragraph about
`example.com` alongside `URL_RETRIEVAL_STATUS_ERROR` for that exact URL. Nothing
in the text says which happened. So a reading is accepted only when the metadata
says the URL was actually retrieved, and a reading with no metadata at all is
refused rather than trusted: silence is not success.

What is stored is written as an account, attributed, and never dressed up as the
page itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from . import usage

log = logging.getLogger(__name__)

# The provider's own ceiling, recorded so a caller can see it rather than
# discover it. 20 URLs per request, 34 MB per URL.
MAX_URLS = 20
READ_TIMEOUT_SECONDS = 300.0
MAX_OUTPUT_TOKENS = 8192

RETRIEVED = "URL_RETRIEVAL_STATUS_SUCCESS"

PROMPT = (
    "Read the page at the URL below and produce a faithful account of it for a "
    "searchable knowledge base. State what the page is, who published it, when "
    "if it says, and what it actually claims -- section by section, in the "
    "order it appears. Quote a short phrase where the exact wording matters and "
    "attribute it.\n\n"
    "Report only what is on the page. If you cannot retrieve it, say exactly "
    "that and write nothing else -- do not describe what the page is likely to "
    "contain, and do not answer from what you already know about the site. An "
    "account assembled from memory is indistinguishable from one that was read, "
    "and it is worse than no account at all."
)


class UrlNotRead(Exception):
    """The page was not retrieved. `retryable` separates a rate limit from a
    refusal, a paywall or a robots block -- the first is worth another attempt
    and the others never will be."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass
class Read:
    url: str
    account: str
    model_id: str
    # Every URL the tool reported on, with what happened to it. Kept whole
    # because "we asked and it refused" is a different fact from "we never
    # asked", and only this can tell them apart afterwards.
    retrieval: list[dict] = field(default_factory=list)
    tokens: int = 0
    tool_tokens: int = 0
    model_version: str | None = None
    response_id: str | None = None


class NullUrlReader:
    """URL Context is off. Says so, specifically."""

    enabled = False
    model_id = "none"

    async def read(self, url: str) -> Read:
        raise UrlNotRead(
            "reading a page with URL Context is not enabled on this deployment")


class GeminiUrlReader:
    """Hands the URL to Gemini and lets it do the fetching.

    The page is never downloaded here, which is the point: this exists for
    pages the deployment's own fetcher cannot reach, so fetching it ourselves
    to hand it over would defeat the purpose and fail in the same way.
    """

    enabled = True

    def __init__(self, api_key: str, model_id: str) -> None:
        self.model_id = model_id
        self._api_key = api_key
        self._base = "https://generativelanguage.googleapis.com/v1beta"

    async def read(self, url: str) -> Read:
        # Metered here for the reason `multimodal` and `youtube` meter in the
        # engine: nothing above opens a metered block, and the page's own
        # content is billed as input tokens under `toolUsePromptTokenCount`.
        async with usage.meter("url_context", "gemini", model_id=self.model_id):
            return await self._read(url)

    async def _read(self, url: str) -> Read:
        body = {
            "contents": [{"role": "user",
                          "parts": [{"text": f"{PROMPT}\n\nURL: {url}"}]}],
            # The tool is declared empty -- the URLs come from the prompt text,
            # not from a parameter. That is the API's shape, not a shortcut.
            "tools": [{"url_context": {}}],
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
                raise UrlNotRead(f"the model is rate limiting: {detail or exc}",
                                 retryable=True) from exc
            if exc.response.status_code in (500, 503):
                raise UrlNotRead(
                    f"the model is temporarily unavailable: {detail or exc}",
                    retryable=True) from exc
            raise UrlNotRead(
                f"the page could not be read ({exc.response.status_code}): "
                f"{detail or exc}") from exc
        except httpx.HTTPError as exc:
            raise UrlNotRead(f"reading the page failed: {exc}", retryable=True) from exc

        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise UrlNotRead(f"the model returned nothing ({reason})")
        candidate = candidates[0]

        # The safety property, and the reason this module is careful rather
        # than short. The model answers whether or not it reached the page, so
        # the *text* is not evidence of retrieval -- only this is.
        metadata = (candidate.get("urlContextMetadata") or {}).get("urlMetadata") or []
        if not metadata:
            raise UrlNotRead(
                "the model answered without reporting any retrieval, so the "
                "account cannot be distinguished from one written from memory")
        statuses = {m.get("retrievedUrl"): m.get("urlRetrievalStatus") for m in metadata}
        if RETRIEVED not in statuses.values():
            failed = ", ".join(f"{u} ({s})" for u, s in statuses.items()) or "unknown"
            raise UrlNotRead(f"the page was not retrieved: {failed}")

        parts = candidate.get("content", {}).get("parts", [])
        account = "\n".join(p.get("text", "") for p in parts).strip()
        if not account:
            raise UrlNotRead("the page was retrieved but the model wrote nothing")

        meta = data.get("usageMetadata") or {}
        usage.observe(
            tokens_in=meta.get("promptTokenCount", 0),
            tokens_out=meta.get("candidatesTokenCount", 0),
            tokens_cached=meta.get("cachedContentTokenCount", 0),
        )
        return Read(
            url=url, account=account, model_id=self.model_id, retrieval=metadata,
            tokens=meta.get("totalTokenCount", 0),
            # The page's own bytes, billed as input. Recorded separately because
            # it is the part that scales with the page rather than the prompt,
            # and it is what makes a big page expensive.
            tool_tokens=meta.get("toolUsePromptTokenCount", 0),
            model_version=data.get("modelVersion"),
            response_id=data.get("responseId"),
        )


def _message(response) -> str:
    """The provider's own sentence, or nothing. Never raises: this runs while
    handling an error, and failing to parse an error body must not replace the
    error being reported."""
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001
        return (response.text or "").strip()[:400]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:400]
    return str(payload)[:400]


def build_url_reader(settings):
    """Off unless both switched on and given a key.

    `url_context` rides the same `GEMINI_API_KEY` as everything else and is
    gated on its own flag, because it spends a model call per page on a path
    that today costs an HTTP GET -- that is a decision a deployment makes, not
    one it inherits.
    """
    if not getattr(settings, "url_context", False):
        return NullUrlReader()
    if not settings.gemini_api_key:
        log.warning("URL_CONTEXT is on but GEMINI_API_KEY is unset; pages stay unread")
        return NullUrlReader()
    return GeminiUrlReader(settings.gemini_api_key,
                           getattr(settings, "url_context_model", "")
                           or settings.multimodal_model)
