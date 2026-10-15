"""Tests for /api/v1/intelligence/whale-flow (Phase 4)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.intelligence import router as intelligence_router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(intelligence_router, prefix="/api/v1")
    return app


class TestWhaleFlowEndpoint:
    def test_returns_expected_shape(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/v1/intelligence/whale-flow", params={"symbol": "BTC"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] is None
        data = body["data"]
        assert data["symbol"] == "BTC"
        assert "items" in data
        assert isinstance(data["items"], list)
        assert "count" in data
        assert data["count"] == len(data["items"])

    def test_symbol_uppercased(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/v1/intelligence/whale-flow", params={"symbol": "btc"})
        assert resp.status_code == 200
        assert resp.json()["data"]["symbol"] == "BTC"

    def test_limit_caps_item_count(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get(
            "/api/v1/intelligence/whale-flow",
            params={"symbol": "BTC", "limit": 1},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["count"] <= 1
        assert len(data["items"]) <= 1

    def test_item_shape_when_present(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/v1/intelligence/whale-flow", params={"symbol": "ETH"})
        items = resp.json()["data"]["items"]
        for item in items:
            assert "ts" in item
            assert "symbol" in item
            assert "direction" in item
            assert "source" in item
            assert item["source"] in {"onchain", "news", "social"}
            assert "severity" in item
            assert item["severity"] in {"high", "medium", "low"}

    def test_limit_below_min_rejected(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get(
            "/api/v1/intelligence/whale-flow",
            params={"symbol": "BTC", "limit": 0},
        )
        assert resp.status_code == 422
