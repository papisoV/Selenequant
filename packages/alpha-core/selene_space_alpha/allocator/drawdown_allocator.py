"""Drawdown-based allocator — inverse drawdown weighting.

Strategies in deeper drawdown get less capital. Those at max drawdown
cap get zero.
"""

from __future__ import annotations


class DrawdownBasedAllocator:
    """Allocates capital inversely to current drawdown.

    Weight ∝ 1 / (drawdown + epsilon), where epsilon prevents division
    by zero.

    Args:
        epsilon: Small offset to prevent division by zero (default 0.01).
    """

    def __init__(self, epsilon: float = 0.01) -> None:
        self._epsilon = epsilon

    def allocate(
        self,
        strategies: dict[str, dict],
        max_drawdown_cap: float = 0.50,
    ) -> dict[str, float]:
        """Allocate capital by inverse drawdown.

        Each strategy dict must have: current_drawdown (float, 0-1).

        Args:
            strategies: Dict of strategy_id → stats.
            max_drawdown_cap: Strategies at or above this drawdown get 0.

        Returns:
            Dict of strategy_id → weight (sum = 1.0).
        """
        if not strategies:
            return {}

        scores: dict[str, float] = {}
        for sid, stats in strategies.items():
            dd = stats.get("current_drawdown", 0.0)
            if dd >= max_drawdown_cap:
                scores[sid] = 0.0
            else:
                scores[sid] = 1.0 / (dd + self._epsilon)

        total = sum(scores.values())
        if total <= 0:
            return {sid: 0.0 for sid in strategies}

        return {sid: round(v / total, 8) for sid, v in scores.items()}
