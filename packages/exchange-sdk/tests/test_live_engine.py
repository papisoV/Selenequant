"""LiveExecutionEngine: order lifecycle, WS update intake, Phase 6 approval gate."""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

# place_order() refuses to fire until this switch flips; mock-client tests opt in here.
os.environ.setdefault("SELENE_ENABLE_REAL_TRADING", "1")

from selene_space_exchange.execution.live_engine import LiveExecutionEngine
from selene_space_exchange.trading import (
    BalanceInfo,
    OrderRequest,
    OrderResponse,
    TradingClient,
)
from selene_space_types.trading import (
    BalanceUpdate,
    ExchangeOrderUpdate,
    ExecutionMode,
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
)

RECONCILE_DISABLED_S = 9999
EXCH = "mock-live"
FILL_TS = 1711000000000


def _response(**fields) -> OrderResponse:
    """Minimal canned ack; callers override whatever they care about."""
    base = {
        "exchange": EXCH,
        "side": OrderSide.BUY,
        "order_type": OrderType.LIMIT,
        "quantity": 0.0,
    }
    return OrderResponse(**{**base, **fields})


class RecordingLiveClient(TradingClient):
    """Trading client that records every call and answers with sequential ids."""

    def __init__(self) -> None:
        self.placed: list[OrderRequest] = []
        self.cancelled: list[tuple[str, str]] = []

    @property
    def exchange_name(self) -> str:
        return EXCH

    async def place_order(self, request: OrderRequest) -> OrderResponse:
        self.placed.append(request)
        seq = len(self.placed)
        return _response(
            order_id=f"exch-{seq}",
            client_order_id=request.client_order_id or "",
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            status=OrderStatus.ACCEPTED,
            quantity=request.quantity,
            price=request.price,
        )

    async def cancel_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        self.cancelled.append((symbol, order_id))
        return _response(order_id=order_id, symbol=symbol, status=OrderStatus.CANCELLED)

    async def get_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        return _response(order_id=order_id, symbol=symbol, status=OrderStatus.ACCEPTED)

    async def get_open_orders(self, symbol=None) -> list[OrderResponse]:  # type: ignore[override]
        return []

    async def get_balances(self) -> list[BalanceInfo]:
        return [BalanceInfo(asset="USDT", free=50000.0, locked=0.0, total=50000.0)]

    async def test_connectivity(self) -> bool:
        return True


@pytest_asyncio.fixture
async def engine() -> LiveExecutionEngine:
    eng = LiveExecutionEngine(
        RecordingLiveClient(), reconciliation_interval_s=RECONCILE_DISABLED_S
    )
    await eng.start()
    # Conftest auto-approves via the constructor patch; belt-and-braces anyway.
    eng.approve_first_trade()
    return eng


def _buy_request() -> dict:
    return dict(
        account_id="test",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=0.01,
        price=60000.0,
    )


def _ws_update(status: str, **overrides) -> ExchangeOrderUpdate:
    did_fill = status == "FILLED"
    qty, px = (0.01, 60000.0) if did_fill else (0.0, 0.0)
    body = {
        "exchange": EXCH,
        "exchange_order_id": "exch-1",
        "client_order_id": None,
        "symbol": "BTC/USDT",
        "side": OrderSide.BUY,
        "order_type": "LIMIT",
        "status": status,
        "quantity": 0.01,
        "filled_quantity": qty,
        "avg_fill_price": px,
        "last_fill_price": px,
        "last_fill_quantity": qty,
        "commission": round(qty * px * 1e-5, 6),
        "commission_asset": "USDT",
        "timestamp": FILL_TS,
    }
    return ExchangeOrderUpdate(**{**body, **overrides})


class TestEngineBasics:
    @pytest.mark.asyncio
    async def test_mode_is_live(self, engine: LiveExecutionEngine) -> None:
        assert engine.mode is ExecutionMode.LIVE

    @pytest.mark.asyncio
    async def test_placed_order_reaches_accepted_and_fans_out(
        self, engine: LiveExecutionEngine
    ) -> None:
        order_events: list[Order] = []
        engine.on_order_update(order_events.append)

        placed = await engine.place_order(**_buy_request())

        checks = {
            "symbol": placed.symbol == "BTC/USDT",
            "status": placed.status is OrderStatus.ACCEPTED,
            "event-fanout": len(order_events) >= 1,
        }
        assert all(
            checks.values()
        ), f"failing: {[k for k, ok in checks.items() if not ok]}"

    @pytest.mark.asyncio
    async def test_cancellation_round_trip(self, engine: LiveExecutionEngine) -> None:
        placed = await engine.place_order(**_buy_request())
        cancelled = await engine.cancel_order(placed.id)
        assert cancelled.status is OrderStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_fills_list_starts_empty_but_queryable(
        self, engine: LiveExecutionEngine
    ) -> None:
        assert isinstance(await engine.get_fills("test"), list)


class TestExchangeUpdateIntake:
    @pytest.mark.asyncio
    async def test_ws_fill_updates_order_and_emits_fill_event(
        self, engine: LiveExecutionEngine
    ) -> None:
        fill_events: list[Fill] = []
        engine.on_fill(fill_events.append)

        await engine.place_order(**_buy_request())
        await engine.handle_exchange_order_update(_ws_update("FILLED"))

        orders = await engine.get_orders("test")
        checks = {
            "one-filled": sum(o.status is OrderStatus.FILLED for o in orders) >= 1,
            "fill-event": len(fill_events) >= 1,
        }
        assert all(
            checks.values()
        ), f"failing: {[k for k, ok in checks.items() if not ok]}"

    @pytest.mark.asyncio
    async def test_expired_status_is_preserved_not_downgraded(
        self, engine: LiveExecutionEngine
    ) -> None:
        """Phase 3.2: natural IOC expiry must stay EXPIRED — never look like a user cancel."""
        await engine.place_order(**_buy_request())
        await engine.handle_exchange_order_update(_ws_update("EXPIRED"))

        orders = await engine.get_orders("test")
        expired_count = sum(o.status is OrderStatus.EXPIRED for o in orders)
        cancelled_count = sum(o.status is OrderStatus.CANCELLED for o in orders)

        checks = {
            "expired-recorded": expired_count >= 1,
            "no-cancel-downgrade": cancelled_count == 0,
        }
        assert all(
            checks.values()
        ), f"failing: {[k for k, ok in checks.items() if not ok]}"

    @pytest.mark.asyncio
    async def test_balance_push_updates_cache_and_fires_event(
        self, engine: LiveExecutionEngine
    ) -> None:
        balance_events: list[list[BalanceUpdate]] = []
        engine.on_balance_update(balance_events.append)

        push = [
            BalanceUpdate(
                exchange=EXCH,
                asset="USDT",
                free=99000.0,
                locked=1000.0,
                timestamp=FILL_TS,
            )
        ]
        await engine.handle_balance_update(push)

        balances = await engine.get_balances("test")
        checks = {
            "cached": len(balances) >= 1,
            "free-value": balances[0].free == 99000.0,
            "event": len(balance_events) >= 1,
        }
        assert all(
            checks.values()
        ), f"failing: {[k for k, ok in checks.items() if not ok]}"


@pytest.mark.asyncio
async def test_stop_tears_down_cleanly() -> None:
    eng = LiveExecutionEngine(
        RecordingLiveClient(), reconciliation_interval_s=RECONCILE_DISABLED_S
    )
    await eng.start()
    await eng.stop()


class TestFirstTradeGate:
    """place_order refuses to fire until approve_first_trade(); reset per engine."""

    pytestmark = pytest.mark.no_auto_approve

    @staticmethod
    async def _fresh_engine(client: RecordingLiveClient) -> LiveExecutionEngine:
        eng = LiveExecutionEngine(
            client, reconciliation_interval_s=RECONCILE_DISABLED_S
        )
        await eng.start()
        return eng

    @pytest.mark.asyncio
    async def test_unapproved_engine_refuses_to_trade(self) -> None:
        client = RecordingLiveClient()
        eng = await self._fresh_engine(client)
        try:
            assert eng.first_trade_approved is False
            with pytest.raises(RuntimeError, match="first-trade approval not granted"):
                await eng.place_order(**_buy_request())
            assert client.placed == []  # nothing reached the wire
        finally:
            await eng.stop()

    @pytest.mark.asyncio
    async def test_approval_unlocks_trading(self) -> None:
        client = RecordingLiveClient()
        eng = await self._fresh_engine(client)
        try:
            eng.approve_first_trade()
            placed = await eng.place_order(**_buy_request())
            checks = {
                "accepted": placed.status is OrderStatus.ACCEPTED,
                "wire-count": len(client.placed) == 1,
            }
            assert all(
                checks.values()
            ), f"failing: {[k for k, ok in checks.items() if not ok]}"
        finally:
            await eng.stop()

    @pytest.mark.asyncio
    async def test_new_engine_starts_unapproved_again(self) -> None:
        first = await self._fresh_engine(RecordingLiveClient())
        first.approve_first_trade()
        assert first.first_trade_approved is True
        await first.stop()

        second = await self._fresh_engine(RecordingLiveClient())
        try:
            # Core Phase 6 guarantee: no approval leaks across restarts.
            assert second.first_trade_approved is False
        finally:
            await second.stop()

    @pytest.mark.asyncio
    async def test_double_approval_is_harmless(self) -> None:
        eng = await self._fresh_engine(RecordingLiveClient())
        try:
            eng.approve_first_trade()
            eng.approve_first_trade()
            assert eng.first_trade_approved is True
        finally:
            await eng.stop()


class TestOrderRequestCarriesReduceOnlyAndPosSide:
    """Fix①: place_order must forward reduce_only/pos_side into the
    OrderRequest sent to the adapter — previously dropped at the
    construction site, making stop-close semantics dead on the wire."""

    @pytest.mark.asyncio
    async def test_reduce_only_reaches_the_adapter(
        self, engine: LiveExecutionEngine
    ) -> None:
        await engine.place_order(
            account_id="test",
            symbol="BTC/USDT",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=0.01,
            reduce_only=True,
        )
        assert engine._client.placed[-1].reduce_only is True

    @pytest.mark.asyncio
    async def test_pos_side_reaches_the_adapter(
        self, engine: LiveExecutionEngine
    ) -> None:
        await engine.place_order(
            account_id="test",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.01,
            pos_side="LONG",
        )
        assert engine._client.placed[-1].pos_side == "LONG"

    @pytest.mark.asyncio
    async def test_default_is_net_order_without_pos_side(
        self, engine: LiveExecutionEngine
    ) -> None:
        await engine.place_order(
            account_id="test",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.01,
        )
        sent = engine._client.placed[-1]
        assert sent.reduce_only is False
        assert sent.pos_side is None
