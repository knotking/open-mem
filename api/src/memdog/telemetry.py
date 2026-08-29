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

# Labels that must never reach a metric. Each one is unbounded, and a metrics
# store is a time series per distinct label combination -- so a single
# `user_id` label does not add a dimension, it multiplies the series count by
# the number of users. Getting this wrong takes down the metrics store, which
# then takes down the ability to see anything at all, including that it is
# down.
#
# They are all still perfectly fine on spans, which is where you go when you
# have a specific id in hand and want to know what happened to it. The split
# is deliberate: metrics answer "is this healthy", traces answer "what
# happened to this one".
UNBOUNDED_LABELS = frozenset({
    "user_id", "data_id", "case_id", "run_id", "query_id", "chunk_id",
    "external_id", "delivery_id", "memory_id", "host", "url", "project_id",
})


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

    # Cloud Trace and Cloud Monitoring speak their own protocols rather than
    # OTLP, so on GCP the Google exporters are used directly instead of
    # standing up a collector to translate. Selected by env, so the same image
    # runs locally with nothing configured and exports in Cloud Run.
    gcp_project = os.environ.get("OTEL_GCP_PROJECT")

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if gcp_project:
        try:
            from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

            provider.add_span_processor(
                BatchSpanProcessor(CloudTraceSpanExporter(project_id=gcp_project))
            )
        except Exception as exc:  # noqa: BLE001
            # Telemetry must never be the reason the service fails to start.
            # A process that cannot export traces is degraded; one that will
            # not boot is an outage.
            log.warning("Cloud Trace exporter unavailable: %s", exc)
    elif endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    elif os.environ.get("OTEL_CONSOLE") == "true":
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    # With neither configured the provider records spans and drops them, which
    # is the correct default: instrumentation should cost nothing until someone
    # is listening.

    trace.set_tracer_provider(provider)
    readers = []
    if gcp_project:
        try:
            from opentelemetry.exporter.cloud_monitoring import (
                CloudMonitoringMetricsExporter,
            )

            readers.append(PeriodicExportingMetricReader(
                CloudMonitoringMetricsExporter(project_id=gcp_project),
                # Cloud Monitoring rejects points written more often than once
                # a minute for the same series, so exporting faster does not
                # produce finer data -- it produces errors.
                export_interval_millis=60_000,
            ))
        except Exception as exc:  # noqa: BLE001
            log.warning("Cloud Monitoring exporter unavailable: %s", exc)
    elif endpoint:
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

    # `resolved_by` is the label that earns this counter: an entity layer that
    # resolves everything by fuzzy name match and nothing by identifier is
    # merging people, and it looks identical to one that is working until
    # somebody breaks the two apart.
    _metrics["entity_mentions"] = meter.create_counter(
        "memdog.entity.mentions",
        description="Entity mentions resolved, by type and by what resolved them",
    )

    # ------------------------------------------------------------------ cost
    #
    # The meter in `usage.py` is the durable record and these are the live view
    # of it. Both exist because they answer different questions: a counter can
    # be dropped under load, which is fine for "is spend climbing" and
    # disqualifying for "what does this tenant owe".
    _metrics["usage_credits"] = meter.create_counter(
        "memdog.usage.credits",
        description="Cost-weighted credits consumed, by purpose, engine and status",
    )
    _metrics["usage_crossed_to_paid"] = meter.create_counter(
        "memdog.usage.crossed_to_paid",
        description="Fallbacks that moved a call from a free engine to a paid one",
    )
    # A model call with nobody to bill. Its own counter because the alternative
    # is spend that simply does not appear anywhere -- which looks identical to
    # spend that did not happen.
    _metrics["usage_unattributed"] = meter.create_counter(
        "memdog.usage.unattributed",
        description="Inference calls made with no attribution to charge",
    )
    _metrics["usage_write_failures"] = meter.create_counter(
        "memdog.usage.write_failures",
        description="Usage rows the meter could not persist",
    )
    # Enrichment withheld by a sensitivity policy. Not a failure and not a
    # success: an operator who does not know this is firing sees clinical
    # records that never get summaries and no reason anywhere.
    _metrics["enrich_refused"] = meter.create_counter(
        "memdog.enrich.refused",
        description="Records not enriched because no permitted engine was available",
    )

    # --------------------------------------------------------------- inbound
    #
    # A disabled webhook answers 200 and drops the payload, so an error rate
    # here is correctly zero while data is silently going nowhere. That is why
    # dropped is its own counter rather than a status label on a failure
    # metric: the thing you need to alert on does not look like an error.
    _metrics["ingest_dropped"] = meter.create_counter(
        "memdog.ingest.dropped",
        description="Payloads accepted and deliberately not stored, by reason",
    )
    _metrics["inbound_deliveries"] = meter.create_counter(
        "memdog.inbound.deliveries",
        description="Webhook deliveries, by provider and outcome",
    )
    _metrics["inbound_rejected"] = meter.create_counter(
        "memdog.inbound.rejected",
        description="Deliveries refused, by reason: auth, signature, body_size, unknown",
    )

    # ------------------------------------------------------------- inference
    #
    # Running permanently on a fallback looks exactly like running normally
    # unless something says so: the answers keep arriving, they are just worse
    # and cheaper than the ones being paid for.
    _metrics["inference_fallback_depth"] = meter.create_histogram(
        "memdog.inference.fallback_depth",
        description="How far down the chain the engine that answered was; 0 is the primary",
    )
    _metrics["inference_attempts"] = meter.create_counter(
        "memdog.inference.attempts",
        description="Engine attempts by outcome: served, unavailable, rejected, skipped",
    )

    # ----------------------------------------------------------------- crawl
    _metrics["crawl_discovered"] = meter.create_counter(
        "memdog.crawl.discovered",
        description="Items discovered, per crawler. Trending to zero is the "
                    "crawler equivalent of a dead connection",
    )
    _metrics["crawl_emitted"] = meter.create_counter(
        "memdog.crawl.emitted", description="Items written, per crawler"
    )
    _metrics["crawl_dedupe_hits"] = meter.create_counter(
        "memdog.crawl.dedupe_hits",
        description="Items skipped as unchanged -- the work that was avoided",
    )
    _metrics["crawl_runs"] = meter.create_counter(
        "memdog.crawl.runs", description="Runs, by terminal status"
    )
    _metrics["crawl_robots_denied"] = meter.create_counter(
        "memdog.crawl.robots_denied", description="URLs robots.txt disallowed"
    )
    _metrics["crawl_duration"] = meter.create_histogram(
        "memdog.crawl.duration", unit="s", description="Wall clock per run"
    )
    # A ratio, not a duration: above 1.0 means a run takes longer than the
    # interval it is scheduled on, so the next tick always overlaps and the
    # crawler falls permanently behind. That is invisible in the duration alone
    # because whether it is too slow depends on the schedule.
    _metrics["crawl_duration_vs_interval"] = meter.create_histogram(
        "memdog.crawl.duration_vs_interval",
        description="Run duration over its schedule interval; above 1.0 overlaps forever",
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
    """Emit one measurement, with the cardinality rule enforced here.

    Dropping the label rather than refusing the metric is the right failure:
    losing a dimension degrades a dashboard, while losing the measurement
    hides the outage it was there to show.
    """
    instrument = _metrics.get(metric)
    if instrument is None:
        return
    labels = {
        k: v for k, v in attributes.items()
        if v is not None and k not in UNBOUNDED_LABELS
    }
    if hasattr(instrument, "record"):
        instrument.record(value, labels)       # histogram
    else:
        instrument.add(value, labels)          # counter
