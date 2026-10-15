"""Tests for inventory management in market making."""

from __future__ import annotations


from selene_space_strategy.market_making.as_model import ASQuotes
from selene_space_strategy.market_making.inventory import (
    InventoryManager,
    InventoryState,
    adjust_sizes,
    skew_quotes,
)


# ---------------------------------------------------------------------------
# InventoryManager
# ---------------------------------------------------------------------------


class TestInventoryManagerUpdate:
    """Tests for InventoryManager.update."""

    def test_buy_increases_inventory(self) -> None:
        """A buy fill increases inventory."""
        mgr = InventoryManager(max_inventory=10.0)
        mgr.update("buy", 2.0, 100.0)
        assert mgr.get_inventory() == 2.0

    def test_sell_decreases_inventory(self) -> None:
        """A sell fill decreases inventory."""
        mgr = InventoryManager(max_inventory=10.0)
        mgr.update("sell", 2.0, 100.0)
        assert mgr.get_inventory() == -2.0

    def test_sequential_fills(self) -> None:
        """Multiple fills accumulate correctly."""
        mgr = InventoryManager(max_inventory=10.0)
        mgr.update("buy", 3.0, 100.0)
        mgr.update("buy", 2.0, 101.0)
        mgr.update("sell", 1.0, 102.0)
        assert mgr.get_inventory() == 4.0  # 3 + 2 - 1

    def test_trade_count_increments(self) -> None:
        """Each valid fill increments trade count."""
        mgr = InventoryManager(max_inventory=10.0)
        mgr.update("buy", 1.0, 100.0)
        mgr.update("sell", 1.0, 101.0)
        assert mgr._trade_count == 2

    def test_zero_qty_no_effect(self) -> None:
        """Zero quantity fill has no effect."""
        mgr = InventoryManager(max_inventory=10.0)
        mgr.update("buy", 0.0, 100.0)
        assert mgr.get_inventory() == 0.0
        assert mgr._trade_count == 0

    def test_unknown_side_no_effect(self) -> None:
        """Unknown fill side has no effect."""
        mgr = InventoryManager(max_inventory=10.0)
        mgr.update("hold", 1.0, 100.0)
        assert mgr.get_inventory() == 0.0
        assert mgr._trade_count == 0


class TestInventoryManagerCanAccept:
    """Tests for InventoryManager.can_accept."""

    def test_accept_within_limit(self) -> None:
        """Fill within limits is accepted."""
        mgr = InventoryManager(max_inventory=10.0)
        mgr.update("buy", 5.0, 100.0)
        assert mgr.can_accept("buy", 4.0) is True

    def test_reject_at_limit(self) -> None:
        """Fill that would exceed limit is rejected."""
        mgr = InventoryManager(max_inventory=10.0)
        mgr.update("buy", 8.0, 100.0)
        assert mgr.can_accept("buy", 3.0) is False  # 8 + 3 = 11 > 10

    def test_accept_exact_limit(self) -> None:
        """Fill that exactly reaches the limit is accepted."""
        mgr = InventoryManager(max_inventory=10.0)
        mgr.update("buy", 5.0, 100.0)
        assert mgr.can_accept("buy", 5.0) is True  # 5 + 5 = 10 <= 10

    def test_sell_direction(self) -> None:
        """Sell fills are checked against negative limit."""
        mgr = InventoryManager(max_inventory=10.0)
        mgr.update("sell", 8.0, 100.0)  # inventory = -8
        assert mgr.can_accept("sell", 3.0) is False  # -8 - 3 = -11, |11| > 10
        assert mgr.can_accept("sell", 2.0) is True  # -8 - 2 = -10, |10| <= 10

    def test_reduce_position_always_accepted(self) -> None:
        """Fills that reduce position are always accepted."""
        mgr = InventoryManager(max_inventory=10.0)
        mgr.update("buy", 10.0, 100.0)  # at max
        assert mgr.can_accept("sell", 5.0) is True  # reduces inventory

    def test_zero_qty_accepted(self) -> None:
        """Zero quantity is always accepted."""
        mgr = InventoryManager(max_inventory=10.0)
        assert mgr.can_accept("buy", 0.0) is True

    def test_unknown_side_rejected(self) -> None:
        """Unknown side is rejected."""
        mgr = InventoryManager(max_inventory=10.0)
        assert mgr.can_accept("hold", 1.0) is False


class TestInventoryManagerGetUtilization:
    """Tests for InventoryManager.get_utilization."""

    def test_zero_inventory(self) -> None:
        """Zero inventory gives zero utilization."""
        mgr = InventoryManager(max_inventory=10.0)
        assert mgr.get_utilization() == 0.0

    def test_half_inventory(self) -> None:
        """Half max inventory gives 0.5 utilization."""
        mgr = InventoryManager(max_inventory=10.0)
        mgr.update("buy", 5.0, 100.0)
        assert mgr.get_utilization() == 0.5

    def test_full_inventory(self) -> None:
        """Full max inventory gives 1.0 utilization."""
        mgr = InventoryManager(max_inventory=10.0)
        mgr.update("buy", 10.0, 100.0)
        assert mgr.get_utilization() == 1.0

    def test_negative_inventory(self) -> None:
        """Utilization uses absolute value of inventory."""
        mgr = InventoryManager(max_inventory=10.0)
        mgr.update("sell", 5.0, 100.0)
        assert mgr.get_utilization() == 0.5


class TestInventoryManagerReset:
    """Tests for InventoryManager.reset."""

    def test_reset_clears_state(self) -> None:
        """Reset clears inventory, PnL, and trade count."""
        mgr = InventoryManager(max_inventory=10.0)
        mgr.update("buy", 5.0, 100.0)
        mgr.reset()
        assert mgr.get_inventory() == 0.0
        assert mgr._realized_pnl == 0.0
        assert mgr._trade_count == 0


# ---------------------------------------------------------------------------
# InventoryState dataclass
# ---------------------------------------------------------------------------


class TestInventoryState:
    """Tests for InventoryState dataclass."""

    def test_creation(self) -> None:
        """InventoryState can be created with all fields."""
        state = InventoryState(
            current_inventory=5.0,
            realized_pnl=100.0,
            trade_count=10,
            max_inventory=20.0,
        )
        assert state.current_inventory == 5.0
        assert state.realized_pnl == 100.0
        assert state.trade_count == 10
        assert state.max_inventory == 20.0


# ---------------------------------------------------------------------------
# skew_quotes
# ---------------------------------------------------------------------------


class TestSkewQuotes:
    """Tests for skew_quotes."""

    def _make_quotes(self) -> ASQuotes:
        """Create a sample ASQuotes for testing."""
        return ASQuotes(
            bid_price=99.0,
            ask_price=101.0,
            spread=2.0,
            reservation_price=100.0,
            bid_size=1.0,
            ask_size=1.0,
        )

    def test_zero_inventory_no_skew(self) -> None:
        """Zero inventory produces no skew."""
        q = self._make_quotes()
        skewed = skew_quotes(q, inventory=0.0, max_inventory=10.0)
        assert skewed.bid_price == q.bid_price
        assert skewed.ask_price == q.ask_price

    def test_long_inventory_lowers_prices(self) -> None:
        """Long inventory lowers both bid and ask (encourage selling)."""
        q = self._make_quotes()
        skewed = skew_quotes(q, inventory=5.0, max_inventory=10.0, skew_factor=0.5)
        assert skewed.bid_price < q.bid_price
        assert skewed.ask_price < q.ask_price

    def test_short_inventory_raises_prices(self) -> None:
        """Short inventory raises both bid and ask (encourage buying)."""
        q = self._make_quotes()
        skewed = skew_quotes(q, inventory=-5.0, max_inventory=10.0, skew_factor=0.5)
        assert skewed.bid_price > q.bid_price
        assert skewed.ask_price > q.ask_price

    def test_skew_preserves_spread(self) -> None:
        """Skew shifts both prices by the same amount (spread unchanged)."""
        q = self._make_quotes()
        skewed = skew_quotes(q, inventory=5.0, max_inventory=10.0, skew_factor=0.5)
        assert abs(skewed.spread - q.spread) < 1e-10

    def test_zero_max_inventory_no_skew(self) -> None:
        """Zero max_inventory produces no skew (avoid division by zero)."""
        q = self._make_quotes()
        skewed = skew_quotes(q, inventory=5.0, max_inventory=0.0)
        assert skewed.bid_price == q.bid_price

    def test_higher_skew_factor_more_shift(self) -> None:
        """Higher skew_factor produces more aggressive shifting."""
        q = self._make_quotes()
        low = skew_quotes(q, inventory=5.0, max_inventory=10.0, skew_factor=0.2)
        high = skew_quotes(q, inventory=5.0, max_inventory=10.0, skew_factor=0.8)
        assert abs(high.bid_price - q.bid_price) > abs(low.bid_price - q.bid_price)


# ---------------------------------------------------------------------------
# adjust_sizes
# ---------------------------------------------------------------------------


class TestAdjustSizes:
    """Tests for adjust_sizes."""

    def _make_quotes(self) -> ASQuotes:
        """Create a sample ASQuotes for testing."""
        return ASQuotes(
            bid_price=99.0,
            ask_price=101.0,
            spread=2.0,
            reservation_price=100.0,
            bid_size=1.0,
            ask_size=1.0,
        )

    def test_zero_inventory_equal_sizes(self) -> None:
        """Zero inventory gives equal sizes on both sides."""
        q = self._make_quotes()
        adjusted = adjust_sizes(q, inventory=0.0, max_inventory=10.0, base_size=1.0)
        assert adjusted.bid_size == 1.0
        assert adjusted.ask_size == 1.0

    def test_long_inventory_larger_ask_size(self) -> None:
        """Long inventory: ask size > bid size (encourage selling)."""
        q = self._make_quotes()
        adjusted = adjust_sizes(q, inventory=5.0, max_inventory=10.0, base_size=1.0)
        assert adjusted.ask_size > adjusted.bid_size

    def test_short_inventory_larger_bid_size(self) -> None:
        """Short inventory: bid size > ask size (encourage buying)."""
        q = self._make_quotes()
        adjusted = adjust_sizes(q, inventory=-5.0, max_inventory=10.0, base_size=1.0)
        assert adjusted.bid_size > adjusted.ask_size

    def test_base_size_scales(self) -> None:
        """Base size scales both sides proportionally."""
        q = self._make_quotes()
        adjusted = adjust_sizes(q, inventory=0.0, max_inventory=10.0, base_size=5.0)
        assert adjusted.bid_size == 5.0
        assert adjusted.ask_size == 5.0

    def test_zero_max_inventory_equal_sizes(self) -> None:
        """Zero max_inventory gives equal sizes (avoid division by zero)."""
        q = self._make_quotes()
        adjusted = adjust_sizes(q, inventory=5.0, max_inventory=0.0, base_size=1.0)
        assert adjusted.bid_size == 1.0
        assert adjusted.ask_size == 1.0

    def test_reducing_side_size_greater_than_base(self) -> None:
        """The inventory-reducing side has size > base_size."""
        q = self._make_quotes()
        adjusted = adjust_sizes(q, inventory=5.0, max_inventory=10.0, base_size=1.0)
        # Long inventory: ask side reduces inventory
        assert adjusted.ask_size > 1.0

    def test_prices_unchanged(self) -> None:
        """adjust_sizes does not change prices, only sizes."""
        q = self._make_quotes()
        adjusted = adjust_sizes(q, inventory=5.0, max_inventory=10.0, base_size=1.0)
        assert adjusted.bid_price == q.bid_price
        assert adjusted.ask_price == q.ask_price
        assert adjusted.spread == q.spread
        assert adjusted.reservation_price == q.reservation_price


# ---------------------------------------------------------------------------
# Integration: max inventory edge case
# ---------------------------------------------------------------------------


class TestMaxInventoryEdge:
    """Edge case: at max inventory, can't accept more in same direction."""

    def test_at_max_buy_rejected(self) -> None:
        """At max long inventory, additional buy is rejected."""
        mgr = InventoryManager(max_inventory=10.0)
        mgr.update("buy", 10.0, 100.0)
        assert mgr.can_accept("buy", 0.1) is False

    def test_at_max_sell_rejected(self) -> None:
        """At max short inventory, additional sell is rejected."""
        mgr = InventoryManager(max_inventory=10.0)
        mgr.update("sell", 10.0, 100.0)
        assert mgr.can_accept("sell", 0.1) is False

    def test_at_max_opposite_direction_accepted(self) -> None:
        """At max inventory, opposite direction is still accepted."""
        mgr = InventoryManager(max_inventory=10.0)
        mgr.update("buy", 10.0, 100.0)
        assert mgr.can_accept("sell", 1.0) is True
