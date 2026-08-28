"""Telemetry, and the one way it can cause an outage.

Instrumentation is supposed to be free. The way it stops being free is
cardinality: a metrics store keeps one time series per distinct combination of
labels, so a single unbounded label does not add a dimension -- it multiplies
the series count by the number of distinct values. Getting that wrong takes
down the metrics store, which then takes down the ability to see anything,
including that it is down.
"""

from __future__ import annotations

from memdog import telemetry


class Recorder:
    """Stands in for an OTel instrument, capturing what it was handed."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def add(self, value, labels):
        self.calls.append((value, labels))


def test_an_unbounded_label_never_reaches_a_metric(monkeypatch):
    instrument = Recorder()
    monkeypatch.setitem(telemetry._metrics, "probe", instrument)

    telemetry.record(
        "probe", 1,
        provider="slack",          # bounded -- kept
        org_id="org_1",            # bounded at target scale -- kept
        user_id="usr_1",           # unbounded -- dropped
        data_id="data_1",          # unbounded -- dropped
        run_id="crun_1",           # unbounded -- dropped
        host="docs.example.com",   # unbounded -- dropped
    )

    value, labels = instrument.calls[0]
    assert value == 1
    assert labels == {"provider": "slack", "org_id": "org_1"}


def test_the_measurement_survives_even_when_every_label_is_dropped(monkeypatch):
    """Dropping the label rather than refusing the metric is the right
    failure. Losing a dimension degrades a dashboard; losing the measurement
    hides the outage it existed to show."""
    instrument = Recorder()
    monkeypatch.setitem(telemetry._metrics, "probe", instrument)

    telemetry.record("probe", 5, user_id="usr_1", data_id="data_1")
    assert instrument.calls == [(5, {})]


def test_recording_an_unregistered_metric_is_a_no_op():
    """Instrumentation must cost nothing when nothing is listening -- which is
    the default, and must not be the case that raises."""
    telemetry.record("no_such_metric", 1, provider="slack")


def test_the_signals_the_alerts_are_built_on_are_registered():
    """Each of these maps to a named alert in the telemetry design. A metric
    that is silently absent produces an alert that silently never fires, which
    is worse than having neither."""
    telemetry.setup()
    for signal in ("crawl_discovered", "crawl_runs", "crawl_duration_vs_interval",
                   "ingest_dropped", "inbound_deliveries", "inbound_rejected"):
        assert signal in telemetry._metrics, signal


def test_crawler_identity_is_kept_because_the_alert_needs_it():
    """`crawl.discovered` at zero is only meaningful against a single
    crawler's own baseline -- an aggregate across every crawler hides exactly
    the one that died. So this label is bounded by configuration count and is
    deliberately not on the unbounded list."""
    assert "crawler_id" not in telemetry.UNBOUNDED_LABELS
    assert "strategy" not in telemetry.UNBOUNDED_LABELS
