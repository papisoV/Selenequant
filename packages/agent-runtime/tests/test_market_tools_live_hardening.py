"""Live-path hardening for market tools: A1 snapshot, A2 sentinel, D4 as-of."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from selene_space_agent.tools.market_tools import (
    MarketKlineTool,
    MarketOrderbookTool,
    MarketSnapshotTool,
    MarketTickerTool,
)
from selene_space_types.market import (
    KlineEvent,
    OrderBookL2Snapshot,
    PriceLevel,
    TickerEvent,
)
from selene_space_types.risk import RiskLevel

_TS = 1_710_000_000_000  # 2024-03-10 00:00:00 UTC+8
_PAIR = "SOL/USDT"

_TICKER = TickerEvent(
    exchange="okx",
    symbol=_PAIR,
    timestamp=_TS,
    last_price=148.20,
    bid=148.15,
    ask=148.25,
    volume_24h=987_654.32,
    change_24h_pct=-1.75,
)

_KLINE = KlineEvent(
    exchange="okx",
    symbol=_PAIR,
    timestamp=_TS,
    interval="4h",
    open=147.0,
    high=149.5,
    low=146.5,
    close=148.2,
    volume=12_500.0,
    closed=True,
)

_ORDERBOOK = OrderBookL2Snapshot(
    exchange="okx",
    symbol=_PAIR,
    timestamp=_TS,
    sequence_id=77,
    bids=[PriceLevel(price=148.0, quantity=4.0)],
    asks=[PriceLevel(price=148.3, quantity=3.0)],
)


class _Stub:
    def __init__(
        self, *, ticker: object = None, kline: object = None, book: object = None
    ):
        self._t, self._k, self._b = ticker, kline, book

    def get_ticker(self, symbol: str) -> object:
        return self._t

    def get_kline(self, symbol: str) -> object:
        return self._k

    def get_orderbook(self, symbol: str) -> object:
        return self._b


# ---------------------------------------------------------------------------
# A2: sentinel on recoverable miss paths
# ---------------------------------------------------------------------------


class TestA2SentinelMissPaths:
    def test_ticker_miss_emits_sentinel(self) -> None:
        out = MarketTickerTool(_Stub()).execute({"symbol": _PAIR})
        assert out.error == ""
        assert "NO_DATA_AVAILABLE" in out.output
        assert "ticker" in out.output
        assert f"{_PAIR}" in out.output

    def test_kline_miss_emits_sentinel(self) -> None:
        out = MarketKlineTool(_Stub()).execute({"symbol": _PAIR})
        assert out.error == ""
        assert "NO_DATA_AVAILABLE" in out.output

    def test_orderbook_miss_emits_sentinel(self) -> None:
        out = MarketOrderbookTool(_Stub()).execute({"symbol": _PAIR})
        assert out.error == ""
        assert "NO_DATA_AVAILABLE" in out.output

    def test_missing_param_still_uses_error_channel(self) -> None:
        # 程序性缺参走 error 通道（react.py 只给 LLM 看 error 串），A2 不碰
        out = MarketTickerTool(_Stub(ticker=_TICKER)).execute({})
        assert out.error is not None
        assert "symbol" in out.error


# ---------------------------------------------------------------------------
# D4: data-as-of notice on success paths
# ---------------------------------------------------------------------------


class TestD4AsOfNotice:
    def test_ticker_hit_has_as_of(self) -> None:
        out = MarketTickerTool(_Stub(ticker=_TICKER)).execute({"symbol": _PAIR})
        assert "数据截至: 2024-03-10 00:00:00 (UTC+8)" in out.output

    def test_kline_hit_has_as_of(self) -> None:
        out = MarketKlineTool(_Stub(kline=_KLINE)).execute({"symbol": _PAIR})
        assert "数据截至:" in out.output

    def test_orderbook_hit_has_as_of(self) -> None:
        out = MarketOrderbookTool(_Stub(book=_ORDERBOOK)).execute({"symbol": _PAIR})
        assert "数据截至:" in out.output

    def test_zero_timestamp_reports_unknown(self) -> None:
        stale = _TICKER.model_copy(update={"timestamp": 0})
        out = MarketTickerTool(_Stub(ticker=stale)).execute({"symbol": _PAIR})
        assert "数据截至: 未知（数据源未带时间戳）" in out.output


# ---------------------------------------------------------------------------
# A1: MarketSnapshotTool
# ---------------------------------------------------------------------------


def _frame(n: int = 800, base: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": [_TS + i * 3_600_000 for i in range(n)],
            "open": [base] * n,
            "high": [base + 1] * n,
            "low": [base - 1] * n,
            "close": [base + 0.5] * n,
            "volume": [10.0] * n,
        }
    )


class TestA1SnapshotTool:
    def test_safe_risk_level(self) -> None:
        assert MarketSnapshotTool().risk_level == RiskLevel.SAFE

    def test_normal_path_lists_indicators_and_as_of(self) -> None:
        with patch("selene_space_market.kline_db.KlineDB") as fake_db:
            fake_db.return_value.get_klines.return_value = _frame()
            out = MarketSnapshotTool().execute({"symbol": _PAIR})
        assert not out.error
        assert "SMA20" in out.output
        assert "RSI14" in out.output
        assert "数据截至:" in out.output
        assert "N/A" not in out.output

    def test_indicator_failure_degrades_to_na(self) -> None:
        tool = MarketSnapshotTool()
        # closes as non-numeric forces every pandas-based indicator to raise
        # (Bars span is len-only and cannot fail → ≥11 of 12 must degrade)
        broken = pd.DataFrame({"close": ["x"] * 60, "timestamp": [1] * 60})
        out = tool._indicators(broken["close"])
        assert len(out) == tool._INDICATOR_COUNT
        assert sum("N/A (" in ln for ln in out) >= 11

    def test_empty_partition_emits_sentinel(self) -> None:
        with patch("selene_space_market.kline_db.KlineDB") as fake_db:
            fake_db.return_value.get_klines.return_value = pd.DataFrame()
            out = MarketSnapshotTool().execute({"symbol": _PAIR})
        assert out.error == ""
        assert "NO_DATA_AVAILABLE" in out.output

    def test_query_failure_emits_sentinel(self) -> None:
        with patch("selene_space_market.kline_db.KlineDB") as fake_db:
            fake_db.return_value.get_klines.side_effect = RuntimeError("db locked")
            out = MarketSnapshotTool().execute({"symbol": _PAIR})
        assert out.error == ""
        assert "NO_DATA_AVAILABLE" in out.output
        assert "db locked" in out.output

    def test_missing_param_uses_error_channel(self) -> None:
        out = MarketSnapshotTool().execute({})
        assert out.error is not None

    def test_import_failure_emits_sentinel(self) -> None:
        import builtins

        real_import = builtins.__import__

        def _block(name: str, *a: object, **k: object) -> object:
            if name == "selene_space_market.kline_db":
                raise ImportError("blocked")
            return real_import(name, *a, **k)  # type: ignore[arg-type]

        with patch("builtins.__import__", side_effect=_block):
            out = MarketSnapshotTool().execute({"symbol": _PAIR})
        assert out.error == ""
        assert "NO_DATA_AVAILABLE" in out.output
