"""Tests for /api/v1/chart/* (unified bars gateway).

The gateway is the single REST bars contract for us/commodities/ashare/taiwan
(R1: ``t`` = Unix seconds UTC; 4h aggregated for us; per-market capabilities
via /chart/capabilities). Crypto is intentionally NOT served here (Decision A).

Mock isolation: each market branch's data source is monkeypatched at call time
(chart.py imports lazily inside the branch), so no network/keys needed.
"""

from __future__ import annotations

import time

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import chart as chart_api
from app.api.v1.chart import router as chart_router
from app.core import dependencies
from app.middleware.error_handler import install_error_handlers

_SH_TZ = ZoneInfo("Asia/Shanghai")


def _make_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(chart_router, prefix="/api/v1")
    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_make_app())


@pytest.fixture(autouse=True)
def _pin_kline_manager_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC1-0: _make_app() runs no lifespan, so the module-global kline manager
    would otherwise leak from other test files. Pin to None (Yahoo fallback)
    for every test unless the test itself installs a fake."""
    monkeypatch.setattr(dependencies, "_kline_manager", None)


# ---------------------------------------------------------------------------
# Yahoo fixture helpers
# ---------------------------------------------------------------------------


def _yf_chart(timestamps: list[int]) -> dict:
    n = len(timestamps)
    return {
        "chart": {
            "result": [
                {
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [
                            {
                                "open": [100.0] * n,
                                "high": [102.0] * n,
                                "low": [99.0] * n,
                                "close": [101.0] * n,
                                "volume": [1000] * n,
                            }
                        ]
                    },
                }
            ]
        }
    }


class _FakeYfClient:
    def __init__(self, raw: dict | None = None, exc: Exception | None = None):
        self._raw = raw
        self._exc = exc
        self.calls: list[tuple[str, str]] = []

    async def get_chart(self, symbol: str, interval: str, range_: str) -> dict | None:
        self.calls.append((interval, range_))
        if self._exc:
            raise self._exc
        return self._raw


def _fake_get_client(fake: _FakeYfClient):
    """Return an async callable for ``_get_client`` (chart.py awaits it)."""

    async def _inner():
        return fake

    return _inner


# ---------------------------------------------------------------------------
# ① 4 markets -> 200 with full {t,o,h,l,c,v} shape, t = int Unix seconds
# ---------------------------------------------------------------------------


def test_us_bars_200_shape(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        chart_api,
        "_get_client",
        _fake_get_client(_FakeYfClient(_yf_chart([1700000000, 1700003600]))),
    )
    resp = client.get(
        "/api/v1/chart/bars",
        params={"market": "us", "symbol": "AAPL", "interval": "1d"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["market"] == "us"
    assert data["symbol"] == "AAPL"
    assert data["interval"] == "1d"
    bar = data["bars"][0]
    assert set(bar) == {"t", "o", "h", "l", "c", "v"}
    assert isinstance(bar["t"], int)
    assert bar["t"] == 1700000000


def test_commodities_bars_200(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    df_mod = pytest.importorskip("selene_space_commodities_fx.data_fetcher")
    df = pd.DataFrame(
        {
            "timestamp": ["2026-08-15 09:00:00", "2026-08-15 09:05:00"],
            "open": [5000.0, 5010.0],
            "high": [5020.0, 5030.0],
            "low": [4990.0, 5000.0],
            "close": [5015.0, 5025.0],
            "volume": [100, 120],
        }
    )
    monkeypatch.setattr(df_mod, "get_futures_kline", lambda *a, **k: df)

    resp = client.get(
        "/api/v1/chart/bars",
        params={"market": "commodities", "symbol": "AU0", "interval": "5m"},
    )
    assert resp.status_code == 200
    bar = resp.json()["data"]["bars"][0]
    assert bar["t"] == int(datetime(2026, 8, 15, 9, 0, tzinfo=_SH_TZ).timestamp())
    assert bar["o"] == 5000.0


def test_ashare_bars_200(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    df_mod = pytest.importorskip("selene_space_ashare.data_fetcher")
    df = pd.DataFrame(
        {
            "date": ["20240101", "20240102"],
            "open": [10.0, 11.0],
            "high": [11.5, 11.2],
            "low": [9.8, 10.5],
            "close": [11.0, 10.8],
            "volume": [1_000_000, 1_200_000],
        }
    )
    monkeypatch.setattr(df_mod, "get_daily_klines", lambda *a, **k: df)

    resp = client.get(
        "/api/v1/chart/bars",
        params={"market": "ashare", "symbol": "600519", "interval": "1d"},
    )
    assert resp.status_code == 200
    bar = resp.json()["data"]["bars"][0]
    assert bar["t"] == int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    assert bar["c"] == 11.0


def test_taiwan_bars_200(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    tw = pytest.importorskip("selene_space_stock.taiwan_finmind")
    bars = [
        {
            "date": "2026-08-10",
            "open": 900.0,
            "high": 920.0,
            "low": 895.0,
            "close": 910.0,
            "volume": 20_000_000,
        }
    ]
    monkeypatch.setattr(tw, "kline", lambda *a, **k: bars)

    resp = client.get(
        "/api/v1/chart/bars",
        params={"market": "taiwan", "symbol": "2330", "interval": "1d"},
    )
    assert resp.status_code == 200
    bar = resp.json()["data"]["bars"][0]
    assert bar["t"] == int(datetime(2026, 8, 10, tzinfo=timezone.utc).timestamp())
    assert bar["v"] == 20_000_000


# ---------------------------------------------------------------------------
# ② invalid market -> 422 (Literal)
# ---------------------------------------------------------------------------


def test_invalid_market_returns_422(client: TestClient) -> None:
    resp = client.get(
        "/api/v1/chart/bars",
        params={"market": "ftx", "symbol": "BTC", "interval": "1d"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["details"]["errors"][0]["type"] == "literal_error"


# ---------------------------------------------------------------------------
# ③ global invalid interval -> 422; per-market unsupported -> 422
# ---------------------------------------------------------------------------


def test_invalid_global_interval_returns_422(client: TestClient) -> None:
    resp = client.get(
        "/api/v1/chart/bars",
        params={"market": "us", "symbol": "AAPL", "interval": "1H"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_market_unsupported_interval_returns_422(client: TestClient) -> None:
    """'1h' is globally valid but unsupported for ashare -> manual 422."""
    resp = client.get(
        "/api/v1/chart/bars",
        params={"market": "ashare", "symbol": "600519", "interval": "1h"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["details"]["errors"][0]["loc"] == ["query", "interval"]


# ---------------------------------------------------------------------------
# ④ empty -> 404
# ---------------------------------------------------------------------------


def test_us_empty_returns_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        chart_api, "_get_client", _fake_get_client(_FakeYfClient(_yf_chart([])))
    )
    resp = client.get(
        "/api/v1/chart/bars",
        params={"market": "us", "symbol": "AAPL", "interval": "1d"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_ashare_empty_returns_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    df_mod = pytest.importorskip("selene_space_ashare.data_fetcher")
    monkeypatch.setattr(df_mod, "get_daily_klines", lambda *a, **k: pd.DataFrame())
    resp = client.get(
        "/api/v1/chart/bars",
        params={"market": "ashare", "symbol": "600519", "interval": "1d"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# ⑤ upstream failure -> 502
# ---------------------------------------------------------------------------


def test_us_upstream_error_returns_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        chart_api,
        "_get_client",
        _fake_get_client(_FakeYfClient(exc=RuntimeError("yahoo timeout"))),
    )
    resp = client.get(
        "/api/v1/chart/bars",
        params={"market": "us", "symbol": "AAPL", "interval": "1d"},
    )
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "EXCHANGE_ERROR"


# ---------------------------------------------------------------------------
# ⑥ capabilities
# ---------------------------------------------------------------------------


def test_capabilities_reports_per_market_intervals(client: TestClient) -> None:
    resp = client.get("/api/v1/chart/capabilities")
    assert resp.status_code == 200
    caps = resp.json()["data"]
    assert caps["us"] == ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]
    assert caps["commodities"] == ["1m", "5m", "15m", "30m", "1h", "1d"]
    assert caps["ashare"] == ["1d"]
    assert caps["taiwan"] == ["1d"]
    assert "crypto" in caps  # reported for uniform rendering, never served


# ---------------------------------------------------------------------------
# ⑦ us t unit-agnostic: ms (13-digit) and seconds (10-digit) -> int seconds
# ---------------------------------------------------------------------------


def test_us_t_unit_agnostic(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    for timestamps in ([1700000000, 1700003600], [1700000000000, 1700003600000]):
        monkeypatch.setattr(
            chart_api,
            "_get_client",
            _fake_get_client(_FakeYfClient(_yf_chart(timestamps))),
        )
        resp = client.get(
            "/api/v1/chart/bars",
            params={"market": "us", "symbol": "AAPL", "interval": "1d"},
        )
        assert resp.status_code == 200
        bar = resp.json()["data"]["bars"][0]
        assert isinstance(bar["t"], int)
        assert bar["t"] == 1700000000


# ---------------------------------------------------------------------------
# R2 — us 4h aggregation
# ---------------------------------------------------------------------------


def test_us_4h_aggregates_and_drops_partial_tail(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    n = 10  # 8 full 1h bars -> 2×4h; last 2 dropped
    ts = [1700000000 + i * 3600 for i in range(n)]
    raw = {
        "chart": {
            "result": [
                {
                    "timestamp": ts,
                    "indicators": {
                        "quote": [
                            {
                                "open": [float(100 + i) for i in range(n)],
                                "high": [float(102 + i) for i in range(n)],
                                "low": [float(99 + i) for i in range(n)],
                                "close": [float(101 + i) for i in range(n)],
                                "volume": [1000] * n,
                            }
                        ]
                    },
                }
            ]
        }
    }
    monkeypatch.setattr(chart_api, "_get_client", _fake_get_client(_FakeYfClient(raw)))

    resp = client.get(
        "/api/v1/chart/bars",
        params={"market": "us", "symbol": "AAPL", "interval": "4h"},
    )
    assert resp.status_code == 200
    bars = resp.json()["data"]["bars"]
    assert len(bars) == 2
    first = bars[0]
    assert first["t"] == ts[3]  # last 1h of the first 4h chunk
    assert first["o"] == 100.0
    assert first["h"] == 105.0
    assert first["l"] == 99.0
    assert first["c"] == 104.0
    assert first["v"] == 4000


def test_us_4h_requests_1h_from_yahoo(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """4h must pull 1h and aggregate ONCE.

    Regression: _us_bars used to pass "4h" straight to Yahoo (native 4h bars)
    and then run _aggregate_4h over them — 4h folded into 16h, ~40 candles
    collapsed to ~10. The fold must only ever see 1h input.
    """
    n = 8
    ts = [1700000000 + i * 3600 for i in range(n)]
    raw = {
        "chart": {
            "result": [
                {
                    "timestamp": ts,
                    "indicators": {
                        "quote": [
                            {
                                "open": [float(100 + i) for i in range(n)],
                                "high": [float(102 + i) for i in range(n)],
                                "low": [float(99 + i) for i in range(n)],
                                "close": [float(101 + i) for i in range(n)],
                                "volume": [1000] * n,
                            }
                        ]
                    },
                }
            ]
        }
    }
    fake = _FakeYfClient(raw)
    monkeypatch.setattr(chart_api, "_get_client", _fake_get_client(fake))

    resp = client.get(
        "/api/v1/chart/bars",
        params={"market": "us", "symbol": "AAPL", "interval": "4h"},
    )
    assert resp.status_code == 200
    assert fake.calls == [("1h", "3mo")]  # never asks Yahoo for "4h"
    assert len(resp.json()["data"]["bars"]) == 2  # 8×1h -> 2×4h


def test_us_intraday_ranges_within_yahoo_caps(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every Yahoo range must respect the per-interval ceiling.

    Regression: 15m/30m were requested with "3mo" (~91d) but Yahoo caps
    intraday at 60d -> HTTP 422 -> 502 -> empty chart. 1m caps at 7d.
    """
    # interval -> max range in days Yahoo accepts (empirically verified).
    _CAP_DAYS = {"1m": 7, "5m": 60, "15m": 60, "30m": 60}
    _UNIT = {"d": 1, "mo": 30, "y": 365}

    for interval, cap_days in _CAP_DAYS.items():
        fake = _FakeYfClient(_yf_chart([1700000000]))
        monkeypatch.setattr(chart_api, "_get_client", _fake_get_client(fake))
        resp = client.get(
            "/api/v1/chart/bars",
            params={"market": "us", "symbol": "AAPL", "interval": interval},
        )
        assert resp.status_code == 200
        ((yahoo_interval, range_),) = fake.calls
        assert yahoo_interval == interval
        num = int(range_[:-2] if range_.endswith("mo") else range_[:-1])
        unit = range_[-2:] if range_.endswith("mo") else range_[-1]
        assert (
            num * _UNIT[unit] <= cap_days
        ), f"{interval} range {range_} exceeds {cap_days}d"


# ---------------------------------------------------------------------------
# ⑧ /chart/symbols unified symbol search (5 markets, independent degradation)
# ---------------------------------------------------------------------------


def _reset_ashare_memo(
    monkeypatch: pytest.MonkeyPatch, df: pd.DataFrame | None = None
) -> None:
    """Point _ashare_search's akshare source at a fake df and clear its memo."""
    df_mod = pytest.importorskip("selene_space_ashare.data_fetcher")
    monkeypatch.setattr(df_mod, "get_a_share_list", lambda *a, **k: df)
    chart_api._ASHARE_DF_CACHE = None


def test_symbols_empty_q_returns_hot(client: TestClient) -> None:
    resp = client.get("/api/v1/chart/symbols")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["query"] == ""
    items = {i["symbol"]: i for i in data["items"]}
    assert "BTC" in items and items["BTC"]["market"] == "crypto"
    assert "600519" in items and items["600519"]["market"] == "ashare"
    assert "SPY" in items and items["SPY"]["market"] == "us"


def test_symbols_us_match(client: TestClient) -> None:
    resp = client.get("/api/v1/chart/symbols", params={"q": "aapl"})
    assert resp.status_code == 200
    markets = [(i["symbol"], i["market"]) for i in resp.json()["data"]["items"]]
    assert ("AAPL", "us") in markets


def test_symbols_ashare_match(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    df = pd.DataFrame({"code": ["600519", "000001"], "name": ["贵州茅台", "平安银行"]})
    _reset_ashare_memo(monkeypatch, df)
    resp = client.get("/api/v1/chart/symbols", params={"q": "600"})
    assert resp.status_code == 200
    ashare = [
        (i["symbol"], i["market"])
        for i in resp.json()["data"]["items"]
        if i["market"] == "ashare"
    ]
    assert ("600519", "ashare") in ashare


def test_symbols_commodities_match(client: TestClient) -> None:
    resp = client.get("/api/v1/chart/symbols", params={"q": "au"})
    assert resp.status_code == 200
    comm = [
        (i["symbol"], i["name"])
        for i in resp.json()["data"]["items"]
        if i["market"] == "commodities"
    ]
    assert ("AU0", "沪金") in comm


def test_symbols_crypto_match(client: TestClient) -> None:
    resp = client.get("/api/v1/chart/symbols", params={"q": "btc"})
    assert resp.status_code == 200
    crypto = [
        i["symbol"] for i in resp.json()["data"]["items"] if i["market"] == "crypto"
    ]
    assert "BTC" in crypto


def test_symbols_taiwan_match(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """刀4 重写后 _taiwan_search 直调 taiwan_finmind._get("TaiwanStockInfo")，patch _get 返回静态列表形状。"""
    tw = pytest.importorskip("selene_space_stock.taiwan_finmind")
    monkeypatch.setattr(
        tw,
        "_get",
        lambda dataset: [{"stock_id": "2330", "stock_name": "台積電"}]
        if dataset == "TaiwanStockInfo"
        else [],
    )
    chart_api._TAIWAN_ROWS_CACHE = None
    resp = client.get("/api/v1/chart/symbols", params={"q": "2330"})
    assert resp.status_code == 200
    tw_items = [
        i["symbol"] for i in resp.json()["data"]["items"] if i["market"] == "taiwan"
    ]
    assert "2330" in tw_items


def test_symbols_ashare_failure_degrades_to_empty(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken akshare list must not 500 the whole endpoint."""
    df_mod = pytest.importorskip("selene_space_ashare.data_fetcher")
    monkeypatch.setattr(
        df_mod,
        "get_a_share_list",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("akshare down")),
    )
    chart_api._ASHARE_DF_CACHE = None
    resp = client.get("/api/v1/chart/symbols", params={"q": "600"})
    assert resp.status_code == 200
    markets = {i["market"] for i in resp.json()["data"]["items"]}
    assert "ashare" not in markets  # degraded, not erroring


def test_symbols_ashare_pinyin_initials(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """拼音首字母：gzmt → 贵州茅台。pypinyin 缺席时跳过（initials 功能整体降级）。"""
    pytest.importorskip("pypinyin")
    df = pd.DataFrame({"code": ["600519", "000001"], "name": ["贵州茅台", "平安银行"]})
    _reset_ashare_memo(monkeypatch, df)
    resp = client.get("/api/v1/chart/symbols", params={"q": "gzmt"})
    assert resp.status_code == 200
    ashare = [
        (i["symbol"], i["name"])
        for i in resp.json()["data"]["items"]
        if i["market"] == "ashare"
    ]
    assert ("600519", "贵州茅台") in ashare


def test_symbols_taiwan_pinyin_initials(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """繁体也走 pypinyin：tjd → 台積電（積=twa5 繁体归一化 j）。"""
    pytest.importorskip("pypinyin")
    tw = pytest.importorskip("selene_space_stock.taiwan_finmind")
    monkeypatch.setattr(
        tw,
        "_get",
        lambda dataset: [{"stock_id": "2330", "stock_name": "台積電"}]
        if dataset == "TaiwanStockInfo"
        else [],
    )
    chart_api._TAIWAN_ROWS_CACHE = None
    resp = client.get("/api/v1/chart/symbols", params={"q": "tjd"})
    assert resp.status_code == 200
    tw_items = [
        i["symbol"] for i in resp.json()["data"]["items"] if i["market"] == "taiwan"
    ]
    assert "2330" in tw_items


def test_symbols_no_ascii_pinyin_match_does_not_crash(client: TestClient) -> None:
    """非字母 q（数字/中文）不触发 initials 匹配路径，原样返回。"""
    resp = client.get("/api/v1/chart/symbols", params={"q": "600519"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# ⑨ us local-first (US-001): klines.db yahoo/us_stock partitions, Yahoo fallback
# ---------------------------------------------------------------------------


def _local_df(timestamps_ms: list[int], base: float = 100.0) -> pd.DataFrame:
    """Fake kline_manager.get_klines return: timestamp(ms) OHLCV frame."""
    n = len(timestamps_ms)
    return pd.DataFrame(
        {
            "timestamp": timestamps_ms,
            "open": [base] * n,
            "high": [base + 2.0] * n,
            "low": [base - 1.0] * n,
            "close": [base + 1.0] * n,
            "volume": [1000.0] * n,
        }
    )


class _FakeKm:
    """Records get_klines kwargs; returns the configured df (or raises)."""

    def __init__(self, df: pd.DataFrame | None, exc: Exception | None = None):
        self._df = df
        self._exc = exc
        self.calls: list[dict] = []

    async def get_klines(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc:
            raise self._exc
        return self._df


def _install_km(monkeypatch: pytest.MonkeyPatch, km: _FakeKm) -> None:
    monkeypatch.setattr(dependencies, "_kline_manager", km)


def _now_ms() -> int:
    return int(time.time() * 1000)


def test_us_local_hit_serves_local_without_yahoo(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1-1: fresh local partition -> source=local, Yahoo client never built."""

    def _boom():
        raise AssertionError("Yahoo fallback must not run on a fresh local hit")

    monkeypatch.setattr(chart_api, "_get_client", _boom)
    # ASC order mirrors kline_db (ORDER BY timestamp ASC); last bar 1h old.
    ts = [_now_ms() - (20 - i) * 3600_000 for i in range(20)]
    km = _FakeKm(_local_df(ts))
    _install_km(monkeypatch, km)

    resp = client.get(
        "/api/v1/chart/bars",
        params={"market": "us", "symbol": "AAPL", "interval": "1h"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["source"] == "local"
    assert len(data["bars"]) == 20
    bar = data["bars"][0]
    assert isinstance(bar["t"], int)
    assert (
        bar["o"] == 100.0
        and bar["h"] == 102.0
        and bar["l"] == 99.0
        and bar["c"] == 101.0
    )
    # partition key + no write-source flag
    assert km.calls[0]["exchange"] == "yahoo"
    assert km.calls[0]["market_type"] == "us_stock"
    assert km.calls[0]["symbol"] == "AAPL"
    assert km.calls[0]["interval"] == "1h"
    assert km.calls[0]["source"] == "local"


def test_us_local_empty_falls_back_to_yahoo(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1-2: empty/short local df -> Yahoo fallback, source=yahoo."""
    km = _FakeKm(_local_df([_now_ms() - 3600_000 * i for i in range(5)]))  # <10 rows
    _install_km(monkeypatch, km)
    monkeypatch.setattr(
        chart_api,
        "_get_client",
        _fake_get_client(_FakeYfClient(_yf_chart([1700000000, 1700003600]))),
    )

    resp = client.get(
        "/api/v1/chart/bars",
        params={"market": "us", "symbol": "AAPL", "interval": "1d"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["source"] == "yahoo"
    assert len(data["bars"]) == 2


def test_us_local_none_manager_falls_back_to_yahoo(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """km=None (no lifespan / manager not built) -> Yahoo fallback."""
    _install_km(monkeypatch, _FakeKm(None))  # also covers df None
    monkeypatch.setattr(
        chart_api,
        "_get_client",
        _fake_get_client(_FakeYfClient(_yf_chart([1700000000]))),
    )
    resp = client.get(
        "/api/v1/chart/bars",
        params={"market": "us", "symbol": "AAPL", "interval": "1d"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["source"] == "yahoo"


def test_us_local_stale_gate_1d(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1-3: 1d gate = 5 days. 3d-old tail -> local; 8d-old -> yahoo.

    12 daily bars in both cases so only the freshness gate decides.
    """
    ts_fresh = [_now_ms() - 3 * 86_400_000 - (11 - i) * 86_400_000 for i in range(12)]
    km_fresh = _FakeKm(_local_df(ts_fresh))
    _install_km(monkeypatch, km_fresh)
    resp = client.get(
        "/api/v1/chart/bars",
        params={"market": "us", "symbol": "AAPL", "interval": "1d"},
    )
    assert resp.json()["data"]["source"] == "local"

    ts_stale = [_now_ms() - 8 * 86_400_000 - (11 - i) * 86_400_000 for i in range(12)]
    km_stale = _FakeKm(_local_df(ts_stale))
    _install_km(monkeypatch, km_stale)
    monkeypatch.setattr(
        chart_api,
        "_get_client",
        _fake_get_client(_FakeYfClient(_yf_chart([1700000000]))),
    )
    resp = client.get(
        "/api/v1/chart/bars",
        params={"market": "us", "symbol": "AAPL", "interval": "1d"},
    )
    assert resp.json()["data"]["source"] == "yahoo"


def test_us_local_stale_gate_1h(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1-3: intraday gate = max(30min, min(3*interval, 2d)). 1h: 1h-old ok, 4h-old stale."""
    ts_fresh = [_now_ms() - 3600_000 - (11 - i) * 3600_000 for i in range(12)]
    km_fresh = _FakeKm(_local_df(ts_fresh, base=150.0))
    _install_km(monkeypatch, km_fresh)
    resp = client.get(
        "/api/v1/chart/bars",
        params={"market": "us", "symbol": "AAPL", "interval": "1h"},
    )
    assert resp.json()["data"]["source"] == "local"

    ts_stale = [_now_ms() - 4 * 3600_000 - (11 - i) * 3600_000 for i in range(12)]
    km_stale = _FakeKm(_local_df(ts_stale, base=150.0))
    _install_km(monkeypatch, km_stale)
    monkeypatch.setattr(
        chart_api,
        "_get_client",
        _fake_get_client(_FakeYfClient(_yf_chart([1700000000]))),
    )
    resp = client.get(
        "/api/v1/chart/bars",
        params={"market": "us", "symbol": "AAPL", "interval": "1h"},
    )
    assert resp.json()["data"]["source"] == "yahoo"


def test_us_local_1w_gate_allows_weekend(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1-3: 1w gate = 10 days (US weekend/holiday closure)."""
    ts = [_now_ms() - 6 * 86_400_000 - (11 - i) * 7 * 86_400_000 for i in range(12)]
    km = _FakeKm(_local_df(ts))
    _install_km(monkeypatch, km)
    resp = client.get(
        "/api/v1/chart/bars",
        params={"market": "us", "symbol": "AAPL", "interval": "1w"},
    )
    assert resp.json()["data"]["source"] == "local"


def test_us_local_4h_reads_1h_partition_and_aggregates(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1-4: 4h reads the 1h partition (window=30d) and aggregates 4->1."""
    n = 10  # 8 full 1h bars -> 2×4h; partial tail dropped
    km = _FakeKm(_local_df([_now_ms() - (n - 1 - i) * 3600_000 for i in range(n)]))
    _install_km(monkeypatch, km)

    resp = client.get(
        "/api/v1/chart/bars",
        params={"market": "us", "symbol": "AAPL", "interval": "4h"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["source"] == "local"
    assert km.calls[0]["interval"] == "1h"
    bars = data["bars"]
    assert len(bars) == 2
    first = bars[0]
    assert first["o"] == 100.0 and first["c"] == 101.0
    assert first["h"] == 102.0 and first["l"] == 99.0
    assert first["v"] == 4000.0


def test_us_local_km_error_falls_back_to_yahoo(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken local read must degrade to Yahoo, not 500."""
    km = _FakeKm(None, exc=RuntimeError("db locked"))
    _install_km(monkeypatch, km)
    monkeypatch.setattr(
        chart_api,
        "_get_client",
        _fake_get_client(_FakeYfClient(_yf_chart([1700000000]))),
    )
    resp = client.get(
        "/api/v1/chart/bars",
        params={"market": "us", "symbol": "AAPL", "interval": "1d"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["source"] == "yahoo"


def test_us_legacy_yahoo_tests_keep_source_yahoo(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1-5 guard: with km pinned None, the legacy Yahoo path still works and
    reports source=yahoo."""
    monkeypatch.setattr(
        chart_api,
        "_get_client",
        _fake_get_client(_FakeYfClient(_yf_chart([1700000000, 1700003600]))),
    )
    resp = client.get(
        "/api/v1/chart/bars",
        params={"market": "us", "symbol": "AAPL", "interval": "1d"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["source"] == "yahoo"
    assert data["bars"][0]["t"] == 1700000000


# ---------------------------------------------------------------------------
# ICT structure overlay (REWRITE_SPEC_ICT_STRUCTURE.md §2.4)
# ---------------------------------------------------------------------------


class _FakeKlineDB:
    """Duck-typed KlineDB returning a fixed frame via get_klines."""

    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df
        self.calls: list[tuple] = []

    def get_klines(self, exchange, market_type, symbol, interval, start_date, end_date):
        self.calls.append((exchange, market_type, symbol, interval))
        return self._df.copy()


def _structure_df(n: int = 60) -> pd.DataFrame:
    """Zigzag OHLC frame with real swings: hourly timestamps in ms."""
    import math

    rows = []
    base_ms = 1_700_000_000_000
    for i in range(n):
        price = 100 + 10 * math.sin(i / 5.0)
        rows.append(
            {
                "exchange": "binance",
                "market_type": "futures",
                "symbol": "BTCUSDT",
                "interval": "1h",
                "timestamp": base_ms + i * 3_600_000,
                "open": price - 0.5,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price + 0.5,
                "volume": 1000.0,
            }
        )
    return pd.DataFrame(rows)


@pytest.fixture()
def fake_db(monkeypatch: pytest.MonkeyPatch) -> _FakeKlineDB:
    db = _FakeKlineDB(_structure_df())
    monkeypatch.setenv("SELENE_KLINES_DB", "unused.db")

    import selene_space_market.kline_db as kline_db_mod

    monkeypatch.setattr(kline_db_mod, "KlineDB", lambda path: db)
    return db


class TestIctStructureOverlay:
    def test_returns_swings_events_bars(
        self, client: TestClient, fake_db: _FakeKlineDB
    ) -> None:
        resp = client.get(
            "/api/v1/chart/ict-structure-overlay",
            params={
                "exchange": "binance",
                "market_type": "futures",
                "symbol": "BTCUSDT",
                "interval": "1h",
                "include_bars": "true",
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["exchange"] == "binance"
        assert data["market_type"] == "futures"
        assert data["bar_count"] == 60
        assert len(data["bars"]) == 60
        assert isinstance(data["swings"], list)
        assert isinstance(data["events"], list)

    def test_bars_omitted_by_default(
        self, client: TestClient, fake_db: _FakeKlineDB
    ) -> None:
        """The chart already holds the bars; echoing 500 of them is dead weight."""
        data = client.get(
            "/api/v1/chart/ict-structure-overlay",
            params={"symbol": "BTCUSDT", "interval": "1h"},
        ).json()["data"]
        assert data["bars"] == []
        assert data["bar_count"] == 60
        assert data["swings"], "swings must survive without the bar echo"

    def test_query_pins_all_four_keys(
        self, client: TestClient, fake_db: _FakeKlineDB
    ) -> None:
        client.get(
            "/api/v1/chart/ict-structure-overlay",
            params={
                "exchange": "binance",
                "market_type": "futures",
                "symbol": "BTCUSDT",
                "interval": "1h",
            },
        )
        assert fake_db.calls, "worker must hit the DB"
        exchange, market_type, symbol, interval = fake_db.calls[0]
        assert (exchange, market_type, symbol, interval) == (
            "binance",
            "futures",
            "BTCUSDT",
            "1h",
        )

    def test_swing_times_are_seconds(
        self, client: TestClient, fake_db: _FakeKlineDB
    ) -> None:
        data = client.get(
            "/api/v1/chart/ict-structure-overlay",
            params={
                "exchange": "binance",
                "market_type": "futures",
                "symbol": "BTCUSDT",
                "interval": "1h",
            },
        ).json()["data"]
        assert data["swings"], "zigzag must produce swings"
        for s in data["swings"]:
            assert set(s) >= {"index", "price", "kind", "tier", "time"}
            assert s["time"] == 1_700_000_000 + s["index"] * 3600

    def test_mixed_source_guard_raises(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        df = _structure_df(10)
        df.loc[df.index[-1], "timestamp"] = df["timestamp"].iloc[0]  # duplicate ts
        db = _FakeKlineDB(df)
        monkeypatch.setenv("SELENE_KLINES_DB", "unused.db")
        import selene_space_market.kline_db as kline_db_mod

        monkeypatch.setattr(kline_db_mod, "KlineDB", lambda path: db)
        resp = client.get(
            "/api/v1/chart/ict-structure-overlay",
            params={
                "exchange": "binance",
                "market_type": "futures",
                "symbol": "BTCUSDT",
                "interval": "1h",
            },
        )
        assert resp.status_code == 500
        assert "Mixed-source" in resp.json()["error"]["message"]

    def test_empty_partition_404(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = _FakeKlineDB(pd.DataFrame())
        monkeypatch.setenv("SELENE_KLINES_DB", "unused.db")
        import selene_space_market.kline_db as kline_db_mod

        monkeypatch.setattr(kline_db_mod, "KlineDB", lambda path: db)
        resp = client.get(
            "/api/v1/chart/ict-structure-overlay",
            params={
                "exchange": "binance",
                "market_type": "futures",
                "symbol": "BTCUSDT",
                "interval": "1h",
            },
        )
        assert resp.status_code == 404


class TestIctTimingAndPositioningLayers:
    """Killzone + dealing range ride along on the same overlay request.

    Both reuse the structure pass's ``tail(limit)`` frame, so their timestamps
    must line up with ``swings`` even though the detectors read the clock
    differently (killzone needs wall-clock time, the range needs swings).
    """

    def _get(self, client: TestClient, **params) -> dict:
        data = client.get(
            "/api/v1/chart/ict-structure-overlay",
            params={"symbol": "BTCUSDT", "interval": "1h", **params},
        ).json()["data"]
        assert isinstance(data, dict)
        return data

    def test_killzone_reports_windows_and_per_bar_membership(
        self, client: TestClient, fake_db: _FakeKlineDB
    ) -> None:
        kz = self._get(client)["killzone"]
        assert kz["sessions"] == ["london", "new_york"]
        assert [w["name"] for w in kz["windows"]] == ["london", "new_york"]
        assert kz["tz_offset_hours"] == -5
        # The fixture carries a real epoch-ms column, so the clock is readable.
        assert kz["clock_source"] == "timestamp"
        assert len(kz["bars"]) == 60
        assert set(b["v"] for b in kz["bars"]) <= {0, 1}
        assert kz["inside_count"] == sum(1 for b in kz["bars"] if b["v"] == 1)

    def test_killzone_times_match_the_bars_frame(
        self, client: TestClient, fake_db: _FakeKlineDB
    ) -> None:
        """Every killzone row is one of the analysed bars, in order."""
        data = self._get(client)
        expected = [1_700_000_000 + i * 3600 for i in range(data["bar_count"])]
        assert [b["t"] for b in data["killzone"]["bars"]] == expected

    def test_killzone_honours_the_offset(
        self, client: TestClient, fake_db: _FakeKlineDB
    ) -> None:
        """Shifting the clock moves which bars land inside the windows.

        A -5 offset against these hourly bars cannot cover all 24 hours, so
        the two offsets must disagree on at least one bar. Equality here would
        mean the offset was silently dropped.
        """
        ny = self._get(client, tz_offset_hours=-5)["killzone"]
        tokyo = self._get(client, tz_offset_hours=9)["killzone"]
        assert [b["v"] for b in ny["bars"]] != [b["v"] for b in tokyo["bars"]]

    def test_killzone_sessions_narrow_the_windows(
        self, client: TestClient, fake_db: _FakeKlineDB
    ) -> None:
        both = self._get(client)["killzone"]
        london_only = self._get(client, sessions="london")["killzone"]
        assert london_only["sessions"] == ["london"]
        assert london_only["inside_count"] <= both["inside_count"]

    def test_killzone_unknown_session_is_4xx_not_a_silent_empty(
        self, client: TestClient, fake_db: _FakeKlineDB
    ) -> None:
        resp = client.get(
            "/api/v1/chart/ict-structure-overlay",
            params={"symbol": "BTCUSDT", "interval": "1h", "sessions": "mars"},
        )
        assert resp.status_code >= 400

    def test_dealing_range_divides_the_last_leg(
        self, client: TestClient, fake_db: _FakeKlineDB
    ) -> None:
        rng = self._get(client)["dealing_range"]
        assert rng is not None, "zigzag fixture must produce two confirmed swings"
        assert rng["low"] < rng["high"]
        # Thirds: the boundaries must sit strictly inside the leg, in order.
        assert (
            rng["low"]
            < rng["discount_to"]
            < rng["equilibrium"]
            < rng["premium_from"]
            < rng["high"]
        )
        assert rng["zone"] in {"discount", "equilibrium", "premium"}
        assert 0.0 <= rng["position"] <= 1.0 or rng["position"] is None
        assert rng["time"] == 1_700_000_000 + rng["bar_index"] * 3600

    def test_dealing_range_thresholds_move_the_boundaries(
        self, client: TestClient, fake_db: _FakeKlineDB
    ) -> None:
        wide = self._get(client)["dealing_range"]
        narrow = self._get(client, premium_threshold=0.95, discount_threshold=0.05)[
            "dealing_range"
        ]
        span = wide["high"] - wide["low"]
        assert (
            abs(
                (narrow["premium_from"] - wide["premium_from"]) - span * (0.95 - 0.6667)
            )
            < 1e-6
        )
        assert (
            abs((narrow["discount_to"] - wide["discount_to"]) - span * (0.05 - 0.3333))
            < 1e-6
        )

    def test_dealing_range_absent_when_unreadable(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Too few bars to confirm a swing pair → null, not a fabricated range."""
        db = _FakeKlineDB(_structure_df(20))
        monkeypatch.setenv("SELENE_KLINES_DB", "unused.db")
        import selene_space_market.kline_db as kline_db_mod

        monkeypatch.setattr(kline_db_mod, "KlineDB", lambda path: db)
        data = client.get(
            "/api/v1/chart/ict-structure-overlay",
            params={"symbol": "BTCUSDT", "interval": "1h"},
        ).json()["data"]
        assert data["dealing_range"] is None
        # The killzone layer still works — it needs only a clock, not swings.
        assert data["killzone"]["clock_source"] == "timestamp"

    def test_bar_echo_off_still_carries_both_layers(
        self, client: TestClient, fake_db: _FakeKlineDB
    ) -> None:
        data = self._get(client)
        assert data["bars"] == []
        assert data["killzone"]["bars"], "killzone rows are not the bar echo"
        assert data["dealing_range"] is not None


class TestSymbolVariants:
    """The klines table stores ``BTC/USDT``; the chart UI carries ``BTCUSDT``."""

    @pytest.mark.parametrize(
        "given, expected",
        [
            ("BTCUSDT", ["BTCUSDT", "BTC/USDT"]),
            ("BTC/USDT", ["BTC/USDT", "BTCUSDT"]),
            ("btc-usdt", ["BTC/USDT", "BTCUSDT"]),
            ("ETH_USDC", ["ETH/USDC", "ETHUSDC"]),
            ("AAPL/USDT", ["AAPL/USDT", "AAPLUSDT"]),
        ],
    )
    def test_variants_cover_both_spellings(
        self, given: str, expected: list[str]
    ) -> None:
        from app.api.v1.chart import _symbol_variants

        assert _symbol_variants(given) == expected

    def test_falls_back_to_slash_spelling(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """UI sends BTCUSDT, DB has only BTC/USDT -> must still resolve."""
        df = _structure_df(60).assign(symbol="BTC/USDT")
        db = _FakeKlineDB(df)
        df_per_symbol = {"BTC/USDT": db._df}

        def _get_klines(exchange, market_type, symbol, interval, start, end):
            db.calls.append((exchange, market_type, symbol, interval))
            frame = df_per_symbol.get(symbol)
            return frame.copy() if frame is not None else pd.DataFrame()

        monkeypatch.setattr(db, "get_klines", _get_klines)
        monkeypatch.setenv("SELENE_KLINES_DB", "unused.db")
        import selene_space_market.kline_db as kline_db_mod

        monkeypatch.setattr(kline_db_mod, "KlineDB", lambda path: db)
        data = client.get(
            "/api/v1/chart/ict-structure-overlay",
            params={"symbol": "BTCUSDT", "interval": "1h"},
        ).json()["data"]

        assert data["symbol"] == "BTC/USDT"
        assert [call[2] for call in db.calls] == ["BTCUSDT", "BTC/USDT"]
