"""Tests for portfolio rebalance engine (P3 #34)."""

from __future__ import annotations

import pytest
import numpy as np

from selene_space_alpha.portfolio.rebalance import (
    compute_rebalance_orders,
    compute_rebalance_from_optresult,
    estimate_turnover,
)


def _opt_result(weights: list[float], success: bool = True) -> object:
    """Create a mock OptResult."""
    class MockOptResult:
        def __init__(self) -> None:
            self.weights = np.array(weights)
            self.success = success
            self.objective = "test"
            self.message = ""
    return MockOptResult()


class TestComputeRebalanceOrders:
    def test_no_drift_no_orders(self) -> None:
        orders = compute_rebalance_orders(
            target_weights={"BTC/USDT": 0.5, "ETH/USDT": 0.5},
            current_positions={"BTC/USDT": 5000, "ETH/USDT": 5000},
            prices={"BTC/USDT": 50000, "ETH/USDT": 3000},
            total_equity=10000,
        )
        assert len(orders) == 0

    def test_buy_new_symbol(self) -> None:
        orders = compute_rebalance_orders(
            target_weights={"BTC/USDT": 0.5, "ETH/USDT": 0.5},
            current_positions={"BTC/USDT": 10000},
            prices={"BTC/USDT": 50000, "ETH/USDT": 3000},
            total_equity=10000,
        )
        # BTC should sell (100% → 50%), ETH should buy (0% → 50%)
        buy_orders = [o for o in orders if o.side == "buy"]
        sell_orders = [o for o in orders if o.side == "sell"]
        assert len(buy_orders) >= 1
        assert any(o.symbol == "ETH/USDT" for o in buy_orders)

    def test_sell_excess_position(self) -> None:
        orders = compute_rebalance_orders(
            target_weights={"BTC/USDT": 0.3, "ETH/USDT": 0.7},
            current_positions={"BTC/USDT": 7000, "ETH/USDT": 3000},
            prices={"BTC/USDT": 50000, "ETH/USDT": 3000},
            total_equity=10000,
        )
        btc_orders = [o for o in orders if o.symbol == "BTC/USDT"]
        assert len(btc_orders) == 1
        assert btc_orders[0].side == "sell"
        assert btc_orders[0].current_weight == pytest.approx(0.7, abs=0.01)
        assert btc_orders[0].target_weight == pytest.approx(0.3, abs=0.01)

    def test_sorted_by_drift(self) -> None:
        orders = compute_rebalance_orders(
            target_weights={"A": 0.1, "B": 0.2, "C": 0.7},
            current_positions={"A": 7000, "B": 2000, "C": 1000},
            prices={"A": 100, "B": 100, "C": 100},
            total_equity=10000,
        )
        # C has largest drift (0.7→0.1 = 0.6), A second (0.1→0.7 = 0.6)
        # Both have same drift, so just verify sorted descending
        for i in range(len(orders) - 1):
            assert orders[i].weight_drift >= orders[i + 1].weight_drift

    def test_min_drift_filter(self) -> None:
        """Small drifts below min_drift should be filtered out."""
        orders = compute_rebalance_orders(
            target_weights={"BTC/USDT": 0.505, "ETH/USDT": 0.495},
            current_positions={"BTC/USDT": 5000, "ETH/USDT": 5000},
            prices={"BTC/USDT": 50000, "ETH/USDT": 3000},
            total_equity=10000,
            min_drift=0.02,
        )
        assert len(orders) == 0

    def test_zero_equity_returns_empty(self) -> None:
        orders = compute_rebalance_orders(
            target_weights={"BTC/USDT": 1.0},
            current_positions={},
            prices={"BTC/USDT": 50000},
            total_equity=0,
        )
        assert len(orders) == 0

    def test_missing_price_skipped(self) -> None:
        orders = compute_rebalance_orders(
            target_weights={"BTC/USDT": 0.5, "ETH/USDT": 0.5},
            current_positions={"BTC/USDT": 10000},
            prices={"BTC/USDT": 50000},  # ETH price missing
            total_equity=10000,
        )
        # Only BTC should appear (ETH has no price)
        assert all(o.symbol == "BTC/USDT" for o in orders)

    def test_order_quantity_correct(self) -> None:
        """Verify order quantity = dollar_delta / price."""
        orders = compute_rebalance_orders(
            target_weights={"ETH/USDT": 0.5},
            current_positions={"ETH/USDT": 0},
            prices={"ETH/USDT": 3000},
            total_equity=10000,
        )
        assert len(orders) == 1
        # dollar_delta = 0.5 * 10000 = $5000, quantity = 5000 / 3000 ≈ 1.667
        assert orders[0].quantity == pytest.approx(5000 / 3000, rel=0.01)
        assert orders[0].side == "buy"


class TestComputeRebalanceFromOptresult:
    def test_from_optresult(self) -> None:
        opt = _opt_result([0.4, 0.6])
        symbols = ["BTC/USDT", "ETH/USDT"]
        orders = compute_rebalance_from_optresult(
            opt_result=opt,
            symbols=symbols,
            current_positions={"BTC/USDT": 10000},
            prices={"BTC/USDT": 50000, "ETH/USDT": 3000},
            total_equity=10000,
        )
        assert len(orders) > 0

    def test_failed_optimization_returns_empty(self) -> None:
        opt = _opt_result([0.5, 0.5], success=False)
        orders = compute_rebalance_from_optresult(
            opt_result=opt,
            symbols=["BTC/USDT", "ETH/USDT"],
            current_positions={},
            prices={"BTC/USDT": 50000, "ETH/USDT": 3000},
            total_equity=10000,
        )
        assert len(orders) == 0

    def test_length_mismatch_raises(self) -> None:
        opt = _opt_result([0.5, 0.5])
        with pytest.raises(ValueError, match="weights length"):
            compute_rebalance_from_optresult(
                opt_result=opt,
                symbols=["BTC/USDT"],  # 1 symbol but 2 weights
                current_positions={},
                prices={"BTC/USDT": 50000},
                total_equity=10000,
            )


class TestEstimateTurnover:
    def test_no_change_zero_turnover(self) -> None:
        t = estimate_turnover(
            target_weights={"A": 0.5, "B": 0.5},
            current_positions={"A": 5000, "B": 5000},
            total_equity=10000,
        )
        assert t == pytest.approx(0.0, abs=0.01)

    def test_full_rebalance(self) -> None:
        """Moving from 100% A to 100% B = 100% turnover."""
        t = estimate_turnover(
            target_weights={"A": 0.0, "B": 1.0},
            current_positions={"A": 10000, "B": 0},
            total_equity=10000,
        )
        # A: 1.0→0.0 = 1.0 drift, B: 0.0→1.0 = 1.0 drift, total=2.0/2=1.0
        assert t == pytest.approx(1.0, abs=0.01)

    def test_partial_rebalance(self) -> None:
        t = estimate_turnover(
            target_weights={"A": 0.3, "B": 0.7},
            current_positions={"A": 5000, "B": 5000},
            total_equity=10000,
        )
        # A: 0.5→0.3 = 0.2, B: 0.5→0.7 = 0.2, total=0.4/2=0.2
        assert t == pytest.approx(0.2, abs=0.01)

    def test_zero_equity(self) -> None:
        t = estimate_turnover(
            target_weights={"A": 1.0},
            current_positions={"A": 0},
            total_equity=0,
        )
        assert t == 0.0
