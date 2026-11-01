"""Arithmetic (equal-spacing) grid generation and rung-replacement rules.

Shared by neutral / long / short grid modes. Pure functions -- no I/O.
Inspired by a Node.js grid bot's arithmetic grid generation (clean-room reimplementation, no source code copied).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from selene_space_exchange.dex.types import GridMode
from selene_space_types.trading import OrderSide


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def round8(x: float) -> float:
    """Round to 8 decimal places (mirrors JS ``round`` in grid.js)."""
    return round(x * 1e8) / 1e8


def _grid_spacing(levels: List[float]) -> float:
    """Infer spacing from the first two levels."""
    return (levels[1] - levels[0]) if len(levels) > 1 else 0.0


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GridSpec:
    """Result of ``build_grid``: the level array, spacing, and cell count."""

    levels: List[float]
    spacing: float
    count: int


@dataclass(frozen=True)
class SeedOrder:
    """An initial order to place when seeding a grid."""

    level_index: int
    price: float
    side: OrderSide
    reduce_only: bool


@dataclass(frozen=True)
class ReplacementOrder:
    """The replacement order to place after a fill (opposite side, one rung away)."""

    level_index: int
    price: float
    side: OrderSide
    reduce_only: bool


@dataclass(frozen=True)
class FilledOrder:
    """A just-filled grid order used as input to ``replacement_for``."""

    level_index: int
    price: float
    side: OrderSide


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def build_grid(*, lower: float, upper: float, grid_count: int) -> GridSpec:
    """Build an arithmetic grid.

    Returns a ``GridSpec`` where ``levels[0] == lower`` and
    ``levels[count] == upper``.  *count* is the number of cells (gaps),
    so the level array has ``count + 1`` entries.

    Raises:
        ValueError: If ``upper`` <= ``lower`` or ``grid_count`` < 2.
    """
    if not (upper > lower):
        raise ValueError("upper must be greater than lower")
    if not (grid_count >= 2):
        raise ValueError("grid_count must be at least 2")

    spacing = (upper - lower) / grid_count
    levels: List[float] = [round8(lower + i * spacing) for i in range(grid_count + 1)]
    return GridSpec(levels=levels, spacing=round8(spacing), count=grid_count)


def is_reduce_only(side: str, mode: str) -> bool:
    """Return whether an order of *side* is reduce-only under *mode*.

    - ``long``  mode: sells only close longs.
    - ``short`` mode: buys only close shorts.
    - ``neutral`` mode: both sides can open.
    """
    if mode == GridMode.LONG.value:
        return side == OrderSide.SELL.value
    if mode == GridMode.SHORT.value:
        return side == OrderSide.BUY.value
    return False


def seed_orders(
    *,
    levels: List[float],
    price: float,
    mode: GridMode,
    skip_band: float = 0.25,
    spacing: Optional[float] = None,
) -> List[SeedOrder]:
    """Generate initial orders given current *price* and grid *mode*.

    - **neutral**: buys below price, sells above price.
    - **long**: buys below price only (accumulate long, take profit on rise).
    - **short**: sells above price only (build short, take profit on fall).

    Levels within ``skip_band`` (fraction of spacing) of the current price
    are skipped so we don't immediately cross the market.
    """
    band = (spacing if spacing is not None else _grid_spacing(levels)) * skip_band
    orders: List[SeedOrder] = []

    for i, lvl in enumerate(levels):
        if abs(lvl - price) < band:
            continue

        if lvl < price:
            if mode in (GridMode.NEUTRAL, GridMode.LONG):
                orders.append(
                    SeedOrder(
                        level_index=i,
                        price=lvl,
                        side=OrderSide.BUY,
                        reduce_only=is_reduce_only(OrderSide.BUY.value, mode.value),
                    )
                )
        elif lvl > price:
            if mode in (GridMode.NEUTRAL, GridMode.SHORT):
                orders.append(
                    SeedOrder(
                        level_index=i,
                        price=lvl,
                        side=OrderSide.SELL,
                        reduce_only=is_reduce_only(OrderSide.SELL.value, mode.value),
                    )
                )

    return orders


def replacement_for(
    filled: FilledOrder,
    levels: List[float],
    mode: GridMode,
) -> Optional[ReplacementOrder]:
    """Return the replacement order after *filled*, or ``None`` if outside the grid.

    - Buy filled at i  -> Sell at i+1 (lock in one grid rung of profit).
    - Sell filled at i -> Buy  at i-1.
    """
    if filled.side == OrderSide.BUY:
        j = filled.level_index + 1
        if j > len(levels) - 1:
            return None
        return ReplacementOrder(
            level_index=j,
            price=levels[j],
            side=OrderSide.SELL,
            reduce_only=is_reduce_only(OrderSide.SELL.value, mode.value),
        )
    else:
        j = filled.level_index - 1
        if j < 0:
            return None
        return ReplacementOrder(
            level_index=j,
            price=levels[j],
            side=OrderSide.BUY,
            reduce_only=is_reduce_only(OrderSide.BUY.value, mode.value),
        )


def rung_profit(spacing: float, size_base: float) -> float:
    """Profit (quote currency) captured by one completed buy->sell rung."""
    return spacing * size_base
