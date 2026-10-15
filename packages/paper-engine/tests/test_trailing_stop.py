"""Tests for TrailingStopTracker — ratcheting peak + SELL emission."""

from __future__ import annotations


from selene_space_paper.trailing_stop import (
    TrailingStopConfig,
    TrailingStopTracker,
)
from selene_space_types.trading import OrderSide


class TestTrailingStopBasics:
    def test_no_position_returns_none(self) -> None:
        tracker = TrailingStopTracker()
        assert tracker.update("BTC/USDT", 67000.0, 0.1) is None

    def test_zero_price_returns_none(self) -> None:
        tracker = TrailingStopTracker()
        tracker.open_position("BTC/USDT", 67000.0)
        assert tracker.update("BTC/USDT", 0.0, 0.1) is None

    def test_zero_quantity_clears_state(self) -> None:
        """If position_quantity == 0, tracker clears and returns None."""
        tracker = TrailingStopTracker()
        tracker.open_position("BTC/USDT", 67000.0)
        result = tracker.update("BTC/USDT", 68000.0, 0.0)
        assert result is None
        # State was cleared.
        assert tracker.status("BTC/USDT") is None


class TestPreActivation:
    """Before activation_gain reached: use fixed initial_stop_pct."""

    def test_initial_stop_triggers_on_drop(self) -> None:
        cfg = TrailingStopConfig(
            activation_gain=0.05, initial_stop_pct=0.03, trail_drop=0.02
        )
        tracker = TrailingStopTracker(config=cfg)
        tracker.open_position("BTC/USDT", 10000.0)
        # Small uptick not enough to activate (need +5%).
        tracker.update("BTC/USDT", 10100.0, 0.1, now_epoch=1000.0)
        # Now price falls below initial stop (10000 * (1 - 0.03) = 9700).
        sig = tracker.update("BTC/USDT", 9650.0, 0.1, now_epoch=1001.0)
        assert sig is not None
        assert sig.side == OrderSide.SELL
        assert "initial_stop" in sig.reason
        assert sig.quantity == 0.1

    def test_pre_activation_ignores_small_dip(self) -> None:
        cfg = TrailingStopConfig(
            activation_gain=0.05, initial_stop_pct=0.03, trail_drop=0.02
        )
        tracker = TrailingStopTracker(config=cfg)
        tracker.open_position("BTC/USDT", 10000.0)
        # Slight uptick.
        tracker.update("BTC/USDT", 10100.0, 0.1, now_epoch=1000.0)
        # Small dip — still above initial stop.
        sig = tracker.update("BTC/USDT", 9850.0, 0.1, now_epoch=1001.0)
        assert sig is None


class TestPostActivation:
    """After activation_gain reached: trail peak by trail_drop."""

    def test_activation_engages_after_gain(self) -> None:
        cfg = TrailingStopConfig(
            activation_gain=0.05, initial_stop_pct=0.03, trail_drop=0.02
        )
        tracker = TrailingStopTracker(config=cfg)
        tracker.open_position("BTC/USDT", 10000.0)
        # Climb to +6% — activates trailing.
        tracker.update("BTC/USDT", 10600.0, 0.1, now_epoch=1000.0)
        assert tracker.status("BTC/USDT")["activated"] is True

    def test_trailing_sells_when_drawdown_exceeds_drop(self) -> None:
        cfg = TrailingStopConfig(
            activation_gain=0.05, initial_stop_pct=0.03, trail_drop=0.03
        )
        tracker = TrailingStopTracker(config=cfg)
        tracker.open_position("BTC/USDT", 10000.0)
        # Activate.
        tracker.update("BTC/USDT", 10600.0, 0.1, now_epoch=1000.0)
        # Peak ratchets to 11000.
        tracker.update("BTC/USDT", 11000.0, 0.1, now_epoch=1001.0)
        # Drop = (11000 - 10600) / 11000 = 3.6% > 3% trail_drop → SELL.
        sig = tracker.update("BTC/USDT", 10600.0, 0.1, now_epoch=1002.0)
        assert sig is not None
        assert sig.side == OrderSide.SELL
        assert "trailing_stop" in sig.reason

    def test_trailing_does_not_sell_within_drop(self) -> None:
        cfg = TrailingStopConfig(
            activation_gain=0.05, initial_stop_pct=0.03, trail_drop=0.05
        )
        tracker = TrailingStopTracker(config=cfg)
        tracker.open_position("BTC/USDT", 10000.0)
        tracker.update("BTC/USDT", 10600.0, 0.1, now_epoch=1000.0)
        tracker.update("BTC/USDT", 11000.0, 0.1, now_epoch=1001.0)
        # 2% drop from peak — below 5% threshold.
        sig = tracker.update("BTC/USDT", 10780.0, 0.1, now_epoch=1002.0)
        assert sig is None

    def test_peak_ratchets_only_up(self) -> None:
        cfg = TrailingStopConfig(
            activation_gain=0.05, initial_stop_pct=0.03, trail_drop=0.02
        )
        tracker = TrailingStopTracker(config=cfg)
        tracker.open_position("BTC/USDT", 10000.0)
        tracker.update("BTC/USDT", 11000.0, 0.1, now_epoch=1000.0)
        # Price drops — peak should stay at 11000.
        tracker.update("BTC/USDT", 10500.0, 0.1, now_epoch=1001.0)
        assert tracker.status("BTC/USDT")["peak_price"] == 11000.0


class TestThrottle:
    def test_emit_throttled_within_interval(self) -> None:
        cfg = TrailingStopConfig(
            activation_gain=0.05,
            initial_stop_pct=0.10,
            trail_drop=0.01,
            min_emit_interval_seconds=30.0,
        )
        tracker = TrailingStopTracker(config=cfg)
        tracker.open_position("BTC/USDT", 10000.0)
        # Activate.
        tracker.update("BTC/USDT", 11000.0, 0.1, now_epoch=1000.0)
        # First SELL.
        sig1 = tracker.update("BTC/USDT", 10500.0, 0.1, now_epoch=1001.0)
        assert sig1 is not None
        # Second tick 5s later — throttled.
        sig2 = tracker.update("BTC/USDT", 10000.0, 0.1, now_epoch=1006.0)
        assert sig2 is None
        # After 30s+ — can emit again.
        sig3 = tracker.update("BTC/USDT", 10000.0, 0.1, now_epoch=1032.0)
        assert sig3 is not None


class TestClearAndStatus:
    def test_clear_removes_state(self) -> None:
        tracker = TrailingStopTracker()
        tracker.open_position("BTC/USDT", 10000.0)
        tracker.clear("BTC/USDT")
        assert tracker.status("BTC/USDT") is None
        assert tracker.update("BTC/USDT", 67000.0, 0.1) is None

    def test_status_returns_full_snapshot(self) -> None:
        cfg = TrailingStopConfig(
            activation_gain=0.05, initial_stop_pct=0.03, trail_drop=0.02
        )
        tracker = TrailingStopTracker(config=cfg)
        tracker.open_position("BTC/USDT", 10000.0)
        tracker.update("BTC/USDT", 10600.0, 0.1, now_epoch=1000.0)
        s = tracker.status("BTC/USDT")
        assert s is not None
        assert s["entry_price"] == 10000.0
        assert s["peak_price"] == 10600.0
        assert s["activated"] is True
        # Trailing stop = 10600 * (1 - 0.02) = 10388.
        assert abs(s["current_trailing_stop"] - 10388.0) < 1e-6

    def test_open_position_is_idempotent_resets_peak(self) -> None:
        tracker = TrailingStopTracker()
        tracker.open_position("BTC/USDT", 10000.0)
        tracker.update("BTC/USDT", 11000.0, 0.1, now_epoch=1000.0)
        # Re-open with new entry — resets peak.
        tracker.open_position("BTC/USDT", 10500.0)
        s = tracker.status("BTC/USDT")
        assert s["entry_price"] == 10500.0
        assert s["peak_price"] == 10500.0
        assert s["activated"] is False
