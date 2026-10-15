"""Tests for /api/v1/onchain/smart-money (Phase 4)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.onchain import router as onchain_router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(onchain_router, prefix="/api/v1")
    return app


class TestSmartMoneyEndpoint:
    def test_returns_expected_shape(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/v1/onchain/smart-money", params={"symbol": "BTC"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] is None
        data = body["data"]
        assert data["symbol"] == "BTC"
        assert "movements" in data
        assert isinstance(data["movements"], list)
        assert "count" in data
        assert data["count"] == len(data["movements"])

    def test_symbol_uppercased(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/v1/onchain/smart-money", params={"symbol": "eth"})
        assert resp.status_code == 200
        assert resp.json()["data"]["symbol"] == "ETH"

    def test_movement_labels_are_valid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/v1/onchain/smart-money", params={"symbol": "BTC"})
        movements = resp.json()["data"]["movements"]
        valid_labels = {
            "smart_money_in",
            "smart_money_out",
            "exchange_transfer",
        }
        for m in movements:
            assert m["label"] in valid_labels
            assert m["direction"] in {"accumulate", "distribute", "transfer"}
            assert 0.0 <= m["confidence"] <= 1.0

    def test_limit_below_min_rejected(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get(
            "/api/v1/onchain/smart-money",
            params={"symbol": "BTC", "limit": 0},
        )
        assert resp.status_code == 422

    def test_limit_above_max_rejected(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get(
            "/api/v1/onchain/smart-money",
            params={"symbol": "BTC", "limit": 500},
        )
        assert resp.status_code == 422

    def test_timestamp_present_in_response(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/v1/onchain/smart-money", params={"symbol": "BTC"})
        data = resp.json()["data"]
        assert "timestamp" in data
        assert isinstance(data["timestamp"], int)
