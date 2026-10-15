"""Tests for ShadowExecutionEngine market order fill semantics (Phase 2.1).

Verifies that:
- Market orders are NOT filled instantly on submission (no fake placeholder price)
- Market orders fill on the next `on_price_tick` using the real tick price ± slippage
- The legacy `_simulate_fill` with `1.0` placeholder and `asyncio.call_later` is gone
"""

from __future__ import annotations

import pytest

from selene_space_paper.fills import PaperSlippageModel  # noqa: F401  (kept for future realistic-slippage tests)
from selene_space_types.trading import (
    MarginMode,
    OrderSide,
    OrderType,
    PositionSide,
)

from selene_space_exchange.execution.shadow_engine import ShadowExecutionEngine
from selene_space_exchange.execution.order_journal import OrderJournal
from selene_space_exchange.execution.live_state import LiveStateRepository


class _NoSlippage:
    """Deterministic zero-slippage model to make fill price assertions exact.

    Duck-typed to match PaperSlippageModel.apply signature without inheriting
    from the Protocol (which would require typing.runtime_checkable).
    """

    def apply(self, price: float, side, *, order_notional: float = 0.0, avg_24h_volume: float = 0.0) -> float:
        return price


class TestShadowMarketOrderFillOnTick:
    """Phase 2.1: market orders fill on next tick, not on submission."""

    @pytest.mark.asyncio
    async def test_market_order_not_filled_on_submission(self) -> None:
        engine = ShadowExecutionEngine(slippage_model=_NoSlippage(), fill_on_next_tick=True)
        await engine.start()

        order = await engine.place_order(
            account_id="acct-1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=1.0,
            leverage=1,
            margin_mode=MarginMode.CROSS,
            pos_side=PositionSide.NET,
            reduce_only=False,
        )

        # Order is ACCEPTED but NOT FILLED — no tick has arrived yet
        assert order.status.value == "accepted"
        assert order.filled_quantity == 0.0
        # Pending queue should hold this order
        assert len(engine._pending_market_orders["BTC/USDT"]) == 1

    @pytest.mark.asyncio
    async def test_market_order_fills_on_next_tick_with_real_price(self) -> None:
        engine = ShadowExecutionEngine(slippage_model=_NoSlippage(), fill_on_next_tick=True)
        await engine.start()

        fills: list = []
        engine.on_fill(lambda f: fills.append(f))

        order = await engine.place_order(
            account_id="acct-1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=2.0,
            leverage=1,
            margin_mode=MarginMode.CROSS,
            pos_side=PositionSide.NET,
            reduce_only=False,
        )

        # No fill yet
        assert len(fills) == 0

        # Real tick arrives → order fills at tick price
        await engine.on_price_tick("BTC/USDT", 50000.0)

        assert len(fills) == 1
        assert fills[0].price == 50000.0
        assert fills[0].quantity == 2.0
        assert fills[0].symbol == "BTC/USDT"

        # Order should now be FILLED
        updated_orders = await engine.get_orders("acct-1")
        assert updated_orders[0].status.value == "filled"
        assert updated_orders[0].filled_quantity == 2.0

    @pytest.mark.asyncio
    async def test_market_order_fill_does_not_use_placeholder_price(self) -> None:
        """Regression: the old _simulate_fill used 1.0 as placeholder mid price.
        The new tick-driven flow must never produce a fill at price=1.0 when
        the tick price is something else."""
        engine = ShadowExecutionEngine(slippage_model=_NoSlippage(), fill_on_next_tick=True)
        await engine.start()

        fills: list = []
        engine.on_fill(lambda f: fills.append(f))

        await engine.place_order(
            account_id="acct-1",
            symbol="ETH/USDT",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=0.5,
            leverage=1,
            margin_mode=MarginMode.CROSS,
            pos_side=PositionSide.NET,
            reduce_only=False,
        )

        await engine.on_price_tick("ETH/USDT", 3000.0)

        assert len(fills) == 1
        assert fills[0].price == 3000.0  # NOT 1.0

    @pytest.mark.asyncio
    async def test_multiple_market_orders_fill_in_order_on_same_tick(self) -> None:
        engine = ShadowExecutionEngine(slippage_model=_NoSlippage(), fill_on_next_tick=True)
        await engine.start()

        fills: list = []
        engine.on_fill(lambda f: fills.append(f))

        for _ in range(3):
            await engine.place_order(
                account_id="acct-1",
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=1.0,
                leverage=1,
                margin_mode=MarginMode.CROSS,
                pos_side=PositionSide.NET,
                reduce_only=False,
            )

        await engine.on_price_tick("BTC/USDT", 40000.0)

        assert len(fills) == 3
        for f in fills:
            assert f.price == 40000.0

    @pytest.mark.asyncio
    async def test_pending_queue_drained_after_tick(self) -> None:
        engine = ShadowExecutionEngine(slippage_model=_NoSlippage(), fill_on_next_tick=True)
        await engine.start()

        await engine.place_order(
            account_id="acct-1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=1.0,
            leverage=1,
            margin_mode=MarginMode.CROSS,
            pos_side=PositionSide.NET,
            reduce_only=False,
        )

        assert len(engine._pending_market_orders.get("BTC/USDT", [])) == 1

        await engine.on_price_tick("BTC/USDT", 50000.0)

        # Queue should be empty after tick processed
        assert len(engine._pending_market_orders.get("BTC/USDT", [])) == 0


# ---------------------------------------------------------------------------
# get_fills / get_balances / get_positions — P0 stub fix
# ---------------------------------------------------------------------------


class TestShadowGetFillsAndBalances:
    """get_fills / get_balances used to return [] silently, making dry-run
    verification look like zero activity. Now they return real data."""

    @pytest.mark.asyncio
    async def test_get_fills_returns_filled_market_order(self) -> None:
        engine = ShadowExecutionEngine(slippage_model=_NoSlippage())
        await engine.start()
        try:
            await engine.place_order(
                "acct-1",
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=0.1,
                margin_mode=MarginMode.CROSS,
                pos_side=PositionSide.NET,
            )
            # No fills before a tick arrives.
            assert await engine.get_fills("acct-1") == []
            await engine.on_price_tick("BTC/USDT", 50000.0)
            fills = await engine.get_fills("acct-1")
            assert len(fills) == 1
            assert fills[0].symbol == "BTC/USDT"
            assert fills[0].side == "buy"
        finally:
            await engine.stop()

    @pytest.mark.asyncio
    async def test_get_fills_accumulates_across_multiple_ticks(self) -> None:
        engine = ShadowExecutionEngine(slippage_model=_NoSlippage())
        await engine.start()
        try:
            for _ in range(3):
                await engine.place_order(
                    "acct-1",
                    symbol="BTC/USDT",
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    quantity=0.05,
                    margin_mode=MarginMode.CROSS,
                    pos_side=PositionSide.NET,
                )
                await engine.on_price_tick("BTC/USDT", 50000.0)
            fills = await engine.get_fills("acct-1")
            assert len(fills) == 3
        finally:
            await engine.stop()

    @pytest.mark.asyncio
    async def test_get_balances_empty_for_unknown_account(self) -> None:
        engine = ShadowExecutionEngine()
        await engine.start()
        try:
            assert await engine.get_balances("never-traded") == []
        finally:
            await engine.stop()

    @pytest.mark.asyncio
    async def test_get_positions_returns_empty_with_docstring_rationale(self) -> None:
        """get_positions still returns [] — Position requires unrealized PnL
        which shadow cannot compute without a live mark price source.
        Documented in the method docstring."""
        engine = ShadowExecutionEngine()
        await engine.start()
        try:
            positions = await engine.get_positions("acct-1")
            assert positions == []
        finally:
            await engine.stop()

    @pytest.mark.asyncio
    async def test_strategy_signals_fired_counter_increments_on_place_order(self) -> None:
        """Phase 5.5-P0: place_order() increments _strategy_signals_fired counter.

        Observability contract: counter starts at 0, increments by 1 per call,
        readable via the strategy_signals_fired property for /api/v1/live/status.
        """
        engine = ShadowExecutionEngine()
        await engine.start()
        try:
            assert engine.strategy_signals_fired == 0
            await engine.place_order(
                "acct-1",
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=0.001,
                margin_mode=MarginMode.CROSS,
                pos_side=PositionSide.NET,
            )
            assert engine.strategy_signals_fired == 1
            await engine.place_order(
                "acct-1",
                symbol="ETH/USDT",
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=0.01,
                margin_mode=MarginMode.CROSS,
                pos_side=PositionSide.NET,
            )
            assert engine.strategy_signals_fired == 2
        finally:
            await engine.stop()

    @pytest.mark.asyncio
    async def test_ticker_msg_count_30min_tracks_on_price_tick(self) -> None:
        """Phase 5.5-P0: on_price_tick() increments _ticker_msg_count + updates _last_msg_ts.

        Observability contract: ticker_msg_count_30min returns 0 before any tick,
        returns N after N ticks within the 30-min window.
        """
        engine = ShadowExecutionEngine()
        await engine.start()
        try:
            assert engine.ticker_msg_count_30min == 0
            await engine.on_price_tick("BTC/USDT", 50000.0)
            await engine.on_price_tick("BTC/USDT", 50100.0)
            await engine.on_price_tick("BTC/USDT", 50200.0)
            assert engine.ticker_msg_count_30min == 3
        finally:
            await engine.stop()


# ---------------------------------------------------------------------------
# Shadow vs paper-mirror equity deviation (Stage B criterion 5)
# ---------------------------------------------------------------------------


class TestShadowEquityDeviation:
    """Dual-ledger comparison: shadow (realistic slippage) vs paper-mirror (1bp).

    Stage B criterion 5 requires |shadow − paper| / paper < 5%. The deviation
    is produced purely by the slippage-model difference between the two ledgers.
    """

    @pytest.mark.asyncio
    async def test_no_fills_deviation_is_zero(self) -> None:
        engine = ShadowExecutionEngine()
        await engine.start()
        try:
            assert engine.shadow_equity == 10_000.0
            assert engine.paper_mirror_equity == 10_000.0
            assert engine.equity_deviation_pct == 0.0
        finally:
            await engine.stop()

    @pytest.mark.asyncio
    async def test_fill_produces_small_positive_deviation(self) -> None:
        """Zero-slippage mirror makes the two ledgers diverge at the shadow fill price."""
        engine = ShadowExecutionEngine(paper_slippage=_NoSlippage())
        await engine.start()
        try:
            await engine.place_order(
                "acct-1",
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=1.0,
                margin_mode=MarginMode.CROSS,
                pos_side=PositionSide.NET,
            )
            await engine.on_price_tick("BTC/USDT", 50000.0)
            assert engine.shadow_equity != engine.paper_mirror_equity
            dev = engine.equity_deviation_pct
            assert dev is not None
            assert 0 < dev < 5.0  # Stage B threshold
        finally:
            await engine.stop()

    @pytest.mark.asyncio
    async def test_deviation_accumulates_across_fills(self) -> None:
        engine = ShadowExecutionEngine(paper_slippage=_NoSlippage())
        await engine.start()
        try:
            for i in range(5):
                await engine.place_order(
                    "acct-1",
                    symbol="BTC/USDT",
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    quantity=0.2,
                    margin_mode=MarginMode.CROSS,
                    pos_side=PositionSide.NET,
                )
                await engine.on_price_tick("BTC/USDT", 50_000.0 + i)
            assert engine.equity_deviation_pct is not None
            assert engine.shadow_equity != engine.paper_mirror_equity
        finally:
            await engine.stop()


# ---------------------------------------------------------------------------
# Kill-switch activation cancels open orders (Stage B criterion 6)
# ---------------------------------------------------------------------------


class TestShadowKillSwitch:
    """KillSwitch.activate() must actually cancel resting + pending shadow orders."""

    @pytest.mark.asyncio
    async def test_activation_cancels_pending_and_resting_orders(self, tmp_path) -> None:
        from selene_space_risk.kill_switch import KillSwitch

        KillSwitch._reset_singleton()  # isolate from other tests / real state
        ks = KillSwitch(tmp_path / "kill_switch.json")
        journal = OrderJournal(journal_dir=tmp_path / "journal")
        state = LiveStateRepository(state_dir=tmp_path / "live")
        engine = ShadowExecutionEngine(journal=journal, state_repo=state)
        await engine.start()
        try:
            engine.set_kill_switch(ks)
            # resting limit order (sits in the book)
            await engine.place_order(
                "acct-1",
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=1.0,
                price=40000.0,
                margin_mode=MarginMode.CROSS,
                pos_side=PositionSide.NET,
            )
            # pending market order (no tick yet)
            await engine.place_order(
                "acct-1",
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=0.5,
                margin_mode=MarginMode.CROSS,
                pos_side=PositionSide.NET,
            )
            assert len(engine._pending_market_orders.get("BTC/USDT", [])) == 1
            assert len(await engine.get_open_orders("acct-1")) == 2

            ks.activate("stage-b manual kill-switch test")

            assert len(engine._pending_market_orders.get("BTC/USDT", [])) == 0
            assert await engine.get_open_orders("acct-1") == []
            # WAL must not be left with stale intent/submitted entries
            assert journal.get_pending_entries() == []
        finally:
            await engine.stop()
