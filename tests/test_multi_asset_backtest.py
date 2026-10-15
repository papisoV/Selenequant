"""Tests for multi-asset portfolio backtest engine.

Covers:
  - MultiAssetPortfolio: tracks cash + positions across symbols
  - MultiAssetEngine: merge-sorted kline loop with allocation
  - AllocationModel: equal-weight, fixed-weight strategies
"""

from __future__ import annotations


import pytest

from selene_space_backtest.multi_asset.allocation_model import (
    EqualWeightAllocation,
    FixedWeightAllocation,
)
from selene_space_backtest.multi_asset.multi_asset_engine import (
    MultiAssetBacktestConfig,
    MultiAssetEngine,
)
from selene_space_backtest.multi_asset.portfolio_broker import MultiAssetPortfolio
from selene_space_types.market import KlineEvent


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _make_kline(
    symbol: str,
    timestamp: int,
    open_p: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    interval: str = "1h",
) -> KlineEvent:
    return KlineEvent(
        exchange="backtest",
        symbol=symbol,
        timestamp=timestamp,
        interval=interval,
        open=open_p,
        high=high,
        low=low,
        close=close,
        volume=volume,
        closed=True,
    )


def _make_two_symbol_klines() -> list[KlineEvent]:
    """BTC and ETH klines interleaved, 3 bars each."""
    base_ts = 1_000_000_000_000
    interval_ms = 3_600_000  # 1h
    klines: list[KlineEvent] = []
    for i in range(3):
        ts = base_ts + i * interval_ms
        klines.append(_make_kline("BTC/USDT", ts, 50000 + i * 1000, 51000, 49000, 50500 + i * 1000, 100.0))
        klines.append(_make_kline("ETH/USDT", ts, 3000 + i * 100, 3100, 2900, 3050 + i * 100, 500.0))
    return klines


# ---------------------------------------------------------------------------
# MultiAssetPortfolio
# ---------------------------------------------------------------------------


class TestMultiAssetPortfolio:
    def test_initial_cash(self) -> None:
        pf = MultiAssetPortfolio(initial_cash=50_000.0)
        assert pf.cash == 50_000.0
        assert pf.total_equity == 50_000.0

    def test_open_long_updates_position_and_cash(self) -> None:
        pf = MultiAssetPortfolio(initial_cash=100_000.0)
        pf.apply_buy("BTC/USDT", quantity=0.5, price=60000.0, fee=10.0)
        assert pf.get_position("BTC/USDT") == pytest.approx(0.5)
        assert pf.cash == pytest.approx(100_000.0 - 30000.0 - 10.0)

    def test_close_position_returns_cash(self) -> None:
        pf = MultiAssetPortfolio(initial_cash=100_000.0)
        pf.apply_buy("BTC/USDT", quantity=1.0, price=50000.0, fee=10.0)
        pf.apply_sell("BTC/USDT", quantity=1.0, price=55000.0, fee=10.0)
        assert pf.get_position("BTC/USDT") == pytest.approx(0.0)
        # cash = 100k - 50k - 10 + 55k - 10 = 104980
        assert pf.cash == pytest.approx(104980.0)

    def test_multiple_symbols_independent(self) -> None:
        pf = MultiAssetPortfolio(initial_cash=100_000.0)
        pf.apply_buy("BTC/USDT", quantity=0.5, price=60000.0, fee=10.0)
        pf.apply_buy("ETH/USDT", quantity=10.0, price=3000.0, fee=5.0)
        assert pf.get_position("BTC/USDT") == pytest.approx(0.5)
        assert pf.get_position("ETH/USDT") == pytest.approx(10.0)

    def test_mark_to_market_equity(self) -> None:
        pf = MultiAssetPortfolio(initial_cash=100_000.0)
        pf.apply_buy("BTC/USDT", quantity=1.0, price=50000.0, fee=0.0)
        pf.mark_to_market({"BTC/USDT": 55000.0})
        # cash = 50k, btc = 55k → total = 105k
        assert pf.total_equity == pytest.approx(105000.0)

    def test_equity_curve_records_history(self) -> None:
        pf = MultiAssetPortfolio(initial_cash=100_000.0)
        pf.mark_to_market({"BTC/USDT": 50000.0})
        pf.mark_to_market({"BTC/USDT": 51000.0})
        assert len(pf.equity_curve) == 2

    def test_reset_clears_all(self) -> None:
        pf = MultiAssetPortfolio(initial_cash=100_000.0)
        pf.apply_buy("BTC/USDT", quantity=1.0, price=50000.0, fee=0.0)
        pf.reset()
        assert pf.cash == 100_000.0
        assert pf.get_position("BTC/USDT") == 0.0
        assert len(pf.equity_curve) == 0


# ---------------------------------------------------------------------------
# Allocation Models
# ---------------------------------------------------------------------------


class TestEqualWeightAllocation:
    def test_equal_split(self) -> None:
        alloc = EqualWeightAllocation()
        weights = alloc.compute_weights(
            ["BTC/USDT", "ETH/USDT", "SOL/USDT"], 100_000.0, {}
        )
        assert weights == {"BTC/USDT": pytest.approx(1 / 3), "ETH/USDT": pytest.approx(1 / 3), "SOL/USDT": pytest.approx(1 / 3)}

    def test_single_symbol_gets_all(self) -> None:
        alloc = EqualWeightAllocation()
        weights = alloc.compute_weights(["BTC/USDT"], 100_000.0, {})
        assert weights == {"BTC/USDT": 1.0}


class TestFixedWeightAllocation:
    def test_fixed_weights(self) -> None:
        alloc = FixedWeightAllocation({"BTC/USDT": 0.60, "ETH/USDT": 0.40})
        weights = alloc.compute_weights(["BTC/USDT", "ETH/USDT"], 100_000.0, {})
        assert weights["BTC/USDT"] == pytest.approx(0.60)
        assert weights["ETH/USDT"] == pytest.approx(0.40)

    def test_normalizes_non_sum_one(self) -> None:
        """权重和不为 1 时**归一化**而非报错（_sanitize 的设计意图：
        FixedWeightAllocation 每次调用重新归一，见其类 docstring）。"""
        alloc = FixedWeightAllocation({"BTC/USDT": 0.60, "ETH/USDT": 0.50})
        weights = alloc.compute_weights(["BTC/USDT", "ETH/USDT"], 100_000.0, {})
        assert sum(weights.values()) == pytest.approx(1.0)
        assert weights["BTC/USDT"] == pytest.approx(0.60 / 1.10)
        assert weights["ETH/USDT"] == pytest.approx(0.50 / 1.10)


# ---------------------------------------------------------------------------
# MultiAssetEngine
# ---------------------------------------------------------------------------


class TestMultiAssetEngine:
    def test_merge_sorts_klines_by_timestamp(self) -> None:
        klines = _make_two_symbol_klines()
        config = MultiAssetBacktestConfig(
            initial_cash=100_000.0,
            symbols=["BTC/USDT", "ETH/USDT"],
        )
        engine = MultiAssetEngine(config)
        result = engine.run(klines)
        # Both symbols processed
        assert result.symbol == "BTC/USDT,ETH/USDT"

    def test_equity_curve_has_length_of_unique_timestamps(self) -> None:
        klines = _make_two_symbol_klines()
        config = MultiAssetBacktestConfig(
            initial_cash=100_000.0,
            symbols=["BTC/USDT", "ETH/USDT"],
        )
        engine = MultiAssetEngine(config)
        result = engine.run(klines)
        # 3 unique timestamps × 2 symbols = 6 klines, but 3 equity snapshots
        assert len(result.equity_curve) == 3

    def test_initial_equity_equals_cash(self) -> None:
        klines = _make_two_symbol_klines()
        config = MultiAssetBacktestConfig(
            initial_cash=100_000.0,
            symbols=["BTC/USDT", "ETH/USDT"],
        )
        engine = MultiAssetEngine(config)
        result = engine.run(klines)
        assert result.equity_curve[0] == 100_000.0

    def test_buy_and_hold_two_assets(self) -> None:
        """Simple buy-and-hold across two assets: allocate 50/50 at bar 1, hold through bar 3."""
        klines = _make_two_symbol_klines()
        config = MultiAssetBacktestConfig(
            initial_cash=100_000.0,
            symbols=["BTC/USDT", "ETH/USDT"],
            allocation=EqualWeightAllocation(),
            rebalance_on_start=True,
        )
        engine = MultiAssetEngine(config)
        result = engine.run(klines)
        # Equity should reflect price changes of both assets
        assert result.equity_curve[-1] != 100_000.0
        # Metrics should be computed
        assert result.metrics is not None
        assert result.metrics.total_return is not None

    def test_empty_klines_raises(self) -> None:
        config = MultiAssetBacktestConfig(initial_cash=100_000.0, symbols=["BTC/USDT"])
        engine = MultiAssetEngine(config)
        with pytest.raises(ValueError):
            engine.run([])

    def test_no_allocation_no_trades(self) -> None:
        """Without rebalance_on_start, no allocation means no trades → equity flat."""
        klines = _make_two_symbol_klines()
        config = MultiAssetBacktestConfig(
            initial_cash=100_000.0,
            symbols=["BTC/USDT", "ETH/USDT"],
            rebalance_on_start=False,
        )
        engine = MultiAssetEngine(config)
        result = engine.run(klines)
        # All equity values should equal cash (no trades, no price exposure)
        for eq in result.equity_curve:
            assert eq == 100_000.0

    def test_symbols_from_data_when_not_configured(self) -> None:
        klines = _make_two_symbol_klines()
        config = MultiAssetBacktestConfig(initial_cash=100_000.0)
        engine = MultiAssetEngine(config)
        result = engine.run(klines)
        assert set(result.symbol.split(",")) == {"BTC/USDT", "ETH/USDT"}

    def test_run_returns_metrics(self) -> None:
        klines = _make_two_symbol_klines()
        config = MultiAssetBacktestConfig(
            initial_cash=100_000.0,
            symbols=["BTC/USDT", "ETH/USDT"],
            allocation=EqualWeightAllocation(),
            rebalance_on_start=True,
        )
        engine = MultiAssetEngine(config)
        result = engine.run(klines)
        assert result.metrics.sharpe_ratio is not None
        assert result.metrics.max_drawdown is not None
