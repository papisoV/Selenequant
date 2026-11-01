"""PaperExecutionEngine end-to-end: orders, margin, fills, equity, callbacks."""

from __future__ import annotations

import pytest
import pytest_asyncio

from selene_space_paper.paper_execution import PaperExecutionEngine
from selene_space_types.trading import (
    BalanceUpdate,
    ExecutionMode,
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionSide,
)

ACCOUNT = "paper-default"
SEED = 100_000.0
FEE = 0.001
SYMBOL = "BTC-USDT-SWAP"
LISTING = 67_000.0


@pytest_asyncio.fixture
async def engine() -> PaperExecutionEngine:
    eng = PaperExecutionEngine(initial_balance=SEED, fee_rate=FEE)
    await eng.start()
    return eng


async def _market(engine: PaperExecutionEngine, px: float) -> None:
    await engine.on_price_tick(SYMBOL, px)


def _buy(
    engine: PaperExecutionEngine,
    notional: float,
    *,
    leverage: int = 10,
    pos_side: PositionSide | None = None,
):
    return _submit(
        engine,
        OrderSide.BUY,
        OrderType.MARKET,
        quantity=notional,
        leverage=leverage,
        pos_side=pos_side,
    )


def _submit(
    engine: PaperExecutionEngine,
    side: OrderSide,
    order_type: OrderType,
    *,
    quantity: float,
    price: float | None = None,
    leverage: int = 10,
    pos_side: PositionSide | None = None,
    reduce_only: bool | None = None,
):
    kwargs = dict(
        account_id=ACCOUNT,
        symbol=SYMBOL,
        side=side,
        order_type=order_type,
        quantity=quantity,
        leverage=leverage,
    )
    if price is not None:
        kwargs["price"] = price
    if pos_side is not None:
        kwargs["pos_side"] = pos_side
    if reduce_only is not None:
        kwargs["reduce_only"] = reduce_only
    return engine.place_order(**kwargs)


class TestEngineIdentityAndWallet:
    @pytest.mark.asyncio
    async def test_reports_paper_mode(self, engine: PaperExecutionEngine) -> None:
        assert (engine.mode, engine.exchange) == (ExecutionMode.PAPER, "paper")

    @pytest.mark.asyncio
    async def test_wallet_starts_with_single_usdt_lot(self, engine: PaperExecutionEngine) -> None:
        [lot] = await engine.get_balances(ACCOUNT)
        assert (lot.asset, lot.free) == ("USDT", SEED)


class TestMarketOrders:
    @pytest.mark.asyncio
    async def test_leveraged_market_order_fills_whole_size(
        self, engine: PaperExecutionEngine
    ) -> None:
        await _market(engine, LISTING)
        order = await _buy(engine, 1_000.0)
        assert (order.status, order.filled_quantity, order.leverage) == (
            OrderStatus.FILLED,
            1_000.0,
            10,
        )

    @pytest.mark.asyncio
    async def test_margin_plus_taker_fee_leave_the_wallet(
        self, engine: PaperExecutionEngine
    ) -> None:
        """10K notional at 10x → 1K margin + 5 fee (10K × 0.0005) = 98995 free."""
        await _market(engine, LISTING)
        await _buy(engine, 10_000.0)
        [lot] = await engine.get_balances(ACCOUNT)
        assert lot.free == pytest.approx(98_995.0)

    @pytest.mark.asyncio
    async def test_notional_beyond_wallet_is_refused(self, engine: PaperExecutionEngine) -> None:
        await _market(engine, LISTING)
        with pytest.raises(ValueError, match="Insufficient margin"):
            await _submit(engine, OrderSide.BUY, OrderType.MARKET, quantity=2_000_000.0, leverage=1)

    @pytest.mark.asyncio
    async def test_fill_lands_in_ledger_at_slipped_price(
        self, engine: PaperExecutionEngine
    ) -> None:
        """Default 1bp slippage keeps the fill within a hair of the tick."""
        await _market(engine, LISTING)
        await _buy(engine, 1_000.0)
        fills = await engine.get_fills(ACCOUNT)
        assert len(fills) >= 1
        assert float(fills[0].price) == pytest.approx(LISTING, rel=0.001)
        assert float(fills[0].quantity) == pytest.approx(1_000.0)

    @pytest.mark.asyncio
    async def test_fill_carries_execution_metadata(self, engine: PaperExecutionEngine) -> None:
        await _market(engine, LISTING)
        await _buy(engine, 1_000.0, pos_side=PositionSide.LONG)
        f = (await engine.get_fills(ACCOUNT))[0]
        checks = {
            "side": f.side == "buy",
            "pos_side": f.pos_side == "long",
            "symbol": f.symbol == SYMBOL,
            "leverage": f.leverage == 10,
            "exec_type": f.exec_type == "taker",
            "fee_rate": f.fee_rate > 0,
        }
        assert all(checks.values()), f"failed: {[k for k, ok in checks.items() if not ok]}"


class TestLimitOrders:
    @pytest.mark.asyncio
    async def test_distant_limit_rests_unfilled(self, engine: PaperExecutionEngine) -> None:
        await _market(engine, LISTING)
        order = await _submit(
            engine, OrderSide.BUY, OrderType.LIMIT, quantity=1_000.0, price=60_000.0
        )
        assert order.status in (OrderStatus.ACCEPTED, OrderStatus.CREATED)
        assert order.filled_quantity == 0.0

    @pytest.mark.asyncio
    async def test_price_crossing_completes_the_limit(self, engine: PaperExecutionEngine) -> None:
        await _market(engine, LISTING)
        resting = await _submit(
            engine, OrderSide.BUY, OrderType.LIMIT, quantity=1_000.0, price=66_000.0
        )
        assert resting.filled_quantity == 0.0

        await _market(engine, 65_000.0)
        (matched,) = [o for o in await engine.get_orders(ACCOUNT) if o.id == resting.id]
        assert matched.status == OrderStatus.FILLED
        assert matched.filled_quantity == 1_000.0

    @pytest.mark.asyncio
    async def test_cancel_pulls_a_resting_order(self, engine: PaperExecutionEngine) -> None:
        await _market(engine, LISTING)
        resting = await _submit(
            engine, OrderSide.BUY, OrderType.LIMIT, quantity=1_000.0, price=60_000.0
        )
        pulled = await engine.cancel_order(resting.id)
        assert pulled.status == OrderStatus.CANCELLED


class TestPositionsFromTrades:
    @pytest.mark.asyncio
    async def test_opening_a_trade_registers_the_position(
        self, engine: PaperExecutionEngine
    ) -> None:
        await _market(engine, LISTING)
        await _buy(engine, 5_000.0, pos_side=PositionSide.LONG)
        positions = await engine.get_positions(ACCOUNT)
        btc = next((p for p in positions if "BTC" in p.symbol), None)
        assert btc is not None
        assert (btc.quantity, btc.side, btc.leverage) == (5_000.0, OrderSide.BUY, 10)

    @pytest.mark.asyncio
    async def test_realized_pnl_persists_across_cycles(
        self, engine: PaperExecutionEngine
    ) -> None:
        await _market(engine, LISTING)
        await _buy(engine, 67_000.0, pos_side=PositionSide.LONG)
        await _market(engine, 68_000.0)
        await _submit(
            engine, OrderSide.SELL, OrderType.MARKET,
            quantity=67_000.0, pos_side=PositionSide.LONG, reduce_only=True,
        )
        acct = engine._account_mgr.get_account(ACCOUNT)
        assert acct is not None
        assert acct.total_realized_pnl != 0.0

    @pytest.mark.asyncio
    async def test_fee_tally_grows_on_trade(self, engine: PaperExecutionEngine) -> None:
        await _market(engine, LISTING)
        await _buy(engine, 10_000.0)
        acct = engine._account_mgr.get_account(ACCOUNT)
        assert acct is not None
        assert acct.total_fee > 0


class TestEquityAndFeeConfig:
    @pytest.mark.asyncio
    async def test_equity_dips_once_margin_is_locked(self, engine: PaperExecutionEngine) -> None:
        assert engine.compute_equity(ACCOUNT) == pytest.approx(SEED)
        await _market(engine, LISTING)
        await _buy(engine, 10_000.0, pos_side=PositionSide.LONG)
        assert engine.compute_equity(ACCOUNT) < SEED

    @pytest.mark.asyncio
    async def test_custom_fee_rates_take_effect(self, engine: PaperExecutionEngine) -> None:
        engine.update_fee_rates(ACCOUNT, 0.0001, 0.0003)
        maker, taker = engine._get_fee_rates(ACCOUNT)
        assert maker == pytest.approx(0.0001)
        assert taker == pytest.approx(0.0003)


class TestEventFanout:
    @pytest.mark.asyncio
    async def test_all_four_streams_fire_for_one_trade(
        self, engine: PaperExecutionEngine
    ) -> None:
        seen: dict[str, int] = {"order": 0, "fill": 0, "position": 0, "balance": 0}
        orders: list[Order] = []
        fills: list[Fill] = []
        positions: list[Position] = []
        balances: list[list[BalanceUpdate]] = []

        engine.on_order_update(lambda o: orders.append(o))
        engine.on_fill(lambda f: fills.append(f))
        engine.on_position_update(lambda p: positions.append(p))
        engine.on_balance_update(lambda b: balances.append(b))

        await _market(engine, LISTING)
        await _buy(engine, 1_000.0)

        seen.update(order=len(orders), fill=len(fills), position=len(positions), balance=len(balances))
        assert all(v >= 1 for v in seen.values()), f"silent streams: {[k for k, v in seen.items() if v < 1]}"
