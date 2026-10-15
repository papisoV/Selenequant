"""Fill simulation: trigger rules, latency, partials, and fees.

Quantities are USDT notional throughout, so a fill's fee is simply
``quantity * fee_rate``. Maker/taker selection follows venue convention:
market orders cross the spread (taker), resting limit orders provide
liquidity (maker).

Execution is modelled as a pipeline of small decisions: can the order
trade at all (status + remaining size), does the market touch its trigger
price, how far does slippage bend the price, how much size actually fills,
and which fee tier applies. Each stage lives in its own helper so the top
level ``try_fill`` reads as the sequence rather than a wall of branches.

Slippage models are NOT defined here: ``RealisticSlippage``,
``FixedPaperSlippage``, and ``PaperSlippageModel`` are re-exported from
``selene_space_backtest.slippage``, the single canonical implementation
shared by backtest, paper, and shadow execution. This package sits above
backtest-engine in the layering, so importing downward is legal (the old
duplicate copy here existed only because the dependency direction was
never revisited).
"""

from __future__ import annotations

import random
import time
from decimal import Decimal, ROUND_DOWN
import uuid

from selene_space_backtest.slippage import (
    FixedSlippage as FixedPaperSlippage,
    RealisticSlippage,
    SlippageModel as PaperSlippageModel,
)

from selene_space_types.trading import Fill, Order, OrderSide

DEFAULT_FEE_RATE = Decimal("0.001")
DEFAULT_SLIPPAGE_BPS = Decimal("1.0")
DEFAULT_LATENCY_RANGE_MS = (50, 500)

_ONE = Decimal("1")

__all__ = [
    "DEFAULT_FEE_RATE",
    "DEFAULT_LATENCY_RANGE_MS",
    "DEFAULT_SLIPPAGE_BPS",
    "FixedPaperSlippage",
    "PaperSlippageModel",
    "RealisticSlippage",
    "try_fill",
]


def _decide_fill_price(order: Order, current_price: float) -> tuple[bool, Decimal]:
    """Return (triggered, execution_price_before_slippage).

    Stop orders carry no ``price`` — they are triggered by
    ``PaperExecutionEngine.on_price_tick`` once the market crosses their stop
    level, and this function is then called to price the resulting market fill.
    Treating them like a limit order without a limit (the previous behaviour)
    rejected every stop fill, so a protective stop could be accepted and then
    never execute.
    """
    from selene_space_types.trading import OrderType

    px = Decimal(str(current_price))
    limit = order.price
    if order.type in (OrderType.MARKET, OrderType.STOP_MARKET):
        # Market-like: fill at the touch, no price bound.
        return True, px
    if limit is None:
        # STOP_LIMIT without a limit price (or any malformed order) has nothing
        # to price against.
        return False, px
    if (order.side == OrderSide.BUY and px <= limit) or (
        order.side == OrderSide.SELL and px >= limit
    ):
        return True, px
    return False, px


def _clip_fill_size(
    remaining: Decimal,
    *,
    partial_fill_rate: float,
    available_liquidity: float | None,
) -> Decimal:
    capped = (
        available_liquidity is not None
        and Decimal(str(available_liquidity)) < remaining
    )
    if not capped and partial_fill_rate >= 1.0:
        return remaining
    if capped:
        size = Decimal(str(available_liquidity))
    else:
        size = (remaining * Decimal(str(partial_fill_rate))).to_integral_value(
            rounding=ROUND_DOWN
        )
    return max(_ONE, min(size, remaining))


def _pick_fee_tier(
    order: Order,
    fee_rate: float | Decimal,
    maker_fee_rate: float | None,
    taker_fee_rate: float | None,
) -> tuple[Decimal, str]:
    """Explicit maker/taker rates win; liquidity role follows order type."""
    from selene_space_types.trading import OrderType

    is_taker = order.type == OrderType.MARKET
    if is_taker:
        return Decimal(
            str(taker_fee_rate if taker_fee_rate is not None else fee_rate)
        ), "taker"
    return Decimal(
        str(maker_fee_rate if maker_fee_rate is not None else fee_rate)
    ), "maker"


def _is_fillable(order: Order) -> bool:
    """Only live orders with remaining size can take on new quantity."""
    from selene_space_types.trading import OrderStatus

    still_open = order.status in (OrderStatus.ACCEPTED, OrderStatus.PARTIAL)
    has_room = order.filled_quantity < order.quantity
    return still_open and has_room


def _fill_timestamp(
    timestamp_ms: int | None,
    latency_range_ms: tuple[int, int] | None,
) -> int:
    base = timestamp_ms if timestamp_ms is not None else round(time.time() * 1000)
    if latency_range_ms is None:
        return base
    lo, hi = latency_range_ms
    return base + random.randint(int(lo), int(hi))


def try_fill(
    order: Order,
    current_price: float,
    *,
    fee_rate: float | Decimal = DEFAULT_FEE_RATE,
    maker_fee_rate: float | None = None,
    taker_fee_rate: float | None = None,
    timestamp_ms: int | None = None,
    slippage_model: PaperSlippageModel | None = None,
    latency_range_ms: tuple[int, int] | None = DEFAULT_LATENCY_RANGE_MS,
    partial_fill_rate: float = 1.0,
    available_liquidity: float | None = None,
    avg_24h_volume: float = 0.0,
) -> Fill | None:
    """Attempt to execute ``order`` at ``current_price``; None when it can't trade.

    Mutates the order in place (filled_quantity / avg_fill_price) so callers
    can reconcile bookkeeping afterwards via ``PaperOrderManager.sync_fill``.
    """
    if not _is_fillable(order):
        return None

    triggered, base_price = _decide_fill_price(order, current_price)
    if not triggered:
        return None

    exec_px = base_price
    if slippage_model is not None:
        bent = slippage_model.apply(
            float(base_price),
            order.side,
            order_notional=float(order.quantity - order.filled_quantity),
            avg_24h_volume=avg_24h_volume,
        )
        exec_px = Decimal(str(bent))

    remaining = order.quantity - order.filled_quantity
    size = _clip_fill_size(
        remaining,
        partial_fill_rate=partial_fill_rate,
        available_liquidity=available_liquidity,
    )

    rate, role = _pick_fee_tier(order, fee_rate, maker_fee_rate, taker_fee_rate)
    # Quantity is USDT notional, so the fee is notional × rate directly.
    fee = size * rate

    prior_qty = order.filled_quantity
    new_total = prior_qty + size
    prior_cost = prior_qty * (order.avg_fill_price or exec_px)
    order.avg_fill_price = (prior_cost + size * exec_px) / new_total
    order.filled_quantity = new_total

    stamp = _fill_timestamp(timestamp_ms, latency_range_ms)

    return Fill(
        id=f"pf-{uuid.uuid4().hex[:12]}",
        order_id=order.id,
        client_order_id=order.client_order_id or "",
        price=exec_px,
        quantity=size,
        fee=fee,
        fee_currency="USDT",
        fee_rate=rate,
        realized_pnl=Decimal("0"),
        exec_type=role,
        side=str(order.side.value),
        pos_side=str(order.pos_side.value),
        symbol=order.symbol,
        leverage=order.leverage,
        reduce_only=order.reduce_only,
        timestamp=stamp,
    )
