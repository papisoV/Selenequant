"""Dual-leg delta-neutral executor for funding-rate arbitrage.

Funding arb holds two legs as a matched pair — a spot long and a perp
short — so the position is delta-neutral and only funding is captured.
This module orchestrates the two legs so they land together:

    entry: spot BUY  (long the asset) + perp SELL (open a short)
    exit : spot SELL (flat the spot) + perp BUY  (reduce-only, closes short)

A half-filled pair is a *directional* exposure, not a delta-neutral one, so
the executor reconciles fills after every pair and recovers a missing leg:
retry the shortfall, and if the retry keeps failing, flatten the surviving
leg. Flattening priority: never leave a naked perp short — if the spot leg
fails to open, the perp short is closed back out even if the spot never
filled.

Clients are injected (``spot_client`` / ``perp_client``), so the executor
is unit-testable and agnostic to which exchange backend backs each leg.
Real startup wiring lives in ``services/local-api/app/main.py``: a spot-
capable key for one client and a futures-capable key for the other.

Close legs reuse :func:`close_with_ioc` — market-IOC first, then limit-IOC
escalation on retries — so a no-fill never leaves a resting order locking
margin into a stale price.

Open legs are plain market orders (fill now). ``reduce_only`` / ``pos_side``
are forwarded for the perp leg: False on open (a SELL that creates the
short), True on close (a BUY that only reduces, never flips to long).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from selene_space_exchange.execution.ioc_close import close_with_ioc
from selene_space_exchange.trading import OrderRequest, OrderResponse, TradingClient
from selene_space_types.trading import OrderSide, OrderStatus, OrderType

logger = logging.getLogger(__name__)

_FILL_TOLERANCE = Decimal("0.0001")


@dataclass(frozen=True)
class LegExecution:
    """Outcome of one leg of a dual-leg pair."""

    leg: str  # "spot" | "perp"
    symbol: str
    side: OrderSide
    target_quantity: Decimal
    filled_quantity: Decimal
    status: OrderStatus | None
    order_id: str | None = None

    @property
    def complete(self) -> bool:
        """Filled at least the target quantity (tolerance allows tiny overshoot)."""
        return self.filled_quantity >= self.target_quantity - _FILL_TOLERANCE

    @property
    def shortfall(self) -> Decimal:
        return max(Decimal("0"), self.target_quantity - self.filled_quantity)


@dataclass(frozen=True)
class PairResult:
    """Result of an entry or exit pair, after reconciliation + recovery."""

    phase: str  # "open" | "close"
    legs: list[LegExecution]
    flattened: bool  # recovery flattened a surviving leg to avoid directionality
    residual: Decimal  # unfilled qty on a still-open leg after recovery
    # Net change to the SPOT position after recovery, for the caller's
    # position tracker. 0 when the spot leg was flattened back out.
    spot_position_delta: Decimal = Decimal("0")

    @property
    def delta_neutral(self) -> bool:
        return all(leg.complete for leg in self.legs)

    def leg(self, name: str) -> LegExecution:
        return next(leg for leg in self.legs if leg.leg == name)


class KillSwitch(Protocol):
    def is_active_for(self, *, account_id: str | None = None, strategy_id: str | None = None) -> bool: ...


class _ClientAsEngine:
    """Adapts a raw TradingClient to the engine-shaped interface close_with_ioc expects.

    Lets the executor reuse the tested IOC-close strategy (market-IOC then
    limit-IOC) without going through a LiveExecutionEngine.
    """

    def __init__(
        self,
        client: TradingClient,
        *,
        pos_side: str | None = None,
        client_order_id: str | None = None,
    ) -> None:
        self._client = client
        self._pos_side = pos_side
        self._client_order_id = client_order_id

    async def place_order(
        self,
        *,
        account_id: str,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: Any,
        price: Any,
        time_in_force: str,
        reduce_only: bool,
        **kwargs: Any,
    ) -> OrderResponse:
        return await self._client.place_order(
            OrderRequest(
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=_dec(quantity),
                price=_dec(price) if price is not None else None,
                time_in_force=time_in_force,
                reduce_only=reduce_only,
                pos_side=self._pos_side,
                client_order_id=self._client_order_id,
            )
        )


def _dec(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _accumulate_resp(leg: LegExecution, resp: OrderResponse) -> LegExecution:
    """Merge a shortfall order's fill into the original leg (keeps target qty)."""
    return LegExecution(
        leg=leg.leg,
        symbol=leg.symbol,
        side=leg.side,
        target_quantity=leg.target_quantity,
        filled_quantity=leg.filled_quantity + _dec(resp.filled_quantity),
        status=resp.status,
        order_id=resp.order_id or leg.order_id,
    )


def _accumulate_leg(leg: LegExecution, attempt: LegExecution) -> LegExecution:
    """Merge a retried leg's fill into the original leg (keeps target qty)."""
    return LegExecution(
        leg=leg.leg,
        symbol=leg.symbol,
        side=leg.side,
        target_quantity=leg.target_quantity,
        filled_quantity=leg.filled_quantity + attempt.filled_quantity,
        status=attempt.status,
        order_id=attempt.order_id or leg.order_id,
    )


class DualLegExecutor:
    """Places and reconciles the spot + perp legs of a funding-arb pair.

    Args:
        spot_client: TradingClient for the spot venue.
        perp_client: TradingClient for the perpetual venue (Binance futures).
        kill_switch: Optional KillSwitch-compatible guard. When active for the
            strategy/account, the executor refuses to open a new pair (a forced
            flatten is still allowed, so recovery can always de-risk).
        leg_retries: Max attempts per leg during recovery (default 3).
        retry_delay_s: Pause between recovery attempts (default 0.5).
    """

    def __init__(
        self,
        spot_client: TradingClient,
        perp_client: TradingClient,
        *,
        kill_switch: KillSwitch | None = None,
        leg_retries: int = 3,
        retry_delay_s: float = 0.5,
    ) -> None:
        self._spot = spot_client
        self._perp = perp_client
        self._kill_switch = kill_switch
        self._leg_retries = leg_retries
        self._retry_delay_s = retry_delay_s

    # -- public API ----------------------------------------------------

    async def open_delta_neutral(
        self,
        *,
        account_id: str,
        strategy_id: str,
        spot_symbol: str,
        perp_symbol: str,
        notional_usdt: float,
        spot_price: float,
        perp_price: float,
        perp_pos_side: str | None = None,
        client_order_id_prefix: str = "arb",
    ) -> PairResult:
        """Open the pair: spot BUY + perp SELL (new short).

        Args:
            spot_symbol: As the spot client expects it ("BTC/USDT").
            perp_symbol: As the perp client expects it (Binance futures:
                "BTC/USDT"; OKX: "BTC/USDT-SWAP").
            notional_usdt: Quote-currency notional for the full pair.
            spot_price / perp_price: Reference prices for quantity sizing.
            perp_pos_side: Hedge-mode side for the perp leg (None = net mode).
        """
        self._require_trading_allowed(account_id, strategy_id, phase="open")

        spot_qty = _dec(notional_usdt) / _dec(spot_price)
        perp_qty = _dec(notional_usdt) / _dec(perp_price)

        prefix = f"{client_order_id_prefix}-open"
        spot = await self._place_leg(
            self._spot, leg="spot", symbol=spot_symbol, side=OrderSide.BUY,
            quantity=spot_qty, reduce_only=False, pos_side=None,
            client_order_id=f"{prefix}-spot", order_type=OrderType.MARKET,
        )
        perp = await self._place_leg(
            self._perp, leg="perp", symbol=perp_symbol, side=OrderSide.SELL,
            quantity=perp_qty, reduce_only=False, pos_side=perp_pos_side,
            client_order_id=f"{prefix}-perp", order_type=OrderType.MARKET,
        )

        return await self._recover_open(
            spot=spot, perp=perp,
            spot_symbol=spot_symbol, perp_symbol=perp_symbol,
            spot_price=spot_price, perp_price=perp_price,
            perp_pos_side=perp_pos_side, prefix=prefix,
        )

    async def close_delta_neutral(
        self,
        *,
        account_id: str,
        strategy_id: str,
        spot_symbol: str,
        perp_symbol: str,
        spot_qty: float,
        perp_qty: float,
        spot_price: float,
        perp_price: float,
        perp_pos_side: str | None = None,
        client_order_id_prefix: str = "arb",
    ) -> PairResult:
        """Close the pair: spot SELL + perp BUY (reduce-only), both via IOC.

        Quantity is explicit (the caller passes the open positions), not
        re-derived from notional.
        """
        prefix = f"{client_order_id_prefix}-close"
        spot = await self._close_leg(
            self._spot, leg="spot", symbol=spot_symbol, side=OrderSide.SELL,
            quantity=_dec(spot_qty), price=_dec(spot_price), reduce_only=False,
            client_order_id=f"{prefix}-spot",
        )
        perp = await self._close_leg(
            self._perp, leg="perp", symbol=perp_symbol, side=OrderSide.BUY,
            quantity=_dec(perp_qty), price=_dec(perp_price), reduce_only=True,
            pos_side=perp_pos_side, client_order_id=f"{prefix}-perp",
        )

        return await self._recover_close(
            spot=spot, perp=perp,
            spot_symbol=spot_symbol, perp_symbol=perp_symbol,
            spot_price=spot_price, perp_price=perp_price,
            perp_pos_side=perp_pos_side, prefix=prefix,
        )

    async def flatten(
        self,
        *,
        account_id: str,
        strategy_id: str,
        spot_symbol: str,
        perp_symbol: str,
        spot_qty: float,
        perp_qty: float,
        spot_price: float,
        perp_price: float,
        perp_pos_side: str | None = None,
        client_order_id_prefix: str = "arb",
    ) -> PairResult:
        """Force-flat the pair (KillSwitch / reversal monitor).

        Same as close_delta_neutral but without the kill-switch gate, so a
        global stop can always unwind a position.
        """
        return await self.close_delta_neutral(
            account_id=account_id, strategy_id=strategy_id,
            spot_symbol=spot_symbol, perp_symbol=perp_symbol,
            spot_qty=spot_qty, perp_qty=perp_qty,
            spot_price=spot_price, perp_price=perp_price,
            perp_pos_side=perp_pos_side, client_order_id_prefix=client_order_id_prefix,
        )

    # -- order placement ------------------------------------------------

    async def _place_leg(
        self,
        client: TradingClient,
        *,
        leg: str,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        reduce_only: bool,
        pos_side: str | None,
        client_order_id: str,
        order_type: OrderType,
    ) -> LegExecution:
        resp = await client.place_order(
            OrderRequest(
                symbol=symbol, side=side, order_type=order_type,
                quantity=quantity, reduce_only=reduce_only, pos_side=pos_side,
                client_order_id=client_order_id,
            )
        )
        return LegExecution(
            leg=leg, symbol=symbol, side=side,
            target_quantity=quantity,
            filled_quantity=_dec(resp.filled_quantity),
            status=resp.status, order_id=resp.order_id,
        )

    async def _close_leg(
        self,
        client: TradingClient,
        *,
        leg: str,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        price: Decimal,
        reduce_only: bool,
        pos_side: str | None = None,
        client_order_id: str,
    ) -> LegExecution:
        final = await close_with_ioc(
            _ClientAsEngine(client, pos_side=pos_side, client_order_id=client_order_id),
            account_id="arb",
            symbol=symbol, side=side, quantity=quantity,
            aggressive_price=float(price) if price else None,
            max_retries=self._leg_retries, retry_delay_s=self._retry_delay_s,
            reduce_only=reduce_only,
        )
        return LegExecution(
            leg=leg, symbol=symbol, side=side,
            target_quantity=quantity,
            filled_quantity=_dec(final.filled_quantity),
            status=final.status, order_id=final.order_id,
        )

    # -- reconciliation + recovery -------------------------------------

    async def _recover_open(
        self,
        *,
        spot: LegExecution,
        perp: LegExecution,
        spot_symbol: str,
        perp_symbol: str,
        spot_price: float,
        perp_price: float,
        perp_pos_side: str | None,
        prefix: str,
    ) -> PairResult:
        # Retry each incomplete leg's shortfall (market, fill now), accumulating
        # fills into the original leg.
        for _ in range(self._leg_retries):
            if spot.complete and perp.complete:
                break
            if not spot.complete:
                resp = await self._open_shortfall(
                    self._spot, symbol=spot_symbol, side=OrderSide.BUY,
                    quantity=spot.shortfall, reduce_only=False, pos_side=None,
                    client_order_id=f"{prefix}-spot-r",
                )
                if resp is not None:
                    spot = _accumulate_resp(spot, resp)
            if not perp.complete:
                resp = await self._open_shortfall(
                    self._perp, symbol=perp_symbol, side=OrderSide.SELL,
                    quantity=perp.shortfall, reduce_only=False, pos_side=perp_pos_side,
                    client_order_id=f"{prefix}-perp-r",
                )
                if resp is not None:
                    perp = _accumulate_resp(perp, resp)
            if self._retry_delay_s > 0:
                await asyncio.sleep(self._retry_delay_s)

        # A lone leg is directional. Flatten priority: never leave a naked perp
        # short — if the spot never landed, close the perp back out too.
        flattened = False
        residual = Decimal("0")
        if not perp.complete and spot.complete:
            await self._flatten_leg(
                self._spot, leg="spot", symbol=spot_symbol, side=OrderSide.SELL,
                quantity=spot.filled_quantity, reduce_only=False, price=_dec(spot_price),
                client_order_id=f"{prefix}-flat-spot",
            )
            flattened = True
        elif not spot.complete and perp.complete:
            await self._flatten_leg(
                self._perp, leg="perp", symbol=perp_symbol, side=OrderSide.BUY,
                quantity=perp.filled_quantity, reduce_only=True,
                price=_dec(perp_price), pos_side=perp_pos_side,
                client_order_id=f"{prefix}-flat-perp",
            )
            flattened = True
        else:
            residual = spot.shortfall + perp.shortfall

        delta = spot.filled_quantity if not flattened else Decimal("0")
        return PairResult(
            phase="open", legs=[spot, perp], flattened=flattened,
            residual=residual, spot_position_delta=delta,
        )

    async def _recover_close(
        self,
        *,
        spot: LegExecution,
        perp: LegExecution,
        spot_symbol: str,
        perp_symbol: str,
        spot_price: float,
        perp_price: float,
        perp_pos_side: str | None,
        prefix: str,
    ) -> PairResult:
        # Close recovery retries the missing leg's shortfall (IOC); unlike open,
        # there is no survivor to flatten — a filled close cannot be un-filled.
        # Whatever shortfall remains is reported for escalation.
        for _ in range(self._leg_retries):
            if spot.complete and perp.complete:
                break
            if not spot.complete:
                retry = await self._close_leg(
                    self._spot, leg="spot", symbol=spot_symbol, side=OrderSide.SELL,
                    quantity=spot.shortfall, price=_dec(spot_price), reduce_only=False,
                    client_order_id=f"{prefix}-spot-r",
                )
                spot = _accumulate_leg(spot, retry)
            if not perp.complete:
                retry = await self._close_leg(
                    self._perp, leg="perp", symbol=perp_symbol, side=OrderSide.BUY,
                    quantity=perp.shortfall, price=_dec(perp_price), reduce_only=True,
                    pos_side=perp_pos_side, client_order_id=f"{prefix}-perp-r",
                )
                perp = _accumulate_leg(perp, retry)
            if self._retry_delay_s > 0:
                await asyncio.sleep(self._retry_delay_s)

        residual = spot.shortfall + perp.shortfall
        return PairResult(
            phase="close", legs=[spot, perp], flattened=False,
            residual=residual, spot_position_delta=-spot.filled_quantity,
        )

    async def _open_shortfall(
        self,
        client: TradingClient,
        *,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        reduce_only: bool,
        pos_side: str | None,
        client_order_id: str,
    ) -> OrderResponse | None:
        if quantity <= Decimal("0"):
            return None
        return await client.place_order(
            OrderRequest(
                symbol=symbol, side=side, order_type=OrderType.MARKET,
                quantity=quantity, reduce_only=reduce_only, pos_side=pos_side,
                client_order_id=client_order_id,
            )
        )

    async def _flatten_leg(
        self,
        client: TradingClient,
        *,
        leg: str,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        reduce_only: bool,
        price: Decimal,
        pos_side: str | None = None,
        client_order_id: str,
    ) -> None:
        if quantity <= Decimal("0"):
            return
        await close_with_ioc(
            _ClientAsEngine(client, pos_side=pos_side, client_order_id=client_order_id),
            account_id="arb",
            symbol=symbol, side=side, quantity=quantity,
            aggressive_price=float(price) if price else None,
            max_retries=self._leg_retries, retry_delay_s=self._retry_delay_s,
            reduce_only=reduce_only,
        )

    def _require_trading_allowed(self, account_id: str, strategy_id: str, *, phase: str) -> None:
        if self._kill_switch is None:
            return
        if self._kill_switch.is_active_for(account_id=account_id, strategy_id=strategy_id):
            raise RuntimeError(
                f"Kill switch active — refusing to {phase} funding-arb pair "
                f"(account={account_id} strategy={strategy_id})"
            )
