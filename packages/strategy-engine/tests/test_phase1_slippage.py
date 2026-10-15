"""Phase 1 tests: slippage + double-sided fee in BacktestStrategyTemplate._calculate_pnl.

Verifies that the new cost model (slippage on entry/exit + fee on actual notional)
produces realistic PnL values aligned with V4 TradingCosts defaults.
"""

from __future__ import annotations


import pandas as pd
import pytest

from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent


class _StubStrategy(BacktestStrategyTemplate):
    """Minimal strategy: always emits BUY then SELL on alternating bars."""

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        if len(data) < 2:
            return SignalType.HOLD
        # Alternate: BUY on bar 2, SELL on bar 3, etc.
        if len(data) % 2 == 0:
            return SignalType.BUY
        return SignalType.SELL


def _make_kline(ts_ms: int, price: float, symbol: str = "BTC/USDT") -> KlineEvent:
    return KlineEvent(
        exchange="binance",
        symbol=symbol,
        timestamp=ts_ms,
        interval="15m",
        open=price,
        high=price * 1.001,
        low=price * 0.999,
        close=price,
        volume=100.0,
        closed=True,
    )


class TestSlippagePnL:
    """Test the Phase 1 cost model in _calculate_pnl."""

    def test_zero_slippage_legacy_pnl_formula(self) -> None:
        """With slippage_bps=0 and taker_fee_rate=None, behavior should
        approximate the legacy formula: fee = capital * fee_rate * 2 * frac.
        Exact equivalence only holds when exit_price == entry_price.
        """
        strategy = _StubStrategy(
            symbol="BTC/USDT",
            initial_capital=10_000.0,
            fee_rate=0.0005,
            slippage_bps=0.0,
            taker_fee_rate=None,
        )
        # Force a known position
        strategy._entry_price = 100.0
        strategy._position_fraction_at_entry = 1.0
        strategy._capital = 10_000.0

        # Exit at entry price → zero gross PnL, only fees
        net_pnl = strategy._calculate_pnl(exit_price=100.0, direction="long")
        # Legacy: fee = 10000 * 0.0005 * 2 * 1 = 10.0
        # New: qty = 10000/100 = 100; entry_notional = 100*100 = 10000;
        #      exit_notional = 100*100 = 10000; fee = 20000 * 0.0005 = 10.0
        assert net_pnl == pytest.approx(-10.0, abs=1e-6)

    def test_slippage_reduces_pnl_on_round_trip(self) -> None:
        """A 5bp slippage round-trip should cost ~10bp more than 0 slippage."""
        base_capital = 10_000.0
        entry_price = 100.0
        exit_price = 110.0  # 10% gain

        # No slippage
        s_no_slip = _StubStrategy(
            initial_capital=base_capital, slippage_bps=0.0, taker_fee_rate=0.0
        )
        s_no_slip._entry_price = entry_price
        s_no_slip._position_fraction_at_entry = 1.0
        s_no_slip._capital = base_capital
        pnl_no_slip = s_no_slip._calculate_pnl(exit_price, "long")

        # With 5bp slippage per side, fee = 0
        s_slip = _StubStrategy(
            initial_capital=base_capital, slippage_bps=5.0, taker_fee_rate=0.0
        )
        s_slip._entry_price = entry_price
        s_slip._position_fraction_at_entry = 1.0
        s_slip._capital = base_capital
        pnl_slip = s_slip._calculate_pnl(exit_price, "long")

        # Difference should be approximately 10bp of notional
        qty = base_capital / entry_price  # 100
        expected_slip_cost = entry_price * 5e-4 * qty + exit_price * 5e-4 * qty
        assert (pnl_no_slip - pnl_slip) == pytest.approx(expected_slip_cost, rel=1e-6)

    def test_taker_fee_overrides_fee_rate(self) -> None:
        """When taker_fee_rate is set, it should be used instead of fee_rate."""
        strategy = _StubStrategy(
            initial_capital=10_000.0,
            fee_rate=0.001,        # High fallback
            taker_fee_rate=0.0005,  # Explicit taker
            slippage_bps=0.0,
        )
        strategy._entry_price = 100.0
        strategy._position_fraction_at_entry = 1.0
        strategy._capital = 10_000.0

        # Exit at entry → fee only
        net_pnl = strategy._calculate_pnl(exit_price=100.0, direction="long")
        # With taker_fee_rate=0.0005: fee = (10000 + 10000) * 0.0005 = 10.0
        # If fee_rate=0.001 were used: fee = 20000 * 0.001 = 20.0
        assert net_pnl == pytest.approx(-10.0, abs=1e-6)

    def test_short_direction_slippage(self) -> None:
        """Short position: entry (sell) receives bid, exit (buy) pays ask."""
        strategy = _StubStrategy(
            initial_capital=10_000.0,
            slippage_bps=5.0,
            taker_fee_rate=0.0,  # isolate slippage effect
            long_only=False,
        )
        strategy._entry_price = 100.0
        strategy._position_fraction_at_entry = 1.0
        strategy._capital = 10_000.0

        # Short at 100, cover at 90 → 10% gain
        net_pnl = strategy._calculate_pnl(exit_price=90.0, direction="short")
        # qty = 10000/100 = 100
        # sell_price = 100 * (1 - 5e-4) = 99.95
        # buy_price  = 90  * (1 + 5e-4) = 90.045
        # gross = (99.95 - 90.045) * 100 = 990.5
        # fee = 0
        assert net_pnl == pytest.approx(990.5, rel=1e-6)

    def test_cost_config_in_get_parameters(self) -> None:
        """get_parameters() should expose slippage_bps and taker_fee_rate."""
        strategy = _StubStrategy(
            slippage_bps=2.5, taker_fee_rate=0.0004
        )
        params = strategy.get_parameters()
        assert params["slippage_bps"] == 2.5
        assert params["taker_fee_rate"] == 0.0004

    def test_default_slippage_aligned_with_v4(self) -> None:
        """Default slippage_bps should be 1.5 (V4 TradingCosts default)."""
        strategy = _StubStrategy()
        assert strategy.slippage_bps == 1.5
        assert strategy.taker_fee_rate is None  # falls back to fee_rate


class TestEndToEndBacktestWithSlippage:
    """Verify that run_backtest produces different equity with slippage."""

    def _make_klines(self, n: int = 50) -> list[KlineEvent]:
        """Generate a simple uptrend: price goes from 100 to 110."""
        klines = []
        for i in range(n):
            price = 100.0 + i * 0.2  # linear uptrend
            klines.append(_make_kline(ts_ms=i * 900_000, price=price))
        return klines

    def test_slippage_reduces_final_equity(self) -> None:
        """Backtest with 5bp slippage should end with less equity than 0bp."""
        klines = self._make_klines()

        s_no_slip = _StubStrategy(
            initial_capital=10_000.0,
            slippage_bps=0.0,
            taker_fee_rate=0.0,
            max_bars=100,
        )
        result_no_slip = s_no_slip.run_backtest(klines)

        s_slip = _StubStrategy(
            initial_capital=10_000.0,
            slippage_bps=5.0,
            taker_fee_rate=0.0,
            max_bars=100,
        )
        result_slip = s_slip.run_backtest(klines)

        # With slippage, each trade loses ~10bp of notional; cumulative effect
        assert result_slip.equity_curve[-1] < result_no_slip.equity_curve[-1]
