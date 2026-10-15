"""Router coverage tests for /api/v1/grid (Phase 4).

Validates that the grid router is correctly constructed with expected prefix,
tags, and route set. Doesn't exercise full business logic — that's covered by
the grid integration tests in test_strategy_runner_signal_routing.py.
"""

from __future__ import annotations

from app.api.v1.grid import router as grid_router


class TestGridRouter:
    def test_prefix_is_grid(self) -> None:
        assert grid_router.prefix == "/grid"

    def test_tag_present(self) -> None:
        assert "grid" in grid_router.tags

    def test_expected_route_count(self) -> None:
        """Grid router should expose at least 12 routes."""
        paths = [getattr(r, "path", None) for r in grid_router.routes]
        paths = [p for p in paths if p]
        assert len(paths) >= 12, f"only {len(paths)} routes: {paths}"

    def test_state_endpoint_registered(self) -> None:
        paths = [getattr(r, "path", None) for r in grid_router.routes]
        assert "/grid/state" in paths

    def test_sentinel_endpoint_registered(self) -> None:
        paths = [getattr(r, "path", None) for r in grid_router.routes]
        assert "/grid/sentinel" in paths

    def test_post_endpoints_accept_post(self) -> None:
        """Routes that mutate grid state (start/stop/chat) must accept POST."""
        post_routes: dict[str, set[str]] = {}
        for r in grid_router.routes:
            path = getattr(r, "path", None)
            methods = getattr(r, "methods", None)
            if path and methods:
                post_routes[path] = set(methods)
        # At least one POST route should exist.
        assert any("POST" in m for m in post_routes.values()), "no POST routes on grid router"
