"""Tests for the Prometheus metrics exposition endpoint.

Covers:
- collect_metrics() emits all expected metric names
- Output contains ``# HELP`` and ``# TYPE`` lines (Prometheus spec)
- Calling with all module globals None does not raise — returns 0 values
"""
from __future__ import annotations


from tgwatcher.web import api as api_mod
from tgwatcher.web.metrics import collect_metrics


EXPECTED_METRIC_NAMES = [
    "tgwatcher_up",
    "tgwatcher_sse_listeners",
    "tgwatcher_sse_events_buffered",
    "tgwatcher_sse_event_id_counter",
    "tgwatcher_auto_poll_enabled",
    "tgwatcher_auto_poll_total",
    "tgwatcher_crawl_running",
    "tgwatcher_signal_engine_enabled",
    "tgwatcher_listener_running",
    "tgwatcher_webhook_configured",
    "tgwatcher_messages_total",
]


def test_collect_metrics_contains_all_expected_metric_names():
    out = collect_metrics()
    assert isinstance(out, str)
    for name in EXPECTED_METRIC_NAMES:
        # Each metric appears in a sample line (``name <value>``).
        assert f"\n{name} " in out, f"metric {name!r} missing from output"


def test_output_has_help_and_type_lines():
    out = collect_metrics()
    # Prometheus exposition spec requires HELP then TYPE then sample(s).
    assert "# HELP " in out
    assert "# TYPE " in out
    # Each expected metric must carry both HELP and TYPE lines.
    for name in EXPECTED_METRIC_NAMES:
        assert f"# HELP {name} " in out, f"HELP line for {name!r} missing"
        assert f"# TYPE {name} " in out, f"TYPE line for {name!r} missing"
    # Sanity: types must be gauge or counter only.
    for line in out.splitlines():
        if line.startswith("# TYPE "):
            # Format: ``# TYPE <name> <type>``
            parts = line.split(" ")
            assert len(parts) == 4, f"bad TYPE line: {line!r}"
            t = parts[3]
            assert t in {"gauge", "counter"}, f"unexpected type: {t!r}"


def test_collect_metrics_with_uninitialized_globals_returns_zeros():
    """If all api globals are None/empty, collect_metrics must not raise."""
    orig = {
        "_storage": api_mod._storage,
        "_crawl_service": api_mod._crawl_service,
        "_signal_engine": api_mod._signal_engine,
        "_webhook_dispatcher": api_mod._webhook_dispatcher,
        "_listener_daemon": api_mod._listener_daemon,
        "_auto_poll_state": getattr(api_mod, "_auto_poll_state", {}),
        "_sse_bus": api_mod._sse_bus,
    }
    from tgwatcher.web.sse_bus import SSEBus
    from tgwatcher.web.listener_daemon import ListenerDaemon
    try:
        api_mod._storage = None
        api_mod._crawl_service = None
        api_mod._signal_engine = None
        api_mod._webhook_dispatcher = None
        api_mod._listener_daemon = ListenerDaemon()
        api_mod._auto_poll_state = {}
        # Fresh SSE bus with no events/listeners
        api_mod._sse_bus = SSEBus()

        out = collect_metrics()
        assert isinstance(out, str)
        # tgwatcher_up is always 1 (endpoint responding = alive).
        assert "\ntgwatcher_up 1\n" in out
        # Everything else reads as 0 with no globals populated.
        for name in EXPECTED_METRIC_NAMES:
            if name == "tgwatcher_up":
                continue
            assert f"\n{name} 0\n" in out, f"expected {name} to be 0 with no globals"
    finally:
        for k, v in orig.items():
            setattr(api_mod, k, v)
