"""POV (Percentage of Volume) execution algorithm.

Executes a parent order at a fixed participation rate of estimated market
volume. Slices are sized so each child represents `participation_rate` of
the estimated volume during its interval. This is more market-responsive
than TWAP — when volume is high, the algo trades more; when low, less.
"""

from __future__ import annotations

from selene_space_exchange.execution.algo.base import ChildOrder


class POVExecutor:
    """Percentage-of-Volume execution scheduler.

    Args:
        participation_rate: Fraction of market volume to target per slice
            (0 < rate <= 1.0). E.g. 0.10 = target 10% of volume.
        max_slices: Upper bound on number of child orders.
        estimated_volume: Total estimated market volume during the execution
            window (in base currency). Used to compute per-slice target.
            Default 0 means adaptive: slice until parent_qty is exhausted.
    """

    def __init__(
        self,
        *,
        participation_rate: float,
        max_slices: int,
        estimated_volume: float = 0.0,
    ) -> None:
        if not (0.0 < participation_rate <= 1.0):
            raise ValueError(
                f"participation_rate must be in (0, 1], got {participation_rate}"
            )
        if max_slices < 1:
            raise ValueError(f"max_slices must be >= 1, got {max_slices}")

        self._rate = participation_rate
        self._max_slices = max_slices
        self._est_volume = estimated_volume

    def schedule(self, parent_qty: float, price: float) -> list[ChildOrder]:
        if parent_qty <= 0:
            raise ValueError(f"parent_qty must be positive, got {parent_qty}")

        # Per-slice target: participation_rate of per-interval volume
        if self._est_volume > 0:
            per_slice_target = self._est_volume * self._rate / self._max_slices
        else:
            # No volume estimate — divide parent_qty by max_slices
            per_slice_target = parent_qty / self._max_slices

        slices: list[ChildOrder] = []
        remaining = parent_qty
        for i in range(self._max_slices):
            if remaining <= 0:
                break
            qty = min(per_slice_target, remaining)
            if qty <= 0:
                break
            slices.append(
                ChildOrder(
                    index=i,
                    start_offset_s=float(i),
                    quantity=qty,
                )
            )
            remaining -= qty

        # Absorb any remainder into the last slice
        if slices and remaining > 0:
            last = slices[-1]
            slices[-1] = ChildOrder(
                index=last.index,
                start_offset_s=last.start_offset_s,
                quantity=last.quantity + remaining,
            )

        return slices
