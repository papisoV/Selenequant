"""Adaptive execution algorithm.

Adjusts slice sizes based on real-time spread conditions. When the spread is
tight relative to a baseline, the algo front-loads (trades more early). When
wide, it back-loads (waits for spread to normalize). This reduces slippage
by avoiding trading during adverse market conditions.
"""

from __future__ import annotations

from selene_space_exchange.execution.algo.base import ChildOrder

_VeryLargeFloat: float = 1e300


class AdaptiveExecutor:
    """Spread-aware adaptive execution scheduler.

    Args:
        duration_s: Total schedule duration in seconds.
        n_slices: Number of child orders.
        base_spread: Baseline spread (fraction, e.g. 0.001 = 10bp) for
            normalizing the current spread. Must be >= 0.
    """

    def __init__(
        self,
        *,
        duration_s: float,
        n_slices: int,
        base_spread: float = 0.001,
    ) -> None:
        if duration_s <= 0:
            raise ValueError(f"duration_s must be positive, got {duration_s}")
        if n_slices < 1:
            raise ValueError(f"n_slices must be >= 1, got {n_slices}")
        if base_spread < 0:
            raise ValueError(f"base_spread must be >= 0, got {base_spread}")

        self._duration = duration_s
        self._n_slices = n_slices
        self._base_spread = base_spread
        self._interval = duration_s / n_slices

    def schedule(
        self,
        parent_qty: float,
        price: float,
        *,
        current_spread: float | None = None,
    ) -> list[ChildOrder]:
        """Split parent_qty into slices weighted by spread conditions.

        Args:
            parent_qty: Total quantity to execute.
            price: Current market price.
            current_spread: Current bid-ask spread as fraction of price.
                None or 0 = uniform slicing (no spread info).

        Returns:
            Ordered list of ChildOrder slices.
        """
        if parent_qty <= 0:
            raise ValueError(f"parent_qty must be positive, got {parent_qty}")

        weights = self._compute_spread_weights(current_spread)
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

    def _compute_spread_weights(self, current_spread: float | None) -> list[float]:
        """Compute per-slice weights based on spread conditions.

        Strategy:
        - spread <= base: front-load (exponentially decaying weights)
        - spread > base: back-load (exponentially increasing weights)
        - no spread info: uniform
        """
        n = self._n_slices
        uniform = [1.0 / n] * n

        if current_spread is None or current_spread <= 0 or self._base_spread <= 0:
            return uniform

        ratio = current_spread / self._base_spread

        if ratio <= 1.0:
            # Tight spread: front-load — earlier slices get more
            # weight[i] ∝ exp(-i / n) → decaying
            raw = [float(__import__("math").exp(-3.0 * i / max(n - 1, 1))) for i in range(n)]
        else:
            # Wide spread: back-load — later slices get more
            # weight[i] ∝ exp(i / n) → growing. Clamp ratio effect.
            decay_scale = min(ratio, 5.0)
            raw = [float(__import__("math").exp(decay_scale * i / max(n - 1, 1))) for i in range(n)]

        total = sum(raw)
        if total <= 0:
            return uniform

        return [w / total for w in raw]
