"""Solana DEX trading adapter implementing the unified TradingClient interface."""

from __future__ import annotations

import logging

from selene_space_exchange.trading import (
    BalanceInfo,
    OrderRequest,
    OrderResponse,
    TradingClient,
)
from selene_space_exchange.exchanges.solana import SolanaTrader

logger = logging.getLogger(__name__)


class SolanaTradingAdapter(TradingClient):
    """Adapts SolanaTrader (Jupiter swaps) to SeleneQuant's TradingClient ABC.

    DEX trades are immediate fills (no open order book), so place_order
    performs a swap and returns a FILLED response on success.

    Symbol format: ``SOL:<mint_address>`` (e.g. ``SOL:DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263``).
    """

    def __init__(self) -> None:
        self._trader = SolanaTrader()

    @property
    def exchange_name(self) -> str:
        return "solana-dex"

    async def place_order(self, request: OrderRequest) -> OrderResponse:
        from selene_space_types.trading import OrderSide, OrderStatus, OrderType

        if not request.symbol.startswith("SOL:"):
            return OrderResponse(
                exchange=self.exchange_name,
                order_id="",
                symbol=request.symbol,
                side=request.side,
                order_type=request.order_type,
                status=OrderStatus.REJECTED,
                quantity=request.quantity,
                raw={"error": "Invalid symbol format. Expected SOL:<mint_address>"},
            )

        token_address = request.symbol[4:]

        if request.side == OrderSide.BUY:
            amount_sol = request.quantity
            sig = await self._trader.buy(
                token_address=token_address,
                amount_sol=amount_sol,
                slippage_bps=500,
            )
        else:
            sig = await self._trader.sell(
                token_address=token_address,
                percentage=1.0,
                slippage_bps=500,
            )

        if sig:
            return OrderResponse(
                exchange=self.exchange_name,
                order_id=sig,
                symbol=request.symbol,
                side=request.side,
                order_type=OrderType.MARKET,
                status=OrderStatus.FILLED,
                quantity=request.quantity,
                raw={"signature": sig},
            )

        return OrderResponse(
            exchange=self.exchange_name,
            order_id="",
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            status=OrderStatus.REJECTED,
            quantity=request.quantity,
            raw={"error": "Swap failed"},
        )

    async def cancel_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        from selene_space_types.trading import OrderSide, OrderStatus, OrderType

        # DEX swaps are immediate fills — no open orders to cancel
        return OrderResponse(
            exchange=self.exchange_name,
            order_id=order_id,
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            status=OrderStatus.CANCELLED,
            quantity=0,
            raw={"note": "DEX swaps are immediate, no open orders"},
        )

    async def get_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        from selene_space_types.trading import OrderSide, OrderStatus, OrderType

        # On-chain tx can be checked via sig but simplified here
        return OrderResponse(
            exchange=self.exchange_name,
            order_id=order_id,
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            status=OrderStatus.FILLED,
            quantity=0,
            raw={"signature": order_id},
        )

    async def get_open_orders(self, symbol: str | None = None) -> list[OrderResponse]:
        # DEX has no open order book
        return []

    async def get_balances(self) -> list[BalanceInfo]:
        balance = await self._trader.rpc.get_balance()
        return [BalanceInfo(asset="SOL", free=balance, total=balance)]

    async def test_connectivity(self) -> bool:
        try:
            balance = await self._trader.rpc.get_balance()
            return balance >= 0
        except Exception:
            return False

    async def close(self) -> None:
        await self._trader.close()
