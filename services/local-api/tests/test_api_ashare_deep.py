"""Tests for /api/v1/ashare_deep/* endpoints (资金流/龙虎榜/融资融券/北向).

Endpoints call data_fetcher functions lazily, so monkeypatching
``selene_space_ashare.data_fetcher.<fn>`` is picked up at call time.
Empty upstream data -> 404 NOT_FOUND, data-source failure -> 502
EXCHANGE_ERROR, akshare missing -> 404 with explicit hint.
"""

from __future__ import annotations

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.error_handler import install_error_handlers

pytest.importorskip("selene_space_ashare")

from app.api.v1.ashare_deep import router as ashare_deep_router  # noqa: E402
from selene_space_ashare import data_fetcher as df_mod  # noqa: E402


def _make_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(ashare_deep_router, prefix="/api/v1")
    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_make_app())


def _flow_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["20240101", "20240102"],
            "主力净流入-净额": [100.0, 200.0],
            "超大单净流入-净额": [50.0, 60.0],
            "code": ["600519", "600519"],
        }
    )


def test_fund_flow_populated_returns_200(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(df_mod, "get_fund_flow", lambda *a, **k: _flow_df())

    resp = client.get("/api/v1/ashare/fund-flow/600519")
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    assert len(body["data"]) == 2
    assert body["data"][0]["date"] == "20240101"


def test_fund_flow_empty_returns_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(df_mod, "get_fund_flow", lambda *a, **k: pd.DataFrame())

    resp = client.get("/api/v1/ashare/fund-flow/600519")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_lhb_empty_returns_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(df_mod, "get_lhb", lambda *a, **k: pd.DataFrame())

    resp = client.get("/api/v1/ashare/lhb", params={"date": "20240101"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_margin_upstream_error_returns_502(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*a, **k) -> pd.DataFrame:
        raise RuntimeError("akshare network failure")

    monkeypatch.setattr(df_mod, "get_margin_data", _boom)

    resp = client.get("/api/v1/ashare/margin/600519")
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "EXCHANGE_ERROR"


def test_northbound_populated_returns_200(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    df = pd.DataFrame({"date": ["20240101"], "value": [123.0]})
    monkeypatch.setattr(df_mod, "get_northbound_flow", lambda *a, **k: df)

    resp = client.get("/api/v1/ashare/northbound")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 1


def test_akshare_missing_returns_404_with_hint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_akshare(*a, **k) -> pd.DataFrame:
        raise ImportError("No module named 'akshare'")

    monkeypatch.setattr(df_mod, "get_fund_flow", _no_akshare)

    resp = client.get("/api/v1/ashare/fund-flow/600519")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert "akshare" in body["error"]["message"]


# ---------------------------------------------------------------------------
# kline
# ---------------------------------------------------------------------------


def _kline_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["20240101", "20240102"],
            "open": [10.0, 11.0],
            "high": [11.5, 11.2],
            "low": [9.8, 10.5],
            "close": [11.0, 10.8],
            "volume": [1_000_000, 1_200_000],
        }
    )


def test_kline_populated_returns_200_with_rows(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(df_mod, "get_daily_klines", lambda *a, **k: _kline_df())

    resp = client.get("/api/v1/ashare/600519/kline")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) >= 1
    assert data[0]["date"] == "20240101"
    assert data[0]["high"] == 11.5


def test_kline_non_6_digit_symbol_returns_422(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    resp = client.get("/api/v1/ashare/60051/kline")
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["details"]["errors"][0]["type"] == "string_pattern_mismatch"


def test_kline_empty_returns_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(df_mod, "get_daily_klines", lambda *a, **k: pd.DataFrame())

    resp = client.get("/api/v1/ashare/600519/kline")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_kline_ths_dash_date_passes_through(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the contract: THS direct-API dash dates reach the client unchanged
    (gateway does the format-agnostic t conversion, not this endpoint)."""
    df = pd.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-02"],
            "open": [10.0, 11.0],
            "high": [11.5, 11.2],
            "low": [9.8, 10.5],
            "close": [11.0, 10.8],
            "volume": [1_000_000, 1_200_000],
        }
    )
    monkeypatch.setattr(df_mod, "get_daily_klines", lambda *a, **k: df)

    resp = client.get("/api/v1/ashare/600519/kline")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data[0]["date"] == "2024-01-01"
    assert len(data) == 2
