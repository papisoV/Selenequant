"""Tests for /api/v1/dashboard aggregator (Phase 4)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.dashboard import (
    _CACHE_KEY,
    _cache,
    router as dashboard_router,
)


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(dashboard_router, prefix="/api/v1")
    return app


def _clear_cache() -> None:
    _cache.pop(_CACHE_KEY, None)


class TestDashboardEndpoint:
    def test_returns_snapshot_with_expected_shape(self) -> None:
        _clear_cache()
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/v1/dashboard")
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] is None
        snap = body["data"]
        assert "ts" in snap
        assert isinstance(snap["markets"], list)
        assert "slow_subcalls" in snap
        assert isinstance(snap["slow_subcalls"], list)

    def test_snapshot_caches_within_ttl(self) -> None:
        _clear_cache()
        app = _make_app()
        client = TestClient(app)
        resp1 = client.get("/api/v1/dashboard")
        ts1 = resp1.json()["data"]["ts"]
        resp2 = client.get("/api/v1/dashboard")
        ts2 = resp2.json()["data"]["ts"]
        # Within TTL — same timestamp returned from cache.
        assert ts1 == ts2

    def test_force_bypasses_cache(self) -> None:
        _clear_cache()
        app = _make_app()
        client = TestClient(app)
        resp1 = client.get("/api/v1/dashboard")
        assert resp1.status_code == 200
        # Force refresh — cache should be invalidated, new snapshot built.
        resp2 = client.get("/api/v1/dashboard?force=true")
        assert resp2.status_code == 200
        assert resp2.json()["data"] is not None

    def test_snapshot_includes_slow_subcalls_field(self) -> None:
        """Even when all sub-calls succeed, slow_subcalls must be present."""
        _clear_cache()
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/v1/dashboard?force=true")
        body = resp.json()["data"]
        assert "slow_subcalls" in body
        assert isinstance(body["slow_subcalls"], list)
        # All 4 sub-call labels are valid slow_subcalls entries.
        valid_labels = {"markets", "paper", "trading", "signals"}
        for label in body["slow_subcalls"]:
            assert label in valid_labels

    def test_snapshot_ts_is_monotonic(self) -> None:
        """Successive fresh snapshots should have non-decreasing ts."""
        _clear_cache()
        app = _make_app()
        client = TestClient(app)
        ts1 = client.get("/api/v1/dashboard?force=true").json()["data"]["ts"]
        ts2 = client.get("/api/v1/dashboard?force=true").json()["data"]["ts"]
        assert ts2 >= ts1
