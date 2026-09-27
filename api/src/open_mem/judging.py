"""Judging a batch of transitions against a description.

The third model seam, and it exists because neither of the other two fits. The
extractor returns a typed envelope about one document; the answerer returns
prose with citation markers. A judge answers a different shape of question:
**for each of these forty candidates, does it match this description?**

Two rules the implementation is built around.

**It never guesses.** Extraction falls back to a local heuristic because a worse
envelope is recoverable and a missing one stalls an item forever. A judgement is
not like that: a wrong `matched` is a false alarm and a wrong `not matched` is a
silence nobody notices. So when no model is available this **defers** -- the run
fails, the watermark does not move, and the sweep tries again. That is the same
choice embeddings make, for the same reason.

**A candidate the model does not mention is not matched.** Models omit things.
Treating an omission as a match would invent alerts; treating it as an error
would stall the batch on one bad row. Absent means no, recorded as such.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

log = logging.getLogger(__name__)


class JudgeUnavailable(Exception):
    """No model could answer. Deliberately not a verdict."""


@dataclass(frozen=True)
class Verdict:
    key: str
    matched: bool
    confidence: float
    evidence: str | None = None


class Judge(Protocol):
    model_id: str

    async def judge(
        self, description: str, candidates: list[dict], *, surface: str
    ) -> list[Verdict]: ...


SYSTEM = (
    "You decide whether each candidate event matches a description. "
    "Answer for every candidate you are given, using its key. "
    "Match only when the description is clearly satisfied by the candidate's "
    "own fields -- do not infer beyond them, and do not match on topic "
    "similarity alone. When uncertain, do not match."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "matched": {"type": "boolean"},
                    "confidence": {"type": "number"},
                    "evidence": {"type": "string"},
                },
                "required": ["key", "matched", "confidence"],
            },
        }
    },
    "required": ["verdicts"],
}


class GeminiJudge:
    """One call for the whole batch, schema-constrained.

    One call per candidate would make `llm` mode cost what per-write evaluation
    was rejected for. The batch is the reason this mode is affordable at all.
    """

    def __init__(self, api_key: str, model_id: str) -> None:
        self.model_id = model_id
        self._api_key = api_key
        self._base = "https://generativelanguage.googleapis.com/v1beta"

    async def judge(
        self, description: str, candidates: list[dict], *, surface: str
    ) -> list[Verdict]:
        if not candidates:
            return []
        user = json.dumps({
            "description": description,
            "event_kind": surface,
            "candidates": candidates,
        }, default=str)[:200_000]
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                response = await client.post(
                    f"{self._base}/models/{self.model_id}:generateContent",
                    headers={"x-goog-api-key": self._api_key},
                    json={
                        "systemInstruction": {"parts": [{"text": SYSTEM}]},
                        "contents": [{"role": "user", "parts": [{"text": user}]}],
                        "generationConfig": {
                            "temperature": 0,
                            "maxOutputTokens": 8192,
                            "responseMimeType": "application/json",
                            "responseSchema": _SCHEMA,
                        },
                    },
                )
            if response.status_code != 200:
                raise JudgeUnavailable(f"{self.model_id}: HTTP {response.status_code}")
            body = response.json()
            text = body["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
        except JudgeUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise JudgeUnavailable(f"{self.model_id}: {exc}") from exc

        return [
            Verdict(
                key=str(v["key"]),
                matched=bool(v["matched"]),
                confidence=float(v.get("confidence") or 0.0),
                evidence=(v.get("evidence") or None),
            )
            for v in parsed.get("verdicts", [])
        ]


class RefusingJudge:
    """The floor, and it refuses rather than answering.

    There is deliberately no local heuristic here. A keyword match dressed as a
    judgement would fire alerts nobody asked for and stay silent on ones they
    did, and both failures look like the feature working.
    """

    model_id = "none"

    async def judge(self, description, candidates, *, surface):  # noqa: ANN001
        raise JudgeUnavailable(
            "no judging model is configured; set a Gemini key or use rule mode")


def build_judge(settings) -> Judge:
    key = getattr(settings, "gemini_api_key", "")
    model = getattr(settings, "multimodal_model", "") or "gemini-3.7-flash"
    if key:
        return GeminiJudge(key, model)
    return RefusingJudge()
