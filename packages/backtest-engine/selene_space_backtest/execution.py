"""Execution algorithms — TWAP and VWAP order slicing.

Splits large orders into smaller child orders executed over time (TWAP)
or weighted by volume (VWAP) to minimize market impact.
"""

from __future__ import annotations

from dataclasses import dataclass

from selene_space_types.trading import Order


@dataclass(frozen=True)
class SliceParams:
    """Parameters for order slicing."""

    num_slices: int = 5
    interval_seconds: float = 60.0


@dataclass(frozen=True)
class ChildOrder:
    """A child order produced by an execution algorithm."""

    order: Order
    slice_index: int
    total_slices: int
    scheduled_time: int  # ms timestamp when this slice should be sent


def twap_slice(
    order: Order,
    num_slices: int = 5,
    interval_seconds: float = 60.0,
) -> list[ChildOrder]:
    """Split a large order into time-weighted equal-size slices.

    Each child order gets an equal fraction of the total quantity,
    spaced evenly over (num_slices * interval_seconds) seconds.

    Args:
        order: The parent order to slice.
        num_slices: Number of child orders (default 5).
        interval_seconds: Time between slices in seconds (default 60).

    Returns:
        List of ChildOrder objects with scheduled execution times.
    """
    if num_slices < 1:
        raise ValueError("num_slices must be >= 1")
    if order.quantity <= 0:
        return []

    # order.quantity is Decimal (H19). Keep slicing in float (plan v4 §3.1
    # 阶段 2C); the Order constructor below coerces float → Decimal.
    slice_qty = float(order.quantity) / num_slices
    base_ts = order.created_at
    children: list[ChildOrder] = []

    for i in range(num_slices):
        child = Order(
            id=f"{order.id}-slice-{i}",
            symbol=order.symbol,
            side=order.side,
            type=order.type,
            quantity=slice_qty,
            price=order.price,
            stop_price=order.stop_price,
            created_at=base_ts,
            updated_at=base_ts,
        )
        scheduled = base_ts + int(i * interval_seconds * 1000)
        children.append(ChildOrder(
            order=child,
            slice_index=i,
            total_slices=num_slices,
            scheduled_time=scheduled,
        ))

    return children


def vwap_slice(
    order: Order,
    volume_profile: list[float],
    interval_seconds: float = 60.0,
) -> list[ChildOrder]:
    """Split a large order into volume-weighted slices.

    Each child order's quantity is proportional to the expected volume
    in that time slot, reducing market impact by trading more when
    liquidity is higher.

    Args:
        order: The parent order to slice.
        volume_profile: Expected volume for each time slot. Length = num_slices.
            Values are relative (will be normalized). E.g. [100, 200, 150].
        interval_seconds: Time between slices in seconds (default 60).

    Returns:
        List of ChildOrder objects with volume-weighted quantities.
    """
    if not volume_profile:
        raise ValueError("volume_profile must not be empty")
    if order.quantity <= 0:
        return []

    total_vol = sum(volume_profile)
    if total_vol <= 0:
        raise ValueError("volume_profile must have positive values")

    # order.quantity is Decimal (H19). Keep slicing computation in the float
    # domain (plan v4 §3.1 阶段 2C); the Order constructor below coerces the
    # float slice_qty back to Decimal via the field validator.
    order_qty = float(order.quantity)
    base_ts = order.created_at
    children: list[ChildOrder] = []
    remaining = order_qty

    for i, vol in enumerate(volume_profile):
        weight = vol / total_vol
        if i == len(volume_profile) - 1:
            # Last slice gets the remainder to avoid rounding drift
            slice_qty = remaining
        else:
            slice_qty = order_qty * weight
            remaining -= slice_qty

        child = Order(
            id=f"{order.id}-vwap-{i}",
            symbol=order.symbol,
            side=order.side,
            type=order.type,
            quantity=slice_qty,
            price=order.price,
            stop_price=order.stop_price,
            created_at=base_ts,
            updated_at=base_ts,
        )
        scheduled = base_ts + int(i * interval_seconds * 1000)
        children.append(ChildOrder(
            order=child,
            slice_index=i,
            total_slices=len(volume_profile),
            scheduled_time=scheduled,
        ))

    return children
