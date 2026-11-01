"""Diagnostics locks for selene_space_core.diagnostics.metrics — counter /
gauge / histogram semantics, label dimensions, snapshot shape, no-op safety."""

from selene_space_core.diagnostics.metrics import MetricsCollector, MetricType


def test_counter_accumulates_increments():
    collector = MetricsCollector()
    collector.register("ticks.seen", MetricType.COUNTER, "Tick count")
    collector.increment("ticks.seen")
    collector.increment("ticks.seen", 5)
    assert collector.get_value("ticks.seen") == 6.0


def test_gauge_holds_latest_value():
    collector = MetricsCollector()
    collector.register("orders.live", MetricType.GAUGE)
    collector.set("orders.live", 17)
    assert collector.get_value("orders.live") == 17


def test_histogram_keeps_observation_series():
    collector = MetricsCollector()
    collector.register("llm.latency", MetricType.HISTOGRAM)
    for sample in (120.5, 340.25):
        collector.observe("llm.latency", sample)
    assert collector.get_value("llm.latency") == [120.5, 340.25]


def test_label_dimensions_track_separately():
    collector = MetricsCollector()
    collector.register("ws.frames", MetricType.COUNTER)
    collector.increment("ws.frames", labels={"venue": "binance"})
    collector.increment("ws.frames", labels={"venue": "okx"})
    collector.increment("ws.frames", labels={"venue": "binance"}, value=2)
    pair = (
        collector.get_value("ws.frames", labels={"venue": "binance"}),
        collector.get_value("ws.frames", labels={"venue": "okx"}),
    )
    assert pair == (3, 1)


def test_snapshot_exposes_type_metadata():
    collector = MetricsCollector()
    collector.register("m.alpha", MetricType.COUNTER, "desc")
    collector.increment("m.alpha")
    snap = collector.snapshot()
    checks = ("m.alpha" in snap, snap["m.alpha"]["type"] == "counter")
    assert all(checks)


def test_touching_unknown_metric_is_harmless():
    collector = MetricsCollector()
    collector.increment("ghost.metric")  # Should not raise
    assert collector.get_value("ghost.metric") is None
