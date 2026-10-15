"""Order replay: derive arbitrary portfolio snapshots from a trade ledger.

Absorption level L1 (idea): "orders + initial cash -> any-point portfolio
snapshot" replay concept, reworked for selene's own round-trip trade records.
No source-level copying from the upstream reference.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class PortfolioReplayResult(BaseModel):
    """Time-indexed portfolio snapshots reconstructed from a trade ledger."""

    cash_curve: list[float]
    position_qty: list[float]
    equity_curve: list[float]
    exposure_curve: list[float]
    leverage_curve: list[float]
    allocation_curve: list[dict]
    timestamps: list[int]
    final_state: dict


def replay_orders(
    trades: list[dict],
    initial_cash: float,
    *,
    timestamps: list[int] | None = None,
) -> PortfolioReplayResult:
    """Replay a completed round-trip trade ledger into portfolio snapshots.

    Each trade is treated as one open event (``entry_time``) and one close
    event (``exit_time``). Cash, net positions, equity, exposure, leverage and
    symbol allocation are recomputed at every point. ``pnl`` is the whole-trip
    result and is settled in full at close (no double counting).

    Parameters
    ----------
    trades
        Complete round-trip records shaped like the engine's ``_trade_record``.
    initial_cash
        Starting cash balance before any trade.
    timestamps
        Snapshot points to replay at. Defaults to the union of every
        entry/exit time. May include points with no trade activity.

    Returns
    -------
    PortfolioReplayResult
        Time-indexed cash / position / equity / exposure / leverage /
        allocation curves plus the final account state.
    """
    cash = float(initial_cash)
    positions: dict[str, float] = {}
    marks: dict[str, float] = {}  # symbol -> last known price at or before t

    # Events carry both prices: entry settles cash at close, exit updates the
    # mark used for equity/exposure at the instant of the event.
    # Tuple: (time, kind, symbol, side, entry_price, exit_price, quantity, pnl)
    events: list[tuple] = []
    for tr in trades:
        events.append(
            (
                int(tr["entry_time"]),
                "open",
                str(tr["symbol"]),
                str(tr["side"]),
                float(tr["entry_price"]),
                float(tr["exit_price"]),
                float(tr["quantity"]),
                0.0,
            )
        )
        events.append(
            (
                int(tr["exit_time"]),
                "close",
                str(tr["symbol"]),
                str(tr["side"]),
                float(tr["entry_price"]),
                float(tr["exit_price"]),
                float(tr["quantity"]),
                float(tr["pnl"]),
            )
        )

    if timestamps is not None:
        points = list(timestamps)
    else:
        if not events:
            points = [0]
        else:
            points = sorted({e[0] for e in events})

    cursor = 0
    ordered = sorted(events, key=lambda e: e[0])

    cash_curve: list[float] = []
    position_qty: list[float] = []
    equity_curve: list[float] = []
    exposure_curve: list[float] = []
    leverage_curve: list[float] = []
    allocation_curve: list[dict] = []

    for t in points:
        while cursor < len(ordered) and ordered[cursor][0] <= t:
            (
                _time,
                kind,
                symbol,
                side,
                entry_price,
                exit_price,
                quantity,
                pnl,
            ) = ordered[cursor]
            if kind == "open":
                marks[symbol] = entry_price
                if side == "long":
                    cash -= entry_price * quantity
                    positions[symbol] = positions.get(symbol, 0.0) + quantity
                else:
                    cash += entry_price * quantity
                    positions[symbol] = positions.get(symbol, 0.0) - quantity
            else:  # close
                marks[symbol] = exit_price
                if side == "long":
                    cash += entry_price * quantity + pnl
                    positions[symbol] = positions.get(symbol, 0.0) - quantity
                else:
                    cash -= entry_price * quantity - pnl
                    positions[symbol] = positions.get(symbol, 0.0) + quantity
            cursor += 1

        exposure = 0.0
        allocation: dict[str, float] = {}
        for symbol, qty in positions.items():
            if qty == 0.0:
                continue
            mark = marks.get(symbol, 0.0)
            notional = abs(qty * mark)
            exposure += notional
            allocation[symbol] = notional
        equity = cash + sum(qty * marks.get(s, 0.0) for s, qty in positions.items())
        leverage = exposure / equity if equity != 0.0 else 0.0
        if equity != 0.0:
            allocation = {s: v / equity for s, v in allocation.items()}

        cash_curve.append(cash)
        position_qty.append(sum(positions.values()))
        equity_curve.append(equity)
        exposure_curve.append(exposure)
        leverage_curve.append(leverage)
        allocation_curve.append(allocation)

    final_state: dict[str, Any] = {
        "cash": cash,
        "positions": {s: q for s, q in positions.items() if q != 0.0},
        "equity": equity,
    }
    return PortfolioReplayResult(
        cash_curve=cash_curve,
        position_qty=position_qty,
        equity_curve=equity_curve,
        exposure_curve=exposure_curve,
        leverage_curve=leverage_curve,
        allocation_curve=allocation_curve,
        timestamps=points,
        final_state=final_state,
    )
