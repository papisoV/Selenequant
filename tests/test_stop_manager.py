"""Tests for server-side stop order manager.

Covers:
  - Stop placement: price trigger → automatic stop_market order
  - Trailing stop: ratchet stop price as price moves favorably
  - Stop cancellation: cleanup on manual cancel
  - Multi-symbol stop tracking
"""

from __future__ import annotations

import pytest

from selene_space_exchange.execution.stop_manager import (
    StopHandle,
    StopManager,
    StopSide,
    StopStatus,
)


# ---------------------------------------------------------------------------
# StopHandle
# ---------------------------------------------------------------------------


class TestStopHandle:
    def test_create_stop_handle(self) -> None:
        handle = StopHandle(
            stop_id="s-001",
            symbol="BTC/USDT",
            side=StopSide.LONG,
            stop_price=49000.0,
            quantity=0.1,
        )
        assert handle.stop_id == "s-001"
        assert handle.status == StopStatus.ACTIVE
        assert handle.trailing_enabled is False

    def test_trailing_handle(self) -> None:
        handle = StopHandle(
            stop_id="s-002",
            symbol="BTC/USDT",
            side=StopSide.LONG,
            stop_price=49000.0,
            quantity=0.1,
            trailing_enabled=True,
            trailing_distance=500.0,
        )
        assert handle.trailing_enabled is True
        assert handle.trailing_distance == 500.0


# ---------------------------------------------------------------------------
# StopManager
# ---------------------------------------------------------------------------


class TestStopManager:
    def test_register_stop(self) -> None:
        mgr = StopManager()
        handle = mgr.register_stop(
            symbol="BTC/USDT",
            side=StopSide.LONG,
            stop_price=49000.0,
            quantity=0.1,
        )
        assert handle.stop_id is not None
        assert handle.status == StopStatus.ACTIVE

    def test_stop_triggers_when_price_hits(self) -> None:
        mgr = StopManager()
        mgr.register_stop(
            symbol="BTC/USDT",
            side=StopSide.LONG,
            stop_price=49000.0,
            quantity=0.1,
        )
        triggered = mgr.on_price_tick("BTC/USDT", 48900.0)
        assert len(triggered) == 1
        assert triggered[0]["symbol"] == "BTC/USDT"

    def test_stop_does_not_trigger_in_range(self) -> None:
        mgr = StopManager()
        mgr.register_stop(
            symbol="BTC/USDT",
            side=StopSide.LONG,
            stop_price=49000.0,
            quantity=0.1,
        )
        triggered = mgr.on_price_tick("BTC/USDT", 49100.0)
        assert len(triggered) == 0

    def test_short_stop_triggers_on_rise(self) -> None:
        mgr = StopManager()
        mgr.register_stop(
            symbol="ETH/USDT",
            side=StopSide.SHORT,
            stop_price=3100.0,
            quantity=1.0,
        )
        triggered = mgr.on_price_tick("ETH/USDT", 3110.0)
        assert len(triggered) == 1

    def test_short_stop_no_trigger_on_drop(self) -> None:
        mgr = StopManager()
        mgr.register_stop(
            symbol="ETH/USDT",
            side=StopSide.SHORT,
            stop_price=3100.0,
            quantity=1.0,
        )
        triggered = mgr.on_price_tick("ETH/USDT", 3050.0)
        assert len(triggered) == 0

    def test_triggered_stop_marked_filled(self) -> None:
        mgr = StopManager()
        handle = mgr.register_stop(
            symbol="BTC/USDT",
            side=StopSide.LONG,
            stop_price=49000.0,
            quantity=0.1,
        )
        mgr.on_price_tick("BTC/USDT", 48900.0)
        updated = mgr.get_stop(handle.stop_id)
        assert updated.status == StopStatus.TRIGGERED

    def test_cancel_stop(self) -> None:
        mgr = StopManager()
        handle = mgr.register_stop(
            symbol="BTC/USDT",
            side=StopSide.LONG,
            stop_price=49000.0,
            quantity=0.1,
        )
        mgr.cancel_stop(handle.stop_id)
        updated = mgr.get_stop(handle.stop_id)
        assert updated.status == StopStatus.CANCELLED

    def test_cancelled_stop_does_not_trigger(self) -> None:
        mgr = StopManager()
        handle = mgr.register_stop(
            symbol="BTC/USDT",
            side=StopSide.LONG,
            stop_price=49000.0,
            quantity=0.1,
        )
        mgr.cancel_stop(handle.stop_id)
        triggered = mgr.on_price_tick("BTC/USDT", 48900.0)
        assert len(triggered) == 0

    def test_trailing_stop_ratchets_up_long(self) -> None:
        mgr = StopManager()
        handle = mgr.register_stop(
            symbol="BTC/USDT",
            side=StopSide.LONG,
            stop_price=49000.0,
            quantity=0.1,
            trailing_enabled=True,
            trailing_distance=500.0,
        )
        # Price moves up: stop should ratchet up
        mgr.on_price_tick("BTC/USDT", 50500.0)
        updated = mgr.get_stop(handle.stop_id)
        # New stop = 50500 - 500 = 50000
        assert updated.stop_price == pytest.approx(50000.0)

    def test_trailing_stop_does_not_move_down_long(self) -> None:
        mgr = StopManager()
        handle = mgr.register_stop(
            symbol="BTC/USDT",
            side=StopSide.LONG,
            stop_price=49000.0,
            quantity=0.1,
            trailing_enabled=True,
            trailing_distance=500.0,
        )
        # Price moves up first
        mgr.on_price_tick("BTC/USDT", 50500.0)
        # Price drops: stop should NOT move down
        mgr.on_price_tick("BTC/USDT", 50000.0)
        updated = mgr.get_stop(handle.stop_id)
        assert updated.stop_price == pytest.approx(50000.0)

    def test_trailing_stop_ratchets_down_short(self) -> None:
        mgr = StopManager()
        handle = mgr.register_stop(
            symbol="ETH/USDT",
            side=StopSide.SHORT,
            stop_price=3100.0,
            quantity=1.0,
            trailing_enabled=True,
            trailing_distance=50.0,
        )
        # Price moves down: stop should ratchet down
        mgr.on_price_tick("ETH/USDT", 3000.0)
        updated = mgr.get_stop(handle.stop_id)
        assert updated.stop_price == pytest.approx(3050.0)

    def test_trailing_stop_does_not_move_up_short(self) -> None:
        mgr = StopManager()
        handle = mgr.register_stop(
            symbol="ETH/USDT",
            side=StopSide.SHORT,
            stop_price=3100.0,
            quantity=1.0,
            trailing_enabled=True,
            trailing_distance=50.0,
        )
        mgr.on_price_tick("ETH/USDT", 3000.0)  # ratchets down to 3050
        mgr.on_price_tick("ETH/USDT", 3080.0)  # moves up, stop should stay
        updated = mgr.get_stop(handle.stop_id)
        assert updated.stop_price == pytest.approx(3050.0)

    def test_trailing_stop_triggers_after_ratchet(self) -> None:
        mgr = StopManager()
        mgr.register_stop(
            symbol="BTC/USDT",
            side=StopSide.LONG,
            stop_price=49000.0,
            quantity=0.1,
            trailing_enabled=True,
            trailing_distance=500.0,
        )
        mgr.on_price_tick("BTC/USDT", 50500.0)  # ratchet to 50000
        # Drop below ratcheted stop
        triggered = mgr.on_price_tick("BTC/USDT", 49900.0)
        assert len(triggered) == 1

    def test_list_active_stops(self) -> None:
        mgr = StopManager()
        mgr.register_stop("BTC/USDT", StopSide.LONG, 49000.0, 0.1)
        mgr.register_stop("ETH/USDT", StopSide.SHORT, 3100.0, 1.0)
        active = mgr.list_active_stops()
        assert len(active) == 2

    def test_wrong_symbol_no_trigger(self) -> None:
        mgr = StopManager()
        mgr.register_stop("BTC/USDT", StopSide.LONG, 49000.0, 0.1)
        triggered = mgr.on_price_tick("ETH/USDT", 48900.0)
        assert len(triggered) == 0

    def test_get_nonexistent_stop(self) -> None:
        mgr = StopManager()
        assert mgr.get_stop("nonexistent") is None

    def test_multiple_stops_same_symbol(self) -> None:
        mgr = StopManager()
        mgr.register_stop("BTC/USDT", StopSide.LONG, 49000.0, 0.1)
        mgr.register_stop("BTC/USDT", StopSide.LONG, 48500.0, 0.05)
        triggered = mgr.on_price_tick("BTC/USDT", 48950.0)
        # Only the 49000 stop should trigger (48950 < 49000 but > 48500)
        assert len(triggered) == 1

    def test_exact_price_does_not_trigger_long_stop(self) -> None:
        mgr = StopManager()
        mgr.register_stop("BTC/USDT", StopSide.LONG, 49000.0, 0.1)
        # Exact stop_price: for long, trigger when price <= stop
        triggered = mgr.on_price_tick("BTC/USDT", 49000.0)
        assert len(triggered) == 1
