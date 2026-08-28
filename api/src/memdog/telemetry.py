"""Telemetry -- and why it is not the audit log.

`audit_events` and `access_log` are **evidence**: written in the same
transaction as the change they describe, never sampled, never expired, and they
must outlive the data they refer to. Traces are the opposite -- sampled, lossy
and short-lived. Merging them would make "who read this?" unanswerable in
exactly the cases it matters.

So this is observability only, and it exists to answer one question the
component metrics cannot: **where did the time go between a write and a
searchable item?**

The queue hop is the part that is genuinely awkward to add later. A span that
ends when the request returns tells you nothing about the work that request
queued, so the trace context travels in the message headers and the worker
continues the trace rather than starting a new one.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager

from opentelemetry import metrics, trace
from opentelemetry.propagate import extract, inject
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import Status, StatusCode

log = logging.getLogger(__name__)

_tracer: trace.Tracer | None = None
_metrics: dict[str, object] = {}


def setup(service_name: str = "memdog-api") -> None:
    """Configure once at startup. Safe to call twice."""
    global _tracer
    if _tracer is not None:
        return

    resource = Resource.create({
        "service.name": service_name,
        "service.version": os.environ.get("IMAGE_TAG", "dev"),
    })
    provider = TracerProvider(resource=resource)

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    elif os.environ.get("OTEL_CONSOLE") == "true":
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    # With neither configured the provider records spans and drops them, which
    # is the correct default: instrumentation should cost nothing until someone
    # is listening.

    trace.set_tracer_provider(provider)
    readers = []
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

        readers.append(PeriodicExportingMetricReader(OTLPMetricExporter()))
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=readers))

    _tracer = trace.get_tracer(service_name)
    meter = metrics.get_meter(service_name)

    # The two SLIs component metrics cannot show. Both are measured from
    # `ingested_at`, not from when a worker happened to pick the job up --
    # queue latency is part of the number a user experiences.
    _metrics["ingest_to_searchable"] = meter.create_histogram(
        "memdog.ingest_to_searchable", unit="s",
        description="Write commit to retrievable",
    )
    _metrics["ingest_to_enriched"] = meter.create_histogram(
        "memdog.ingest_to_enriched", unit="s",
        description="Write commit to enriched",
    )
    _metrics["items_written"] = meter.create_counter(
        "memdog.items_written", description="Items accepted by the write endpoint"
    )
    _metrics["parse_failures"] = meter.create_counter(
        "memdog.parse_failures", description="Items that could not be turned into text"
    )
    _metrics["model_tokens"] = meter.create_counter(
        "memdog.model_tokens", description="Tokens spent, by model and purpose"
    )


def tracer() -> trace.Tracer:
    if _tracer is None:
        setup()
    return trace.get_tracer("memdog-api")


@contextmanager
def span(name: str, **attributes):
    """A span that records the exception before re-raising it.

    A failed span with no error attached is a span that says something was
    slow, when what happened is that it broke.
    """
    with tracer().start_as_current_span(name) as current:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(key, value)
        try:
            yield current
        except Exception as exc:
            current.record_exception(exc)
            current.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


def inject_context(headers: dict[str, str]) -> dict[str, str]:
    """Carry the trace across the queue hop.

    Without this the write and the enrichment it queued are two unrelated
    traces, and the interesting question -- how long from commit to searchable
    -- has no single trace that answers it.
    """
    inject(headers)
    return headers


@contextmanager
def continue_trace(name: str, headers: dict[str, str], **attributes):
    """The worker side: resume the producer's trace instead of starting a new one."""
    context = extract(headers or {})
    with tracer().start_as_current_span(name, context=context) as current:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(key, value)
        try:
            yield current
        except Exception as exc:
            current.record_exception(exc)
            current.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


def record(metric: str, value: float | int, **attributes) -> None:
    instrument = _metrics.get(metric)
    if instrument is None:
        return
    labels = {k: v for k, v in attributes.items() if v is not None}
    if hasattr(instrument, "record"):
        instrument.record(value, labels)       # histogram
    else:
        instrument.add(value, labels)          # counter
