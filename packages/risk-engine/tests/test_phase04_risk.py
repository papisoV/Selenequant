"""Phase 4 tests — self-trade prevention rule."""

from __future__ import annotations

import pytest

from selene_space_risk.self_trade import (
    ActiveOrder,
    OrderTracker,
    SelfTradePreventionRule,
)
from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskLevel
from selene_space_types.trading import OrderSide


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _intent(
    symbol: str = "BTC/USDT",
    side: OrderSide = OrderSide.BUY,
    price: float | None = None,
    strategy_id: str = "strat-A",
) -> TradeIntent:
    return TradeIntent(
        symbol=symbol,
        side=side,
        quantity=1.0,
        price=price,
        order_type="market",
        reasoning="test",
        confidence=0.8,
        timestamp=1711000000000,
    )


def _active(
    oid: str = "ord-1",
    symbol: str = "BTC/USDT",
    side: OrderSide = OrderSide.SELL,
    strategy_id: str = "strat-B",
    qty: float = 1.0,
    price: float | None = 50000.0,
) -> ActiveOrder:
    return ActiveOrder(
        order_id=oid,
        symbol=symbol,
        side=side,
        strategy_id=strategy_id,
        quantity=qty,
        price=price,
    )


# ===========================================================================
# OrderTracker Tests
# ===========================================================================


class TestOrderTracker:
    """Order tracking for self-trade detection."""

    def test_add_and_count(self) -> None:
        tracker = OrderTracker()
        tracker.add(_active(oid="o1"))
        tracker.add(_active(oid="o2"))
        assert tracker.active_count == 2

    def test_remove(self) -> None:
        tracker = OrderTracker()
        tracker.add(_active(oid="o1"))
        tracker.remove("o1")
        assert tracker.active_count == 0

    def test_remove_nonexistent(self) -> None:
        tracker = OrderTracker()
        tracker.remove("ghost")  # Should not raise

    def test_clear(self) -> None:
        tracker = OrderTracker()
        tracker.add(_active(oid="o1"))
        tracker.add(_active(oid="o2"))
        tracker.clear()
        assert tracker.active_count == 0

    def test_detect_opposing_same_symbol(self) -> None:
        """Buy order conflicts with existing sell on same symbol."""
        tracker = OrderTracker()
        tracker.add(_active(side=OrderSide.SELL, strategy_id="strat-B"))

        conflicts = tracker.get_opposing_orders(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            strategy_id="strat-A",
        )
        assert len(conflicts) == 1
        assert conflicts[0].strategy_id == "strat-B"

    def test_no_conflict_same_strategy(self) -> None:
        """Orders from the same strategy don't conflict."""
        tracker = OrderTracker()
        tracker.add(_active(side=OrderSide.SELL, strategy_id="strat-A"))

        conflicts = tracker.get_opposing_orders(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            strategy_id="strat-A",  # Same strategy
        )
        assert len(conflicts) == 0

    def test_no_conflict_same_side(self) -> None:
        """Two buy orders don't conflict."""
        tracker = OrderTracker()
        tracker.add(_active(side=OrderSide.BUY, strategy_id="strat-B"))

        conflicts = tracker.get_opposing_orders(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            strategy_id="strat-A",
        )
        assert len(conflicts) == 0

    def test_no_conflict_different_symbol(self) -> None:
        """Orders on different symbols don't conflict."""
        tracker = OrderTracker()
        tracker.add(_active(symbol="ETH/USDT", side=OrderSide.SELL, strategy_id="strat-B"))

        conflicts = tracker.get_opposing_orders(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            strategy_id="strat-A",
        )
        assert len(conflicts) == 0

    def test_limit_price_overlap_buy(self) -> None:
        """Buy limit at 50100 matches Sell limit at 50000."""
        tracker = OrderTracker()
        tracker.add(_active(side=OrderSide.SELL, price=50000.0, strategy_id="strat-B"))

        # Buy at 50100 >= Sell at 50000 → match
        conflicts = tracker.get_opposing_orders(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            strategy_id="strat-A",
            price=50100.0,
        )
        assert len(conflicts) == 1

    def test_limit_price_no_overlap_buy(self) -> None:
        """Buy limit at 49900 does NOT match Sell limit at 50000."""
        tracker = OrderTracker()
        tracker.add(_active(side=OrderSide.SELL, price=50000.0, strategy_id="strat-B"))

        # Buy at 49900 < Sell at 50000 → no match
        conflicts = tracker.get_opposing_orders(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            strategy_id="strat-A",
            price=49900.0,
        )
        assert len(conflicts) == 0

    def test_limit_price_overlap_sell(self) -> None:
        """Sell limit at 49900 matches Buy limit at 50000."""
        tracker = OrderTracker()
        tracker.add(_active(side=OrderSide.BUY, price=50000.0, strategy_id="strat-B"))

        # Sell at 49900 <= Buy at 50000 → match
        conflicts = tracker.get_opposing_orders(
            symbol="BTC/USDT",
            side=OrderSide.SELL,
            strategy_id="strat-A",
            price=49900.0,
        )
        assert len(conflicts) == 1

    def test_market_order_always_conflicts(self) -> None:
        """Market orders (no price) always conflict with opposing orders."""
        tracker = OrderTracker()
        tracker.add(_active(side=OrderSide.SELL, strategy_id="strat-B"))

        # Market buy — no price info, assume conflict
        conflicts = tracker.get_opposing_orders(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            strategy_id="strat-A",
            price=None,
        )
        assert len(conflicts) == 1

    def test_multiple_conflicts(self) -> None:
        """Multiple opposing orders from different strategies all detected."""
        tracker = OrderTracker()
        tracker.add(_active(oid="o1", side=OrderSide.SELL, strategy_id="strat-B"))
        tracker.add(_active(oid="o2", side=OrderSide.SELL, strategy_id="strat-C"))
        tracker.add(_active(oid="o3", side=OrderSide.BUY, strategy_id="strat-D"))  # Same side, no conflict

        conflicts = tracker.get_opposing_orders(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            strategy_id="strat-A",
        )
        assert len(conflicts) == 2


# ===========================================================================
# SelfTradePreventionRule Tests
# ===========================================================================


class TestSelfTradePreventionRule:
    """Self-trade prevention risk rule."""

    def test_block_mode_returns_critical(self) -> None:
        """Block mode returns CRITICAL when self-trade detected."""
        rule = SelfTradePreventionRule(mode="block")
        tracker = OrderTracker()
        tracker.add(_active(side=OrderSide.SELL, strategy_id="strat-B"))

        alert = rule.check(
            _intent(side=OrderSide.BUY, strategy_id="strat-A"),
            {"order_tracker": tracker, "strategy_id": "strat-A"},
        )
        assert alert is not None
        assert alert.level == RiskLevel.CRITICAL
        assert "自交易拦截" in alert.message

    def test_warn_mode_returns_warning(self) -> None:
        """Warn mode returns WARNING when self-trade detected."""
        rule = SelfTradePreventionRule(mode="warn")
        tracker = OrderTracker()
        tracker.add(_active(side=OrderSide.SELL, strategy_id="strat-B"))

        alert = rule.check(
            _intent(side=OrderSide.BUY, strategy_id="strat-A"),
            {"order_tracker": tracker, "strategy_id": "strat-A"},
        )
        assert alert is not None
        assert alert.level == RiskLevel.WARNING
        assert "自交易警告" in alert.message

    def test_cancel_oldest_mode(self) -> None:
        """Cancel-oldest mode returns WARNING with cancel info."""
        rule = SelfTradePreventionRule(mode="cancel_oldest")
        tracker = OrderTracker()
        tracker.add(_active(oid="old-order", side=OrderSide.SELL, strategy_id="strat-B"))

        alert = rule.check(
            _intent(side=OrderSide.BUY, strategy_id="strat-A"),
            {"order_tracker": tracker, "strategy_id": "strat-A"},
        )
        assert alert is not None
        assert alert.level == RiskLevel.WARNING
        assert "old-order" in alert.message

    def test_no_conflict_returns_none(self) -> None:
        """No alert when no self-trade conflict exists."""
        rule = SelfTradePreventionRule(mode="block")
        tracker = OrderTracker()
        # No opposing orders

        alert = rule.check(
            _intent(side=OrderSide.BUY, strategy_id="strat-A"),
            {"order_tracker": tracker, "strategy_id": "strat-A"},
        )
        assert alert is None

    def test_no_tracker_returns_none(self) -> None:
        """No alert when no tracker in context."""
        rule = SelfTradePreventionRule(mode="block")
        alert = rule.check(_intent(), {})
        assert alert is None

    def test_no_strategy_id_returns_none(self) -> None:
        """No alert when no strategy_id in context."""
        rule = SelfTradePreventionRule(mode="block")
        tracker = OrderTracker()
        alert = rule.check(_intent(), {"order_tracker": tracker})
        assert alert is None

    def test_disabled_rule_returns_none(self) -> None:
        """Disabled rule returns None even with conflict."""
        rule = SelfTradePreventionRule(mode="block", enabled=False)
        tracker = OrderTracker()
        tracker.add(_active(side=OrderSide.SELL, strategy_id="strat-B"))

        alert = rule.check(
            _intent(side=OrderSide.BUY, strategy_id="strat-A"),
            {"order_tracker": tracker, "strategy_id": "strat-A"},
        )
        assert alert is None

    def test_invalid_mode_rejected(self) -> None:
        """Invalid mode raises ValueError."""
        with pytest.raises(ValueError):
            SelfTradePreventionRule(mode="invalid")

    def test_conflict_context_includes_details(self) -> None:
        """Alert context includes conflicting orders and strategies."""
        rule = SelfTradePreventionRule(mode="block")
        tracker = OrderTracker()
        tracker.add(_active(oid="o1", side=OrderSide.SELL, strategy_id="strat-B"))
        tracker.add(_active(oid="o2", side=OrderSide.SELL, strategy_id="strat-C"))

        alert = rule.check(
            _intent(side=OrderSide.BUY, strategy_id="strat-A"),
            {"order_tracker": tracker, "strategy_id": "strat-A"},
        )
        assert alert is not None
        assert "o1" in alert.context["conflicting_orders"]
        assert "o2" in alert.context["conflicting_orders"]
        assert "strat-B" in alert.context["conflicting_strategies"]
        assert "strat-C" in alert.context["conflicting_strategies"]

    def test_rule_id(self) -> None:
        assert SelfTradePreventionRule().rule_id == "self_trade_prevention"

    def test_enable_disable(self) -> None:
        rule = SelfTradePreventionRule()
        assert rule.enabled is True
        rule.enabled = False
        assert rule.enabled is False
