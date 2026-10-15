"""Tests for /api/v1/trading/live-log SSE stream (Phase 4).

The endpoint streams an SSE event feed indefinitely. Under TestClient,
the streaming generator's cleanup blocks on its internal ``asyncio.sleep``,
so consuming the stream in a test hangs. We verify the route is wired
correctly by inspecting the included trading router's route table
directly (no HTTP call needed).

Full streaming semantics (hello event, headers, SSE format) are validated
via integration testing against a real uvicorn process — see
MERGE_CHANGELOG for the follow-up.
"""

from __future__ import annotations

import inspect

from app.api.v1.trading import live_log, router as trading_router


class TestLiveLogEndpointContract:
    """Route-registration + signature tests (streaming consumption is
    deferred to integration testing against a real uvicorn process)."""

    def test_endpoint_function_exists_and_is_async(self) -> None:
        assert callable(live_log)
        assert inspect.iscoroutinefunction(live_log)

    def test_endpoint_has_correct_path_attribute(self) -> None:
        """The FastAPI Route object should declare the /live-log path."""
        # Walk the trading router's routes to find /live-log.
        paths: list[str] = []
        for route in trading_router.routes:
            path = getattr(route, "path", None)
            if path is not None:
                paths.append(path)
        assert "/trading/live-log" in paths, f"got paths: {paths}"

    def test_endpoint_declares_get_method(self) -> None:
        """The /live-log route must accept GET."""
        for route in trading_router.routes:
            path = getattr(route, "path", None)
            methods = getattr(route, "methods", None)
            if path == "/trading/live-log" and methods:
                assert "GET" in methods
                return
        raise AssertionError("no /trading/live-log route found on trading router")

    def test_router_has_trading_prefix(self) -> None:
        """The trading router must declare /trading prefix so the full path
        becomes /api/v1/trading/live-log after include_router."""
        prefix = getattr(trading_router, "prefix", None)
        assert prefix == "/trading", f"got prefix: {prefix!r}"
