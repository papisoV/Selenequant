"""Tests for the /api/v1/portfolio/recommend endpoint (M4 exposure)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.portfolio import router as portfolio_router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(portfolio_router, prefix="/api/v1")
    return app


class TestPortfolioRecommendEndpoint:
    def test_basic_recommendation_with_supplied_cov(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/portfolio/recommend",
            json={
                "assets": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
                "market_weights": [0.6, 0.3, 0.1],
                "cov": [
                    [0.04, 0.02, 0.01],
                    [0.02, 0.06, 0.015],
                    [0.01, 0.015, 0.09],
                ],
                "signals": {"BTC/USDT": 0.10, "ETH/USDT": 0.15},
                "objective": "min_variance",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert set(body["assets"]) == {"BTC/USDT", "ETH/USDT", "SOL/USDT"}
        assert len(body["weights"]) == 3
        assert body["success"] is True
        assert body["n_views"] == 2
        assert body["turnover"] == 0.0
        # Weights roughly sum to 1.0 (full investment).
        assert abs(sum(body["weights"]) - 1.0) < 1e-3

    def test_default_market_weights_equal_weight(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/portfolio/recommend",
            json={
                "assets": ["BTC/USDT", "ETH/USDT"],
                "cov": [[0.04, 0.01], [0.01, 0.06]],
                "signals": {},
                "objective": "min_variance",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        # No signals → n_views = 0.
        assert body["n_views"] == 0

    def test_invalid_market_weights_sum(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/portfolio/recommend",
            json={
                "assets": ["BTC/USDT", "ETH/USDT"],
                "market_weights": [0.5, 0.3],  # sums to 0.8
                "cov": [[0.04, 0.0], [0.0, 0.06]],
                "signals": {},
            },
        )
        assert resp.status_code == 400
        assert "sum to 1.0" in resp.json()["detail"]

    def test_invalid_cov_shape(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/portfolio/recommend",
            json={
                "assets": ["BTC/USDT", "ETH/USDT"],
                "market_weights": [0.5, 0.5],
                "cov": [[0.04, 0.0, 0.0], [0.0, 0.06, 0.0]],  # 2x3
                "signals": {},
            },
        )
        assert resp.status_code == 400
        assert "cov must be" in resp.json()["detail"]

    def test_invalid_objective(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/portfolio/recommend",
            json={
                "assets": ["BTC/USDT"],
                "market_weights": [1.0],
                "cov": [[0.04]],
                "signals": {},
                "objective": "bogus_objective",
            },
        )
        assert resp.status_code == 400
        assert "objective must be one of" in resp.json()["detail"]

    def test_non_psd_cov_returns_400(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/portfolio/recommend",
            json={
                "assets": ["BTC/USDT", "ETH/USDT"],
                "market_weights": [0.6, 0.4],
                "cov": [[1.0, 2.0], [2.0, 1.0]],  # indefinite
                "signals": {},
                "objective": "min_variance",
            },
        )
        assert resp.status_code == 400
        assert "PSD" in resp.json()["detail"]

    def test_max_sharpe_with_signals(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/portfolio/recommend",
            json={
                "assets": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
                "market_weights": [0.6, 0.3, 0.1],
                "cov": [
                    [0.04, 0.02, 0.01],
                    [0.02, 0.06, 0.015],
                    [0.01, 0.015, 0.09],
                ],
                "signals": {
                    "BTC/USDT": 0.08,
                    "ETH/USDT": 0.12,
                    "SOL/USDT": 0.20,
                },
                "objective": "max_sharpe",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["objective"] == "max_sharpe"
        assert body["n_views"] == 3
        # All weights non-negative (long_only default).
        assert all(w >= -1e-6 for w in body["weights"])

    def test_weight_cap_enforced(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/portfolio/recommend",
            json={
                "assets": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
                "market_weights": [0.6, 0.3, 0.1],
                "cov": [
                    [0.04, 0.02, 0.01],
                    [0.02, 0.06, 0.015],
                    [0.01, 0.015, 0.09],
                ],
                "signals": {"BTC/USDT": 0.30},
                "objective": "max_sharpe",
                "weight_cap": 0.4,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert all(w <= 0.4 + 1e-6 for w in body["weights"])

    def test_prev_weights_triggers_turnover(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/portfolio/recommend",
            json={
                "assets": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
                "market_weights": [0.6, 0.3, 0.1],
                "cov": [
                    [0.04, 0.02, 0.01],
                    [0.02, 0.06, 0.015],
                    [0.01, 0.015, 0.09],
                ],
                "signals": {},
                "objective": "min_variance",
                "prev_weights": [0.5, 0.4, 0.1],
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        # turnover > 0 because prev_weights differ from optimized weights.
        assert body["turnover"] > 0.0

    def test_empty_assets_rejected(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/portfolio/recommend",
            json={
                "assets": [],
                "signals": {},
            },
        )
        # Pydantic validation error (min_length=1 on assets).
        assert resp.status_code == 422
