"""Tests for shadow engine price-feed bridge (P1-4 Stage A).

Validates that ``_bridge_price_to_shadow`` wires the MarketDataService
ticker callback to ShadowExecutionEngine.on_price_tick so shadow market
orders actually fill when real ticks arrive. Before this bridge, the
shadow engine's ``_pending_market_orders`` queue never drained — shadow
mode silently did nothing.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from selene_space_exchange.execution.shadow_engine import ShadowExecutionEngine
from selene_space_types.market import TickerEvent
from selene_space_types.trading import OrderSide, OrderStatus, OrderType


class _FakeMarketService:
    """Captures the on_ticker callback so tests can fire synthetic ticks."""

    def __init__(self) -> None:
        self._callbacks: list[Any] = []

    def on_ticker(self, cb: Any) -> None:
        self._callbacks.append(cb)

    def fire_ticker(self, symbol: str, price: float) -> None:
        event = TickerEvent(
            exchange="binance",
            symbol=symbol,
            timestamp=0,
            last_price=price,
            bid=price * 0.999,
            ask=price * 1.001,
            volume_24h=1000.0,
            change_24h_pct=0.0,
        )
        for cb in self._callbacks:
            cb(event)


@pytest.mark.asyncio
async def test_bridge_routes_ticker_to_shadow_on_price_tick() -> None:
    """A ticker event fires → shadow.on_price_tick is invoked.

    We detect the call by placing a pending market order and verifying
    that the tick drains it into a Fill.
    """
    shadow = ShadowExecutionEngine()
    await shadow.start()
    try:
        market = _FakeMarketService()
        # Import here so the test fails clearly if main.py moves the helper.
        from app.main import _bridge_price_to_shadow

        _bridge_price_to_shadow(market, shadow)

        # Place a market buy — goes into _pending_market_orders[symbol].
        order = await shadow.place_order(
            account_id="acct-1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.1,
            price=None,
            strategy_id="acct-1",
        )
        assert order.status in (OrderStatus.ACCEPTED, OrderStatus.PARTIAL)
        # Pending queue should have our order.
        assert "BTC/USDT" in shadow._pending_market_orders
        assert len(shadow._pending_market_orders["BTC/USDT"]) == 1

        # Fire a ticker tick — this should drain the pending queue and fill.
        market.fire_ticker("BTC/USDT", 50_000.0)
        # on_price_tick is async — give the event loop a beat to run it.
        await asyncio.sleep(0.01)

        # Pending queue should now be empty (drained by on_price_tick).
        assert "BTC/USDT" not in shadow._pending_market_orders or \
            len(shadow._pending_market_orders.get("BTC/USDT", [])) == 0

        # Fill should have been recorded via _fire_fill callbacks. Since no
        # callbacks are registered, the side effect is the order moving to
        # FILLED in the order manager + a persisted Fill in state repo.
        updated = shadow._order_manager.get_order(order.id)
        assert updated is not None
        assert updated.status == OrderStatus.FILLED
    finally:
        await shadow.stop()


@pytest.mark.asyncio
async def test_shadow_market_order_fills_on_tick_with_slippage() -> None:
    """Fill price reflects the slippage model applied to the tick price.

    Default RealisticSlippage adds a small spread. For a BUY at 50_000,
    fill price should be slightly above 50_000.
    """
    shadow = ShadowExecutionEngine()
    await shadow.start()
    try:
        market = _FakeMarketService()
        from app.main import _bridge_price_to_shadow

        _bridge_price_to_shadow(market, shadow)

        order = await shadow.place_order(
            account_id="acct-1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.1,
            price=None,
            strategy_id="acct-1",
        )

        market.fire_ticker("BTC/USDT", 50_000.0)
        await asyncio.sleep(0.01)

        updated = shadow._order_manager.get_order(order.id)
        assert updated is not None
        assert updated.status == OrderStatus.FILLED
        # BUY slippage pushes fill price slightly above the tick price.
        assert updated.avg_fill_price >= 50_000.0
        # Sanity: slippage should be small (< 1% of price).
        assert updated.avg_fill_price < 50_500.0
    finally:
        await shadow.stop()


@pytest.mark.asyncio
async def test_shadow_ignores_ticks_for_symbols_with_no_pending_orders() -> None:
    """A tick for ETH when only BTC has pending orders is a no-op for BTC."""
    shadow = ShadowExecutionEngine()
    await shadow.start()
    try:
        market = _FakeMarketService()
        from app.main import _bridge_price_to_shadow

        _bridge_price_to_shadow(market, shadow)

        order = await shadow.place_order(
            account_id="acct-1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.1,
            price=None,
            strategy_id="acct-1",
        )

        # Fire ETH tick — should not affect BTC pending orders.
        market.fire_ticker("ETH/USDT", 3_000.0)
        await asyncio.sleep(0.01)

        # BTC order still pending (not filled by an unrelated symbol's tick).
        assert "BTC/USDT" in shadow._pending_market_orders
        assert len(shadow._pending_market_orders["BTC/USDT"]) == 1
        updated = shadow._order_manager.get_order(order.id)
        assert updated is not None
        assert updated.status != OrderStatus.FILLED

        # Now fire BTC tick → fills.
        market.fire_ticker("BTC/USDT", 50_000.0)
        await asyncio.sleep(0.01)
        updated = shadow._order_manager.get_order(order.id)
        assert updated is not None
        assert updated.status == OrderStatus.FILLED
    finally:
        await shadow.stop()


@pytest.mark.asyncio
async def test_paper_and_shadow_bridges_both_receive_ticks() -> None:
    """When both bridges are wired, both engines receive the same tick.

    This guards against accidentally sharing a single callback list —
    each engine must get its own ticker dispatch.
    """
    from selene_space_paper.paper_execution import PaperExecutionEngine

    paper = PaperExecutionEngine(initial_balance=10_000.0)
    shadow = ShadowExecutionEngine()
    await shadow.start()
    try:
        market = _FakeMarketService()
        from app.main import _bridge_price_to_paper, _bridge_price_to_shadow

        _bridge_price_to_paper(market, paper)
        _bridge_price_to_shadow(market, shadow)

        # Both engines should have their callback registered.
        assert len(market._callbacks) == 2

        # Place a shadow order, fire tick, verify shadow filled.
        order = await shadow.place_order(
            account_id="acct-1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.1,
            price=None,
            strategy_id="acct-1",
        )
        market.fire_ticker("BTC/USDT", 50_000.0)
        await asyncio.sleep(0.01)

        updated = shadow._order_manager.get_order(order.id)
        assert updated is not None
        assert updated.status == OrderStatus.FILLED
    finally:
        await shadow.stop()
