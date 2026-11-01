"""ReconciliationManager: balance/order/position snapshots and reconnect flow."""

from __future__ import annotations

import asyncio

import pytest

from selene_space_exchange.base.reconciliation import ReconciliationManager
from selene_space_exchange.trading import BalanceInfo, OrderResponse, TradingClient
from selene_space_types.trading import AccountSnapshot, OrderSide, OrderStatus, OrderType


def _raw_position_book() -> list[dict]:
    return [
        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "60000.0"},
        {"symbol": "ETHUSDT", "positionAmt": "-2.0", "entryPrice": "3000.0"},
    ]


def _resting_order() -> OrderResponse:
    return OrderResponse(
        exchange="stub",
        order_id="ord-1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        status=OrderStatus.ACCEPTED,
        quantity=0.01,
        price=60000.0,
    )


class StubTradingClient(TradingClient):
    """TradingClient returning canned orders/balances plus optional raw positions."""

    def __init__(self, positions: list[dict] | None = None) -> None:
        self._raw_positions = positions if positions is not None else []

    @property
    def exchange_name(self) -> str:
        return "stub"

    async def place_order(self, request):  # type: ignore[override]
        raise NotImplementedError

    async def cancel_order(self, *, symbol: str, order_id: str):  # type: ignore[override]
        raise NotImplementedError

    async def get_order(self, *, symbol: str, order_id: str):  # type: ignore[override]
        raise NotImplementedError

    async def get_open_orders(self, symbol=None):  # type: ignore[override]
        return [_resting_order()]

    async def get_balances(self):  # type: ignore[override]
        return [
            BalanceInfo(asset="BTC", free=0.5, locked=0.1, total=0.6),
            BalanceInfo(asset="USDT", free=10000.0, locked=500.0, total=10500.0),
        ]

    async def get_positions(self, symbol=None):  # type: ignore[override]
        return self._raw_positions

    async def test_connectivity(self) -> bool:
        return True


class TestBalanceSnapshot:
    @pytest.mark.asyncio
    async def test_reconnect_snapshot_carries_balances_and_fires_callback(self) -> None:
        captured: list[AccountSnapshot] = []
        mgr = ReconciliationManager(StubTradingClient(), on_snapshot=captured.append)

        snap = await mgr.reconcile_on_reconnect()

        checks = {
            "exchange-tag": snap.exchange == "stub",
            "two-balances": len(snap.balances) == 2,
            "first-asset": (snap.balances[0].asset, snap.balances[0].free) == ("BTC", 0.5),
            "callback-fired-once": len(captured) == 1,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"


class TestOrderSync:
    @pytest.mark.asyncio
    async def test_open_orders_returned_and_pushed_to_callback(self) -> None:
        pushed: list[list[OrderResponse]] = []
        mgr = ReconciliationManager(StubTradingClient(), on_orders=pushed.append)

        orders = await mgr.reconcile_orders()

        checks = {
            "one-order": len(orders) == 1,
            "id": orders[0].order_id == "ord-1",
            "pushed": len(pushed) == 1,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"


class TestPositionSync:
    @pytest.mark.asyncio
    async def test_raw_adapter_dicts_pass_through_unmodified(self) -> None:
        mgr = ReconciliationManager(StubTradingClient(_raw_position_book()))

        positions = await mgr.reconcile_positions()

        assert [p["symbol"] for p in positions] == ["BTCUSDT", "ETHUSDT"]

    @pytest.mark.asyncio
    async def test_on_positions_callback_gets_the_full_list(self) -> None:
        pushed: list[list[dict]] = []
        mgr = ReconciliationManager(
            StubTradingClient(_raw_position_book()), on_positions=pushed.append
        )

        await mgr.reconcile_positions()

        assert (len(pushed), len(pushed[0])) == (1, 2)

    @pytest.mark.asyncio
    async def test_adapters_without_positions_default_to_empty(self) -> None:
        assert await ReconciliationManager(StubTradingClient()).reconcile_positions() == []


class TestReconnectAggregation:
    @pytest.mark.asyncio
    async def test_reconnect_also_pulls_positions(self) -> None:
        """Fills and funding can move positions during a WS outage — always resync."""
        pushed: list[list[dict]] = []
        mgr = ReconciliationManager(
            StubTradingClient(_raw_position_book()), on_positions=pushed.append
        )

        await mgr.reconcile_on_reconnect()

        assert (len(pushed), len(pushed[0])) == (1, 2)

    @pytest.mark.asyncio
    async def test_position_fetch_failure_spares_the_balance_snapshot(self) -> None:
        client = StubTradingClient()

        async def broken(*args, **kwargs):
            raise RuntimeError("positions endpoint down")

        client.get_positions = broken  # type: ignore[assignment]

        snap = await ReconciliationManager(client).reconcile_on_reconnect()
        assert len(snap.balances) == 2


@pytest.mark.asyncio
async def test_periodic_loop_starts_and_stops() -> None:
    mgr = ReconciliationManager(StubTradingClient())
    await mgr.start_periodic(interval_s=0.1)
    await asyncio.sleep(0.05)
    await mgr.stop()
