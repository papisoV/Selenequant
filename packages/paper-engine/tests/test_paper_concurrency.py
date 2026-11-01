"""Concurrency tests for PaperExecutionEngine — H1/H3/H8/H14 fixes.

Covers:
- H1: no double-fill under concurrent on_price_tick storms
- H3: _try_fill_order_locked rolls back order/account/position on exception
- H8: cancel_order vs fill race does not corrupt state
- H14: delete_account_async cancels in-flight fill tasks
- Deadlock: place_order + _try_fill_order do not deadlock under lock
- CancelledError path releases the account lock
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio

from selene_space_paper.paper_execution import PaperExecutionEngine
from selene_space_types.trading import (
    MarginMode,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
)

ACCOUNT = "paper-default"


@pytest_asyncio.fixture
async def engine() -> PaperExecutionEngine:
    eng = PaperExecutionEngine(initial_balance=100_000.0, fee_rate=0.001)
    await eng.start()
    return eng


class TestPaperConcurrency:
    @pytest.mark.asyncio
    async def test_no_double_fill_under_tick_storm(
        self, engine: PaperExecutionEngine
    ) -> None:
        """H1: two concurrent on_price_tick calls must not double-fill an order."""
        await engine.on_price_tick("BTC-USDT-SWAP", 67000.0)

        order = await engine.place_order(
            account_id=ACCOUNT,
            symbol="BTC-USDT-SWAP",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=1000.0,
            price=66000.0,
            leverage=10,
        )
        assert order.filled_quantity == 0.0

        # Two ticks at a fill-triggering price run concurrently. The per-account
        # lock serializes the two fill pipelines; the second must observe the
        # order as FILLED and skip (try_fill status guard).
        await asyncio.gather(
            engine.on_price_tick("BTC-USDT-SWAP", 65000.0),
            engine.on_price_tick("BTC-USDT-SWAP", 65000.0),
        )

        orders = await engine.get_orders(ACCOUNT)
        filled = [o for o in orders if o.id == order.id]
        assert len(filled) == 1
        assert filled[0].status == OrderStatus.FILLED
        assert filled[0].filled_quantity == pytest.approx(1000.0)

        # Exactly one fill record should exist for this order.
        all_fills = await engine.get_fills(ACCOUNT)
        order_fills = [f for f in all_fills if f.order_id == order.id]
        assert len(order_fills) == 1

    @pytest.mark.asyncio
    async def test_cancel_order_during_fill_no_corruption(
        self, engine: PaperExecutionEngine
    ) -> None:
        """H8: cancel_order racing a fill leaves consistent state."""
        await engine.on_price_tick("BTC-USDT-SWAP", 67000.0)

        order = await engine.place_order(
            account_id=ACCOUNT,
            symbol="BTC-USDT-SWAP",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=1000.0,
            price=66000.0,
            leverage=10,
        )

        # Run a fill-triggering tick concurrently with a cancel. Both acquire
        # the per-account lock; whichever runs first wins. Either way the final
        # order state must be a terminal, consistent status (FILLED or
        # CANCELLED), never an intermediate state.
        fill_task = asyncio.ensure_future(
            engine.on_price_tick("BTC-USDT-SWAP", 65000.0)
        )
        cancel_task = asyncio.ensure_future(engine.cancel_order(order.id))
        await asyncio.gather(fill_task, cancel_task, return_exceptions=True)

        final_order = engine._order_mgr.get_order(order.id)
        assert final_order is not None
        assert final_order.status in (OrderStatus.FILLED, OrderStatus.CANCELLED)

        # If FILLED, the order should be fully filled; if CANCELLED, not filled
        # beyond what was already there (0).
        if final_order.status == OrderStatus.FILLED:
            assert final_order.filled_quantity == pytest.approx(1000.0)
        else:
            assert final_order.filled_quantity == pytest.approx(0.0)

        # Account balance must be consistent with the outcome — no half-applied
        # margin debit without a corresponding fill.
        acct = engine._account_mgr.get_account(ACCOUNT)
        assert acct is not None
        if final_order.status == OrderStatus.CANCELLED:
            # No fill happened, balance unchanged from initial minus nothing.
            assert acct.current_balance == pytest.approx(100_000.0)

    @pytest.mark.asyncio
    async def test_try_fill_order_rollback_on_exception(
        self, engine: PaperExecutionEngine
    ) -> None:
        """H3: a mid-fill exception rolls back order/account/position state."""
        await engine.on_price_tick("BTC-USDT-SWAP", 67000.0)

        order = await engine.place_order(
            account_id=ACCOUNT,
            symbol="BTC-USDT-SWAP",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=1000.0,
            price=66000.0,
            leverage=10,
        )

        acct_before = engine._account_mgr.get_account(ACCOUNT)
        assert acct_before is not None
        balance_before = acct_before.current_balance
        realized_before = acct_before.total_realized_pnl
        fee_before = acct_before.total_fee
        order_filled_before = order.filled_quantity
        order_status_before = order.status
        order_avg_before = order.avg_fill_price

        # Force apply_fill_with_symbol to raise mid-pipeline. try_fill mutates
        # the order first (filled_quantity/status/avg_fill_price), then
        # apply_fill_with_symbol runs — the rollback must restore all three.
        original_apply = engine._position_mgr.apply_fill_with_symbol

        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("simulated mid-fill failure")

        with patch.object(
            engine._position_mgr, "apply_fill_with_symbol", side_effect=_boom
        ):
            with pytest.raises(RuntimeError, match="simulated mid-fill failure"):
                await engine._try_fill_order(order, 65000.0)

        # Restore the original method (patch context already does this on exit,
        # but be explicit for clarity).
        del original_apply

        # Order state rolled back.
        assert order.filled_quantity == order_filled_before
        assert order.status == order_status_before
        assert order.avg_fill_price == order_avg_before

        # Account state rolled back.
        acct_after = engine._account_mgr.get_account(ACCOUNT)
        assert acct_after is not None
        assert acct_after.current_balance == pytest.approx(balance_before)
        assert acct_after.total_realized_pnl == pytest.approx(realized_before)
        assert acct_after.total_fee == pytest.approx(fee_before)

        # No fill record should have been appended.
        all_fills = await engine.get_fills(ACCOUNT)
        assert all(f.order_id != order.id for f in all_fills)

    @pytest.mark.asyncio
    async def test_delete_account_async_cancels_inflight_tasks(
        self, engine: PaperExecutionEngine
    ) -> None:
        """H14: delete_account_async cancels in-flight fill tasks."""
        await engine.on_price_tick("BTC-USDT-SWAP", 67000.0)

        # Place a limit order that would fill on a tick at 65000.
        order = await engine.place_order(
            account_id=ACCOUNT,
            symbol="BTC-USDT-SWAP",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=1000.0,
            price=66000.0,
            leverage=10,
        )

        # Make _fire_order_update (called inside the locked fill pipeline) hang
        # on an event we never set, so the fill task acquires the lock and then
        # blocks — keeping itself in-flight and holding the lock.
        hang_event = asyncio.Event()
        original_fire = engine._fire_order_update

        async def _hanging_fire(o: Any) -> None:
            await hang_event.wait()  # never set
            await original_fire(o)

        with patch.object(engine, "_fire_order_update", side_effect=_hanging_fire):
            # Schedule the fill as a background task. It will acquire the lock,
            # run try_fill, then block on _fire_order_update (hang_event).
            fill_task = engine._schedule_fill_task(ACCOUNT, order, 65000.0)
            # Yield so the task starts, acquires the lock, and reaches the hang.
            for _ in range(5):
                await asyncio.sleep(0)
            assert not fill_task.done()

            # delete_account_async must cancel the in-flight fill task. The
            # cancellation propagates into _fire_order_update (which is awaiting
            # hang_event); the async with in _try_fill_order releases the lock
            # on CancelledError, then delete_account_async acquires it and
            # removes the account.
            result = await asyncio.wait_for(
                engine.delete_account_async(ACCOUNT, timeout=5.0),
                timeout=15.0,
            )
            assert result is True

        # Account removed.
        assert engine._account_mgr.get_account(ACCOUNT) is None
        # Fill task was cancelled (not still running).
        assert fill_task.cancelled() or fill_task.done()

    @pytest.mark.asyncio
    async def test_place_order_no_deadlock_with_try_fill(
        self, engine: PaperExecutionEngine
    ) -> None:
        """place_order holds the lock and calls _try_fill_order_locked — no deadlock."""
        await engine.on_price_tick("BTC-USDT-SWAP", 67000.0)

        # A market order goes through place_order -> _place_order_inner ->
        # _try_fill_order_locked under the same lock. If _try_fill_order
        # (re-entrant) were called instead, this would deadlock.
        async def _place() -> Any:
            return await engine.place_order(
                account_id=ACCOUNT,
                symbol="BTC-USDT-SWAP",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=1000.0,
                leverage=10,
                margin_mode=MarginMode.CROSS,
                pos_side=PositionSide.LONG,
            )

        order = await asyncio.wait_for(_place(), timeout=30.0)
        assert order.status == OrderStatus.FILLED
        assert order.filled_quantity == pytest.approx(1000.0)

    @pytest.mark.asyncio
    async def test_cancel_order_lock_released_on_cancelled_error(
        self, engine: PaperExecutionEngine
    ) -> None:
        """CancelledError during cancel_order must still release the account lock."""
        await engine.on_price_tick("BTC-USDT-SWAP", 67000.0)

        order = await engine.place_order(
            account_id=ACCOUNT,
            symbol="BTC-USDT-SWAP",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=1000.0,
            price=60000.0,
            leverage=10,
        )

        # Make _fire_order_update raise CancelledError so cancel_order's locked
        # body raises mid-flight. async with must release the lock.
        async def _cancel_fire(o: Any) -> None:
            raise asyncio.CancelledError()

        with patch.object(engine, "_fire_order_update", side_effect=_cancel_fire):
            with pytest.raises(asyncio.CancelledError):
                await engine.cancel_order(order.id)

        # Lock must be released — a subsequent operation must not deadlock.
        lock = engine._account_locks[ACCOUNT]
        assert not lock.locked()

        # And a real place_order on the same account must complete.
        order2 = await asyncio.wait_for(
            engine.place_order(
                account_id=ACCOUNT,
                symbol="BTC-USDT-SWAP",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=500.0,
                leverage=10,
            ),
            timeout=30.0,
        )
        assert order2.status == OrderStatus.FILLED
