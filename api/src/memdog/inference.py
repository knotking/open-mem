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

from .chunking import CHUNKER_VERSION
from .config import Settings

PARSER_VERSION = "text-1"


class EmbeddingUnavailable(RuntimeError):
    """Defer and retry. Never substitute another model."""


class EmbeddingEngine(Protocol):
    model_id: str
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


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

    async def embed(self, texts: list[str]) -> list[list[float]]:
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

    async def embed(self, texts: list[str]) -> list[list[float]]:
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


def build_embedder(settings: Settings) -> EmbeddingEngine:
    if settings.embed_engine == "ollama":
        if not settings.embed_model:
            raise ValueError("EMBED_MODEL is required when EMBED_ENGINE=ollama")
        return OllamaEmbedder(settings.embed_model, settings.embed_dim, settings.ollama_url)
    return LocalHashEmbedder(settings.embed_dim)
