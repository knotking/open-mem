"""Media through the whole pipeline, and the version history it produces.

Upload bytes -> blob store -> parse or interpret -> searchable -> enriched, with
every revision recorded. The engine is faked here because the point is the
pipeline, not the provider; the real one is exercised against the deployment.
"""

from __future__ import annotations

import base64

import pytest

from memdog.contracts import Inline, WriteItem, WriteRequest, WriteOptions
from memdog.multimodal import Interpreted, MediaTooLarge
from memdog.queue import InProcessQueue
from memdog.retrieval import get_item, get_versions
from memdog.workers import EmbedWorker, EnrichWorker, EventWorker, ParseWorker
from memdog.write import EMBED_TOPIC, PARSE_TOPIC, write_items

pytestmark = pytest.mark.asyncio

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class FakeMultimodal:
    """Stands in for Gemini. Records what it was asked, so the test can assert
    the modality routing rather than the provider's behaviour."""

    model_id = "fake-omni-v1"
    enabled = True

    def __init__(self, text: str = "Speaker 1: the rollback completed at 14:02 UTC.") -> None:
        self.text = text
        self.calls: list[tuple[str, str]] = []

    async def interpret(self, payload: bytes, *, mime: str, modality: str) -> Interpreted:
        self.calls.append((mime, modality))
        return Interpreted(text=self.text, modality=modality,
                            model_id=self.model_id, tokens=1234)


class RefusingMultimodal(FakeMultimodal):
    async def interpret(self, payload: bytes, *, mime: str, modality: str) -> Interpreted:
        raise MediaTooLarge("2 GB exceeds the inline ceiling")


async def _pipeline(pool, blobs, settings, embedder, extractor, multimodal):
    queue = InProcessQueue()
    parse = ParseWorker(pool, blobs, queue=queue, multimodal=multimodal)
    parse.register(queue)
    embed = EmbedWorker(pool, embedder, settings, queue=queue)
    await embed.ensure_generator()
    embed.register(queue, EMBED_TOPIC)
    enrich = EnrichWorker(pool, extractor, settings)
    await enrich.ensure_generator()
    enrich.register(queue)
    EventWorker(
        pool, queue, parse_worker=parse, embed_worker=embed, enrich_worker=enrich
    ).register(queue)
    return queue


async def _write_bytes(pool, queue, blobs, settings, actor, producer_id, name, payload):
    return await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=producer_id, items=[
            WriteItem(external_id=name, content=Inline(bytes_b64=base64.b64encode(payload).decode())),
        ],
                        options=WriteOptions(enrich=True)),
    )


@pytest.mark.parametrize(
    "name,payload,expected_modality",
    [
        ("photo.png", PNG, "image"),
        ("voice.mp3", b"ID3\x03\x00\x00\x00" + b"\x00" * 64, "audio"),
        ("clip.mp4", b"\x00\x00\x00\x20ftypisom" + b"\x00" * 64, "video"),
    ],
)
async def test_media_is_stored_then_interpreted_then_searchable(
    pool, blobs, settings, embedder, extractor, tenant, principal_for,
    name, payload, expected_modality,
):
    actor = await principal_for(tenant.api_key)
    engine = FakeMultimodal()
    queue = await _pipeline(pool, blobs, settings, embedder, extractor, engine)

    written = await _write_bytes(
        pool, queue, blobs, settings, actor, tenant.producer_id, name, payload
    )
    data_id = written.results[0].data_id

    # Before anything is interpreted, the bytes are already durable.
    stored = await get_item(pool, actor, data_id)
    assert stored["storage_ref"].startswith("file://")
    assert stored["checksum"].startswith("sha256:")
    assert stored["state"] == "stored"

    await queue.drain()
    await queue.close()

    assert engine.calls and engine.calls[0][1] == expected_modality
    final = await get_item(pool, actor, data_id)
    assert final["state"] == "enriched"
    # The transcript is `extracted_text`, not `content_text`: the caller sent
    # bytes, and what a model made of them is a derivation, not their input.
    assert final["content_text"] is None
    assert "rollback completed" in final["extracted_text"]
    assert final["parse_status"] == "parsed"
    # The original bytes survive their own interpretation.
    assert final["storage_ref"] is not None
    assert await blobs.get(final["storage_ref"]) == payload


async def test_versions_record_who_produced_each_revision(
    pool, blobs, settings, embedder, extractor, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    engine = FakeMultimodal()
    queue = await _pipeline(pool, blobs, settings, embedder, extractor, engine)
    written = await _write_bytes(
        pool, queue, blobs, settings, actor, tenant.producer_id, "voice.mp3",
        b"ID3\x03\x00\x00\x00" + b"\x00" * 64,
    )
    await queue.drain()
    await queue.close()

    versions = await get_versions(pool, actor, written.results[0].data_id)
    assert [v["revision"] for v in versions] == [2, 1]

    transcript, original = versions
    assert transcript["source"] == "interpret"
    # "Why does this say something different than last week" has an answer.
    assert transcript["model_id"] == "fake-omni-v1"
    assert transcript["tokens"] == 1234
    assert "rollback completed" in transcript["preview"]

    assert original["source"] == "write"
    assert original["content_chars"] == 0        # it was bytes, not text
    assert original["model_id"] is None


async def test_media_stays_stored_when_interpretation_is_off(
    pool, blobs, settings, embedder, extractor, tenant, principal_for
):
    """Off is a policy, not a failure -- and the reason has to survive."""
    from memdog.multimodal import NullMultimodal

    actor = await principal_for(tenant.api_key)
    queue = await _pipeline(pool, blobs, settings, embedder, extractor, NullMultimodal())
    written = await _write_bytes(
        pool, queue, blobs, settings, actor, tenant.producer_id, "photo.png", PNG
    )
    await queue.drain()
    await queue.close()

    item = await get_item(pool, actor, written.results[0].data_id)
    assert item["state"] == "stored"
    assert item["parse_status"] == "needs_model"
    assert item["parse_detail"]["capability"] == "vision"
    assert "not enabled" in item["parse_detail"]["reason"]
    # Nothing is lost: the bytes and their checksum are still there, so this is
    # reversible by turning the engine on, not by re-uploading.
    assert await blobs.get(item["storage_ref"]) == PNG


async def test_a_file_too_large_to_send_says_so_and_is_not_retried(
    pool, blobs, settings, embedder, extractor, tenant, principal_for
):
    actor = await principal_for(tenant.api_key)
    queue = await _pipeline(pool, blobs, settings, embedder, extractor, RefusingMultimodal())
    written = await _write_bytes(
        pool, queue, blobs, settings, actor, tenant.producer_id, "big.mp4",
        b"\x00\x00\x00\x20ftypisom" + b"\x00" * 64,
    )
    await queue.drain()
    await queue.close()

    item = await get_item(pool, actor, written.results[0].data_id)
    assert item["parse_status"] == "needs_model"
    assert "inline ceiling" in item["parse_detail"]["reason"]
    assert queue.dead_letters == []      # terminal, recorded, not retried


async def test_a_document_climbs_the_whole_staircase_from_bytes(
    pool, blobs, settings, embedder, extractor, tenant, principal_for
):
    """The case the spine could not do before: bytes in, retrievable out."""
    from memdog.contracts import RetrieveFilter, RetrieveRequest, WriteOptions
    from memdog.retrieval import retrieve

    actor = await principal_for(tenant.api_key)
    queue = await _pipeline(pool, blobs, settings, embedder, extractor, FakeMultimodal())
    csv_bytes = b"account,amount\nreconciliation,4200\nsettlement,900\n"
    await _write_bytes(
        pool, queue, blobs, settings, actor, tenant.producer_id, "ledger.csv", csv_bytes
    )
    await queue.drain()
    await queue.close()

    found = await retrieve(
        pool, embedder, actor,
        RetrieveRequest(query="reconciliation amount",
                        filter=RetrieveFilter(project_id=tenant.project_id)),
    )
    assert found.results
    assert "reconciliation" in found.results[0].text


async def test_provider_quota_defers_rather_than_failing(
    pool, blobs, settings, embedder, extractor, tenant, principal_for
):
    """A daily quota does not reset inside a backoff window.

    Burning retries against it wastes what little remains and buries the reason
    in a dead letter. The row is left untouched so the reconciler can pick it
    up when quota returns -- the item is not lost, it is waiting.
    """
    from memdog.multimodal import QuotaExhausted
    from memdog.reconcile import reconcile
    from memdog.workers import EmbedWorker

    class OutOfQuota(FakeMultimodal):
        async def interpret(self, payload, *, mime, modality):
            raise QuotaExhausted("429 Too Many Requests")

    actor = await principal_for(tenant.api_key)
    queue = await _pipeline(pool, blobs, settings, embedder, extractor, OutOfQuota())
    written = await _write_bytes(
        pool, queue, blobs, settings, actor, tenant.producer_id, "quota.png", PNG
    )
    await queue.drain()
    data_id = written.results[0].data_id

    item = await get_item(pool, actor, data_id)
    # Untouched: not marked examined, not dead-lettered.
    assert item["state"] == "stored"
    assert item["parse_status"] is None
    assert queue.dead_letters == []

    # And still eligible, so it recovers on its own once quota returns.
    embed = EmbedWorker(pool, embedder, settings, queue=queue)
    swept = await reconcile(pool, queue, embed_generator=embed.generator_version,
                            grace_seconds=0)
    assert swept.parse == 1
    await queue.close()
