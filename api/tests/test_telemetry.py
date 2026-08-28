"""Telemetry, and the one thing that is genuinely awkward to add later.

A span that ends when the request returns says nothing about the work that
request queued. The queue hop has to carry the trace context or the interesting
question -- how long from commit to searchable -- has no single trace that
answers it.
"""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from memdog.contracts import Inline, WriteItem, WriteRequest
from memdog.telemetry import continue_trace, inject_context, setup, span
from memdog.write import write_items

pytestmark = pytest.mark.asyncio


@pytest.fixture
def spans():
    setup()
    exporter = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter
    exporter.clear()


async def test_trace_context_survives_the_queue_hop(spans):
    """The producer's trace id must be the worker's trace id."""
    headers: dict[str, str] = {}
    with span("write") as producer_span:
        produced = producer_span.get_span_context().trace_id
        inject_context(headers)

    assert "traceparent" in headers

    with continue_trace("embed", headers) as consumer_span:
        consumed = consumer_span.get_span_context().trace_id

    # One trace across two processes, not two unrelated ones.
    assert consumed == produced


async def test_a_worker_with_no_context_still_traces(spans):
    """A message published before instrumentation existed, or by a producer
    that does not propagate, must not crash the worker."""
    with continue_trace("embed", {}) as orphan:
        assert orphan.get_span_context().trace_id != 0


async def test_a_failed_span_records_the_exception(spans):
    """A failed span with no error attached says something was slow, when what
    happened is that it broke."""
    with pytest.raises(ValueError):
        with span("parse", data_id="data_x"):
            raise ValueError("unreadable pdf")

    finished = spans.get_finished_spans()
    parse = next(s for s in finished if s.name == "parse")
    assert parse.status.status_code.name == "ERROR"
    assert any(e.name == "exception" for e in parse.events)


async def test_the_write_path_is_traced_end_to_end(
    pool, queue, blobs, settings, tenant, principal_for, spans
):
    actor = await principal_for(tenant.api_key)
    await write_items(
        pool, queue, blobs, settings, actor,
        WriteRequest(producer_id=tenant.producer_id, items=[
            WriteItem(external_id="traced-1", content=Inline(text="Something to index.")),
        ]),
    )
    await queue.drain()

    names = {s.name for s in spans.get_finished_spans()}
    assert {"write", "embed", "enrich"} <= names

    # And they are all the same trace -- which is the whole point.
    by_name = {s.name: s for s in spans.get_finished_spans()}
    assert by_name["embed"].context.trace_id == by_name["write"].context.trace_id
    assert by_name["enrich"].context.trace_id == by_name["write"].context.trace_id
