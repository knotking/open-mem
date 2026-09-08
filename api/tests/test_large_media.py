"""Media too large to send inline, against a Files API that behaves like one.

The inline path has been exercised for months; what is new here is everything
the small path never needed. A file that is not usable when the upload returns.
A handle that has to be cleaned up whether the call worked or not. And a second
ceiling -- the model's -- that no amount of uploading moves, which is the one
most likely to be mistaken for the first.

The simulator in `tools/fake_gemini_files.py` is the point: reading the client
cannot tell you whether the delete happens on the right side of the answer, and
a simulator that returned `ACTIVE` immediately would let the same bug through.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memdog import multimodal                                     # noqa: E402
from memdog.gemini_files import FileNotReady, GeminiFiles         # noqa: E402
from memdog.multimodal import (MAX_INLINE_BYTES, GeminiMultimodal,  # noqa: E402
                               MediaBeyondModel, MediaTooLarge,
                               beyond_model)
from tools import fake_gemini_files                               # noqa: E402

pytestmark = pytest.mark.asyncio

SMALL = b"\x00" * 1024
LARGE = b"\x00" * (MAX_INLINE_BYTES + 1)


@pytest.fixture
def files_api():
    fake_gemini_files.reset()
    server = fake_gemini_files.serve(port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()


@pytest.fixture(autouse=True)
def _quick_polls(monkeypatch):
    """The wait is real and two seconds of it per test is not.

    The number of polls is what matters -- the simulator holds a file in
    PROCESSING for two of them -- so the interval is shortened and the count
    left alone.
    """
    monkeypatch.setattr("memdog.gemini_files.POLL_SECONDS", 0.01)


def _engine(base: str, *, large: bool = True, max_large: int = 0) -> GeminiMultimodal:
    engine = GeminiMultimodal(
        "test-key", "gemini-3.7-flash",
        large_media=large,
        files=GeminiFiles("test-key", base=base, timeout=10.0),
        max_large_bytes=max_large,
    )
    # The same host serves `generateContent`, so both halves of the call land on
    # the simulator rather than one of them reaching the real provider.
    engine._base = f"{base}/v1beta"
    return engine


# ------------------------------------------------------- the routing decision

async def test_a_small_payload_never_touches_the_upload(files_api):
    """The path almost every item takes must not change shape because a new one
    exists beside it."""
    engine = _engine(files_api)
    with pytest.raises(Exception):
        # The simulator refuses a request with no `file_data` part, which is
        # exactly how we know the inline path was taken.
        await engine.interpret(SMALL, mime="image/png", modality="image")
    assert fake_gemini_files.COUNTS.get("start") is None
    assert fake_gemini_files.COUNTS.get("upload") is None


async def test_large_media_off_refuses_exactly_as_before(files_api):
    """Every deployment that has not asked for this keeps the behaviour it has,
    including the sentence -- `test_media.py` still pins the pipeline half."""
    engine = _engine(files_api, large=False)
    with pytest.raises(MediaTooLarge) as exc:
        await engine.interpret(LARGE, mime="video/mp4", modality="video")
    assert "inline ceiling" in str(exc.value)
    assert fake_gemini_files.COUNTS == {}, "refused, and nothing was uploaded"


async def test_large_media_on_uploads_waits_and_interprets(files_api):
    engine = _engine(files_api)
    result = await engine.interpret(LARGE, mime="video/mp4", modality="video")

    assert fake_gemini_files.TRANSCRIPT in result.text
    assert result.modality == "video"
    # The marker the enrich worker reads to decide about the graph.
    assert result.structure["via"] == "files_api"
    assert result.structure["bytes"] == len(LARGE)
    # Uploaded once, polled until active, answered, and cleaned up.
    assert fake_gemini_files.COUNTS["start"] == 1
    assert fake_gemini_files.COUNTS["upload"] == 1
    assert fake_gemini_files.COUNTS["generate"] == 1
    assert fake_gemini_files.COUNTS["delete"] == 1
    assert fake_gemini_files.FILES == {}, "the file outlived the call"


async def test_the_wait_is_real(files_api):
    """A file is `PROCESSING` when the upload returns.

    The simulator refuses a `generateContent` against a file that is not yet
    `ACTIVE`, so code that uploaded and asked immediately would fail here. It
    polls twice before flipping, so polling exactly once is not enough either.
    """
    engine = _engine(files_api)
    await engine.interpret(LARGE, mime="audio/mpeg", modality="audio")
    assert fake_gemini_files.COUNTS["get"] >= fake_gemini_files.POLLS_BEFORE_ACTIVE


async def test_a_file_the_provider_cannot_process_gives_up_by_name(files_api):
    fake_gemini_files.FORCE_STATE["state"] = "FAILED"
    engine = _engine(files_api)
    with pytest.raises(FileNotReady):
        await engine.interpret(LARGE, mime="video/mp4", modality="video")
    # Still cleaned up. A file that failed is a file that still counts against
    # the project's storage quota until it expires.
    assert fake_gemini_files.COUNTS["delete"] == 1


async def test_the_file_is_deleted_even_when_the_model_call_fails(files_api):
    """The `finally` is the whole point.

    An interpretation that raises halfway would otherwise leave its input
    behind, and the input is the largest thing we ever send.
    """
    engine = _engine(files_api)
    # Point `generateContent` somewhere that will not answer, leaving the upload
    # to be tidied by the failure path rather than the success one.
    engine._base = f"{files_api}/v1beta/nowhere"
    with pytest.raises(Exception):
        await engine.interpret(LARGE, mime="video/mp4", modality="video")
    assert fake_gemini_files.COUNTS["delete"] == 1
    assert fake_gemini_files.FILES == {}


# ------------------------------------------------------------ the two ceilings

async def test_beyond_the_model_nothing_is_uploaded(files_api):
    """The distinction the whole design rests on.

    Past the model's own limit no transport helps, so uploading first would
    spend the largest upload the platform makes to arrive at the same refusal.
    """
    limit = multimodal.MODEL_LIMITS["video"]["seconds"]
    enormous = b"\x00" * (multimodal.BYTES_PER_SECOND["video"] * limit + 1)
    engine = _engine(files_api)
    with pytest.raises(MediaBeyondModel) as exc:
        await engine.interpret(enormous, mime="video/mp4", modality="video")

    assert "no upload path changes that" in str(exc.value)
    assert "about an hour of video" in str(exc.value)
    assert fake_gemini_files.COUNTS == {}, "spent an upload on an impossible input"


async def test_the_two_refusals_do_not_share_a_sentence():
    """"Too big to send" is fixed by a setting and "too long to understand" is
    not. Telling somebody to enable something that will not help them is worse
    than telling them nothing."""
    too_big = MAX_INLINE_BYTES + 1
    assert beyond_model(b"\x00" * too_big, "video") is None, (
        "an ordinary large video must not be refused as beyond the model"
    )
    beyond = multimodal.BYTES_PER_SECOND["audio"] * \
        multimodal.MODEL_LIMITS["audio"]["seconds"] + 1
    assert "beyond what the model can take" in beyond_model(b"\x00" * beyond, "audio")


async def test_documents_are_left_to_the_provider():
    """A page count cannot be estimated from the size of a PDF, so guessing
    would refuse real documents to avoid a request that would have said no
    itself."""
    assert beyond_model(b"\x00" * (2 * 1024 * 1024 * 1024), "ocr") is None
    assert beyond_model(b"\x00" * (2 * 1024 * 1024 * 1024), "image") is None


async def test_a_deployment_can_bound_this_below_the_provider(files_api):
    """A first gigabyte video should be a surprise somebody can absorb."""
    engine = _engine(files_api, max_large=len(LARGE) - 1)
    with pytest.raises(MediaTooLarge) as exc:
        await engine.interpret(LARGE, mime="video/mp4", modality="video")
    assert "this deployment allows" in str(exc.value)
    assert fake_gemini_files.COUNTS == {}


# ------------------------------------------------------------------ the prompt

async def test_the_large_path_asks_the_same_question(files_api, monkeypatch):
    """A transcript must not change character with the size of its input.

    Two prompts would make two corpora out of one, and the difference would show
    up as retrieval quality that varies with file size -- which nobody would
    trace back to a prompt.
    """
    seen: list[list[dict]] = []
    engine = _engine(files_api)
    original = engine._generate

    async def capture(parts, model, modality):
        seen.append(parts)
        return await original(parts, model, modality)

    monkeypatch.setattr(engine, "_generate", capture)
    await engine.interpret(LARGE, mime="video/mp4", modality="video")

    texts = [p["text"] for p in seen[0] if "text" in p]
    assert texts == [multimodal.PROMPTS["video"]]
    assert any("file_data" in p for p in seen[0]), "sent inline after all"
