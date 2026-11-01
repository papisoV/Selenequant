"""Integration coverage for the paper execution engine, tick by tick.

Each scenario seeds prices, submits orders through the public engine API and
checks what lands in positions / fills / callbacks afterwards.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from selene_space_paper.paper_execution import PaperExecutionEngine
from selene_space_types.trading import (
    BalanceUpdate,
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)

ACCOUNT = "paper-default"


def _buy_kwargs(symbol: str, qty: float) -> dict:
    return dict(
        account_id=ACCOUNT,
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=qty,
    )


def _sell_kwargs(symbol: str, qty: float) -> dict:
    return dict(
        account_id=ACCOUNT,
        symbol=symbol,
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=qty,
    )


@pytest_asyncio.fixture
async def engine() -> PaperExecutionEngine:
    eng = PaperExecutionEngine(initial_balance=100_000.0, fee_rate=0.001)
    await eng.start()
    return eng


class TestPaperTradingFlow:
    """Round-trip scenarios against the in-memory execution engine."""

    @pytest.mark.asyncio
    async def test_open_and_close_round_trip_books_pnl(
        self, engine: PaperExecutionEngine
    ) -> None:
        orders_seen: list[Order] = []
        fills_seen: list[Fill] = []
        positions_seen: list[Position] = []
        balances_seen: list[list[BalanceUpdate]] = []

        engine.on_order_update(orders_seen.append)
        engine.on_fill(fills_seen.append)
        engine.on_position_update(positions_seen.append)
        engine.on_balance_update(balances_seen.append)

        # Seed the market price so a market buy can fill immediately.
        await engine.on_price_tick("BTC/USDT", 60000.0)

        open_order = await engine.place_order(**_buy_kwargs("BTC/USDT", 1.0))
        assert open_order.status == OrderStatus.FILLED

        held = await engine.get_positions(ACCOUNT)
        assert len(held) == 1
        assert held[0].symbol == "BTC/USDT"
        assert held[0].side == OrderSide.BUY
        assert held[0].quantity == 1.0

        # Price rises 5k, then the position is closed at the better price.
        await engine.on_price_tick("BTC/USDT", 65000.0)

        close_order = await engine.place_order(**_sell_kwargs("BTC/USDT", 1.0))
        assert close_order.status == OrderStatus.FILLED

        history = await engine.get_fills(ACCOUNT)
        assert len(history) == 2
        assert float(history[0].price) == pytest.approx(60000.0, rel=0.001)
        assert float(history[1].price) == pytest.approx(65000.0, rel=0.001)

        assert len(orders_seen) >= 2
        assert len(fills_seen) >= 2
        assert len(positions_seen) >= 2
        assert len(balances_seen) >= 2

    @pytest.mark.asyncio
    async def test_limit_entry_then_stop_exit_trigger_on_ticks(
        self, engine: PaperExecutionEngine
    ) -> None:
        await engine.on_price_tick("ETH/USDT", 3500.0)

        resting_buy = await engine.place_order(
            account_id=ACCOUNT,
            symbol="ETH/USDT",
            side=OrderSide.BUY,
            quantity=10.0,
            price=3400.0,
            order_type=OrderType.LIMIT,
        )
        assert resting_buy.status == OrderStatus.ACCEPTED

        await engine.on_price_tick("ETH/USDT", 3350.0)
        filled_now = await engine.get_orders(ACCOUNT, status=OrderStatus.FILLED)
        assert len(filled_now) == 1

        protective_sell = await engine.place_order(
            account_id=ACCOUNT,
            symbol="ETH/USDT",
            side=OrderSide.SELL,
            quantity=10.0,
            stop_price=3200.0,
            order_type=OrderType.STOP_MARKET,
        )
        assert protective_sell.status == OrderStatus.ACCEPTED

        await engine.on_price_tick("ETH/USDT", 3150.0)
        filled_after_stop = await engine.get_orders(ACCOUNT, status=OrderStatus.FILLED)
        assert len(filled_after_stop) == 2

    @pytest.mark.asyncio
    async def test_positions_track_per_symbol(self, engine: PaperExecutionEngine) -> None:
        for sym, px, qty in (("BTC/USDT", 60000.0, 0.5), ("ETH/USDT", 3500.0, 5.0)):
            await engine.on_price_tick(sym, px)
            await engine.place_order(**_buy_kwargs(sym, qty))

        book = {p.symbol for p in await engine.get_positions(ACCOUNT)}
        assert "BTC/USDT" in book
        assert "ETH/USDT" in book
