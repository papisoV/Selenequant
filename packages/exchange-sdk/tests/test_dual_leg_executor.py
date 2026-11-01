"""Tests for the dual-leg delta-neutral executor (#1).

Covers: entry pairing, missing-leg recovery, naked-short prevention,
IOC close escalation, and the kill-switch gate. Uses mock spot/perp
clients so no live credentials are needed.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

import pytest

os.environ.setdefault("SELENE_ENABLE_REAL_TRADING", "1")

from selene_space_exchange.execution.dual_leg_executor import DualLegExecutor
from selene_space_exchange.trading import (
    BalanceInfo,
    OrderRequest,
    OrderResponse,
    TradingClient,
)
from selene_space_types.trading import OrderSide, OrderStatus, OrderType


def _dec(v) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v))


class _MockClient(TradingClient):
    """Records OrderRequests and returns a status per request."""

    def __init__(self, *, fill=lambda req: OrderStatus.FILLED) -> None:
        self.placed: list[OrderRequest] = []
        self._fill = fill

    @property
    def exchange_name(self) -> str:
        return "mock"

    async def place_order(self, request: OrderRequest) -> OrderResponse:
        self.placed.append(request)
        status = self._fill(request)
        filled = (
            _dec(request.quantity) if status == OrderStatus.FILLED else Decimal("0")
        )
        return OrderResponse(
            exchange="mock",
            order_id=f"o{len(self.placed)}",
            client_order_id=request.client_order_id or "",
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            status=status,
            quantity=request.quantity,
            filled_quantity=filled,
        )

    async def cancel_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        return OrderResponse(
            exchange="mock",
            order_id=order_id,
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            status=OrderStatus.CANCELLED,
            quantity=Decimal(0),
        )

    async def get_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        return OrderResponse(
            exchange="mock",
            order_id=order_id,
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            status=OrderStatus.ACCEPTED,
            quantity=Decimal(0),
        )

    async def get_open_orders(self, symbol=None):  # type: ignore[override]
        return []

    async def get_balances(self) -> list[BalanceInfo]:
        return [
            BalanceInfo(
                asset="USDT",
                free=Decimal("100000"),
                locked=Decimal(0),
                total=Decimal("100000"),
            )
        ]

    async def test_connectivity(self) -> bool:
        return True


def _make_executor(spot: TradingClient, perp: TradingClient, **kw) -> DualLegExecutor:
    return DualLegExecutor(spot, perp, retry_delay_s=0, **kw)


class TestOpenDeltaNeutral:
    @pytest.mark.asyncio
    async def test_places_matched_pair_with_sized_quantities(self) -> None:
        spot, perp = _MockClient(), _MockClient()
        executor = _make_executor(spot, perp)

        result = await executor.open_delta_neutral(
            account_id="acc",
            strategy_id="funding_arb",
            spot_symbol="BTC/USDT",
            perp_symbol="BTC/USDT",
            notional_usdt=10000,
            spot_price=60000,
            perp_price=60050,
        )

        assert result.delta_neutral
        assert not result.flattened
        assert result.residual == 0

        # spot BUY + perp SELL, both market, reduce_only=False (new position).
        assert len(spot.placed) == 1
        assert len(perp.placed) == 1
        sp, pp = spot.placed[0], perp.placed[0]
        assert sp.side == OrderSide.BUY and sp.order_type == OrderType.MARKET
        assert pp.side == OrderSide.SELL and pp.order_type == OrderType.MARKET
        assert not sp.reduce_only and not pp.reduce_only
        assert _dec(sp.quantity) == _dec("10000") / _dec("60000")
        assert _dec(pp.quantity) == _dec("10000") / _dec("60050")

    @pytest.mark.asyncio
    async def test_forwards_pos_side_on_perp_leg(self) -> None:
        spot, perp = _MockClient(), _MockClient()
        executor = _make_executor(spot, perp)

        await executor.open_delta_neutral(
            account_id="acc",
            strategy_id="funding_arb",
            spot_symbol="BTC/USDT",
            perp_symbol="BTC/USDT",
            notional_usdt=1000,
            spot_price=60000,
            perp_price=60050,
            perp_pos_side="SHORT",
        )

        assert spot.placed[0].pos_side is None
        assert perp.placed[0].pos_side == "SHORT"

    @pytest.mark.asyncio
    async def test_retries_missing_perp_then_flattens_spot(self) -> None:
        # Perp always rejects; spot always fills. After retries, the lone spot
        # position must be sold back (never hold a directional position).
        spot = _MockClient()
        perp = _MockClient(fill=lambda req: OrderStatus.REJECTED)
        executor = _make_executor(spot, perp, leg_retries=3)

        result = await executor.open_delta_neutral(
            account_id="acc",
            strategy_id="funding_arb",
            spot_symbol="BTC/USDT",
            perp_symbol="BTC/USDT",
            notional_usdt=10000,
            spot_price=60000,
            perp_price=60050,
        )

        assert result.flattened
        assert not result.delta_neutral
        assert result.leg("perp").filled_quantity == 0
        # Spot: 1 open BUY + 1 flatten SELL (IOC).
        assert len(spot.placed) == 2
        flat = spot.placed[-1]
        assert flat.side == OrderSide.SELL
        assert flat.time_in_force == "IOC"
        assert not flat.reduce_only
        # Perp: 1 open + 3 recovery retries.
        assert len(perp.placed) == 4

    @pytest.mark.asyncio
    async def test_flattens_perp_when_spot_never_lands(self) -> None:
        # Spot always rejects; perp fills. A lone perp short is a naked short —
        # the executor must buy it back (reduce_only) even though the spot leg
        # never opened.
        spot = _MockClient(fill=lambda req: OrderStatus.REJECTED)
        perp = _MockClient()
        executor = _make_executor(spot, perp, leg_retries=2)

        result = await executor.open_delta_neutral(
            account_id="acc",
            strategy_id="funding_arb",
            spot_symbol="BTC/USDT",
            perp_symbol="BTC/USDT",
            notional_usdt=10000,
            spot_price=60000,
            perp_price=60050,
        )

        assert result.flattened
        assert result.leg("spot").filled_quantity == 0
        # Perp: 1 open SELL + 1 flatten BUY (reduce-only IOC).
        assert len(perp.placed) == 2
        flat = perp.placed[-1]
        assert flat.side == OrderSide.BUY
        assert flat.time_in_force == "IOC"
        assert flat.reduce_only


class TestCloseDeltaNeutral:
    @pytest.mark.asyncio
    async def test_closes_both_legs_via_ioc(self) -> None:
        spot, perp = _MockClient(), _MockClient()
        executor = _make_executor(spot, perp)

        result = await executor.close_delta_neutral(
            account_id="acc",
            strategy_id="funding_arb",
            spot_symbol="BTC/USDT",
            perp_symbol="BTC/USDT",
            spot_qty=0.166,
            perp_qty=0.166,
            spot_price=61000,
            perp_price=61050,
        )

        assert result.delta_neutral
        sp, pp = spot.placed[0], perp.placed[0]
        # Spot close: SELL, market-IOC, not reduce-only.
        assert sp.side == OrderSide.SELL
        assert sp.time_in_force == "IOC"
        assert not sp.reduce_only
        # Perp close: BUY, market-IOC, reduce-only (never flips to long).
        assert pp.side == OrderSide.BUY
        assert pp.time_in_force == "IOC"
        assert pp.reduce_only
        assert _dec(pp.quantity) == _dec("0.166")

    @pytest.mark.asyncio
    async def test_escalates_to_limit_ioc_when_market_expires(self) -> None:
        perp_buys = {"count": 0}

        def perp_fill(req: OrderRequest) -> OrderStatus:
            if req.side == OrderSide.BUY:
                perp_buys["count"] += 1
                if perp_buys["count"] == 1:
                    return OrderStatus.EXPIRED
            return OrderStatus.FILLED

        spot, perp = _MockClient(), _MockClient(fill=perp_fill)
        executor = _make_executor(spot, perp)

        result = await executor.close_delta_neutral(
            account_id="acc",
            strategy_id="funding_arb",
            spot_symbol="BTC/USDT",
            perp_symbol="BTC/USDT",
            spot_qty=0.166,
            perp_qty=0.166,
            spot_price=61000,
            perp_price=61050,
        )

        assert result.delta_neutral
        # Perp: attempt 1 = MARKET+IOC (expired), attempt 2 = LIMIT+IOC at price.
        assert len(perp.placed) == 2
        first, second = perp.placed
        assert first.order_type == OrderType.MARKET and first.price is None
        assert second.order_type == OrderType.LIMIT
        assert second.price == _dec("61050")
        assert second.time_in_force == "IOC"
        assert second.reduce_only


class TestKillSwitchGate:
    @pytest.mark.asyncio
    async def test_active_kill_switch_blocks_open_but_allows_close(self) -> None:
        class _KS:
            def is_active_for(self, *, account_id=None, strategy_id=None) -> bool:
                return True

        spot, perp = _MockClient(), _MockClient()
        executor = _make_executor(spot, perp, kill_switch=_KS())

        with pytest.raises(RuntimeError, match="Kill switch active"):
            await executor.open_delta_neutral(
                account_id="acc",
                strategy_id="funding_arb",
                spot_symbol="BTC/USDT",
                perp_symbol="BTC/USDT",
                notional_usdt=1000,
                spot_price=60000,
                perp_price=60050,
            )
        assert not spot.placed and not perp.placed

        # Close/flatten is never gated — a global stop must always unwind.
        result = await executor.close_delta_neutral(
            account_id="acc",
            strategy_id="funding_arb",
            spot_symbol="BTC/USDT",
            perp_symbol="BTC/USDT",
            spot_qty=0.1,
            perp_qty=0.1,
            spot_price=60000,
            perp_price=60050,
        )
        assert result.delta_neutral
        assert len(spot.placed) == 1


class TestBinanceFuturesAdapterForwarding:
    @pytest.mark.asyncio
    async def test_forwards_reduce_only_and_pos_side_to_exchange(self) -> None:
        """Hedge mode (dual-side): positionSide derived from side+reduce_only,
        NOT from the request's pos_side — the runner's mirrored mapping is
        wrong for close orders (BUY-to-close-short must carry SHORT, not LONG).
        reduceOnly is dropped: Binance rejects it in hedge mode."""
        import unittest.mock as um

        mock_client = um.AsyncMock()
        mock_client.place_order.return_value = {
            "orderId": "9",
            "clientOrderId": "",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "type": "LIMIT",
            "status": "NEW",
            "origQty": "0.01",
            "executedQty": "0",
            "price": "61050",
        }
        mock_client.get_position_mode.return_value = True  # hedge mode
        mock_client.set_position_mode.side_effect = RuntimeError("switch failed")

        from selene_space_exchange.trading import BinanceFuturesTradingAdapter

        adapter = BinanceFuturesTradingAdapter(mock_client)
        req = OrderRequest(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("0.01"),
            price=Decimal("61050"),
            time_in_force="IOC",
            reduce_only=True,
            pos_side="LONG",
        )
        await adapter.place_order(req)

        sent = mock_client.place_order.await_args.kwargs
        # BUY + reduce_only=True = closing a short → SHORT (derived, pos_side ignored)
        assert sent.get("position_side") == "SHORT"
        assert "reduce_only" not in sent

    @pytest.mark.asyncio
    async def test_omits_reduce_only_and_pos_side_when_unset(self) -> None:
        """Net mode (or unqueryable mode): no positionSide, no reduce_only
        when the request carries neither."""
        import unittest.mock as um

        mock_client = um.AsyncMock()
        mock_client.place_order.return_value = {
            "orderId": "10",
            "clientOrderId": "",
            "symbol": "BTCUSDT",
            "side": "SELL",
            "type": "MARKET",
            "status": "NEW",
            "origQty": "0.01",
            "executedQty": "0",
            "price": "0",
        }
        mock_client.get_position_mode.return_value = False  # net mode

        from selene_space_exchange.trading import BinanceFuturesTradingAdapter

        adapter = BinanceFuturesTradingAdapter(mock_client)
        req = OrderRequest(
            symbol="BTC/USDT",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.01"),
        )
        await adapter.place_order(req)

        sent = mock_client.place_order.await_args.kwargs
        assert "reduce_only" not in sent
        assert "position_side" not in sent


class TestHedgeModePositionSideDerivation:
    """Fix①: hedge mode derives positionSide from side+reduce_only.
    All four open/close x long/short combinations."""

    @staticmethod
    def _mock_client(mode: bool) -> Any:
        import unittest.mock as um

        mock_client = um.AsyncMock()
        mock_client.place_order.return_value = {
            "orderId": "h1",
            "clientOrderId": "",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "type": "MARKET",
            "status": "NEW",
            "origQty": "0.01",
            "executedQty": "0",
            "price": "0",
        }
        mock_client.get_position_mode.return_value = mode
        if mode:
            # Adapter auto-switches hedge→net on first order; make the switch
            # fail so the account stays hedge and hedge semantics apply.
            mock_client.set_position_mode.side_effect = RuntimeError("switch failed")
        return mock_client

    @staticmethod
    async def _place(mode: bool, side: OrderSide, reduce_only: bool) -> dict[str, Any]:
        from selene_space_exchange.trading import BinanceFuturesTradingAdapter

        mock_client = TestHedgeModePositionSideDerivation._mock_client(mode)
        adapter = BinanceFuturesTradingAdapter(mock_client)
        await adapter.place_order(
            OrderRequest(
                symbol="BTC/USDT",
                side=side,
                order_type=OrderType.MARKET,
                quantity=Decimal("0.01"),
                reduce_only=reduce_only,
            )
        )
        return dict(mock_client.place_order.await_args.kwargs)

    @pytest.mark.asyncio
    async def test_hedge_open_long(self) -> None:
        sent = await self._place(True, OrderSide.BUY, False)
        assert sent.get("position_side") == "LONG"

    @pytest.mark.asyncio
    async def test_hedge_open_short(self) -> None:
        sent = await self._place(True, OrderSide.SELL, False)
        assert sent.get("position_side") == "SHORT"

    @pytest.mark.asyncio
    async def test_hedge_close_long_sells_with_long_side(self) -> None:
        sent = await self._place(True, OrderSide.SELL, True)
        assert sent.get("position_side") == "LONG"
        assert "reduce_only" not in sent

    @pytest.mark.asyncio
    async def test_hedge_close_short_buys_with_short_side(self) -> None:
        sent = await self._place(True, OrderSide.BUY, True)
        assert sent.get("position_side") == "SHORT"
        assert "reduce_only" not in sent

    @pytest.mark.asyncio
    async def test_net_mode_sells_reduce_only_without_position_side(self) -> None:
        sent = await self._place(False, OrderSide.SELL, True)
        assert sent.get("reduce_only") is True
        assert "position_side" not in sent

    @pytest.mark.asyncio
    async def test_unqueryable_mode_stays_net_safe(self) -> None:
        """get_position_mode raising → no positionSide, no crash (not worse
        than pre-fix behavior)."""
        import unittest.mock as um

        mock_client = um.AsyncMock()
        mock_client.place_order.return_value = {
            "orderId": "h2",
            "clientOrderId": "",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "type": "MARKET",
            "status": "NEW",
            "origQty": "0.01",
            "executedQty": "0",
            "price": "0",
        }
        mock_client.get_position_mode.side_effect = RuntimeError("api down")
        from selene_space_exchange.trading import BinanceFuturesTradingAdapter

        adapter = BinanceFuturesTradingAdapter(mock_client)
        await adapter.place_order(
            OrderRequest(
                symbol="BTC/USDT",
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=Decimal("0.01"),
                reduce_only=True,
            )
        )
        sent = mock_client.place_order.await_args.kwargs
        assert sent.get("reduce_only") is True
        assert "position_side" not in sent

    @pytest.mark.asyncio
    async def test_hedge_to_net_switch_updates_mode_state(self) -> None:
        """Account found in hedge mode but auto-switched to net on first order:
        _is_hedge_mode must flip to False or later orders would carry
        positionSide into net mode (-4061)."""
        import unittest.mock as um

        mock_client = um.AsyncMock()
        mock_client.place_order.return_value = {
            "orderId": "h3",
            "clientOrderId": "",
            "symbol": "BTCUSDT",
            "side": "SELL",
            "type": "MARKET",
            "status": "NEW",
            "origQty": "0.01",
            "executedQty": "0",
            "price": "0",
        }
        mock_client.get_position_mode.return_value = True  # hedge found
        from selene_space_exchange.trading import BinanceFuturesTradingAdapter

        adapter = BinanceFuturesTradingAdapter(mock_client)
        # First order triggers query + switch to net.
        await adapter.place_order(
            OrderRequest(
                symbol="BTC/USDT",
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=Decimal("0.01"),
            )
        )
        assert mock_client.set_position_mode.await_count == 1
        assert adapter._is_hedge_mode is False

        # Second order must use NET semantics: reduce_only carried, no positionSide.
        await adapter.place_order(
            OrderRequest(
                symbol="BTC/USDT",
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=Decimal("0.01"),
                reduce_only=True,
            )
        )
        sent = mock_client.place_order.await_args.kwargs
        assert sent.get("reduce_only") is True
        assert "position_side" not in sent
