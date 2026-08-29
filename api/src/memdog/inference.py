"""Embedding engines, and the two rules that keep the vector space comparable.

**Every embedding row carries `model_id`, and retrieval filters on it.** Without
that column a mixed index cannot be identified, let alone repaired: you know
ranking is wrong and cannot tell which rows caused it.

**There is no fallback.** If the assigned engine is unavailable the work is
deferred and retried. Falling back to a second model silently writes vectors
from a different space into the same index, and nothing downstream can tell.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Protocol

import httpx

from . import usage

from .chunking import CHUNKER_VERSION
from .config import Settings

PARSER_VERSION = "text-1"


class EmbeddingUnavailable(RuntimeError):
    """Defer and retry. Never substitute another model."""


class EmbeddingEngine(Protocol):
    model_id: str
    dim: int

    async def embed(self, texts: list[str], *,
                    task: str = "document") -> list[list[float]]: ...


def generator_version(*, purpose: str, model_id: str, spec: dict) -> str:
    """A fingerprint, not a number.

    Staleness detection becomes a join rather than a manual bump someone forgets,
    and it catches changes nobody thought to version.
    """
    canonical = json.dumps(
        {
            "purpose": purpose,
            "model_id": model_id,
            "parser_version": PARSER_VERSION,
            "chunker_version": CHUNKER_VERSION,
            **spec,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "gen_" + hashlib.sha256(canonical.encode()).hexdigest()[:32]


_TOKEN = re.compile(r"[a-z0-9]+")


class LocalHashEmbedder:
    """A deterministic feature hash. Offline, dependency-free, reproducible.

    Be clear about what this is: hashed term frequencies, not learned semantics.
    It exists so the spine, the air-gap acceptance test and CI can run without a
    model server. It is a *registered model like any other* -- its vectors carry
    `model_id = local-hash-v1`, so the day a real engine is assigned, the old
    rows are identifiable and re-embeddable rather than quietly mixed in.
    """

    def __init__(self, dim: int) -> None:
        self.dim = dim
        self.model_id = "local-hash-v1"

    async def embed(self, texts: list[str], *,
                    task: str = "document") -> list[list[float]]:
        # Symmetric: a hash of terms has no notion of a question. Accepted and
        # ignored so the caller does not have to know which engine it holds.
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in _TOKEN.findall(text.lower()):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[index] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            vec[0] = 1.0
            return vec
        return [v / norm for v in vec]


class OllamaEmbedder:
    """The local/GKE inference path. Truncates only where the model says it may.

    Matryoshka-trained models permit taking the first N dimensions as a valid
    embedding; anything else is a dimension mismatch and raises rather than
    padding, because a padded vector is a wrong answer that looks like a right one.
    """

    def __init__(self, model_id: str, dim: int, base_url: str, *, matryoshka: bool = True) -> None:
        self.model_id = model_id
        self.dim = dim
        self._base_url = base_url.rstrip("/")
        self._matryoshka = matryoshka

    async def embed(self, texts: list[str], *,
                    task: str = "document") -> list[list[float]]:
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self._base_url}/api/embed",
                    json={"model": self.model_id, "input": texts},
                )
                response.raise_for_status()
                vectors = response.json()["embeddings"]
        except (httpx.HTTPError, KeyError) as exc:
            raise EmbeddingUnavailable(str(exc)) from exc
        return [self._fit(v) for v in vectors]

    def _fit(self, vector: list[float]) -> list[float]:
        if len(vector) == self.dim:
            return vector
        if len(vector) > self.dim and self._matryoshka:
            head = vector[: self.dim]
            norm = math.sqrt(sum(v * v for v in head)) or 1.0
            return [v / norm for v in head]
        raise EmbeddingUnavailable(
            f"{self.model_id} returned dim {len(vector)}, index is dim {self.dim}"
        )


class GeminiEmbedder:
    """Learned semantics, unlike the hash embedder it replaces.

    Two things here are not incidental.

    **Documents and queries are embedded differently.** A question and the
    passage answering it are not the same kind of text -- "why did checkout
    break?" and "the outage was caused by an expired certificate" share almost
    no vocabulary. Asymmetric task types are what let the vector arm find that
    pair, and symmetric embedding is a large part of why naive vector search
    disappoints.

    **There is no fallback.** Every other engine in this codebase degrades to a
    local one when the provider is unavailable; this one must not. Vectors from
    two models in one index are not comparable, so a fallback would silently
    corrupt retrieval for every row it touched -- and unlike a bad summary, a
    bad vector is invisible. Unavailability defers and retries instead.
    """

    # The API caps a batch; larger requests are rejected rather than truncated.
    BATCH = 100

    TASKS = {
        "document": "RETRIEVAL_DOCUMENT",
        "query": "RETRIEVAL_QUERY",
    }

    def __init__(self, api_key: str, model: str, dim: int) -> None:
        self._api_key = api_key
        self._model = model
        self.dim = dim
        # The dimension is part of the identity: the same model at 768 and at
        # 1536 produces vectors that cannot be compared, so they must not share
        # a model_id or the staleness join will consider them interchangeable.
        self.model_id = f"{model}@{dim}"
        self._base = "https://generativelanguage.googleapis.com/v1beta"

    async def embed(self, texts: list[str], *,
                    task: str = "document") -> list[list[float]]:
        if not texts:
            return []
        task_type = self.TASKS.get(task, self.TASKS["document"])
        vectors: list[list[float]] = []
        # One metered call even though it pages: the batches are an artifact of
        # a provider request ceiling, not separate work the caller asked for.
        # Embedding is the highest-volume model call in the system -- every
        # chunk of every item -- so leaving it unmetered, which is the usual
        # shortcut, misses the largest line in a bulk ingest.
        async with usage.meter("embed", "gemini", model_id=self.model_id):
            async with httpx.AsyncClient(timeout=120.0) as client:
                for start in range(0, len(texts), self.BATCH):
                    chunk = texts[start:start + self.BATCH]
                    vectors.extend(await self._batch(client, chunk, task_type))
        return vectors

    async def _batch(self, client: httpx.AsyncClient, texts: list[str],
                     task_type: str) -> list[list[float]]:
        sent = [text[:20_000] for text in texts]
        payload = {
            "requests": [
                {
                    "model": f"models/{self._model}",
                    "content": {"parts": [{"text": text}]},
                    "taskType": task_type,
                    "outputDimensionality": self.dim,
                }
                for text in sent
            ]
        }
        # `batchEmbedContents` reports no token usage, so this is estimated from
        # the characters actually sent -- after truncation, since the tail we
        # dropped is not billed. Flagged as an estimate rather than presented as
        # a provider figure: a number that is quietly approximate is the one
        # that gets reconciled against an invoice and cannot be explained.
        #
        # Recorded before the request rather than after, so a batch that is
        # accepted and then times out is still counted. That over-counts a
        # connection that never opened, which is the rarer case and the safer
        # direction to be wrong in.
        usage.observe(
            tokens_in=sum(len(text) for text in sent) // 4,
            estimated=True,
        )
        try:
            response = await client.post(
                f"{self._base}/models/{self._model}:batchEmbedContents",
                headers={"x-goog-api-key": self._api_key},
                json=payload,
            )
            if response.status_code == 429:
                retry_after = response.headers.get("retry-after")
                raise EmbeddingUnavailable(
                    "embedding provider is rate limiting"
                    + (f"; retry after {retry_after}s" if retry_after else "")
                )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise EmbeddingUnavailable(f"embedding request failed: {exc}") from exc

        embeddings = data.get("embeddings") or []
        if len(embeddings) != len(texts):
            # A short response would silently misalign vectors with chunks --
            # every embedding after the gap would describe the wrong text.
            raise EmbeddingUnavailable(
                f"expected {len(texts)} embeddings, got {len(embeddings)}"
            )
        vectors = []
        for item in embeddings:
            values = item.get("values") or []
            if len(values) != self.dim:
                raise EmbeddingUnavailable(
                    f"expected {self.dim} dimensions, got {len(values)}"
                )
            vectors.append([float(v) for v in values])
        return vectors


def build_embedder(settings: Settings) -> EmbeddingEngine:
    if settings.embed_engine == "gemini":
        if not settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required when EMBED_ENGINE=gemini")
        return GeminiEmbedder(
            settings.gemini_api_key,
            settings.embed_model or "gemini-embedding-001",
            settings.embed_dim,
        )
    if settings.embed_engine == "ollama":
        if not settings.embed_model:
            raise ValueError("EMBED_MODEL is required when EMBED_ENGINE=ollama")
        return OllamaEmbedder(settings.embed_model, settings.embed_dim, settings.ollama_url)
    return LocalHashEmbedder(settings.embed_dim)
