"""TWAP (Time-Weighted Average Price) order slicer.

Splits a large parent order into N evenly-spaced child orders executed
over a fixed duration. Each child is a slice of the parent quantity,
submitted at a fixed time interval so the parent's notional is spread
evenly across the duration.

This is a minimal implementation — no VWAP / POV / participation-rate
logic, no adaptive scheduling. Each child still goes through the
``LiveExecutionEngine.place_order`` risk gate (pre_check + kill_switch),
so TWAP does not bypass any controls.

Usage::

    schedule = twap_slice(
        parent_qty=10.0,
        duration_seconds=300,
        n_slices=5,
    )
    for offset_s, qty in schedule:
        await asyncio.sleep(offset_s)
        await engine.place_order(..., quantity=qty)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TWAPSlice:
    """A single child order in a TWAP schedule.

    Attributes:
        index: 0-based slice index.
        start_offset_s: Seconds from schedule start when this child should
            be submitted.
        quantity: Quantity for this child (in base currency).
    """

    index: int
    start_offset_s: float
    quantity: float


def twap_slice(
    *,
    parent_qty: float,
    duration_seconds: float,
    n_slices: int,
) -> list[TWAPSlice]:
    """Split a parent order into N evenly-spaced TWAP child slices.

    The schedule spans ``duration_seconds`` from t=0 to t=duration, with
    each child submitted at intervals of ``duration / n_slices``. The
    parent quantity is divided as evenly as possible — the last child
    absorbs any rounding remainder so the slices sum exactly to
    ``parent_qty``.

    Args:
        parent_qty: Total quantity to split (must be > 0).
        duration_seconds: Total schedule duration (must be > 0).
        n_slices: Number of child orders (must be >= 1).

    Returns:
        List of ``TWAPSlice`` ordered by ``start_offset_s`` ascending.

    Raises:
        ValueError: If any argument violates its precondition.
    """
    if parent_qty <= 0:
        raise ValueError(f"parent_qty must be positive, got {parent_qty}")
    if duration_seconds <= 0:
        raise ValueError(f"duration_seconds must be positive, got {duration_seconds}")
    if n_slices < 1:
        raise ValueError(f"n_slices must be >= 1, got {n_slices}")

    interval = duration_seconds / n_slices
    base_qty = parent_qty / n_slices

    slices: list[TWAPSlice] = []
    allocated = 0.0
    for i in range(n_slices):
        if i == n_slices - 1:
            # Last slice absorbs rounding remainder so total == parent_qty.
            qty = parent_qty - allocated
        else:
            qty = base_qty
            allocated += qty
        slices.append(
            TWAPSlice(
                index=i,
                start_offset_s=i * interval,
                quantity=qty,
            )
        )

    return slices
