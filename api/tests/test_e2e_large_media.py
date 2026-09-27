"""A three-hour recording, end to end, from bytes to an answer.

Everything else about this feature can be argued from the code. These two claims
cannot, because both are about what happens in another module entirely:

**"When we create RAGs using this api, it should be in our RAG system."** The
large path returns the same `Interpreted` as the small one, so in principle the
chunker, the embedder and retrieval cannot tell the difference. In principle is
not a test. This one writes an oversized video through the real pipeline and
then *asks a question* -- if the transcript comes back as a citation, the claim
holds.

**"When we default, we do not build knowledge graphs."** Asserting that no
entities exist proves nothing unless something would otherwise have created
them, so the extractor here always returns entities and relations. The test is
that they are dropped on this path and kept when an org asks for them -- a
default that cannot be turned off is not a default, it is a rule wearing one's
clothes.

The provider is `tools/fake_gemini_files.py`; the rest of the pipeline is real.
"""

from __future__ import annotations

import base64
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open_mem.contracts import (Inline, WriteItem, WriteOptions,      # noqa: E402
                              WriteRequest)
from open_mem.extraction import Envelope                              # noqa: E402
from open_mem.gemini_files import GeminiFiles                         # noqa: E402
from open_mem.multimodal import MAX_INLINE_BYTES, GeminiMultimodal    # noqa: E402
from open_mem.queue import InProcessQueue                             # noqa: E402
from open_mem.retrieval import get_item                               # noqa: E402
from open_mem.settings_store import put                               # noqa: E402
from open_mem.contracts import RetrieveFilter, RetrieveRequest        # noqa: E402
from open_mem.retrieval import retrieve                               # noqa: E402
from open_mem.workers import (EmbedWorker, EnrichWorker, EventWorker,  # noqa: E402
                            ParseWorker)
from open_mem.write import EMBED_TOPIC, write_items                   # noqa: E402
from tools import fake_gemini_files                                 # noqa: E402

pytestmark = pytest.mark.asyncio

# Over the inline ceiling, under every other bound, with a container header real
# enough for the sniffer to call it what it is.
def _oversized(header: bytes) -> bytes:
    return header + b"\x00" * (MAX_INLINE_BYTES + 1 - len(header))


MP4 = _oversized(b"\x00\x00\x00\x20ftypisom")
MP3 = _oversized(b"ID3\x03\x00\x00\x00")


def _textless_pdf(pad: int) -> bytes:
    """A real PDF with a real xref and no text layer -- a scan, in other words.

    It has to be genuinely valid: a malformed one is `ParseFailed` and never
    reaches a model at all, so padding a header with nulls would have tested
    the PDF parser's error handling and called it a large-media test. The
    padding is a comment, which is legal anywhere and keeps the offsets
    computable.
    """
    objects = [
        b"1 0 obj\n<</Type/Catalog/Pages 2 0 R>>\nendobj\n",
        b"2 0 obj\n<</Type/Pages/Kids[3 0 R]/Count 1>>\nendobj\n",
        b"3 0 obj\n<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>\nendobj\n",
    ]
    out = b"%PDF-1.4\n%" + b"A" * pad + b"\n"
    offsets = []
    for obj in objects:
        offsets.append(len(out))
        out += obj
    start = len(out)
    out += b"xref\n0 4\n0000000000 65535 f \n"
    for offset in offsets:
        out += ("%010d 00000 n \n" % offset).encode()
    out += (b"trailer\n<</Root 1 0 R/Size 4>>\nstartxref\n"
            + str(start).encode() + b"\n%%EOF\n")
    return out


PDF = _textless_pdf(MAX_INLINE_BYTES)


class AlwaysNamesThings:
    """An extractor that always finds entities and relations.

    Without this the graph assertions would pass on an empty corpus and prove
    nothing at all.
    """

    model_id = "fake-extractor-v1"

    async def extract(self, text, *, data_type, prompt=None, template=None):
        return Envelope(
            title="A long recording",
            summary="Two speakers discuss a migration and a rollback plan.",
            entities=[
                {"name": "Acme Corp", "type": "org"},
                {"name": "Dana Whitfield", "type": "person"},
            ],
            relations=[
                {"subject": "Dana Whitfield", "predicate": "works_for",
                 "object": "Acme Corp"},
            ],
        )


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
    monkeypatch.setattr("open_mem.gemini_files.POLL_SECONDS", 0.01)


def _engine(base: str) -> GeminiMultimodal:
    engine = GeminiMultimodal(
        "test-key", "gemini-3.7-flash", large_media=True,
        files=GeminiFiles("test-key", base=base, timeout=10.0),
    )
    engine._base = f"{base}/v1beta"
    return engine


async def _run(pool, blobs, settings, embedder, actor, producer_id, name, payload,
               *, engine, extractor):
    queue = InProcessQueue()
    parse = ParseWorker(pool, blobs, queue=queue, multimodal=engine)
    parse.register(queue)
    embed = EmbedWorker(pool, embedder, settings, queue=queue)
    await embed.ensure_generator()
    embed.register(queue, EMBED_TOPIC)
    enrich = EnrichWorker(pool, extractor, settings)
    await enrich.ensure_generator()
    enrich.register(queue)
    EventWorker(pool, queue, parse_worker=parse, embed_worker=embed,
                enrich_worker=enrich).register(queue)

    written = await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=producer_id, items=[
            WriteItem(external_id=name,
                      content=Inline(bytes_b64=base64.b64encode(payload).decode())),
        ], options=WriteOptions(enrich=True)),
    )
    await queue.drain()
    await queue.close()
    return written.results[0].data_id


# --------------------------------------------------------------- the whole way

@pytest.mark.parametrize("name,payload", [
    ("keynote.mp4", MP4),
    ("interview.mp3", MP3),
    ("scanned-contract.pdf", PDF),
])
async def test_large_media_climbs_the_whole_staircase(
    pool, blobs, settings, embedder, tenant, principal_for, files_api, name, payload
):
    """All three modalities the request names, through the real pipeline."""
    actor = await principal_for(tenant.api_key)
    data_id = await _run(pool, blobs, settings, embedder, actor,
                         tenant.producer_id, name, payload,
                         engine=_engine(files_api),
                         extractor=AlwaysNamesThings())

    item = await get_item(pool, actor, data_id)
    assert item["state"] == "enriched", item.get("parse_detail")
    # It really did go the long way round.
    assert item["parse_detail"]["structure"]["via"] == "files_api"
    assert fake_gemini_files.COUNTS["upload"] >= 1
    assert fake_gemini_files.FILES == {}, "the uploaded file was not cleaned up"


async def test_the_transcript_is_retrievable_like_anything_else(
    pool, blobs, settings, embedder, tenant, principal_for, files_api
):
    """The RAG claim, asked rather than assumed."""
    actor = await principal_for(tenant.api_key)
    data_id = await _run(pool, blobs, settings, embedder, actor,
                         tenant.producer_id, "keynote.mp4", MP4,
                         engine=_engine(files_api),
                         extractor=AlwaysNamesThings())

    chunks = await pool.fetchval(
        "SELECT count(*) FROM chunks WHERE data_id = $1", data_id)
    assert chunks > 0, "nothing was chunked, so nothing is searchable"
    embeddings = await pool.fetchval(
        "SELECT count(*) FROM embeddings e JOIN chunks c USING (chunk_id) "
        "WHERE c.data_id = $1", data_id)
    assert embeddings == chunks, "chunks exist that were never embedded"

    found = await retrieve(
        pool, embedder, actor,
        RetrieveRequest(query="the migration and the rollback plan",
                        filter=RetrieveFilter(project_id=tenant.project_id)),
    )
    assert data_id in {hit.data_id for hit in found.results}, (
        "the transcript was stored and embedded and retrieval cannot find it"
    )


# ------------------------------------------------------------------- the graph

async def test_no_knowledge_graph_is_built_by_default(
    pool, blobs, settings, embedder, tenant, principal_for, files_api
):
    """The extractor named two entities and a relation. None of them landed."""
    actor = await principal_for(tenant.api_key)
    data_id = await _run(pool, blobs, settings, embedder, actor,
                         tenant.producer_id, "keynote.mp4", MP4,
                         engine=_engine(files_api),
                         extractor=AlwaysNamesThings())

    mentions = await pool.fetchval(
        "SELECT count(*) FROM entity_mentions WHERE data_id = $1", data_id)
    edges = await pool.fetchval(
        "SELECT count(*) FROM entity_edges WHERE source_data_id = $1", data_id)
    assert mentions == 0, "a large-media transcript put entities in the graph"
    assert edges == 0, "a large-media transcript put edges in the graph"

    # The summary is still there. Skipping enrichment outright would have lost
    # it, and would have left the item somewhere the staircase calls stuck.
    item = await get_item(pool, actor, data_id)
    assert item["state"] == "enriched"
    reason = await pool.fetchval(
        "SELECT a.fields->>'graph_skipped' FROM artifacts a "
        "JOIN artifact_sources s USING (artifact_id) WHERE s.data_id = $1",
        data_id)
    assert reason and "large media" in reason, (
        "the graph was skipped and the artifact does not say so"
    )


async def test_an_org_that_wants_the_graph_can_have_it(
    pool, blobs, settings, embedder, tenant, principal_for, files_api
):
    """A default that cannot be turned off is not a default."""
    actor = await principal_for(tenant.api_key)
    await put(pool, "large_media_graph", True, scope="org",
              scope_id=tenant.org_id, set_by=actor.user_id or "test")

    data_id = await _run(pool, blobs, settings, embedder, actor,
                         tenant.producer_id, "keynote.mp4", MP4,
                         engine=_engine(files_api),
                         extractor=AlwaysNamesThings())

    mentions = await pool.fetchval(
        "SELECT count(*) FROM entity_mentions WHERE data_id = $1", data_id)
    assert mentions > 0, "the org asked for a graph and did not get one"


async def test_an_ordinary_item_still_builds_its_graph(
    pool, blobs, settings, embedder, tenant, principal_for, files_api
):
    """The scope of the rule, asserted from the other side.

    Nothing about this change may touch an item that did not come through the
    upload path -- which is most of them.
    """
    actor = await principal_for(tenant.api_key)
    data_id = await _run(pool, blobs, settings, embedder, actor,
                         tenant.producer_id, "note.txt",
                         b"Dana Whitfield works at Acme Corp.",
                         engine=_engine(files_api),
                         extractor=AlwaysNamesThings())

    mentions = await pool.fetchval(
        "SELECT count(*) FROM entity_mentions WHERE data_id = $1", data_id)
    assert mentions > 0, "an ordinary text item lost its graph"
    assert fake_gemini_files.COUNTS == {}, "a text file went through file upload"
