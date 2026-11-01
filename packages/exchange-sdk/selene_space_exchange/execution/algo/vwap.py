"""VWAP (Volume-Weighted Average Price) execution algorithm.

Slices a parent order across a fixed duration, allocating quantity to each
time bucket proportionally to that bucket's historical volume profile. This
minimizes market impact by trading more when the market is more liquid.

When no volume profile is provided, falls back to uniform (TWAP-equivalent)
slicing.
"""

from __future__ import annotations

from selene_space_exchange.execution.algo.base import ChildOrder


class VWAPExecutor:
    """Volume-weighted execution scheduler.

    Args:
        duration_s: Total schedule duration in seconds.
        n_slices: Number of child orders.
        volume_profile: Optional historical volume per bucket. If provided,
            len(volume_profile) must equal n_slices, and slices are weighted
            proportionally to each bucket's volume. If None, falls back to
            uniform weighting (TWAP-equivalent).
    """

    def __init__(
        self,
        *,
        duration_s: float,
        n_slices: int,
        volume_profile: list[float] | None = None,
    ) -> None:
        if duration_s <= 0:
            raise ValueError(f"duration_s must be positive, got {duration_s}")
        if n_slices < 1:
            raise ValueError(f"n_slices must be >= 1, got {n_slices}")
        if volume_profile is not None and len(volume_profile) != n_slices:
            raise ValueError(
                f"volume_profile length ({len(volume_profile)}) must equal n_slices ({n_slices})"
            )

        self._duration = duration_s
        self._n_slices = n_slices
        self._profile = volume_profile
        self._interval = duration_s / n_slices

    def schedule(self, parent_qty: float, price: float) -> list[ChildOrder]:
        if parent_qty <= 0:
            raise ValueError(f"parent_qty must be positive, got {parent_qty}")

        weights = self._compute_weights()
        slices: list[ChildOrder] = []
        allocated = 0.0
        for i in range(self._n_slices):
            if i == self._n_slices - 1:
                qty = parent_qty - allocated
            else:
                qty = parent_qty * weights[i]
                allocated += qty
            slices.append(
                ChildOrder(
                    index=i,
                    start_offset_s=i * self._interval,
                    quantity=qty,
                )
            )

        return slices

    def _compute_weights(self) -> list[float]:
        if self._profile is None:
            return [1.0 / self._n_slices] * self._n_slices

        total_vol = sum(self._profile)
        if total_vol <= 0:
            return [1.0 / self._n_slices] * self._n_slices

        return [v / total_vol for v in self._profile]
