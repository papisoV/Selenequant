"""Tests for selene_space_commodities_fx.data_fetcher.

akshare/network mocked via monkeypatch — no live calls.
"""

from __future__ import annotations

import pandas as pd
import pytest

from selene_space_commodities_fx import data_fetcher as df_mod
from selene_space_commodities_fx.data_fetcher import get_futures_kline, get_fx_quotes


def _minute_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "datetime": ["2024-01-01 09:01:00", "2024-01-01 09:02:00"],
            "open": [1.0, 2.0], "high": [3.0, 4.0], "low": [0.5, 1.5],
            "close": [2.0, 3.0], "volume": [10, 20], "hold": [100, 200],
        }
    )


def _daily_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "日期": ["2024-01-01", "2024-01-02"],
            "开盘价": [1.0, 2.0], "最高价": [3.0, 4.0], "最低价": [0.5, 1.5],
            "收盘价": [2.0, 3.0], "成交量": [10, 20], "持仓量": [100, 200],
        }
    )


def test_minute_kline_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(df_mod, "_fetch_with_retry", lambda fn, **kw: _minute_df())

    df = get_futures_kline("AU0", period="1")
    assert "timestamp" in df.columns
    assert "datetime" not in df.columns
    assert df["symbol"].tolist() == ["AU0", "AU0"]
    assert df["timestamp"].iloc[0] == "2024-01-01 09:01:00"
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume", "hold", "symbol"]


def test_daily_kline_col_map(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(df_mod, "_fetch_with_retry", lambda fn, **kw: _daily_df())

    df = get_futures_kline("AU0", period="1d")
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume", "hold", "symbol"]
    assert df["close"].tolist() == [2.0, 3.0]


def test_kline_empty_returns_empty_df(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(df_mod, "_fetch_with_retry", lambda fn, **kw: None)
    assert get_futures_kline("AU0", period="1").empty


def test_daily_kline_filters_start_end_yyyy_mmdd(monkeypatch: pytest.MonkeyPatch) -> None:
    """MAJOR-5 regression: YYYYMMDD start/end must be normalized to the
    timestamp column's 'YYYY-MM-DD' format before the string filter.

    Pre-fix the lexicographic comparison ``df['timestamp'] >= '20240101'`` is
    always False against 'YYYY-MM-DD HH:MM:SS' values -> every row dropped.
    """
    df = pd.DataFrame(
        {
            "日期": ["2024-01-01", "2024-01-02", "2024-01-03"],
            "开盘价": [1.0, 2.0, 3.0], "最高价": [3.0, 4.0, 5.0], "最低价": [0.5, 1.5, 2.5],
            "收盘价": [2.0, 3.0, 4.0], "成交量": [10, 20, 30], "持仓量": [100, 200, 300],
        }
    )
    monkeypatch.setattr(df_mod, "_fetch_with_retry", lambda fn, **kw: df)

    got = get_futures_kline("AU0", period="1d", start="20240101", end="20240102")
    assert got["timestamp"].tolist() == ["2024-01-01 00:00:00", "2024-01-02 00:00:00"]


def test_minute_kline_filters_start_end_dash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dash-format range still works for minute bars (identity normalize)."""
    df = pd.DataFrame(
        {
            "datetime": ["2024-01-01 09:01:00", "2024-01-02 09:01:00"],
            "open": [1.0, 2.0], "high": [3.0, 4.0], "low": [0.5, 1.5],
            "close": [2.0, 3.0], "volume": [10, 20], "hold": [100, 200],
        }
    )
    monkeypatch.setattr(df_mod, "_fetch_with_retry", lambda fn, **kw: df)

    got = get_futures_kline("AU0", period="1", start="2024-01-01", end="2024-01-01")
    assert got["timestamp"].tolist() == ["2024-01-01 09:01:00"]


def test_fx_quotes_maps_and_concats(monkeypatch: pytest.MonkeyPatch) -> None:
    import akshare

    pair = pd.DataFrame({"货币对": ["AUD/USD"], "买报价": [0.7], "卖报价": [0.71]})
    spot = pd.DataFrame({"货币对": ["USD/CNY"], "买报价": [6.74], "卖报价": [6.75]})
    monkeypatch.setattr(akshare, "fx_pair_quote", lambda: pair)
    monkeypatch.setattr(akshare, "fx_spot_quote", lambda: spot)

    df = get_fx_quotes()
    assert len(df) == 2
    assert list(df.columns) == ["pair", "bid", "ask"]
    assert df["pair"].tolist() == ["AUD/USD", "USD/CNY"]


def test_fx_quotes_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    import akshare

    monkeypatch.setattr(akshare, "fx_pair_quote", lambda: None)
    monkeypatch.setattr(akshare, "fx_spot_quote", lambda: None)
    assert get_fx_quotes().empty
