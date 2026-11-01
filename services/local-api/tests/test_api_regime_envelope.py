"""Tests for /api/v1/regime response envelope contract.

The desktop `useApi` fetcher only unwraps when the body carries BOTH `data`
and `meta`. regime.py previously returned a bare ``{"data": ...}`` (no
`meta`), so the fetcher handed the whole envelope back and BOTH consumers
(watch/regime page, trading/market-regime-indicator) read undefined fields.
These tests pin the envelope, not just the payload keys.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.regime import router as regime_router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(regime_router, prefix="/api/v1")
    return app


def _client() -> TestClient:
    return TestClient(_make_app())


def test_get_regime_returns_full_envelope() -> None:
    resp = _client().get("/api/v1/regime?symbol=BTC/USDT")
    assert resp.status_code == 200
    body = resp.json()
    # envelope: data + meta 都在，fetcher 才会解包
    assert "data" in body and "meta" in body
    assert body["error"] is None
    data = body["data"]
    # payload 键与前端 RegimeData / MarketRegimeData 对齐
    assert data["regime"] in {"bull", "bear", "sideways", "transition"}
    assert "annual_return" in data
    assert "volatility" in data
    assert "timestamp" in data


def test_compute_regime_returns_full_envelope() -> None:
    prices = [100.0 + i for i in range(60)]
    resp = _client().post(
        "/api/v1/regime/compute", params=[("prices", str(p)) for p in prices]
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body and "meta" in body
    assert body["error"] is None
    assert "regime" in body["data"]
