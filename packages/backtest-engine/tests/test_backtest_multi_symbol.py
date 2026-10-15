"""Tests for P1-4 Step 2 — multi-symbol backtest run() data flow.

Covers:
  - run() accepts dict[str, list[KlineEvent]] input and merges by timestamp
  - Multiple symbols' signals are routed to the correct portfolio position
  - Entries are tracked per-symbol (no cross-symbol state bleed)
  - Backward compat: single-symbol list[KlineEvent] input still works
  - Backward compat: single-symbol DataFrame input still works
"""

from __future__ import annotations

import pytest

from selene_space_backtest.commissions import NoCommission
from selene_space_backtest.engine import BacktestConfig, BacktestEngine
from selene_space_backtest.slippage import NoSlippage
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide


class _PerSymbolStrategy:
    """Stateless strategy: BUY on first bar seen for each symbol, SELL on second.

    Tracks per-symbol bar counts so each symbol is handled independently.
    Demonstrates that the engine routes klines to the strategy correctly
    and that strategy state can be per-symbol.

    Declares quantity via signal_quantity sizing: the test target is
    signal routing, not sizing; a shared cash pool with all-in sizing
    would let only the first symbol afford a position (post-fix cash cap).
    """

    QUANTITY = 10.0

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def on_kline(self, event: KlineEvent) -> Signal | None:
        count = self._counts.get(event.symbol, 0) + 1
        self._counts[event.symbol] = count
        if count == 1:
            return Signal(
                strategy_id="multi",
                symbol=event.symbol,
                side=OrderSide.BUY,
                strength=1.0,
                timestamp=event.timestamp,
                reason="open",
                quantity=self.QUANTITY,
            )
        if count == 2:
            return Signal(
                strategy_id="multi",
                symbol=event.symbol,
                side=OrderSide.SELL,
                strength=1.0,
                timestamp=event.timestamp,
                reason="close",
                quantity=self.QUANTITY,
            )
        return None

    def reset(self) -> None:
        self._counts = {}


def _make_klines(
    prices: list[float], symbol: str, start_ts: int = 1711000000000
) -> list[KlineEvent]:
    return [
        KlineEvent(
            exchange="backtest",
            symbol=symbol,
            timestamp=start_ts + i * 3600_000,
            interval="1h",
            open=p,
            high=p * 1.01,
            low=p * 0.99,
            close=p,
            volume=100.0,
            closed=True,
        )
        for i, p in enumerate(prices)
    ]


def _basic_config() -> BacktestConfig:
    return BacktestConfig(
        initial_cash=100_000.0,
        commission=NoCommission(),
        slippage=NoSlippage(),
        position_sizing="signal_quantity",
    )


class TestMultiSymbolRun:
    def test_dict_input_merges_by_timestamp(self) -> None:
        """Two symbols with overlapping timestamps → merged kline list.

        BTC prices: [100, 110, 120]
        ETH prices: [50, 55, 60]
        Both start at same ts. Engine should interleave them: BTC-0, ETH-0,
        BTC-1, ETH-1, BTC-2, ETH-2 (or similar stable order).

        Strategy BUYs each symbol on first sighting, SELLs on second.
        Result: 2 round-trip trades, one per symbol, no cross-contamination.
        """
        btc = _make_klines([100.0, 110.0, 120.0], "BTC/USDT")
        eth = _make_klines([50.0, 55.0, 60.0], "ETH/USDT")
        engine = BacktestEngine(config=_basic_config())
        result = engine.run(_PerSymbolStrategy(), {"BTC/USDT": btc, "ETH/USDT": eth})

        # Each symbol produces one round-trip (BUY then SELL).
        assert result.trades_count == 2
        symbols_in_trades = {t["symbol"] for t in result.trades}
        assert symbols_in_trades == {"BTC/USDT", "ETH/USDT"}

    def test_per_symbol_entry_state_no_bleed(self) -> None:
        """Entries for symbol A do not affect symbol B's entry price.

        BTC: BUY at bar0 (fill open=110), SELL at bar1 (fill open=120)
        ETH: BUY at bar0 (fill open=55), SELL at bar1 (fill open=60)

        Each trade's entry_price must match its own symbol's fill, not the
        other symbol's. (Next-bar-open fill model: bar0 signal → bar1 open.)
        """
        btc = _make_klines([100.0, 110.0, 120.0], "BTC/USDT")
        eth = _make_klines([50.0, 55.0, 60.0], "ETH/USDT")
        engine = BacktestEngine(config=_basic_config())
        result = engine.run(_PerSymbolStrategy(), {"BTC/USDT": btc, "ETH/USDT": eth})

        for trade in result.trades:
            if trade["symbol"] == "BTC/USDT":
                assert trade["entry_price"] == 110.0  # bar0 signal → bar1 open
                assert trade["exit_price"] == 120.0  # bar1 signal → bar2 open
            elif trade["symbol"] == "ETH/USDT":
                assert trade["entry_price"] == 55.0
                assert trade["exit_price"] == 60.0

    def test_staggered_timestamps_merge_in_order(self) -> None:
        """BTC ts=0,2,4 / ETH ts=1,3,5 → interleaved correctly.

        With staggered timestamps, BTC signals fire before ETH at each
        position. Strategy still sees each symbol's bars in order.
        """
        btc = _make_klines([100.0, 110.0, 120.0], "BTC/USDT", start_ts=0)
        eth = _make_klines([50.0, 55.0, 60.0], "ETH/USDT", start_ts=1_000_000)
        engine = BacktestEngine(config=_basic_config())
        result = engine.run(_PerSymbolStrategy(), {"BTC/USDT": btc, "ETH/USDT": eth})

        assert result.trades_count == 2
        # BTC round-trip: entry=100 (bar0 fill at bar1 open=110? No — next-bar
        # open fill). With staggered ts, BTC bar0 fill at BTC bar1 open=110.
        btc_trade = next(t for t in result.trades if t["symbol"] == "BTC/USDT")
        assert btc_trade["entry_price"] == 110.0  # bar0 signal → bar1 open=110
        assert btc_trade["exit_price"] == 120.0  # bar1 signal → bar2 open=120

    def test_single_symbol_list_input_still_works(self) -> None:
        """Backward compat: list[KlineEvent] input for a single symbol."""
        klines = _make_klines([100.0, 110.0, 120.0], "BTC/USDT")
        engine = BacktestEngine(config=_basic_config())
        result = engine.run(_PerSymbolStrategy(), klines)
        assert result.trades_count == 1
        assert result.trades[0]["symbol"] == "BTC/USDT"

    def test_single_symbol_dataframe_input_still_works(self) -> None:
        """Backward compat: pd.DataFrame input for a single symbol."""
        import pandas as pd

        df = pd.DataFrame(
            [
                {
                    "timestamp": 0,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 100.0,
                    "symbol": "BTC/USDT",
                },
                {
                    "timestamp": 1_000_000,
                    "open": 110.0,
                    "high": 111.0,
                    "low": 109.0,
                    "close": 110.0,
                    "volume": 100.0,
                    "symbol": "BTC/USDT",
                },
                {
                    "timestamp": 2_000_000,
                    "open": 120.0,
                    "high": 121.0,
                    "low": 119.0,
                    "close": 120.0,
                    "volume": 100.0,
                    "symbol": "BTC/USDT",
                },
            ]
        )
        engine = BacktestEngine(config=_basic_config())
        result = engine.run(_PerSymbolStrategy(), df)
        assert result.trades_count == 1
        assert result.trades[0]["symbol"] == "BTC/USDT"

    def test_empty_dict_raises(self) -> None:
        """An empty dict input must raise BacktestError."""
        engine = BacktestEngine(config=_basic_config())
        with pytest.raises(Exception, match="No kline data"):
            engine.run(_PerSymbolStrategy(), {})

    def test_three_symbols_independent(self) -> None:
        """Three symbols each produce their own round-trip trade."""
        btc = _make_klines([100.0, 110.0, 120.0], "BTC/USDT")
        eth = _make_klines([50.0, 55.0, 60.0], "ETH/USDT")
        sol = _make_klines([10.0, 11.0, 12.0], "SOL/USDT")
        engine = BacktestEngine(config=_basic_config())
        result = engine.run(
            _PerSymbolStrategy(),
            {"BTC/USDT": btc, "ETH/USDT": eth, "SOL/USDT": sol},
        )
        assert result.trades_count == 3
        syms = {t["symbol"] for t in result.trades}
        assert syms == {"BTC/USDT", "ETH/USDT", "SOL/USDT"}


class TestBenchmarkSymbol:
    def test_multi_symbol_buy_hold_empty_without_benchmark(self) -> None:
        """Default: multi-symbol run produces no Buy & Hold curve."""
        btc = _make_klines([100.0, 110.0, 120.0], "BTC/USDT")
        eth = _make_klines([50.0, 55.0, 60.0], "ETH/USDT")
        engine = BacktestEngine(config=_basic_config())
        result = engine.run(_PerSymbolStrategy(), {"BTC/USDT": btc, "ETH/USDT": eth})
        assert result.buy_hold_curve == []

    def test_multi_symbol_benchmark_symbol_produces_curve(self) -> None:
        """benchmark_symbol="BTC/USDT" → Buy & Hold curve uses BTC closes."""
        btc = _make_klines([100.0, 110.0, 120.0, 130.0], "BTC/USDT")
        eth = _make_klines([50.0, 55.0, 60.0, 65.0], "ETH/USDT")
        config = BacktestConfig(
            initial_cash=100_000.0,
            commission=NoCommission(),
            slippage=NoSlippage(),
            position_sizing="fixed",
            benchmark_symbol="BTC/USDT",
        )
        engine = BacktestEngine(config=config)
        result = engine.run(_PerSymbolStrategy(), {"BTC/USDT": btc, "ETH/USDT": eth})
        # Buy & Hold curve should have len == number of BTC bars (4).
        assert len(result.buy_hold_curve) == 4
        # First point = initial equity (100k * BTC_first_close / BTC_first_close).
        assert result.buy_hold_curve[0] == 100_000.0
        # Second point = 100k * 110 / 100 = 110k.
        assert result.buy_hold_curve[1] == 110_000.0
        # Third point = 100k * 120 / 100 = 120k.
        assert result.buy_hold_curve[2] == 120_000.0

    def test_benchmark_symbol_ignored_for_single_symbol(self) -> None:
        """For a single-symbol run, benchmark_symbol is redundant but harmless —
        the symbol itself is the only candidate."""
        klines = _make_klines([100.0, 110.0, 120.0], "BTC/USDT")
        config = BacktestConfig(
            initial_cash=100_000.0,
            commission=NoCommission(),
            slippage=NoSlippage(),
            position_sizing="fixed",
            benchmark_symbol="BTC/USDT",
        )
        engine = BacktestEngine(config=config)
        result = engine.run(_PerSymbolStrategy(), klines)
        # Single-symbol: buy_hold_curve uses the symbol's closes regardless.
        assert len(result.buy_hold_curve) == 3
        assert result.buy_hold_curve[0] == 100_000.0
