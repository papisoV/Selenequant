"""P2-1: disconnect triage decision tree tests.

Covers the spec's acceptance criteria:
1. triage_disconnect pure function — GRACE / CLOSE_PROTECT / SILENT_RETRY
   (including the inclusive boundary elapsed == grace_seconds * 1000).
2. classify_unknown_order — matching prefix → False, mismatch → True.
3. One-time outside-order warning latch (module flag reset logic).
4. LiveExecutionEngine integration — GRACE timer start / cancel on
   reconnect / flatten_hook fired on grace expiry.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from selene_space_exchange.execution.disconnect_triage import (
    DisconnectLevel,
    classify_unknown_order,
    reset_outside_order_warning,
    should_warn_outside_order,
    triage_disconnect,
)
from selene_space_exchange.execution.live_engine import LiveExecutionEngine


# ---------------------------------------------------------------------------
# 1. triage_disconnect pure function
# ---------------------------------------------------------------------------


class TestTriageDisconnect:
    def test_exposure_inside_window_returns_grace(self) -> None:
        outcome = triage_disconnect(
            has_positions=True,
            has_open_orders=False,
            disconnected_at_ms=1_000,
            now_ms=1_000 + 899_000,  # 899s < 900s grace
        )
        assert outcome.level is DisconnectLevel.GRACE
        assert outcome.grace_seconds == 900
        assert "宽限期" in outcome.reason

    def test_exposure_at_grace_boundary_is_close_protect(self) -> None:
        # Inclusive boundary: elapsed == grace_seconds * 1000 must flip.
        outcome = triage_disconnect(
            has_positions=True,
            has_open_orders=False,
            disconnected_at_ms=1_000,
            now_ms=1_000 + 900 * 1000,  # exactly 900s
        )
        assert outcome.level is DisconnectLevel.CLOSE_PROTECT
        assert "平仓保护" in outcome.reason

    def test_exposure_beyond_grace_returns_close_protect(self) -> None:
        outcome = triage_disconnect(
            has_positions=False,
            has_open_orders=True,
            disconnected_at_ms=1_000,
            now_ms=1_000 + 901_000,
        )
        assert outcome.level is DisconnectLevel.CLOSE_PROTECT

    def test_flat_book_returns_silent_retry(self) -> None:
        outcome = triage_disconnect(
            has_positions=False,
            has_open_orders=False,
            disconnected_at_ms=1_000,
            now_ms=1_000 + 9_000_000,  # very long downtime, but no exposure
        )
        assert outcome.level is DisconnectLevel.SILENT_RETRY

    def test_custom_grace_seconds_respected(self) -> None:
        outcome = triage_disconnect(
            has_positions=True,
            has_open_orders=False,
            disconnected_at_ms=1_000,
            now_ms=1_000 + 10_000,  # 10s
            grace_seconds=5,
        )
        assert outcome.level is DisconnectLevel.CLOSE_PROTECT


# ---------------------------------------------------------------------------
# 2. classify_unknown_order
# ---------------------------------------------------------------------------


class TestClassifyUnknownOrder:
    def test_matching_prefix_is_not_unknown(self) -> None:
        assert classify_unknown_order("selenespace-abcd1234", ["selenespace-"]) is False

    def test_non_matching_prefix_is_unknown(self) -> None:
        assert classify_unknown_order("MANUAL-ORDER-1", ["selenespace-"]) is True

    def test_empty_client_order_id_is_unknown(self) -> None:
        # No client_order_id means we can't prove it's ours → treat as manual.
        assert classify_unknown_order("", ["selenespace-"]) is True


# ---------------------------------------------------------------------------
# 3. One-time outside-order warning latch
# ---------------------------------------------------------------------------


class TestOutsideOrderWarningOnce:
    def setup_method(self) -> None:
        reset_outside_order_warning()

    def test_warns_first_time_only(self) -> None:
        assert should_warn_outside_order() is True
        assert should_warn_outside_order() is False
        assert should_warn_outside_order() is False

    def test_reset_rearms_latch(self) -> None:
        assert should_warn_outside_order() is True
        assert should_warn_outside_order() is False
        reset_outside_order_warning()
        assert should_warn_outside_order() is True
        assert should_warn_outside_order() is False


# ---------------------------------------------------------------------------
# 4. LiveExecutionEngine integration
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> LiveExecutionEngine:
    """Engine wired with a fake flatten_hook; no reconciliation/WS."""
    hook = Mock()
    client: Any = SimpleNamespace(
        exchange_name="test",
        place_order=AsyncMock(),
        cancel_order=AsyncMock(),
        get_open_orders=AsyncMock(return_value=[]),
        get_balances=AsyncMock(return_value=[]),
        get_positions=AsyncMock(return_value=[]),
    )
    eng = LiveExecutionEngine(
        trading_client=client,
        reconciliation_interval_s=9999,
        state_repo=None,
        flatten_hook=hook,
    )
    eng.approve_first_trade()
    return eng


class TestEngineDisconnectTriage:
    @pytest.mark.asyncio
    async def test_disconnect_with_exposure_starts_grace_timer(
        self, engine: LiveExecutionEngine
    ) -> None:
        engine._positions = [
            SimpleNamespace(symbol="BTC-USDT-SWAP", pos_side="net", quantity=1.0)
        ]
        await engine.handle_ws_disconnect(grace_seconds=30)
        assert engine._disconnect_grace_task is not None
        assert engine._disconnected_at_ms is not None
        assert engine._flatten_hook is not None
        assert not engine._flatten_hook.fire_and_forget.called
        await engine._cancel_grace_timer()

    @pytest.mark.asyncio
    async def test_reconnect_cancels_grace_timer(
        self, engine: LiveExecutionEngine
    ) -> None:
        engine._positions = [
            SimpleNamespace(symbol="BTC-USDT-SWAP", pos_side="net", quantity=1.0)
        ]
        await engine.handle_ws_disconnect(grace_seconds=30)
        task = engine._disconnect_grace_task
        assert task is not None
        await engine.handle_ws_reconnect()
        assert engine._disconnect_grace_task is None
        assert engine._disconnected_at_ms is None
        assert task.cancelled() or task.done()
        assert engine._flatten_hook is not None
        assert not engine._flatten_hook.fire_and_forget.called

    @pytest.mark.asyncio
    async def test_grace_expiry_fires_flatten_hook(
        self, engine: LiveExecutionEngine
    ) -> None:
        engine._positions = [
            SimpleNamespace(symbol="BTC-USDT-SWAP", pos_side="net", quantity=1.0)
        ]
        # Past disconnect timestamp → elapsed exceeds a zero-second grace.
        import time as _time

        engine._disconnected_at_ms = int(_time.time() * 1000) - 1
        await engine._grace_timer(grace_seconds=0)
        assert engine._flatten_hook is not None
        assert engine._flatten_hook.fire_and_forget.called

    @pytest.mark.asyncio
    async def test_no_exposure_does_not_start_timer(
        self, engine: LiveExecutionEngine
    ) -> None:
        await engine.handle_ws_disconnect(grace_seconds=30)
        assert engine._disconnect_grace_task is None
        assert engine._flatten_hook is not None
        assert not engine._flatten_hook.fire_and_forget.called


# ---------------------------------------------------------------------------
# 5. Manual-order isolation in handle_exchange_order_update
# ---------------------------------------------------------------------------


class TestManualOrderIsolation:
    @pytest.mark.asyncio
    async def test_manual_client_order_id_is_isolated(
        self, engine: LiveExecutionEngine
    ) -> None:
        reset_outside_order_warning()
        from selene_space_types.trading import ExchangeOrderUpdate, OrderSide

        update = ExchangeOrderUpdate(
            exchange="test",
            exchange_order_id="ex-manual-1",
            client_order_id="MANUAL-ORDER-1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type="LIMIT",
            status="NEW",
            quantity=0.01,
            timestamp=0,
        )
        await engine.handle_exchange_order_update(update)
        # Manual order must NOT be tracked as a system order.
        assert engine._orders == {}
        assert engine._fills == []

    @pytest.mark.asyncio
    async def test_own_prefixed_order_still_tracked(
        self, engine: LiveExecutionEngine
    ) -> None:
        reset_outside_order_warning()
        from selene_space_types.trading import ExchangeOrderUpdate, OrderSide

        update = ExchangeOrderUpdate(
            exchange="test",
            exchange_order_id="ex-own-1",
            client_order_id="selenespace-abc123",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type="LIMIT",
            status="NEW",
            quantity=0.01,
            timestamp=0,
        )
        await engine.handle_exchange_order_update(update)
        assert len(engine._orders) == 1
        assert len(engine._fills) == 0
