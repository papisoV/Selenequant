"""Tests for OCO / bracket order manager.

Covers:
  - Bracket placement: entry + TP + SL as atomic unit
  - TP fill → SL auto-cancel (OCO semantics)
  - SL fill → TP auto-cancel (OCO semantics)
  - Entry unfilled → TP/SL not triggered
  - Bracket state machine transitions
"""

from __future__ import annotations

import pytest

from selene_space_exchange.execution.bracket import (
    BracketOrderManager,
    BracketSide,
    BracketState,
    BracketOrder,
)


# ---------------------------------------------------------------------------
# BracketOrder value object
# ---------------------------------------------------------------------------


class TestBracketOrder:
    def test_create_bracket(self) -> None:
        bracket = BracketOrder(
            bracket_id="b-001",
            symbol="BTC/USDT",
            side=BracketSide.LONG,
            entry_price=50000.0,
            quantity=0.1,
            tp_price=51000.0,
            sl_price=49500.0,
        )
        assert bracket.bracket_id == "b-001"
        assert bracket.symbol == "BTC/USDT"
        assert bracket.state == BracketState.PENDING_ENTRY

    def test_bracket_risk_reward(self) -> None:
        bracket = BracketOrder(
            bracket_id="b-001",
            symbol="BTC/USDT",
            side=BracketSide.LONG,
            entry_price=50000.0,
            quantity=0.1,
            tp_price=51000.0,
            sl_price=49500.0,
        )
        # reward = 1000, risk = 500, R:R = 2:1
        assert bracket.tp_price - bracket.entry_price == 1000.0
        assert bracket.entry_price - bracket.sl_price == 500.0

    def test_short_bracket(self) -> None:
        bracket = BracketOrder(
            bracket_id="b-002",
            symbol="ETH/USDT",
            side=BracketSide.SHORT,
            entry_price=3000.0,
            quantity=1.0,
            tp_price=2900.0,
            sl_price=3050.0,
        )
        assert bracket.side == BracketSide.SHORT
        assert bracket.tp_price < bracket.entry_price  # TP below entry for short
        assert bracket.sl_price > bracket.entry_price  # SL above entry for short


# ---------------------------------------------------------------------------
# BracketOrderManager
# ---------------------------------------------------------------------------


class TestBracketOrderManager:
    def test_place_bracket_returns_bracket(self) -> None:
        mgr = BracketOrderManager()
        bracket = mgr.place_bracket(
            symbol="BTC/USDT",
            side=BracketSide.LONG,
            entry_price=50000.0,
            quantity=0.1,
            tp_price=51000.0,
            sl_price=49500.0,
        )
        assert bracket.bracket_id is not None
        assert bracket.state == BracketState.PENDING_ENTRY

    def test_place_bracket_stores_in_memory(self) -> None:
        mgr = BracketOrderManager()
        bracket = mgr.place_bracket(
            symbol="BTC/USDT",
            side=BracketSide.LONG,
            entry_price=50000.0,
            quantity=0.1,
            tp_price=51000.0,
            sl_price=49500.0,
        )
        retrieved = mgr.get_bracket(bracket.bracket_id)
        assert retrieved is not None
        assert retrieved.bracket_id == bracket.bracket_id

    def test_on_entry_filled_activates_tp_sl(self) -> None:
        mgr = BracketOrderManager()
        bracket = mgr.place_bracket(
            symbol="BTC/USDT",
            side=BracketSide.LONG,
            entry_price=50000.0,
            quantity=0.1,
            tp_price=51000.0,
            sl_price=49500.0,
        )
        mgr.on_entry_filled(bracket.bracket_id)
        updated = mgr.get_bracket(bracket.bracket_id)
        assert updated.state == BracketState.ACTIVE

    def test_on_entry_filled_creates_tp_sl_orders(self) -> None:
        mgr = BracketOrderManager()
        bracket = mgr.place_bracket(
            symbol="BTC/USDT",
            side=BracketSide.LONG,
            entry_price=50000.0,
            quantity=0.1,
            tp_price=51000.0,
            sl_price=49500.0,
        )
        mgr.on_entry_filled(bracket.bracket_id)
        pending = mgr.get_pending_oco_orders(bracket.bracket_id)
        assert len(pending) == 2
        assert any(o["type"] == "tp" for o in pending)
        assert any(o["type"] == "sl" for o in pending)

    def test_tp_filled_cancels_sl(self) -> None:
        mgr = BracketOrderManager()
        bracket = mgr.place_bracket(
            symbol="BTC/USDT",
            side=BracketSide.LONG,
            entry_price=50000.0,
            quantity=0.1,
            tp_price=51000.0,
            sl_price=49500.0,
        )
        mgr.on_entry_filled(bracket.bracket_id)
        mgr.on_take_profit_filled(bracket.bracket_id)
        updated = mgr.get_bracket(bracket.bracket_id)
        assert updated.state == BracketState.TP_HIT
        # SL should be cancelled
        pending = mgr.get_pending_oco_orders(bracket.bracket_id)
        assert len(pending) == 0

    def test_sl_filled_cancels_tp(self) -> None:
        mgr = BracketOrderManager()
        bracket = mgr.place_bracket(
            symbol="BTC/USDT",
            side=BracketSide.LONG,
            entry_price=50000.0,
            quantity=0.1,
            tp_price=51000.0,
            sl_price=49500.0,
        )
        mgr.on_entry_filled(bracket.bracket_id)
        mgr.on_stop_loss_filled(bracket.bracket_id)
        updated = mgr.get_bracket(bracket.bracket_id)
        assert updated.state == BracketState.SL_HIT
        pending = mgr.get_pending_oco_orders(bracket.bracket_id)
        assert len(pending) == 0

    def test_entry_unfilled_tp_sl_not_active(self) -> None:
        mgr = BracketOrderManager()
        bracket = mgr.place_bracket(
            symbol="BTC/USDT",
            side=BracketSide.LONG,
            entry_price=50000.0,
            quantity=0.1,
            tp_price=51000.0,
            sl_price=49500.0,
        )
        # Don't call on_entry_filled — try to hit TP directly
        with pytest.raises(ValueError, match="entry.*not filled"):
            mgr.on_take_profit_filled(bracket.bracket_id)

    def test_cancel_bracket_cleans_up(self) -> None:
        mgr = BracketOrderManager()
        bracket = mgr.place_bracket(
            symbol="BTC/USDT",
            side=BracketSide.LONG,
            entry_price=50000.0,
            quantity=0.1,
            tp_price=51000.0,
            sl_price=49500.0,
        )
        mgr.cancel_bracket(bracket.bracket_id)
        updated = mgr.get_bracket(bracket.bracket_id)
        assert updated.state == BracketState.CANCELLED

    def test_cancel_active_bracket_cancels_oco(self) -> None:
        mgr = BracketOrderManager()
        bracket = mgr.place_bracket(
            symbol="BTC/USDT",
            side=BracketSide.LONG,
            entry_price=50000.0,
            quantity=0.1,
            tp_price=51000.0,
            sl_price=49500.0,
        )
        mgr.on_entry_filled(bracket.bracket_id)
        mgr.cancel_bracket(bracket.bracket_id)
        assert mgr.get_bracket(bracket.bracket_id).state == BracketState.CANCELLED
        pending = mgr.get_pending_oco_orders(bracket.bracket_id)
        assert len(pending) == 0

    def test_list_active_brackets(self) -> None:
        mgr = BracketOrderManager()
        mgr.place_bracket(
            symbol="BTC/USDT", side=BracketSide.LONG,
            entry_price=50000.0, quantity=0.1,
            tp_price=51000.0, sl_price=49500.0,
        )
        mgr.place_bracket(
            symbol="ETH/USDT", side=BracketSide.SHORT,
            entry_price=3000.0, quantity=1.0,
            tp_price=2900.0, sl_price=3050.0,
        )
        active = mgr.list_active_brackets()
        assert len(active) == 2

    def test_get_nonexistent_bracket_returns_none(self) -> None:
        mgr = BracketOrderManager()
        assert mgr.get_bracket("nonexistent") is None

    def test_short_bracket_tp_sl_prices(self) -> None:
        mgr = BracketOrderManager()
        bracket = mgr.place_bracket(
            symbol="ETH/USDT",
            side=BracketSide.SHORT,
            entry_price=3000.0,
            quantity=1.0,
            tp_price=2900.0,
            sl_price=3050.0,
        )
        mgr.on_entry_filled(bracket.bracket_id)
        pending = mgr.get_pending_oco_orders(bracket.bracket_id)
        tp_order = next(o for o in pending if o["type"] == "tp")
        sl_order = next(o for o in pending if o["type"] == "sl")
        # TP triggers when price <= tp_price for short
        assert tp_order["trigger_price"] == 2900.0
        # SL triggers when price >= sl_price for short
        assert sl_order["trigger_price"] == 3050.0

    def test_bracket_transitions(self) -> None:
        mgr = BracketOrderManager()
        b = mgr.place_bracket(
            symbol="BTC/USDT", side=BracketSide.LONG,
            entry_price=50000.0, quantity=0.1,
            tp_price=51000.0, sl_price=49500.0,
        )
        assert b.state == BracketState.PENDING_ENTRY

        mgr.on_entry_filled(b.bracket_id)
        assert mgr.get_bracket(b.bracket_id).state == BracketState.ACTIVE

        mgr.on_take_profit_filled(b.bracket_id)
        assert mgr.get_bracket(b.bracket_id).state == BracketState.TP_HIT

    def test_on_price_tick_triggers_tp(self) -> None:
        mgr = BracketOrderManager()
        bracket = mgr.place_bracket(
            symbol="BTC/USDT", side=BracketSide.LONG,
            entry_price=50000.0, quantity=0.1,
            tp_price=51000.0, sl_price=49500.0,
        )
        mgr.on_entry_filled(bracket.bracket_id)
        triggered = mgr.on_price_tick("BTC/USDT", 51100.0)
        assert len(triggered) == 1
        assert triggered[0]["type"] == "tp"

    def test_on_price_tick_triggers_sl(self) -> None:
        mgr = BracketOrderManager()
        bracket = mgr.place_bracket(
            symbol="BTC/USDT", side=BracketSide.LONG,
            entry_price=50000.0, quantity=0.1,
            tp_price=51000.0, sl_price=49500.0,
        )
        mgr.on_entry_filled(bracket.bracket_id)
        triggered = mgr.on_price_tick("BTC/USDT", 49400.0)
        assert len(triggered) == 1
        assert triggered[0]["type"] == "sl"

    def test_on_price_tick_no_trigger_in_range(self) -> None:
        mgr = BracketOrderManager()
        bracket = mgr.place_bracket(
            symbol="BTC/USDT", side=BracketSide.LONG,
            entry_price=50000.0, quantity=0.1,
            tp_price=51000.0, sl_price=49500.0,
        )
        mgr.on_entry_filled(bracket.bracket_id)
        triggered = mgr.on_price_tick("BTC/USDT", 50500.0)
        assert len(triggered) == 0

    def test_on_price_tick_wrong_symbol_no_trigger(self) -> None:
        mgr = BracketOrderManager()
        bracket = mgr.place_bracket(
            symbol="BTC/USDT", side=BracketSide.LONG,
            entry_price=50000.0, quantity=0.1,
            tp_price=51000.0, sl_price=49500.0,
        )
        mgr.on_entry_filled(bracket.bracket_id)
        triggered = mgr.on_price_tick("ETH/USDT", 51100.0)
        assert len(triggered) == 0

    def test_short_bracket_price_tick_triggers(self) -> None:
        mgr = BracketOrderManager()
        bracket = mgr.place_bracket(
            symbol="ETH/USDT", side=BracketSide.SHORT,
            entry_price=3000.0, quantity=1.0,
            tp_price=2900.0, sl_price=3050.0,
        )
        mgr.on_entry_filled(bracket.bracket_id)
        # TP for short: price drops to or below tp
        assert len(mgr.on_price_tick("ETH/USDT", 2890.0)) == 1
        assert mgr.get_bracket(bracket.bracket_id).state == BracketState.TP_HIT

    def test_short_bracket_sl_triggers(self) -> None:
        mgr = BracketOrderManager()
        bracket = mgr.place_bracket(
            symbol="ETH/USDT", side=BracketSide.SHORT,
            entry_price=3000.0, quantity=1.0,
            tp_price=2900.0, sl_price=3050.0,
        )
        mgr.on_entry_filled(bracket.bracket_id)
        assert len(mgr.on_price_tick("ETH/USDT", 3060.0)) == 1
        assert mgr.get_bracket(bracket.bracket_id).state == BracketState.SL_HIT
