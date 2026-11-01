"""Named metrics: counters accumulate, gauges overwrite, histograms append.
Label dicts partition each metric into independent series."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class MetricType(enum.Enum):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"


def _label_key(labels: dict | None) -> tuple:
    if not labels:
        return ()
    return tuple(sorted(labels.items()))


@dataclass
class _Metric:
    type: MetricType
    description: str
    counters: dict = field(default_factory=dict)
    series: dict = field(default_factory=dict)


class MetricsCollector:
    """Registry-first collector: operations on unregistered names are no-ops."""

    def __init__(self) -> None:
        self._metrics: dict[str, _Metric] = {}

    def register(
        self, name: str, metric_type: MetricType, description: str = ""
    ) -> None:
        self._metrics[name] = _Metric(type=metric_type, description=description)

    def increment(
        self, name: str, value: float = 1.0, labels: dict | None = None
    ) -> None:
        metric = self._metrics.get(name)
        if metric is None or metric.type is not MetricType.COUNTER:
            return
        key = _label_key(labels)
        metric.counters[key] = metric.counters.get(key, 0.0) + value

    def set(self, name: str, value: float, labels: dict | None = None) -> None:
        metric = self._metrics.get(name)
        if metric is None or metric.type is not MetricType.GAUGE:
            return
        key = _label_key(labels)
        metric.counters[key] = value

    def observe(self, name: str, value: float, labels: dict | None = None) -> None:
        metric = self._metrics.get(name)
        if metric is None or metric.type is not MetricType.HISTOGRAM:
            return
        key = _label_key(labels)
        metric.series.setdefault(key, []).append(value)

    def get_value(
        self, name: str, labels: dict | None = None
    ) -> list[float] | float | None:
        metric = self._metrics.get(name)
        if metric is None:
            return None
        key = _label_key(labels)
        if metric.type is MetricType.HISTOGRAM:
            return list(metric.series.get(key, []))
        return metric.counters.get(key, 0.0)

    def snapshot(self) -> dict:
        out: dict = {}
        for name, metric in self._metrics.items():
            entry: dict = {"type": metric.type.value, "description": metric.description}
            if metric.type is MetricType.HISTOGRAM:
                entry["values"] = {str(k): list(v) for k, v in metric.series.items()}
            else:
                entry["values"] = {str(k): v for k, v in metric.counters.items()}
            out[name] = entry
        return out
