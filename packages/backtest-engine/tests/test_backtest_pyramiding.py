"""Tests for multi-symbol backtest P1-4 Step 1 — ``allow_pyramiding`` flag.

Covers:
  - Default behavior (allow_pyramiding=False) keeps legacy single-position
    semantics: a same-direction signal on an existing position is ignored.
  - Pyramiding mode (allow_pyramiding=True) allows same-direction adds and
    tracks a weighted-average entry price per symbol.
  - Round-trip PnL uses the weighted-average entry price after pyramiding.
"""

from __future__ import annotations

import pytest

from selene_space_backtest.commissions import NoCommission
from selene_space_backtest.engine import BacktestConfig, BacktestEngine
from selene_space_backtest.slippage import NoSlippage
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide

# Suppress overflow warnings from metrics.py annual_return on the very
# short test windows here (4 bars with large returns → annualized powers
# exceed float64 range). The feature under test is entry-price tracking,
# not metrics correctness; metrics are validated in test_backtest_metrics.
pytestmark = pytest.mark.filterwarnings(
    "ignore:overflow encountered in scalar power:RuntimeWarning"
)


class _ScriptedStrategy:
    """Replays a precomputed list of signals, one per bar."""

    def __init__(self, sides: list[OrderSide]) -> None:
        self._sides = list(sides)
        self._idx = 0

    def on_kline(self, event: KlineEvent) -> Signal | None:
        if self._idx >= len(self._sides):
            return None
        side = self._sides[self._idx]
        self._idx += 1
        return Signal(
            strategy_id="test",
            symbol=event.symbol,
            side=side,
            strength=1.0,
            timestamp=event.timestamp,
            reason="scripted",
        )

    def reset(self) -> None:
        self._idx = 0


def _make_klines(prices: list[float], symbol: str = "BTC/USDT") -> list[KlineEvent]:
    return [
        KlineEvent(
            exchange="backtest",
            symbol=symbol,
            timestamp=1711000000000 + i * 3600_000,
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


def _basic_config(allow_pyramiding: bool) -> BacktestConfig:
    return BacktestConfig(
        initial_cash=10_000.0,
        commission=NoCommission(),
        slippage=NoSlippage(),
        position_sizing="fixed",
        allow_pyramiding=allow_pyramiding,
    )


class TestLegacyNoPyramiding:
    def test_same_direction_signal_ignored_when_pyramiding_disabled(self) -> None:
        """Two BUY signals in a row → only the first opens a position.

        Without allow_pyramiding, the second BUY signal arrives while
        current_qty > 0 and does not match the close branch, so it is
        silently dropped (legacy single-position behavior).
        """
        klines = _make_klines([100.0, 110.0, 120.0, 130.0])
        # BUY on bar0 (fill at bar1 open=110), BUY on bar1 (ignored), SELL on bar2 (fill at bar3 open=130)
        strategy = _ScriptedStrategy([OrderSide.BUY, OrderSide.BUY, OrderSide.SELL])
        engine = BacktestEngine(config=_basic_config(allow_pyramiding=False))
        result = engine.run(strategy, klines)

        # Exactly one round-trip trade recorded (open + close, no add).
        assert result.trades_count == 1
        trade = result.trades[0]
        assert trade["entry_price"] == 110.0
        assert trade["exit_price"] == 130.0


class TestPyramiding:
    def test_pyramiding_adds_to_existing_long(self) -> None:
        """Two BUY signals both execute; the entry price is the weighted avg.

        BUY at bar0 → fill at bar1 open=100. BUY at bar1 → fill at bar2
        open=110 (smaller qty because most cash is already in position).
        SELL at bar2 → fill at bar3 open=120. Entry price is the
        weighted average of 100 (large qty) and 110 (small qty), so it
        sits close to 100 with a small bump toward 110.
        """
        klines = _make_klines([100.0, 100.0, 110.0, 120.0])
        strategy = _ScriptedStrategy([OrderSide.BUY, OrderSide.BUY, OrderSide.SELL])
        engine = BacktestEngine(config=_basic_config(allow_pyramiding=True))
        result = engine.run(strategy, klines)

        assert result.trades_count == 1
        trade = result.trades[0]
        # Weighted avg of 100 (qty1=95) and 110 (qty2) sits close to 100.
        # 跳空修复(隐形杠杆批)后:第二腿 sizing=4.75 units 但成交时按
        # 实际价 110 cap → affordable = 500/110 ≈ 4.5455 units(旧语义
        # 现金打负硬买 4.75)。
        # Weighted entry = (100*95 + 110*4.5455) / 99.5455 ≈ 100.4566
        # trade["entry_price"] is Decimal (H19) — compare in float domain.
        assert float(trade["entry_price"]) == pytest.approx(100.45662, rel=1e-4)
        assert float(trade["exit_price"]) == 120.0
        # PnL must be positive (exit above weighted entry).
        assert trade["pnl"] > 0

    def test_pyramiding_weighted_average_entry_price(self) -> None:
        """When add quantity differs from initial, entry price is weighted."""
        # Larger initial_cash so the second add can still afford a meaningful
        # position even after the first fill deploys most of the cash.
        # initial_cash=1_000_000 lets both fills use nearly the full sizing
        # at their respective bar's close (100 and 200).
        klines = _make_klines([100.0, 100.0, 200.0, 300.0])
        strategy = _ScriptedStrategy([OrderSide.BUY, OrderSide.BUY, OrderSide.SELL])
        config = BacktestConfig(
            initial_cash=100_000.0,
            commission=NoCommission(),
            slippage=NoSlippage(),
            position_sizing="fixed",
            allow_pyramiding=True,
        )
        engine = BacktestEngine(config=config)
        result = engine.run(strategy, klines)

        assert result.trades_count == 1
        trade = result.trades[0]
        # First fill at open=100, qty1 = 100000/100*0.95 = 950
        # After fill1: cash = 100000 - 95000 = 5000
        # Second signal at bar1 close=100, sizing qty2 = 5000/100*0.95 = 47.5
        # 跳空修复(隐形杠杆批)后:第二腿成交时按实际成交价 200 cap,
        # affordable = 5000/200 = 25 units(旧语义借隐形杠杆买 47.5)。
        # Weighted entry = (100*950 + 200*25) / (950+25)
        #                = (95000 + 5000) / 975 ≈ 102.5641
        # trade["entry_price"] is Decimal (H19) — compare in float domain.
        assert float(trade["entry_price"]) == pytest.approx(102.5641, rel=1e-4)

    def test_pyramiding_short_side(self) -> None:
        """Two SELL signals both execute on the short side."""
        klines = _make_klines([100.0, 100.0, 90.0, 80.0])
        strategy = _ScriptedStrategy([OrderSide.SELL, OrderSide.SELL, OrderSide.BUY])
        engine = BacktestEngine(config=_basic_config(allow_pyramiding=True))
        result = engine.run(strategy, klines)

        assert result.trades_count == 1
        trade = result.trades[0]
        assert trade["side"] == "short"
        # qty1 = 10000/100*0.95 = 95 (fill at bar1 open=100, entry_price1=100)
        # After short sell, cash grows by 95*100 = 9500 → cash=19500
        # qty2 = 19500/100*0.95 = 185.25 (fill at bar2 open=90, entry_price2=90)
        # Weighted entry = (100*95 + 90*185.25) / (95+185.25)
        #               = (9500 + 16672.5) / 280.25 ≈ 93.45
        # trade["entry_price"] is Decimal (H19) — compare in float domain.
        assert float(trade["entry_price"]) == pytest.approx(93.45, rel=1e-3)
        assert float(trade["exit_price"]) == 80.0
        # Short PnL: (entry - exit) * total_qty — positive (entry > exit)
        assert trade["pnl"] > 0

    def test_pyramiding_reverse_signal_closes_position(self) -> None:
        """A reverse signal (SELL against existing BUY) closes, not pyramids."""
        klines = _make_klines([100.0, 110.0, 120.0])
        strategy = _ScriptedStrategy([OrderSide.BUY, OrderSide.SELL])
        engine = BacktestEngine(config=_basic_config(allow_pyramiding=True))
        result = engine.run(strategy, klines)

        assert result.trades_count == 1
        trade = result.trades[0]
        assert trade["entry_price"] == 110.0
        assert trade["exit_price"] == 120.0

    def test_pyramiding_disabled_ignores_add_then_reverse_closes(self) -> None:
        """Sanity: without pyramiding, the add signal is dropped and the
        reverse signal closes the original position at the original entry."""
        klines = _make_klines([100.0, 100.0, 110.0, 120.0])
        strategy = _ScriptedStrategy([OrderSide.BUY, OrderSide.BUY, OrderSide.SELL])
        engine = BacktestEngine(config=_basic_config(allow_pyramiding=False))
        result = engine.run(strategy, klines)

        assert result.trades_count == 1
        trade = result.trades[0]
        # Second BUY was ignored → entry stays at first fill (bar1 open=100).
        # SELL signal on bar2 → fill at bar3 open=120.
        assert trade["entry_price"] == 100.0
        assert trade["exit_price"] == 120.0


class TestMultiSymbolState:
    def test_entries_tracked_per_symbol(self) -> None:
        """Sanity: engine can hold separate entries for different symbols
        via the dict-based state (exercised in Step 2 with multi-symbol
        data; here we verify that with a single-symbol run, the entry
        state for that symbol tracks correctly and persists across bars)."""
        klines = _make_klines([100.0, 110.0, 120.0])
        strategy = _ScriptedStrategy([OrderSide.BUY, OrderSide.SELL])
        engine = BacktestEngine(config=_basic_config(allow_pyramiding=False))
        result = engine.run(strategy, klines)

        # Just verify a clean round-trip — this exercises the dict-based
        # _open_entries path that replaced the legacy 4 scalars.
        assert result.trades_count == 1
        assert result.trades[0]["entry_price"] == 110.0
        assert result.trades[0]["exit_price"] == 120.0
