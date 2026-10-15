"""Weight-scheme plug-ins for the multi-symbol rebalancing engine."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AllocationModel(Protocol):
    """Produces a target weight per symbol for one rebalancing pass.

    Weights are fractions of total portfolio equity and should sum to
    roughly 1.0; symbols absent from the returned mapping get nothing.
    """

    def compute_weights(
        self,
        symbols: list[str],
        equity: float,
        prices: dict[str, float],
    ) -> dict[str, float]:
        """Return ``{symbol: weight}`` restricted to the given universe."""
        ...


def _sanitize(raw: dict[str, float]) -> dict[str, float]:
    """Drop negative weights, then rescale survivors to a 1.0 total."""
    mass = 0.0
    for w in raw.values():
        if w > 0.0:
            mass += w
    if mass <= 0.0:
        return {name: 0.0 for name in raw}
    return {name: (w / mass if w > 0.0 else 0.0) for name, w in raw.items()}


class EqualWeightAllocation:
    """Every symbol in the universe receives an identical fraction."""

    def compute_weights(
        self,
        symbols: list[str],
        equity: float,
        prices: dict[str, float],
    ) -> dict[str, float]:
        if not symbols:
            return {}
        share = 1.0 / len(symbols)
        return {name: share for name in symbols}


class FixedWeightAllocation:
    """User-supplied static targets, re-normalized on each call.

    Symbols missing from the target table weigh zero, so a universe
    broader than the table simply leaves the extras unallocated.
    """

    def __init__(self, weights: dict[str, float]) -> None:
        self._table = {name: float(value) for name, value in weights.items()}

    def compute_weights(
        self,
        symbols: list[str],
        equity: float,
        prices: dict[str, float],
    ) -> dict[str, float]:
        picked = {name: self._table.get(name, 0.0) for name in symbols}
        return _sanitize(picked)
