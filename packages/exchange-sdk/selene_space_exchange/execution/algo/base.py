"""Execution algorithm base types.

Defines the shared Protocol and data model that all execution algorithms
(VWAP, POV, Adaptive, etc.) must satisfy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ChildOrder:
    """A single child order in an execution schedule.

    Attributes:
        index: 0-based slice index.
        start_offset_s: Seconds from schedule start when this child should
            be submitted.
        quantity: Quantity for this child (in base currency).
    """

    index: int
    start_offset_s: float
    quantity: float


@runtime_checkable
class ExecutionAlgo(Protocol):
    """Contract for execution algorithms.

    All algos produce a list of ChildOrder slices given a parent order.
    The caller runs the schedule::

        schedule = algo.schedule(parent_qty=10.0, price=50000.0)
        for child in schedule:
            await asyncio.sleep(child.start_offset_s)
            await engine.place_order(..., quantity=child.quantity)
    """

    def schedule(self, parent_qty: float, price: float) -> list[ChildOrder]:
        """Split a parent order into child slices.

        Args:
            parent_qty: Total quantity to execute.
            price: Current market price (used by adaptive algos).

        Returns:
            Ordered list of ChildOrder slices.
        """
        ...
