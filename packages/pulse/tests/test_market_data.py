from __future__ import annotations

import sys
import types
from datetime import date

import pandas as pd
import pytest

from ai_market_pulse.market_data import (
    MarketDataError,
    _a_share_code,
    _fetch_akshare,
    _fetch_baostock,
    _fetch_tushare,
    _fetch_yfinance,
    _normalize_history,
    _snapshot,
)
from ai_market_pulse.models import Asset


def test_a_share_code_detects_common_symbols() -> None:
    assert _a_share_code(Asset(symbol="600519.SS")) == "600519"
    assert _a_share_code(Asset(symbol="000001.SZ")) == "000001"
    assert _a_share_code(Asset(symbol="300750", market="CN")) == "300750"
    assert _a_share_code(Asset(symbol="AAPL", market="US")) is None


def test_normalize_history_accepts_ohlc_and_builds_snapshot() -> None:
    raw = pd.DataFrame(
        [
            {"Date": "2026-01-02", "Open": "10", "High": "12", "Low": "9", "Close": "11", "Volume": "100"},
            {"Date": "2026-01-03", "Open": "11", "High": "13", "Low": "10", "Close": "12", "Volume": "120"},
        ]
    )

    history = _normalize_history(raw, "TEST")
    snapshot = _snapshot("TEST", "Test Asset", "USD", history, source="unit")

    assert list(history.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]
    assert snapshot.last_close == 12
    assert snapshot.change_pct == 0.090909
    assert snapshot.source == "unit"


def test_fetch_yfinance_widens_calendar_window_and_tails(monkeypatch) -> None:
    calls: dict[str, str] = {}

    class FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def history(self, period: str, auto_adjust: bool) -> pd.DataFrame:
            calls["period"] = period
            rows = 300
            index = pd.date_range("2024-01-01", periods=rows, freq="B")
            index.name = "Date"
            close = [float(i + 1) for i in range(rows)]
            return pd.DataFrame(
                {"Open": close, "High": close, "Low": close, "Close": close, "Volume": [100] * rows},
                index=index,
            )

        @property
        def fast_info(self) -> dict:
            return {}

        @property
        def info(self) -> dict:
            return {"shortName": "Fake Co", "currency": "USD"}

    fake_module = types.ModuleType("yfinance")
    fake_module.Ticker = FakeTicker  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "yfinance", fake_module)

    asset, snapshot, history = _fetch_yfinance(Asset(symbol="AAA"), 220)

    # yfinance period must be widened well beyond the 220 trading-day target.
    assert int(calls["period"].rstrip("d")) > 300
    # history is trimmed back to the trading-day target, which is enough for SMA200.
    assert len(history) == 220
    assert snapshot.source == "yfinance"
    assert asset.currency == "USD"


def _fake_a_share_ohlc_rows(rows: int = 100) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=rows, freq="B")
    close = [float(i + 1) for i in range(rows)]
    return dates, close


def test_fetch_akshare_happy_path_returns_snapshot(monkeypatch) -> None:
    calls: dict[str, str] = {}
    dates, close = _fake_a_share_ohlc_rows()

    def fake_stock_zh_a_hist(symbol, period, start_date, end_date, adjust):
        calls["symbol"] = symbol
        calls["start_date"] = start_date
        calls["end_date"] = end_date
        return pd.DataFrame(
            {
                "日期": dates,
                "开盘": close,
                "最高": [c + 1 for c in close],
                "最低": [c - 1 for c in close],
                "收盘": close,
                "成交量": [100] * len(close),
            }
        )

    fake_module = types.ModuleType("akshare")
    fake_module.stock_zh_a_hist = fake_stock_zh_a_hist  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "akshare", fake_module)

    asset, snapshot, history = _fetch_akshare(Asset(symbol="600519.SS"), 60)

    assert calls["symbol"] == "600519"
    assert snapshot.symbol == "600519.SS"
    assert snapshot.source == "akshare"
    assert asset.currency == "CNY"
    assert len(history) == 60


def test_fetch_akshare_as_of_controls_fetch_window(monkeypatch) -> None:
    calls: dict[str, str] = {}
    dates, close = _fake_a_share_ohlc_rows()

    def fake_stock_zh_a_hist(symbol, period, start_date, end_date, adjust):
        calls["start_date"] = start_date
        calls["end_date"] = end_date
        return pd.DataFrame(
            {
                "日期": dates,
                "开盘": close,
                "最高": [c + 1 for c in close],
                "最低": [c - 1 for c in close],
                "收盘": close,
                "成交量": [100] * len(close),
            }
        )

    fake_module = types.ModuleType("akshare")
    fake_module.stock_zh_a_hist = fake_stock_zh_a_hist  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "akshare", fake_module)

    fixed_as_of = date(2020, 1, 15)
    _fetch_akshare(Asset(symbol="600519.SS"), 60, fixed_as_of)

    assert calls["end_date"] == fixed_as_of.strftime("%Y%m%d")
    assert calls["start_date"] != date.today().strftime("%Y%m%d")


def test_fetch_baostock_happy_path_returns_snapshot(monkeypatch) -> None:
    dates, close = _fake_a_share_ohlc_rows()
    rows = [
        [str(d.date()), str(c), str(c + 1), str(c - 1), str(c), "100"] for d, c in zip(dates, close)
    ]

    class FakeQuery:
        def __init__(self, rows: list[list[str]]) -> None:
            self._rows = list(rows)

        def next(self) -> bool:
            return bool(self._rows)

        def get_row_data(self) -> list[str]:
            return self._rows.pop(0)

    class FakeLoginResult:
        error_code = "0"
        error_msg = ""

    logout_calls = {"count": 0}

    fake_module = types.ModuleType("baostock")
    fake_module.login = lambda: FakeLoginResult()  # type: ignore[attr-defined]
    fake_module.logout = lambda: logout_calls.update(count=logout_calls["count"] + 1)  # type: ignore[attr-defined]
    fake_module.query_history_k_data_plus = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: FakeQuery(rows)
    )
    monkeypatch.setitem(sys.modules, "baostock", fake_module)

    asset, snapshot, history = _fetch_baostock(Asset(symbol="600519.SS"), 60)

    assert snapshot.symbol == "600519.SS"
    assert snapshot.source == "baostock"
    assert asset.currency == "CNY"
    assert logout_calls["count"] == 1


def test_fetch_baostock_login_failure_raises_market_data_error(monkeypatch) -> None:
    class FakeLoginResult:
        error_code = "10001001"
        error_msg = "login failed"

    fake_module = types.ModuleType("baostock")
    fake_module.login = lambda: FakeLoginResult()  # type: ignore[attr-defined]
    fake_module.logout = lambda: None  # type: ignore[attr-defined]
    fake_module.query_history_k_data_plus = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "baostock", fake_module)

    with pytest.raises(MarketDataError, match="Baostock login failed"):
        _fetch_baostock(Asset(symbol="600519.SS"), 60)


def test_fetch_tushare_happy_path_returns_snapshot(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TUSHARE_TOKEN", "fake-token")
    dates, close = _fake_a_share_ohlc_rows()
    calls: dict[str, str] = {}

    class FakePro:
        def daily(self, ts_code, start_date, end_date):
            calls["ts_code"] = ts_code
            calls["start_date"] = start_date
            calls["end_date"] = end_date
            return pd.DataFrame(
                {
                    "trade_date": [d.strftime("%Y%m%d") for d in dates],
                    "open": close,
                    "high": [c + 1 for c in close],
                    "low": [c - 1 for c in close],
                    "close": close,
                    "vol": [100] * len(close),
                }
            )

    fake_module = types.ModuleType("tushare")
    fake_module.pro_api = lambda token: FakePro()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tushare", fake_module)

    asset, snapshot, history = _fetch_tushare(Asset(symbol="600519.SS"), 60)

    assert calls["ts_code"] == "600519.SH"
    assert snapshot.symbol == "600519.SS"
    assert snapshot.source == "tushare"
    assert asset.currency == "CNY"


def test_fetch_tushare_missing_token_raises_market_data_error(monkeypatch) -> None:
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    with pytest.raises(MarketDataError, match="TUSHARE_TOKEN"):
        _fetch_tushare(Asset(symbol="600519.SS"), 60)
