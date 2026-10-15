"""Tests for alpha-execution bridge.

Covers:
  - RebalanceExecutor: consumes compute_rebalance_orders() output, produces trade signals
  - SignalSubscriber: converts AlphaFormulaStrategy Signal to TradeIntent
  - PortfolioState: tracks current vs target weight drift
"""

from __future__ import annotations

import pytest

from selene_space_alpha.bridge.portfolio_state import PortfolioState
from selene_space_alpha.bridge.rebalance_executor import RebalanceExecutor
from selene_space_alpha.bridge.signal_subscriber import SignalSubscriber


# ---------------------------------------------------------------------------
# PortfolioState
# ---------------------------------------------------------------------------


class TestPortfolioState:
    def test_initial_cash(self) -> None:
        state = PortfolioState(initial_cash=100_000.0)
        assert state.total_equity == 100_000.0
        assert state.cash == 100_000.0

    def test_update_positions(self) -> None:
        state = PortfolioState(initial_cash=100_000.0)
        state.update_positions(
            positions={"BTC/USDT": 0.5, "ETH/USDT": 10.0},
            prices={"BTC/USDT": 50000.0, "ETH/USDT": 3000.0},
        )
        # cash(100k) + btc(25k) + eth(30k) = 155k
        assert state.total_equity == 155_000.0
        assert state.get_position_value("BTC/USDT") == pytest.approx(25000.0)

    def test_get_current_weights(self) -> None:
        state = PortfolioState(initial_cash=100_000.0)
        state.update_positions(
            positions={"BTC/USDT": 1.0},
            prices={"BTC/USDT": 50000.0},
        )
        # cash = 100k, btc = 50k → total = 150k, btc weight = 50/150 = 1/3
        weights = state.get_current_weights()
        assert "BTC/USDT" in weights
        assert weights["BTC/USDT"] == pytest.approx(1.0 / 3.0)

    def test_drift_from_target(self) -> None:
        state = PortfolioState(initial_cash=100_000.0)
        state.update_positions(
            positions={"BTC/USDT": 0.5, "ETH/USDT": 5.0},
            prices={"BTC/USDT": 60000.0, "ETH/USDT": 4000.0},
        )
        # btc = 30k, eth = 20k, cash = 100k → total = 150k
        # actual btc weight = 30/150 = 0.2, target = 0.4, drift = 0.2
        target = {"BTC/USDT": 0.4, "ETH/USDT": 0.4, "SOL/USDT": 0.2}
        drift = state.compute_drift(target)
        assert len(drift) == 3
        assert drift["BTC/USDT"] == pytest.approx(0.2)

    def test_set_cash(self) -> None:
        state = PortfolioState(initial_cash=100_000.0)
        state.set_cash(80_000.0)
        assert state.cash == 80_000.0


# ---------------------------------------------------------------------------
# RebalanceExecutor
# ---------------------------------------------------------------------------


class TestRebalanceExecutor:
    def test_generates_orders_from_weights(self) -> None:
        executor = RebalanceExecutor()
        orders = executor.compute_orders(
            target_weights={"BTC/USDT": 0.6, "ETH/USDT": 0.4},
            current_positions={"BTC/USDT": 50000.0, "ETH/USDT": 50000.0},
            prices={"BTC/USDT": 50000.0, "ETH/USDT": 3000.0},
            total_equity=100_000.0,
        )
        assert len(orders) == 2

    def test_min_drift_skips_small_changes(self) -> None:
        executor = RebalanceExecutor(min_drift=0.05)
        # Current: 50/50, Target: 51/49 — drift 1% < 5%
        orders = executor.compute_orders(
            target_weights={"BTC/USDT": 0.51, "ETH/USDT": 0.49},
            current_positions={"BTC/USDT": 50000.0, "ETH/USDT": 50000.0},
            prices={"BTC/USDT": 50000.0, "ETH/USDT": 3000.0},
            total_equity=100_000.0,
        )
        assert len(orders) == 0

    def test_new_asset_included(self) -> None:
        executor = RebalanceExecutor()
        orders = executor.compute_orders(
            target_weights={"BTC/USDT": 0.5, "ETH/USDT": 0.3, "SOL/USDT": 0.2},
            current_positions={"BTC/USDT": 50000.0, "ETH/USDT": 50000.0},
            prices={"BTC/USDT": 50000.0, "ETH/USDT": 3000.0, "SOL/USDT": 150.0},
            total_equity=100_000.0,
        )
        # SOL should appear as a buy
        sol_orders = [o for o in orders if o.symbol == "SOL/USDT"]
        assert len(sol_orders) == 1
        assert sol_orders[0].side == "buy"

    def test_removed_asset_generates_sell(self) -> None:
        executor = RebalanceExecutor()
        orders = executor.compute_orders(
            target_weights={"BTC/USDT": 0.6, "ETH/USDT": 0.4},
            current_positions={"BTC/USDT": 50000.0, "ETH/USDT": 30000.0, "SOL/USDT": 20000.0},
            prices={"BTC/USDT": 50000.0, "ETH/USDT": 3000.0, "SOL/USDT": 150.0},
            total_equity=100_000.0,
        )
        sol_orders = [o for o in orders if o.symbol == "SOL/USDT"]
        assert len(sol_orders) == 1
        assert sol_orders[0].side == "sell"

    def test_zero_equity_returns_empty(self) -> None:
        executor = RebalanceExecutor()
        orders = executor.compute_orders(
            target_weights={"BTC/USDT": 0.5},
            current_positions={},
            prices={"BTC/USDT": 50000.0},
            total_equity=0.0,
        )
        assert len(orders) == 0


# ---------------------------------------------------------------------------
# SignalSubscriber
# ---------------------------------------------------------------------------


class TestSignalSubscriber:
    def test_convert_signal_to_intent(self) -> None:
        subscriber = SignalSubscriber()
        intent = subscriber.convert_signal(
            symbol="BTC/USDT",
            signal_strength=0.8,
            confidence=0.75,
            current_price=50000.0,
        )
        assert intent["symbol"] == "BTC/USDT"
        assert intent["side"] == "buy"
        assert intent["confidence"] == 0.75

    def test_negative_signal_is_sell(self) -> None:
        subscriber = SignalSubscriber()
        intent = subscriber.convert_signal(
            symbol="BTC/USDT",
            signal_strength=-0.6,
            confidence=0.8,
            current_price=50000.0,
        )
        assert intent["side"] == "sell"

    def test_low_confidence_filtered(self) -> None:
        subscriber = SignalSubscriber(min_confidence=0.6)
        intent = subscriber.convert_signal(
            symbol="BTC/USDT",
            signal_strength=0.8,
            confidence=0.5,
            current_price=50000.0,
        )
        assert intent is None

    def test_signal_below_threshold_filtered(self) -> None:
        subscriber = SignalSubscriber(min_signal=0.3)
        intent = subscriber.convert_signal(
            symbol="BTC/USDT",
            signal_strength=0.1,
            confidence=0.9,
            current_price=50000.0,
        )
        assert intent is None

    def test_position_size_from_notional_pct(self) -> None:
        subscriber = SignalSubscriber(notional_pct=0.02, account_equity=100_000.0)
        intent = subscriber.convert_signal(
            symbol="BTC/USDT",
            signal_strength=0.8,
            confidence=0.75,
            current_price=50000.0,
        )
        assert intent["quantity"] == pytest.approx(0.04, abs=1e-4)  # 2000 / 50000
        assert intent["notional"] == pytest.approx(2000.0)

    def test_convert_batch(self) -> None:
        subscriber = SignalSubscriber(min_confidence=0.5)
        signals = [
            {"symbol": "BTC/USDT", "strength": 0.8, "confidence": 0.9},
            {"symbol": "ETH/USDT", "strength": -0.4, "confidence": 0.3},  # filtered
            {"symbol": "SOL/USDT", "strength": 0.6, "confidence": 0.7},
        ]
        intents = subscriber.convert_batch(signals, current_price=100.0)
        assert len(intents) == 2
