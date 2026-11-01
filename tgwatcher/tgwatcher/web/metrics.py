"""Prometheus-format metrics exposition for TGWatcher.

Hand-rolled text format (no prometheus_client dependency). Exposes module
globals from ``tgwatcher.web.api`` as gauges/counters so a Prometheus
scraper can monitor runtime state. Designed to be cheap: never hits the DB
unless ``_storage`` is initialized, and swallows all errors so a scrape
cannot 500.
"""
from __future__ import annotations

from typing import Callable


def _metric(name: str, help_text: str, metric_type: str, value: float) -> str:
    """Format a single Prometheus metric sample with HELP and TYPE lines."""
    return (
        f"# HELP {name} {help_text}\n"
        f"# TYPE {name} {metric_type}\n"
        f"{name} {value}\n"
    )


def _safe_int(fn: Callable[[], int]) -> int:
    """Run ``fn`` and return int result, 0 on any exception."""
    try:
        v = fn()
        return int(v) if v is not None else 0
    except Exception:
        return 0


def collect_metrics() -> str:
    """Return Prometheus text-format exposition of TGWatcher runtime state.

    Imports the ``api`` module lazily inside the function to avoid circular
    imports. Every access is wrapped defensively: if any global is None or
    any attribute is missing, the metric returns 0. Never raises.
    """
    # Lazy import avoids circular import (api.py imports nothing from here).
    from tgwatcher.web import api as _api

    def sse_listeners() -> int:
        bus = getattr(_api, "_sse_bus", None)
        if bus is None:
            return 0
        try:
            return int(bus.listener_count)
        except Exception:
            return 0

    def sse_events_buffered() -> int:
        bus = getattr(_api, "_sse_bus", None)
        if bus is None:
            return 0
        try:
            return int(bus.buffered_event_count)
        except Exception:
            return 0

    def sse_event_id_counter() -> int:
        bus = getattr(_api, "_sse_bus", None)
        if bus is None:
            return 0
        try:
            return int(bus.current_event_id)
        except Exception:
            return 0

    def auto_poll_enabled() -> int:
        state = getattr(_api, "_auto_poll_state", {}) or {}
        return sum(1 for s in state.values() if isinstance(s, dict) and s.get("enabled"))

    def auto_poll_total() -> int:
        state = getattr(_api, "_auto_poll_state", {}) or {}
        return len(state)

    def crawl_running() -> int:
        svc = getattr(_api, "_crawl_service", None)
        status = getattr(svc, "status", None)
        if status is None:
            return 0
        try:
            d = status() if callable(status) else status
        except Exception:
            return 0
        return 1 if isinstance(d, dict) and d.get("running") else 0

    def signal_engine_enabled() -> int:
        return 1 if getattr(_api, "_signal_engine", None) is not None else 0

    def listener_running() -> int:
        daemon = getattr(_api, "_listener_daemon", None)
        return 1 if (daemon is not None and daemon.is_running) else 0

    def webhook_configured() -> int:
        disp = getattr(_api, "_webhook_dispatcher", None)
        if disp is None:
            return 0
        # WebhookDispatcher has a public ``enabled`` property; fall back to
        # the private ``_enabled`` attribute if the property is absent.
        enabled = getattr(disp, "enabled", None)
        if enabled is None:
            enabled = getattr(disp, "_enabled", False)
        try:
            val = enabled() if callable(enabled) else enabled
            return 1 if bool(val) else 0
        except Exception:
            return 0

    def messages_total() -> int:
        storage = getattr(_api, "_storage", None)
        if storage is None:
            return 0
        stats = storage.get_stats()
        return int(stats.get("total_messages", 0))

    parts: list[str] = [
        _metric("tgwatcher_up", "1 if the API process is responding, 0 otherwise.", "gauge", 1),
        _metric("tgwatcher_sse_listeners", "Number of active SSE client connections.", "gauge", _safe_int(sse_listeners)),
        _metric("tgwatcher_sse_events_buffered", "Number of events currently held in the SSE ring buffer.", "gauge", _safe_int(sse_events_buffered)),
        _metric("tgwatcher_sse_event_id_counter", "Last-issued SSE event ID (monotonic counter).", "counter", _safe_int(sse_event_id_counter)),
        _metric("tgwatcher_auto_poll_enabled", "Number of groups with auto-poll enabled.", "gauge", _safe_int(auto_poll_enabled)),
        _metric("tgwatcher_auto_poll_total", "Total number of groups tracked by the auto-poll daemon.", "gauge", _safe_int(auto_poll_total)),
        _metric("tgwatcher_crawl_running", "1 if a crawl is currently running, 0 otherwise.", "gauge", _safe_int(crawl_running)),
        _metric("tgwatcher_signal_engine_enabled", "1 if the signal engine is initialized, 0 otherwise.", "gauge", _safe_int(signal_engine_enabled)),
        _metric("tgwatcher_listener_running", "1 if the real-time Telethon listener is running, 0 otherwise.", "gauge", _safe_int(listener_running)),
        _metric("tgwatcher_webhook_configured", "1 if a webhook dispatcher is enabled, 0 otherwise.", "gauge", _safe_int(webhook_configured)),
        _metric("tgwatcher_messages_total", "Total non-deleted messages in storage (counter snapshot).", "counter", _safe_int(messages_total)),
    ]
    return "".join(parts)
