"""Tests for /api/v1/taiwan/* (FinMind 台股 endpoints).

Uses mock isolation: the taiwan_finmind functions are monkeypatched, so no
network/token is needed. Error-path tests exercise the FinMind→SeleneSpaceError
mapping (401/404/429/502).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import taiwan as taiwan_api
from app.api.v1.taiwan import router as taiwan_router
from app.middleware.error_handler import install_error_handlers
from selene_space_stock import taiwan_finmind


def _make_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(taiwan_router, prefix="/api/v1")
    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_make_app())


def test_quote_normalizes_tw_suffix(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def _fake_quote(stock_id: str) -> dict:
        captured["stock_id"] = stock_id
        return {"stock_id": stock_id, "name": "台积电", "close": 1000.0}

    monkeypatch.setattr(taiwan_api.taiwan_finmind, "quote", _fake_quote)
    resp = client.get("/api/v1/taiwan/2330.TW/quote")
    assert resp.status_code == 200
    assert captured["stock_id"] == "2330"  # bare code for FinMind semantics
    assert resp.json()["data"]["name"] == "台积电"


def test_all_five_symbol_endpoints_exist(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("quote", "valuation", "financials", "revenue", "dividend"):
        monkeypatch.setattr(
            taiwan_api.taiwan_finmind,
            name,
            lambda *a, **k: {"ok": name},
        )
        resp = client.get(f"/api/v1/taiwan/2330/{name}")
        assert resp.status_code == 200, f"/{name} should be 200"
        assert resp.json()["data"]["ok"] == name


def test_search_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        taiwan_api.taiwan_finmind,
        "search",
        lambda q: [{"stock_id": "2330", "name": "台积电", "board": "上市"}],
    )
    resp = client.get("/api/v1/taiwan/search", params={"q": "台积电"})
    assert resp.status_code == 200
    assert resp.json()["data"][0]["stock_id"] == "2330"


def test_token_missing_returns_401(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_token(*args: object, **kwargs: object) -> dict:
        raise taiwan_finmind.FinMindTokenError("FINMIND_TOKEN 未设置")

    monkeypatch.setattr(taiwan_api.taiwan_finmind, "quote", _raise_token)
    resp = client.get("/api/v1/taiwan/2330/quote")
    assert resp.status_code == 401
    body = resp.json()
    assert body["data"] is None
    assert body["error"]["code"] == "AUTHENTICATION_ERROR"


def test_empty_result_returns_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_empty(*args: object, **kwargs: object) -> dict:
        raise taiwan_finmind.FinMindEmptyError("未找到股票 2330 的行情数据")

    monkeypatch.setattr(taiwan_api.taiwan_finmind, "quote", _raise_empty)
    resp = client.get("/api/v1/taiwan/2330/quote")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_rate_limit_returns_429(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_rate(*args: object, **kwargs: object) -> dict:
        raise taiwan_finmind.FinMindRateLimitError("FinMind 请求过频 (HTTP 429)")

    monkeypatch.setattr(taiwan_api.taiwan_finmind, "quote", _raise_rate)
    resp = client.get("/api/v1/taiwan/2330/quote")
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "RATE_LIMITED"


def test_upstream_error_returns_502(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_generic(*args: object, **kwargs: object) -> dict:
        raise taiwan_finmind.FinMindError("FinMind 请求失败: HTTP 403")

    monkeypatch.setattr(taiwan_api.taiwan_finmind, "quote", _raise_generic)
    resp = client.get("/api/v1/taiwan/2330/quote")
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "EXCHANGE_ERROR"


# ---------------------------------------------------------------------------
# kline
# ---------------------------------------------------------------------------


def test_kline_returns_ohlcv_bars(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    bars = [
        {"date": "2026-08-10", "open": 900.0, "high": 920.0, "low": 895.0, "close": 910.0, "volume": 20_000_000},
        {"date": "2026-08-11", "open": 915.0, "high": 940.0, "low": 910.0, "close": 930.0, "volume": 25_000_000},
    ]
    monkeypatch.setattr(taiwan_api.taiwan_finmind, "kline", lambda *a, **k: bars)
    resp = client.get("/api/v1/taiwan/2330/kline")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 2
    assert data[0]["high"] == 920.0
    assert data[0]["volume"] == 20_000_000


def test_kline_normalizes_suffix_and_dates(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str | None] = {}

    def _fake(stock_id: str, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
        captured["stock_id"] = stock_id
        captured["start_date"] = start_date
        captured["end_date"] = end_date
        return [{"date": "2026-08-10", "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 100}]

    monkeypatch.setattr(taiwan_api.taiwan_finmind, "kline", _fake)
    resp = client.get("/api/v1/taiwan/2330.TW/kline", params={"start": "20260101", "end": "20260301"})
    assert resp.status_code == 200
    assert captured["stock_id"] == "2330"
    assert captured["start_date"] == "2026-01-01"
    assert captured["end_date"] == "2026-03-01"


def test_kline_empty_returns_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_empty(*args: object, **kwargs: object) -> dict:
        raise taiwan_finmind.FinMindEmptyError("未找到股票 2330 的 K 线数据")

    monkeypatch.setattr(taiwan_api.taiwan_finmind, "kline", _raise_empty)
    resp = client.get("/api/v1/taiwan/2330/kline")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_kline_token_missing_returns_401(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_token(*args: object, **kwargs: object) -> dict:
        raise taiwan_finmind.FinMindTokenError("FINMIND_TOKEN 未设置")

    monkeypatch.setattr(taiwan_api.taiwan_finmind, "kline", _raise_token)
    resp = client.get("/api/v1/taiwan/2330/kline")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTHENTICATION_ERROR"


def test_kline_start_gt_end_returns_404_not_crash(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_empty(stock_id: str, start_date: str | None = None, end_date: str | None = None) -> dict:
        assert start_date == "2026-03-01" and end_date == "2026-01-01"
        raise taiwan_finmind.FinMindEmptyError("start>end 无数据")

    monkeypatch.setattr(taiwan_api.taiwan_finmind, "kline", _raise_empty)
    resp = client.get("/api/v1/taiwan/2330/kline", params={"start": "20260301", "end": "20260101"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"
