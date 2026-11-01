"""Kelly allocator — allocate capital proportional to Sharpe / volatility."""

from __future__ import annotations


class KellyAllocator:
    """Allocates capital using Kelly criterion (Sharpe-based).

    Kelly fraction f = (sharpe * vol) / max_leverage, clipped to [0, 1].
    Final weights are proportional to the clipped Kelly fractions.

    Args:
        max_leverage: Maximum portfolio leverage (caps per-strategy fraction).
        fractional: Scale factor applied AFTER normalization (1.0 = full Kelly,
            0.25 = quarter Kelly). Defaults to full size for backward compat.
    """

    def __init__(self, max_leverage: float = 2.0, fractional: float = 1.0) -> None:
        if max_leverage <= 0:
            raise ValueError("max_leverage must be positive")
        if fractional <= 0:
            raise ValueError("fractional must be positive")
        self._max_leverage = max_leverage
        self.fractional = fractional

    def allocate(self, strategies: dict[str, dict]) -> dict[str, float]:
        """Allocate capital across strategies.

        Each strategy dict must have: sharpe (float), volatility (float).

        Returns:
            Dict of strategy_id → weight (sum = 1.0).
        """
        if not strategies:
            return {}

        fractions: dict[str, float] = {}
        for sid, stats in strategies.items():
            sharpe = stats.get("sharpe", 0.0)
            if sharpe <= 0:
                fractions[sid] = 0.0
                continue
            # Kelly: f = excess_return / variance ≈ sharpe * vol / max_leverage
            vol = stats.get("volatility", 0.01)
            if vol <= 0:
                fractions[sid] = 0.0
                continue
            f = (sharpe * vol) / self._max_leverage
            fractions[sid] = max(0.0, min(1.0, f))

        total = sum(fractions.values())
        if total <= 0:
            return {sid: 0.0 for sid in strategies}

        return {sid: round(v / total * self.fractional, 8) for sid, v in fractions.items()}
