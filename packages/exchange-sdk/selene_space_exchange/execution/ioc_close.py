"""IOC (immediate-or-cancel) close helper for thin-book markets.

Closes a position with an immediate-fill-or-cancel order and re-sends on
expiry so a no-fill never leaves a resting order locking margin into a
stale price. Aimed at closing perp/derivatives legs into thin books
without the unbounded slippage of a plain market order.

Used by the funding-arb dual-leg executor (closing legs) and the
funding-reversal monitor (forced flatten).

The ``engine`` is duck-typed to ``LiveExecutionEngine.place_order``
(account_id/symbol/side/order_type/quantity/price/time_in_force/...).

Strategy:
    attempt 1        : MARKET + time_in_force="IOC" (fill now or cancel).
    attempts 2..N    : LIMIT + time_in_force="IOC" at ``aggressive_price``
                       (buy → ask-side / sell → bid-side, caller-supplied),
                       or repeat MARKET+IOC when no price is available.
    terminal states  : FILLED / PARTIAL stop the loop (PARTIAL → caller
                       handles the remainder via leg-mismatch recovery).
    retryable states : CANCELLED / EXPIRED (IOC did not fill) → re-send.
    exhausted        : returns the last order; caller may fall back to a
                       plain market order or escalate (never force a fill).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from selene_space_types.trading import OrderSide, OrderStatus, OrderType

logger = logging.getLogger(__name__)

_RETRYABLE = {OrderStatus.CANCELLED, OrderStatus.EXPIRED}


async def close_with_ioc(
    engine: Any,
    *,
    account_id: str,
    symbol: str,
    side: OrderSide,
    quantity: float,
    aggressive_price: float | None = None,
    max_retries: int = 3,
    retry_delay_s: float = 0.5,
    reduce_only: bool = True,
    **engine_kwargs: Any,
) -> Any:
    """Close a position via market-IOC, escalating to limit-IOC on retries.

    Args:
        engine: Place-order engine (duck-typed to LiveExecutionEngine).
        account_id: Engine account to route the close through.
        symbol: Position symbol (e.g. "BTC/USDT" or "BTC/USDT-SWAP").
        side: OrderSide.SELL to close a long; OrderSide.BUY to close a short.
        quantity: Quantity to close.
        aggressive_price: Price for the limit-IOC retries (escapes the spread
            / steps through the touch). None → retries stay MARKET+IOC.
        max_retries: Total attempts (first is MARKET+IOC).
        retry_delay_s: Pause between re-sends.
        reduce_only: Never flip the position on a close (default True).
        **engine_kwargs: Forwarded to engine.place_order (pos_side, etc.).

    Returns:
        The final order. Caller inspects ``status`` / ``filled_quantity``.
    """
    last: Any = None
    for attempt in range(1, max_retries + 1):
        use_limit = attempt > 1 and aggressive_price is not None
        order_type = OrderType.LIMIT if use_limit else OrderType.MARKET
        price = aggressive_price if use_limit else None

        last = await engine.place_order(
            account_id=account_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            time_in_force="IOC",
            reduce_only=reduce_only,
            **engine_kwargs,
        )

        if last.status in (OrderStatus.FILLED, OrderStatus.PARTIAL):
            return last
        if last.status not in _RETRYABLE or attempt == max_retries:
            return last
        logger.info(
            "IOC close %s %s x%s attempt=%d status=%s — re-sending",
            side.value, symbol, quantity, attempt, last.status.value,
        )
        if retry_delay_s > 0:
            await asyncio.sleep(retry_delay_s)

    return last
