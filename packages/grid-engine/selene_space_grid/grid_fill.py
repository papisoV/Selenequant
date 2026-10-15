"""GridEngine: intra-bar fill detection, re-arm, waterfall exit.

Ported from the user's own grid experiment (``grid_sim.py`` @ 43eee46),
decomposed from the engine class into pure functions over a cell list:

- ``check_fills`` — intra-bar high/low touch detection, one fill per cell
  per bar, deterministic ordering by distance from prior close.
- ``fill_entry`` / ``fill_exit`` — limit fills at the cell's own grid level;
  ``target_qty`` persists across exits (position self-decay prevention).
- ``fill_exit_market`` / ``waterfall_exit`` — trend teardown settles at the
  passed market price (1-tick adverse semantics documented in the source),
  never at a phantom grid level.
- ``rearm`` — exits armed first, then entries capped at max_open_orders.

Fees use ``selene_space_backtest.commissions.MakerTakerCommission`` (the
repo's single commission dialect): limit fills at the grid level → LIMIT →
maker rate; waterfall market teardown → MARKET → taker rate.
"""

from __future__ import annotations

from typing import Any

from selene_space_types.trading import OrderType

from selene_space_backtest.commissions import MakerTakerCommission

from selene_space_grid.cell import Cell, FillEvent, GridSpec

MAX_FILLS_PER_BAR_PER_CELL = 1  # a bar crossing entry+exit = one full cycle

DEFAULT_TAKER_RATE = 0.001
DEFAULT_MAKER_RATE = 0.001


def _order_type_for(kind: str) -> OrderType:
    return OrderType.MARKET if kind == "market" else OrderType.LIMIT


def fill_entry(cell: Cell, ts: int, symbol: str) -> FillEvent:
    """Limit entry fills at the cell's own grid level (long→lower, short→upper)."""
    price = cell.lower if cell.side == "long" else cell.upper
    qty = cell.target_qty if cell.target_qty > 0 else cell.qty
    value = qty * price
    cell.qty = qty
    cell.last_entry_price = price
    cell.state = "exit_ready"  # entry filled -> hold, arm exit
    cell.cycles += 1
    return FillEvent(
        ts=ts,
        symbol=symbol,
        index=cell.index,
        side=cell.side,
        kind="entry",
        price=price,
        qty=qty,
        value=value,
    )


def fill_exit(cell: Cell, ts: int, symbol: str) -> FillEvent:
    """Limit exit at the cell's own grid level (long→upper, short→lower)."""
    price = cell.upper if cell.side == "long" else cell.lower
    qty = cell.qty if cell.qty > 0 else 0.0
    value = qty * price
    pnl = 0.0
    if cell.side == "long":
        pnl = (price - cell.last_entry_price) * qty
    else:
        pnl = (cell.last_entry_price - price) * qty
    cell.qty = 0.0  # flat until next entry; target_qty preserved for re-entry
    cell.state = "entry_ready"
    return FillEvent(
        ts=ts,
        symbol=symbol,
        index=cell.index,
        side=cell.side,
        kind="exit",
        price=price,
        qty=qty,
        value=value,
        realized_pnl=pnl,
    )


def fill_exit_market(cell: Cell, ts: int, symbol: str, price: float) -> FillEvent:
    """Close a leg at a given market price (waterfall / stop).

    Slippage is conservatively modeled as a 1-tick adverse move by the caller
    passing an already-adverse price. Unlike ``fill_exit`` (which settles at
    the cell's own grid level), this is the real fill price when the grid is
    being torn down mid-trend."""
    qty = cell.qty if cell.qty > 0 else 0.0
    value = qty * price
    pnl = 0.0
    if cell.side == "long":
        pnl = (price - cell.last_entry_price) * qty
    else:
        pnl = (cell.last_entry_price - price) * qty
    cell.qty = 0.0
    cell.state = "entry_ready"
    return FillEvent(
        ts=ts,
        symbol=symbol,
        index=cell.index,
        side=cell.side,
        kind="exit",
        price=price,
        qty=qty,
        value=value,
        realized_pnl=pnl,
    )


def rearm(cells: list[Cell], spec: GridSpec) -> None:
    """Arm exits first, then entries up to max_open_orders."""
    for cell in cells:
        if cell.state == "exit_ready":
            cell.state = "exit_pending"  # armed exit
    active_entries = sum(1 for c in cells if c.state == "entry_pending")
    for cell in cells:
        if active_entries >= spec.max_open_orders:
            break
        if cell.state == "entry_ready":
            cell.state = "entry_pending"
            active_entries += 1


def waterfall_exit(
    cells: list[Cell],
    ts: int,
    symbol: str,
    price: float,
    commission: MakerTakerCommission | None = None,
) -> list[FillEvent]:
    """Close all open cells at the given market price (bounds broken / gate stop).

    Settles at ``price`` (the market close passed in), NOT the cell's own grid
    level, so a trend that blows through the range is realized at the true
    market price instead of a phantom limit fill."""
    events: list[FillEvent] = []
    for cell in cells:
        if cell.state in ("exit_ready", "exit_pending") and cell.qty > 0:
            events.append(fill_exit_market(cell, ts, symbol, price))
        cell.state = "disabled"
    if commission is not None:
        for ev in events:
            ev.fee = commission.calculate_with_type(
                ev.price, ev.qty, order_type=_order_type_for("market")
            )
    return events


def check_fills(
    cells: list[Cell],
    spec: GridSpec,
    bars: list[dict[str, Any]],
    commission: MakerTakerCommission | None = None,
) -> list[FillEvent]:
    """Process closed bars; return FillEvents in deterministic order.

    Rules (per source plan Q3):
      - skip forming bar (closed=False)
      - one fill per cell per bar (entry+exit crossing = one cycle, no re-entry)
      - entries before exits within the bar for that cell
      - multi-level crossings ordered by distance from prior close
      - buy-entries ascending / sell-entries descending
    """
    events: list[FillEvent] = []
    for bar in bars:
        if not bar.get("closed", True):
            continue
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
        ts = int(bar.get("time") or 0)

        # candidates: cells whose entry/exit line was touched this bar.
        # A limit order fills when price crosses its line:
        #   long entry  -> bar low touches cell.lower (buy low)
        #   long exit   -> bar high touches cell.upper (sell high)
        #   short entry -> bar high touches cell.upper (sell high)
        #   short exit  -> bar low touches cell.lower (buy low to cover)
        touched: list[
            tuple[float, int, str]
        ] = []  # (dist_from_close, cell_index, kind)
        for i, cell in enumerate(cells):
            if cell.state == "disabled":
                continue
            if cell.state in ("entry_ready", "entry_pending"):
                if cell.side == "long" and low <= cell.lower <= high:
                    touched.append((abs(close - cell.lower), i, "entry"))
                elif cell.side == "short" and low <= cell.upper <= high:
                    touched.append((abs(close - cell.upper), i, "entry"))
            if cell.state in ("exit_ready", "exit_pending"):
                if cell.side == "long" and high >= cell.upper:
                    touched.append((abs(close - cell.upper), i, "exit"))
                elif cell.side == "short" and low <= cell.lower:
                    touched.append((abs(close - cell.lower), i, "exit"))

        touched.sort(key=lambda t: t[0])
        for _dist, i, kind in touched:
            cell = cells[i]
            if kind == "entry" and cell.state in ("entry_ready", "entry_pending"):
                if cell.state == "entry_ready":
                    ev = fill_entry(cell, ts, spec.symbol)
                    if commission is not None:
                        ev.fee = commission.calculate_with_type(
                            ev.price, ev.qty, order_type=_order_type_for("limit")
                        )
                    events.append(ev)
            elif kind == "exit" and cell.state in ("exit_ready", "exit_pending"):
                ev = fill_exit(cell, ts, spec.symbol)
                if commission is not None:
                    ev.fee = commission.calculate_with_type(
                        ev.price, ev.qty, order_type=_order_type_for("limit")
                    )
                events.append(ev)
    return events


def open_leg_value(cells: list[Cell]) -> float:
    total = 0.0
    for cell in cells:
        if cell.state in ("exit_ready", "exit_pending") and cell.qty > 0:
            total += cell.qty * cell.last_entry_price
    return total


def snapshot(
    spec: GridSpec, cells: list[Cell], initial_allocated: bool
) -> dict[str, Any]:
    return {
        "spec_fingerprint": spec.fingerprint(),
        "symbol": spec.symbol,
        "cells": [c.to_dict() for c in cells],
        "initial_allocated": initial_allocated,
    }


def restore(
    spec: GridSpec,
    snap: dict[str, Any],
) -> tuple[list[Cell], bool] | None:
    if snap.get("spec_fingerprint") != spec.fingerprint():
        return None
    cells = [Cell.from_dict(c) for c in snap.get("cells", [])]
    initial_allocated = bool(snap.get("initial_allocated", False))
    return cells, initial_allocated
