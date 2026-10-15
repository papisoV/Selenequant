"""Risk context builder for live trading engine.

Fixes the known gaps in live_engine.py risk context:
  - daily_realized_pnl: computed from actual fill ledger
  - peak_equity: tracked historically, not reset to current
  - positions: dict[symbol, dict] with notional, entry_price, unrealized_pnl
"""

from __future__ import annotations

import time
from typing import Any

from selene_space_types.trading import Fill, Position


def compute_daily_realized_pnl(fills: list[Fill]) -> float:
    """Sum realized PnL from fills that occurred today."""
    if not fills:
        return 0.0

    now_ms = int(time.time() * 1000)
    start_of_day_ms = now_ms - (now_ms % (24 * 60 * 60 * 1000))

    total = 0.0
    for f in fills:
        if f.timestamp >= start_of_day_ms:
            # realized_pnl is Decimal (H19); convert via str to avoid float pollution
            pnl = f.realized_pnl
            if hasattr(pnl, '__float__'):
                total += float(pnl)
            else:
                total += pnl
    return round(total, 8)


def compute_peak_equity(
    current_equity: float,
    previous_peak: float | None,
) -> float:
    """Track peak equity over time."""
    if previous_peak is None:
        return current_equity
    return max(current_equity, previous_peak)


def enrich_positions(positions: list[Position]) -> dict[str, dict[str, Any]]:
    """Convert Position list to dict[symbol, dict] with required fields.

    Produces the format expected by advanced risk rules:
        {symbol: {notional, entry_price, unrealized_pnl, side, leverage}}
    """
    result: dict[str, dict[str, Any]] = {}
    for p in positions:
        result[p.symbol] = {
            "notional": p.quantity,
            "entry_price": p.avg_entry_price,
            "unrealized_pnl": p.unrealized_pnl,
            "side": p.side.value if hasattr(p.side, "value") else str(p.side),
            "leverage": p.leverage,
            "quantity": p.quantity_base if p.quantity_base > 0 else p.quantity,
        }
    return result


def build_risk_context(
    *,
    account_id: str,
    positions: list[Position],
    balances: list[Any],
    total_equity: float,
    leverage: int | float,
    fills: list[Fill],
    previous_peak_equity: float | None,
    current_price: float,
    open_orders: list[Any],
    order_tracker: Any = None,
) -> dict[str, Any]:
    """Build complete risk context for pre-trade checks.

    Produces all fields expected by risk rules including:
      - positions as dict[symbol, dict] (not dict[symbol, float])
      - daily_realized_pnl from actual fills
      - peak_equity preserving historical high
    """
    enriched = enrich_positions(positions)
    daily_pnl = compute_daily_realized_pnl(fills)
    peak = compute_peak_equity(total_equity, previous_peak_equity)

    context: dict[str, Any] = {
        "account_id": account_id,
        "positions": enriched,
        "balances": list(balances),
        "total_equity": total_equity,
        "portfolio_leverage": float(leverage),
        "daily_realized_pnl": daily_pnl,
        "peak_equity": peak,
        "current_price": current_price,
        "open_orders": open_orders,
    }

    if order_tracker is not None:
        context["order_tracker"] = order_tracker

    return context
