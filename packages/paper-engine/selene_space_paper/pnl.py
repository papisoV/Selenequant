"""Profit-and-loss snapshots for paper books.

A Position carries notional in USDT but price movement accrues per base
unit, so the mark-to-market swing is the entry-to-mark delta times base
quantity. Shorts invert the sign. Realized history and fees fold into
the total afterwards.
"""

from __future__ import annotations

import time
from decimal import Decimal

from selene_space_types.trading import OrderSide, PnLRecord, Position


def _units(position: Position) -> Decimal:
    """Base-quantity exposure, deriving from notional when the column is blank."""
    explicit = getattr(position, "quantity_base", None)
    if explicit is not None and Decimal(str(explicit)) > 0:
        return Decimal(str(explicit))
    entry = Decimal(str(position.avg_entry_price))
    if entry > 0:
        return Decimal(str(position.quantity)) / entry
    return Decimal("0")


def _swing(entry: Decimal, mark: Decimal, units: Decimal, side: OrderSide) -> Decimal:
    """Signed mark-to-market delta for this book direction."""
    if units <= 0 or mark <= 0:
        return Decimal("0")
    raw = (mark - entry) * units
    return -raw if side == OrderSide.SELL else raw


def _record(position: Position, swing: Decimal, booked: Decimal, paid: Decimal) -> PnLRecord:
    return PnLRecord(
        timestamp=int(time.time() * 1000),
        symbol=position.symbol,
        realized_pnl=booked,
        unrealized_pnl=swing,
        total_pnl=booked + swing - paid,
        fees=paid,
    )


def calculate_pnl(
    position: Position,
    current_price: float,
    *,
    total_fees: float = 0.0,
) -> PnLRecord:
    """Mark ``position`` against ``current_price``; subtract accrued fees."""
    swing = _swing(
        Decimal(str(position.avg_entry_price)),
        Decimal(str(current_price)),
        _units(position),
        position.side,
    )
    return _record(
        position,
        swing,
        Decimal(str(position.realized_pnl)),
        abs(Decimal(str(total_fees))),
    )


def calculate_account_pnl(
    positions: list[Position],
    prices: dict[str, float],
    fees_by_symbol: dict[str, float] | None = None,
) -> list[PnLRecord]:
    """One snapshot per book entry, priced from ``prices`` (absent quote = flat)."""
    fee_map = fees_by_symbol or {}
    return [
        calculate_pnl(pos, prices.get(pos.symbol, 0.0), total_fees=fee_map.get(pos.symbol, 0.0))
        for pos in positions
    ]
