"""Tests for MultiAssetEngine periodic rebalancing (C2).

Covers:
  - rebalance_interval triggers the allocation model every N bars
  - rebalance_interval=None (default) preserves the original no-rebalance behavior
  - _apply_allocation is delta-based: sells over-allocated, buys under-allocated
"""

from __future__ import annotations

from typing import Any

import pytest

from selene_space_backtest.multi_asset.allocation_model import (
    AllocationModel,
    EqualWeightAllocation,
)
from selene_space_backtest.multi_asset.multi_asset_engine import (
    MultiAssetBacktestConfig,
    MultiAssetEngine,
)
from selene_space_backtest.multi_asset.portfolio_broker import MultiAssetPortfolio
from selene_space_backtest.slippage import NoSlippage
from selene_space_types.market import KlineEvent


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


class _CountingAllocation:
    """Records call count and the last prices handed to it."""

    def __init__(self, inner: AllocationModel) -> None:
        self.inner = inner
        self.calls = 0
        self.last_prices: dict[str, float] | None = None

    def compute_weights(
        self,
        symbols: list[str],
        equity: float,
        prices: dict[str, float] | None = None,
    ) -> dict[str, float]:
        self.calls += 1
        self.last_prices = prices
        return self.inner.compute_weights(symbols, equity, prices or {})


def _engine_config(**kwargs: Any) -> MultiAssetBacktestConfig:
    base: dict[str, Any] = dict(
        initial_cash=100_000.0,
        slippage=NoSlippage(),
        commission_rate=0.0,
    )
    base.update(kwargs)
    return MultiAssetBacktestConfig(**base)


def test_periodic_rebalance_calls_model_every_interval() -> None:
    """rebalance_interval=2 over 6 bars → allocation called at bars 2 and 4 (not 0)."""
    a = _make_klines([100.0] * 6, "A/USDT")
    b = _make_klines([100.0] * 6, "B/USDT")
    counting = _CountingAllocation(EqualWeightAllocation())
    engine = MultiAssetEngine(
        _engine_config(
            symbols=["A/USDT", "B/USDT"],
            allocation=counting,
            rebalance_on_start=False,
            rebalance_interval=2,
        )
    )
    engine.run(a + b)

    assert counting.calls == 2  # bars idx 2 and 4
    assert counting.last_prices is not None
    assert set(counting.last_prices) == {"A/USDT", "B/USDT"}


def test_rebalance_on_start_plus_interval() -> None:
    """rebalance_on_start=True + rebalance_interval=2 → calls at 0, 2, 4 (3 total)."""
    a = _make_klines([100.0] * 6, "A/USDT")
    b = _make_klines([100.0] * 6, "B/USDT")
    counting = _CountingAllocation(EqualWeightAllocation())
    engine = MultiAssetEngine(
        _engine_config(
            symbols=["A/USDT", "B/USDT"],
            allocation=counting,
            rebalance_on_start=True,
            rebalance_interval=2,
        )
    )
    engine.run(a + b)

    assert counting.calls == 3


def test_default_no_rebalance_never_calls_model() -> None:
    """rebalance_interval=None + rebalance_on_start=False → allocation never runs."""
    a = _make_klines([100.0] * 6, "A/USDT")
    b = _make_klines([100.0] * 6, "B/USDT")
    counting = _CountingAllocation(EqualWeightAllocation())
    engine = MultiAssetEngine(
        _engine_config(
            symbols=["A/USDT", "B/USDT"],
            allocation=counting,
            rebalance_on_start=False,
            rebalance_interval=None,
        )
    )
    engine.run(a + b)

    assert counting.calls == 0


def test_rebalance_on_start_only_without_interval() -> None:
    """rebalance_on_start=True, no interval → allocation called exactly once."""
    a = _make_klines([100.0] * 4, "A/USDT")
    b = _make_klines([100.0] * 4, "B/USDT")
    counting = _CountingAllocation(EqualWeightAllocation())
    engine = MultiAssetEngine(
        _engine_config(
            symbols=["A/USDT", "B/USDT"],
            allocation=counting,
            rebalance_on_start=True,
            rebalance_interval=None,
        )
    )
    engine.run(a + b)

    assert counting.calls == 1


class _SwitchToSecondSymbol:
    """All-in on the first symbol on the first call, second symbol afterwards."""

    def __init__(self, first: str, second: str) -> None:
        self._first, self._second = first, second
        self.calls = 0

    def compute_weights(
        self,
        symbols: list[str],
        equity: float,
        prices: dict[str, float] | None = None,
    ) -> dict[str, float]:
        self.calls += 1
        winner = self._first if self.calls == 1 else self._second
        return {s: (1.0 if s == winner else 0.0) for s in symbols}


def test_apply_allocation_sells_overallocated() -> None:
    """Rebalancing to a lower target sells the excess position (no stacking).

    Portfolio holds 1000 A; target switches to all-in B → A must be fully sold
    and the freed cash redeployed into B.
    """
    allocation = _SwitchToSecondSymbol("A/USDT", "B/USDT")
    engine = MultiAssetEngine(_engine_config(allocation=allocation))
    portfolio = MultiAssetPortfolio(initial_cash=100_000.0)
    portfolio.apply_buy("A/USDT", quantity=1000.0, price=100.0, fee=0.0)
    portfolio.mark_to_market({"A/USDT": 100.0, "B/USDT": 100.0})

    a_kline = _make_klines([100.0], "A/USDT")
    b_kline = _make_klines([100.0], "B/USDT")
    group = a_kline + b_kline
    prices = {"A/USDT": 100.0, "B/USDT": 100.0}

    # First allocation: all-in A — portfolio is already there, no-op.
    engine._apply_allocation(portfolio, group, ["A/USDT", "B/USDT"], prices)
    # Second allocation: switch target to all-in B → sell A, buy B.
    engine._apply_allocation(portfolio, group, ["A/USDT", "B/USDT"], prices)

    # A fully sold; B bought with the freed cash.
    assert portfolio.get_position("A/USDT") == 0.0
    assert portfolio.get_position("B/USDT") > 0.0
    assert portfolio.cash >= 0.0
    assert portfolio.total_equity == pytest.approx(100_000.0, abs=1e-6)


def test_apply_allocation_buys_shortfall_only() -> None:
    """Rebalancing to a higher target buys just the shortfall, not the full target."""
    from selene_space_backtest.multi_asset.allocation_model import FixedWeightAllocation

    allocation = FixedWeightAllocation({"A/USDT": 0.5, "B/USDT": 0.5})
    engine = MultiAssetEngine(_engine_config(allocation=allocation))
    portfolio = MultiAssetPortfolio(initial_cash=100_000.0)
    portfolio.apply_buy("A/USDT", quantity=200.0, price=100.0, fee=0.0)
    portfolio.apply_buy("B/USDT", quantity=300.0, price=100.0, fee=0.0)
    portfolio.mark_to_market({"A/USDT": 100.0, "B/USDT": 100.0})

    a_kline = _make_klines([100.0], "A/USDT")
    b_kline = _make_klines([100.0], "B/USDT")
    engine._apply_allocation(
        portfolio,
        a_kline + b_kline,
        ["A/USDT", "B/USDT"],
        {"A/USDT": 100.0, "B/USDT": 100.0},
    )

    # Targets are 500 each; current A=200/B=300 → buys 300 + 200 shortfall only.
    assert portfolio.get_position("A/USDT") == pytest.approx(500.0)
    assert portfolio.get_position("B/USDT") == pytest.approx(500.0)


class _CashTrackingPortfolio(MultiAssetPortfolio):
    """MultiAssetPortfolio that also records its cash low-water mark."""

    def __init__(self, initial_cash: float) -> None:
        super().__init__(initial_cash)
        self.min_cash = initial_cash

    def apply_buy(
        self, symbol: str, quantity: float, price: float, fee: float = 0.0
    ) -> None:
        super().apply_buy(symbol, quantity, price, fee)
        self.min_cash = min(self.min_cash, self._cash)


def test_apply_allocation_no_transient_negative_cash_with_buy_first() -> None:
    """Symbol order must not matter: a buy processed before its funding sell
    must never drive cash negative (two-pass sells-then-buys)."""
    allocation = _SwitchToSecondSymbol("A/USDT", "B/USDT")
    engine = MultiAssetEngine(_engine_config(allocation=allocation))
    portfolio = _CashTrackingPortfolio(initial_cash=100_000.0)
    portfolio.apply_buy("A/USDT", quantity=1000.0, price=100.0, fee=0.0)
    portfolio.mark_to_market({"A/USDT": 100.0, "B/USDT": 100.0})

    group = _make_klines([100.0], "A/USDT") + _make_klines([100.0], "B/USDT")
    prices = {"A/USDT": 100.0, "B/USDT": 100.0}

    # Call 1 (all-in A, already there): no-op. Call 2 (switch to all-in B) passes
    # the buy-needing symbol first — the order that used to go cash-negative.
    engine._apply_allocation(portfolio, group, ["A/USDT", "B/USDT"], prices)
    engine._apply_allocation(portfolio, group, ["B/USDT", "A/USDT"], prices)

    assert portfolio.min_cash >= 0.0
    assert portfolio.get_position("A/USDT") == 0.0
    assert portfolio.get_position("B/USDT") == pytest.approx(1000.0)
    assert portfolio.cash >= 0.0


def test_apply_allocation_threads_quantity_to_slippage() -> None:
    """Slippage must see the real order notional (quantity*price), not 1.0*qty."""
    from selene_space_backtest.multi_asset.allocation_model import FixedWeightAllocation

    class _RecordingSlippage:
        def __init__(self) -> None:
            self.order_notionals: list[float] = []

        def apply(
            self,
            price: float,
            side,
            *,
            order_notional: float = 0.0,
            avg_24h_volume: float = 0.0,
        ) -> float:
            self.order_notionals.append(order_notional)
            return price

    recorder = _RecordingSlippage()
    allocation = FixedWeightAllocation({"A/USDT": 1.0})
    engine = MultiAssetEngine(_engine_config(allocation=allocation, slippage=recorder))
    portfolio = MultiAssetPortfolio(initial_cash=100_000.0)
    portfolio.mark_to_market({"A/USDT": 100.0})

    engine._apply_allocation(
        portfolio, _make_klines([100.0], "A/USDT"), ["A/USDT"], {"A/USDT": 100.0}
    )

    # 1000 shares @ 100 = 100,000 notional (previously 1.0 share @ 100 = 100).
    assert recorder.order_notionals == [100_000.0]
