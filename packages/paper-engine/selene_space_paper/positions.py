"""Position bookkeeping: netting, realized PnL on reduce, funding, liquidation.

Positions are keyed by (account, symbol) and sized in USDT notional with a
parallel base-quantity figure. Reducing fills realize PnL immediately against
the weighted-average entry; adding fills re-average the entry and re-estimate
the liquidation price.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from selene_space_types.trading import (
    MarginMode,
    OrderSide,
    Position,
    PositionSide,
)
from selene_space_types.trading import Fill

ZERO = Decimal("0")
_ONE = Decimal("1")
_HALF = Decimal("0.5")
_HOUSE_CUSHION = Decimal("0.95")


def _pos_dec(v: Any) -> Decimal:
    """Decimal via str — same boundary rule as everywhere else in the package."""
    if isinstance(v, Decimal):
        return v
    if v is None:
        return ZERO
    return Decimal(str(v))


def _ms() -> int:
    return int(time.time() * 1000)


def _direction(side: OrderSide) -> Decimal:
    """PnL sign: longs profit when price rises above entry, shorts the reverse."""
    return _ONE if side == OrderSide.BUY else -_ONE


def _base_units(notional: Decimal, price: Decimal) -> Decimal:
    return notional / price if price > 0 else ZERO


def _estimate_liquidation_price(
    entry_price: float | Decimal,
    leverage: int,
    side: OrderSide,
    margin_mode: MarginMode,
    available_balance: float | None = None,
    position_usdt: float | None = None,
) -> float | None:
    """Rough liquidation estimate; always below entry for longs (above for shorts).

    Isolated mode assumes only the position's own margin is at risk, so the
    buffer shrinks as leverage grows. Cross mode folds spare account balance
    into the buffer when provided, pushing liquidation further away.
    """
    entry = _pos_dec(entry_price)
    if entry <= 0 or leverage < 1:
        return None
    lev = Decimal(leverage)

    if margin_mode == MarginMode.ISOLATED:
        # Maintenance-buffer approximation: 1/lev of notional backs the trade;
        # keep a small house cushion so liq sits strictly inside the range.
        move = (entry / lev) * _HOUSE_CUSHION
    else:
        equity = _pos_dec(available_balance) if available_balance is not None else ZERO
        notional = _pos_dec(position_usdt) if position_usdt is not None else ZERO
        own_margin = notional / lev if notional > 0 else entry / lev
        move = (own_margin + equity * _HALF) / max(own_margin, Decimal("0.01"))
        move = min(move * own_margin, entry * _HALF)

    liq_px = entry - move if side == OrderSide.BUY else entry + move
    if liq_px <= 0:
        liq_px = entry / (lev * 10)
    return float(liq_px)


class PositionManager:
    """Applies fills to per-account positions and derives risk figures."""

    def __init__(self) -> None:
        self._book: dict[str, dict[str, Position]] = {}

    # ------------------------------------------------------------------
    # Fill application
    # ------------------------------------------------------------------

    def apply_fill(self, account_id: str, fill: Fill, side: OrderSide) -> Position:
        """Legacy single-symbol-implicit entry point."""
        pos, realized = self.apply_fill_with_symbol(account_id, fill.symbol, fill, side)
        pos.realized_pnl = _pos_dec(pos.realized_pnl) + _pos_dec(realized)
        return pos

    def apply_fill_with_symbol(
        self,
        account_id: str,
        symbol: str,
        fill: Fill,
        side: OrderSide,
        leverage: int = 1,
        margin_mode: MarginMode = MarginMode.CROSS,
        pos_side: PositionSide = PositionSide.NET,
        available_balance: float | None = None,
    ) -> tuple[Position, float]:
        """Net one execution into the book and report realized PnL.

        Opposite-direction fills first close what exists (PnL vs the average
        entry), then flip any remainder into a fresh reverse position.
        Same-direction fills re-weight the entry price. Closing out entirely
        removes the position from the book but still returns it for callers
        that want the final snapshot.
        """
        px = _pos_dec(fill.price)
        size = _pos_dec(fill.quantity)
        now = _ms()
        existing = self.get_position(account_id, symbol)

        live = existing is not None and existing.quantity > 0
        if live and existing.side != side:
            return self._reduce_opposite(
                account_id, symbol, existing, size, px, side, leverage,
                margin_mode, pos_side, now,
            )
        if live:
            self._add_same_side(
                existing, size, px, leverage, margin_mode, pos_side,
                available_balance=available_balance,
            )
            return existing, 0.0

        opened = self._open(
            account_id, symbol, side, size, px, leverage, margin_mode, pos_side, now,
            available_balance=available_balance,
        )
        return opened, 0.0

    def _reduce_opposite(
        self,
        account_id: str,
        symbol: str,
        existing: Position,
        size: Decimal,
        px: Decimal,
        side: OrderSide,
        leverage: int,
        margin_mode: MarginMode,
        pos_side: PositionSide,
        now: int,
    ) -> tuple[Position, float]:
        """Close against the opposite position; flip any overflow."""
        closing = min(size, existing.quantity)
        entry = existing.avg_entry_price
        realized_total = _direction(existing.side) * (px - entry) * closing / entry
        leftover = existing.quantity - closing
        overflow = size - closing

        if leftover > 0:
            existing.quantity = leftover
            existing.updated_at = now
            result_pos = existing
            if overflow > 0:
                # Remainder opens the opposite way — replace the book entry.
                result_pos = self._open(
                    account_id, symbol, side, overflow, px, leverage,
                    margin_mode, pos_side, now,
                )
        elif overflow > 0:
            result_pos = self._open(
                account_id, symbol, side, overflow, px, leverage,
                margin_mode, pos_side, now,
            )
        else:
            self._drop(account_id, symbol)
            result_pos = existing.model_copy(update={"quantity": ZERO, "updated_at": now})

        result_pos.realized_pnl = _pos_dec(result_pos.realized_pnl) + realized_total
        return result_pos, float(realized_total)

    def _add_same_side(
        self,
        existing: Position,
        size: Decimal,
        px: Decimal,
        leverage: int,
        margin_mode: MarginMode,
        pos_side: PositionSide,
        *,
        available_balance: float | None = None,
    ) -> None:
        """Re-weight entry and refresh risk figures on a same-direction add.

        Quantity is USDT notional, so the merged position's value is the
        summed notional and its cost basis is that value over total base.
        """
        added_base = _base_units(size, px)
        merged_qty = existing.quantity + size
        merged_base = _pos_dec(existing.quantity_base) + added_base
        existing.quantity = merged_qty
        existing.quantity_base = merged_base
        existing.avg_entry_price = merged_qty / merged_base if merged_base > 0 else px
        existing.leverage = leverage
        existing.margin_mode = margin_mode
        existing.pos_side = pos_side
        existing.margin = merged_qty / Decimal(max(leverage, 1))
        existing.updated_at = _ms()
        liq = _estimate_liquidation_price(
            existing.avg_entry_price, leverage, existing.side, margin_mode,
            available_balance=available_balance, position_usdt=float(merged_qty),
        )
        existing.liquidation_price = None if liq is None else _pos_dec(liq)

    def _drop(self, account_id: str, symbol: str) -> None:
        book = self._book.get(account_id)
        if book is not None:
            book.pop(symbol, None)

    def _open(
        self,
        account_id: str,
        symbol: str,
        side: OrderSide,
        size: Decimal,
        px: Decimal,
        leverage: int,
        margin_mode: MarginMode,
        pos_side: PositionSide,
        now: int,
        *,
        available_balance: float | None = None,
    ) -> Position:
        liq = _estimate_liquidation_price(
            px, leverage, side, margin_mode,
            available_balance=available_balance, position_usdt=float(size),
        )
        position = Position(
            side=side,
            symbol=symbol,
            quantity=size,
            pos_side=pos_side,
            quantity_base=_base_units(size, px),
            avg_entry_price=px,
            leverage=leverage,
            margin_mode=margin_mode,
            margin=size / Decimal(max(leverage, 1)),
            liquidation_price=None if liq is None else _pos_dec(liq),
            unrealized_pnl=ZERO,
            realized_pnl=ZERO,
            opened_at=now,
            updated_at=now,
        )
        self._book.setdefault(account_id, {})[symbol] = position
        return position

    # ------------------------------------------------------------------
    # Queries & maintenance
    # ------------------------------------------------------------------

    def get_position(self, account_id: str, symbol: str) -> Position | None:
        return self._book.get(account_id, {}).get(symbol)

    def get_positions(self, account_id: str) -> list[Position]:
        return list(self._book.get(account_id, {}).values())

    def get_open_positions(self, account_id: str) -> list[Position]:
        book = self._book.get(account_id, {})
        return [p for p in book.values() if p.quantity > 0]

    def clear_positions(self, account_id: str) -> None:
        self._book.pop(account_id, None)

    def update_unrealized_pnl(
        self, account_id: str, symbol: str, current_price: float
    ) -> list[Position]:
        """Refresh mark-to-market on every position of ``symbol``; returns them."""
        updated: list[Position] = []
        mark = _pos_dec(current_price)
        for pos in self._book.get(account_id, {}).values():
            if pos.symbol != symbol or pos.quantity <= 0:
                continue
            entry = pos.avg_entry_price
            qty = pos.quantity
            signed_move = (mark - entry) if pos.side != OrderSide.SELL else (entry - mark)
            pos.unrealized_pnl = signed_move * qty / entry
            pos.current_price = mark
            pos.unrealized_pnl_pct = (
                pos.unrealized_pnl / pos.margin if pos.margin > 0 else ZERO
            )
            pos.updated_at = _ms()
            updated.append(pos)
        return updated

    # ------------------------------------------------------------------
    # Funding & liquidation
    # ------------------------------------------------------------------

    def apply_funding_rate(
        self, account_id: str, funding_rate: float, timestamp: int
    ) -> dict[str, float]:
        """Long pays, short receives. Returns per-symbol cash flow (negative = paid)."""
        flows: dict[str, float] = {}
        rate = _pos_dec(funding_rate)
        for pos in self.get_open_positions(account_id):
            notional = pos.quantity
            pays = pos.side == OrderSide.BUY
            signed = -(notional * rate) if pays else notional * rate
            pos.accrued_funding = _pos_dec(pos.accrued_funding) + signed
            pos.last_funding_time = timestamp
            flows[pos.symbol] = float(signed)
        return flows

    def check_and_enforce_liquidation(
        self, account_id: str, symbol: str, current_price: float
    ) -> list[tuple[Position, str]]:
        """Force-close positions whose estimated liquidation level was touched.

        The whole remaining loss versus entry is booked into realized PnL and
        the position zeroes out; survivors are returned with their reason.
        """
        forced: list[tuple[Position, str]] = []
        pos = self.get_position(account_id, symbol)
        if pos is None or pos.quantity <= 0 or pos.liquidation_price is None:
            return forced
        px = _pos_dec(current_price)
        liq = pos.liquidation_price
        breached = px <= liq if pos.side == OrderSide.BUY else px >= liq
        if not breached:
            return forced

        entry = pos.avg_entry_price
        loss = _direction(pos.side) * (px - entry) * pos.quantity / entry
        pos.realized_pnl = _pos_dec(pos.realized_pnl) + loss
        pos.unrealized_pnl = ZERO
        pos.quantity = ZERO
        pos.quantity_base = ZERO
        pos.updated_at = _ms()
        reason = (
            f"liquidated {symbol}: mark {float(px):.2f} crossed "
            f"estimated liquidation {float(liq):.2f}"
        )
        forced.append((pos, reason))
        return forced

    # ------------------------------------------------------------------
    # Persistence bridge
    # ------------------------------------------------------------------

    def get_all_data(self) -> dict[str, Any]:
        return {acct: dict(book) for acct, book in self._book.items()}

    def load_data(self, data: dict[str, Any]) -> None:
        rebuilt: dict[str, dict[str, Position]] = {}
        for acct, book in data.items():
            entries: dict[str, Position] = {}
            for sym, value in book.items():
                entries[sym] = (
                    value if isinstance(value, Position) else Position.model_validate(value)
                )
            rebuilt[acct] = entries
        self._book = rebuilt
