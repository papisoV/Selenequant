"""Tests for IOC close capability (#2):
1. LiveExecutionEngine.place_order forwards time_in_force → OrderRequest.
2. BinanceFuturesTradingAdapter forwards MARKET+IOC (not dropped for MARKET).
3. close_with_ioc re-sends on IOC expiry and escalates to limit-IOC.
"""

from __future__ import annotations

import os

import pytest

# Phase 5.5-P0.5: allow the real place_order path through with mock clients.
os.environ.setdefault("SELENE_ENABLE_REAL_TRADING", "1")

from selene_space_exchange.execution.ioc_close import close_with_ioc
from selene_space_exchange.execution.live_engine import LiveExecutionEngine
from selene_space_exchange.trading import (
    BalanceInfo,
    BinanceFuturesTradingAdapter,
    OrderRequest,
    OrderResponse,
    TradingClient,
)
from selene_space_types.trading import OrderSide, OrderStatus, OrderType


class _RecordingClient(TradingClient):
    """Records OrderRequest objects so tests can assert forwarded fields."""

    def __init__(self) -> None:
        self.placed: list[OrderRequest] = []

    @property
    def exchange_name(self) -> str:
        return "mock"

    async def place_order(self, request: OrderRequest) -> OrderResponse:
        self.placed.append(request)
        return OrderResponse(
            exchange="mock",
            order_id=f"exch-{len(self.placed)}",
            client_order_id=request.client_order_id or "",
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            status=OrderStatus.ACCEPTED,
            quantity=request.quantity,
            price=request.price,
        )

    async def cancel_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        return OrderResponse(exchange="mock", order_id=order_id, symbol=symbol,
                             side=OrderSide.BUY, order_type=OrderType.LIMIT,
                             status=OrderStatus.CANCELLED, quantity=0)

    async def get_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        return OrderResponse(exchange="mock", order_id=order_id, symbol=symbol,
                             side=OrderSide.BUY, order_type=OrderType.LIMIT,
                             status=OrderStatus.ACCEPTED, quantity=0)

    async def get_open_orders(self, symbol=None):  # type: ignore[override]
        return []

    async def get_balances(self) -> list[BalanceInfo]:
        return [BalanceInfo(asset="USDT", free=50000.0, locked=0.0, total=50000.0)]

    async def test_connectivity(self) -> bool:
        return True


async def _make_live_engine() -> tuple[LiveExecutionEngine, _RecordingClient]:
    client = _RecordingClient()
    eng = LiveExecutionEngine(client, reconciliation_interval_s=9999)
    await eng.start()
    eng.approve_first_trade()
    return eng, client


class TestLiveEngineTimeInForcePassthrough:
    @pytest.mark.asyncio
    async def test_forwards_ioc_into_order_request(self) -> None:
        eng, client = await _make_live_engine()
        await eng.place_order(
            account_id="acc",
            symbol="BTC/USDT",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=0.01,
            time_in_force="IOC",
        )
        assert client.placed[0].time_in_force == "IOC"

    @pytest.mark.asyncio
    async def test_defaults_to_gtc_when_unspecified(self) -> None:
        eng, client = await _make_live_engine()
        await eng.place_order(
            account_id="acc",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=0.01,
            price=60000.0,
        )
        assert client.placed[0].time_in_force == "GTC"


class TestBinanceFuturesAdapterMarketIOC:
    @pytest.mark.asyncio
    async def test_market_ioc_forwards_time_in_force(self) -> None:
        import unittest.mock as um

        mock_client = um.AsyncMock()
        mock_client.place_order.return_value = {
            "orderId": "1", "clientOrderId": "", "symbol": "BTCUSDT",
            "side": "SELL", "type": "MARKET", "status": "NEW",
            "origQty": "0.01", "executedQty": "0", "price": "0",
        }
        adapter = BinanceFuturesTradingAdapter(mock_client)
        req = OrderRequest(
            symbol="BTC/USDT",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=0.01,
            time_in_force="IOC",
        )
        await adapter.place_order(req)
        sent = mock_client.place_order.await_args.kwargs
        assert sent.get("time_in_force") == "IOC"
        assert sent.get("order_type") == "MARKET"

    @pytest.mark.asyncio
    async def test_market_gtc_does_not_send_redundant_time_in_force(self) -> None:
        import unittest.mock as um

        mock_client = um.AsyncMock()
        mock_client.place_order.return_value = {
            "orderId": "2", "clientOrderId": "", "symbol": "BTCUSDT",
            "side": "BUY", "type": "MARKET", "status": "NEW",
            "origQty": "0.01", "executedQty": "0", "price": "0",
        }
        adapter = BinanceFuturesTradingAdapter(mock_client)
        req = OrderRequest(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.01,
        )  # default time_in_force="GTC"
        await adapter.place_order(req)
        sent = mock_client.place_order.await_args.kwargs
        assert "time_in_force" not in sent


class TestCloseWithIoc:
    @pytest.mark.asyncio
    async def test_retries_on_expiry_then_fills(self) -> None:
        from selene_space_exchange.trading import OrderResponse

        calls: list[dict] = []
        statuses = iter([OrderStatus.CANCELLED, OrderStatus.FILLED])

        class _Eng:
            async def place_order(self, **kw):
                calls.append(kw)
                st = next(statuses)
                return OrderResponse(
                    exchange="mock", order_id=f"o{len(calls)}", symbol=kw["symbol"],
                    side=kw["side"], order_type=kw["order_type"],
                    status=st, quantity=kw["quantity"], price=kw.get("price"),
                )

        result = await close_with_ioc(
            _Eng(), account_id="acc", symbol="BTC/USDT-SWAP", side=OrderSide.BUY,
            quantity=0.01, aggressive_price=61000.0, max_retries=3, retry_delay_s=0,
        )

        assert result.status == OrderStatus.FILLED
        assert len(calls) == 2
        # First attempt is MARKET+IOC; retry escalates to LIMIT+IOC at the price.
        assert calls[0]["order_type"] == OrderType.MARKET
        assert calls[0]["time_in_force"] == "IOC"
        assert calls[0]["price"] is None
        assert calls[1]["order_type"] == OrderType.LIMIT
        assert calls[1]["price"] == 61000.0
        assert calls[1]["time_in_force"] == "IOC"
        assert calls[1]["reduce_only"] is True

    @pytest.mark.asyncio
    async def test_exhausts_retries_and_returns_last_order(self) -> None:
        from selene_space_exchange.trading import OrderResponse

        calls: list[dict] = []

        class _Eng:
            async def place_order(self, **kw):
                calls.append(kw)
                return OrderResponse(
                    exchange="mock", order_id=f"o{len(calls)}", symbol=kw["symbol"],
                    side=kw["side"], order_type=kw["order_type"],
                    status=OrderStatus.EXPIRED, quantity=kw["quantity"],
                    price=kw.get("price"),
                )

        result = await close_with_ioc(
            _Eng(), account_id="acc", symbol="BTC/USDT-SWAP", side=OrderSide.BUY,
            quantity=0.01, aggressive_price=61000.0, max_retries=3, retry_delay_s=0,
        )

        assert result.status == OrderStatus.EXPIRED
        assert len(calls) == 3  # 1 market-IOC + 2 limit-IOC retries

    @pytest.mark.asyncio
    async def test_partial_fill_stops_the_loop(self) -> None:
        from selene_space_exchange.trading import OrderResponse

        calls: list[dict] = []

        class _Eng:
            async def place_order(self, **kw):
                calls.append(kw)
                return OrderResponse(
                    exchange="mock", order_id=f"o{len(calls)}", symbol=kw["symbol"],
                    side=kw["side"], order_type=kw["order_type"],
                    status=OrderStatus.PARTIAL, quantity=kw["quantity"],
                    price=kw.get("price"), filled_quantity=kw["quantity"] / 2,
                )

        result = await close_with_ioc(
            _Eng(), account_id="acc", symbol="BTC/USDT-SWAP", side=OrderSide.BUY,
            quantity=0.01, aggressive_price=61000.0, max_retries=5, retry_delay_s=0,
        )

        assert result.status == OrderStatus.PARTIAL
        assert len(calls) == 1
