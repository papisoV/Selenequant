"""Tests for /api/v1/commodities-fx/* endpoints.

Endpoints call data_fetcher functions lazily, so monkeypatching
``selene_space_commodities_fx.data_fetcher.<fn>`` is picked up at call time.
Empty upstream -> 404 NOT_FOUND, data-source failure -> 502 EXCHANGE_ERROR,
akshare missing -> 404 with explicit hint.
"""

from __future__ import annotations

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.error_handler import install_error_handlers

pytest.importorskip("selene_space_commodities_fx")

from app.api.v1.commodities_fx import router as commodities_fx_router  # noqa: E402
from selene_space_commodities_fx import data_fetcher as df_mod  # noqa: E402


def _make_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(commodities_fx_router, prefix="/api/v1")
    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_make_app())


def _kline_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": ["2026-08-15 00:40:00", "2026-08-17 00:00:00"],
            "open": [953.1, 953.6], "high": [953.2, 953.7], "low": [953.1, 953.4],
            "close": [953.2, 953.7], "volume": [10, 41], "hold": [198542, 199050],
            "symbol": ["AU0", "AU0"],
        }
    )


def test_kline_populated_returns_200(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(df_mod, "get_futures_kline", lambda *a, **k: _kline_df())

    resp = client.get("/api/v1/commodities-fx/AU0/kline", params={"period": "1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    assert len(body["data"]) == 2
    assert body["data"][0]["timestamp"] == "2026-08-15 00:40:00"


def test_kline_empty_returns_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(df_mod, "get_futures_kline", lambda *a, **k: pd.DataFrame())

    resp = client.get("/api/v1/commodities-fx/AU0/kline")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_kline_akshare_missing_returns_404_with_hint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_akshare(*a, **k) -> pd.DataFrame:
        raise ImportError("No module named 'akshare'")

    monkeypatch.setattr(df_mod, "get_futures_kline", _no_akshare)

    resp = client.get("/api/v1/commodities-fx/AU0/kline")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert "akshare" in body["error"]["message"]


def test_fx_quotes_populated_returns_200(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    df = pd.DataFrame({"pair": ["AUD/USD", "USD/CNY"], "bid": [0.7, 6.74], "ask": [0.71, 6.75]})
    monkeypatch.setattr(df_mod, "get_fx_quotes", lambda *a, **k: df)

    resp = client.get("/api/v1/commodities-fx/fx/quotes")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 2


def test_fx_quotes_upstream_error_returns_502(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*a, **k) -> pd.DataFrame:
        raise RuntimeError("akshare network failure")

    monkeypatch.setattr(df_mod, "get_fx_quotes", _boom)

    resp = client.get("/api/v1/commodities-fx/fx/quotes")
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "EXCHANGE_ERROR"
