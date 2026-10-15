"""Tests for funding-rate cost in backtest PnL (P0-1 cost model).

Verifies that ``funding_rate_apy`` is threaded from StrategyOptimizer into
the backtest strategy and that ``_calculate_pnl`` deducts a holding-period
funding cost proportional to notional * hold_days/365 * APY.
"""

from __future__ import annotations

import pytest

from selene_space_strategy.optimizer import StrategyOptimizer
from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent


class BuySellStrategy(BacktestStrategyTemplate):
    """BUY on first bar, SELL on bar 30 — deterministic entry/exit for PnL tests."""

    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        super().__init__(**kwargs)

    def get_signal(self, data) -> SignalType:  # noqa: ANN001
        # on_kline skips the first bar (len < 2), so signals land one bar later:
        # BUY at bar 1 (n=2), SELL at bar 30 (n=31).
        n = len(data)
        if n == 2:
            return SignalType.BUY
        if n == 31:
            return SignalType.SELL
        return SignalType.HOLD


def generate_flat_klines(n: int = 60, start_price: float = 100.0) -> list[KlineEvent]:
    """Flat, zero-volatility 1h klines starting at timestamp 0."""
    klines: list[KlineEvent] = []
    for i in range(n):
        klines.append(
            KlineEvent(
                exchange="binance",
                symbol="BTC/USDT",
                timestamp=i * 3600000,
                interval="1h",
                open=start_price,
                high=start_price,
                low=start_price,
                close=start_price,
                volume=1000.0,
                closed=True,
            )
        )
    return klines


class TestFundingCost:
    def test_apy_zero_no_deduction(self) -> None:
        """With funding_rate_apy=0, flat market yields zero net PnL."""
        strat = BuySellStrategy(
            initial_capital=100000.0,
            slippage_bps=0.0,
            fee_rate=0.0,
            funding_rate_apy=0.0,
        )
        result = strat.run_backtest(generate_flat_klines())
        assert result.total_return == pytest.approx(0.0, abs=1e-9)

    def test_apy_positive_reduces_return(self) -> None:
        """Funding APY 3.65 (365%) on a ~1.2-day hold costs ~1.2% of notional."""
        strat = BuySellStrategy(
            initial_capital=100000.0,
            slippage_bps=0.0,
            fee_rate=0.0,
            funding_rate_apy=3.65,
        )
        result = strat.run_backtest(generate_flat_klines())
        # Hold = 29 hours = 29/24 days. Funding = 100000 * 3.65 * (29/24)/365 = ~1208.
        assert result.total_return == pytest.approx(-1208.0 / 100000.0, rel=0.05)

    def test_pnl_deducts_funding_proportional_to_hold_time(self) -> None:
        """Direct _calculate_pnl check: 100 days at 36.5% APY costs exactly 10%."""
        strat = BuySellStrategy(
            initial_capital=100000.0,
            slippage_bps=0.0,
            fee_rate=0.0,
            funding_rate_apy=0.365,
        )
        strat._bar_index = 1
        strat._position = "long"
        strat._entry_price = 100.0
        strat._entry_time = 0
        strat._capital = 100000.0
        strat._position_fraction_at_entry = 1.0
        strat.leverage = 1

        hold_ms = 100 * 86_400_000
        pnl = strat._calculate_pnl(100.0, "long", exit_ts=hold_ms)
        # notional=100000, funding = 100000 * 0.365 * (100/365) = 10000
        assert pnl == pytest.approx(-10000.0, abs=1.0)


class TestOptimizerFundingPassthrough:
    def test_funding_rate_apy_reaches_strategy(self) -> None:
        opt = StrategyOptimizer(
            BuySellStrategy,
            slippage_bps=0.0,
            fee_rate=0.0,
            funding_rate_apy=0.365,
        )
        strat = opt._create_strategy({}, symbol="BTC/USDT")
        assert strat.funding_rate_apy == pytest.approx(0.365)

    def test_default_funding_is_zero(self) -> None:
        opt = StrategyOptimizer(BuySellStrategy)
        strat = opt._create_strategy({}, symbol="BTC/USDT")
        assert strat.funding_rate_apy == 0.0
