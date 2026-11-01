"""Risk parity allocator — equal risk contribution weighting."""

from __future__ import annotations


class RiskParityAllocator:
    """Allocates capital inversely proportional to volatility.

    Weight ∝ 1 / volatility, so each strategy contributes equal risk.

    This is the simplest form of risk parity — full covariance-based
    risk parity requires quadratic optimization (see portfolio.optimizers).
    """

    def allocate(self, strategies: dict[str, dict]) -> dict[str, float]:
        """Allocate capital using inverse-vol weighting.

        Each strategy dict must have: volatility (float).

        Returns:
            Dict of strategy_id → weight (sum = 1.0).
        """
        if not strategies:
            return {}

        inv_vols: dict[str, float] = {}
        for sid, stats in strategies.items():
            vol = stats.get("volatility", 0.0)
            if vol <= 0:
                inv_vols[sid] = 0.0
            else:
                inv_vols[sid] = 1.0 / vol

        total = sum(inv_vols.values())
        if total <= 0:
            return {sid: 0.0 for sid in strategies}

        return {sid: round(v / total, 8) for sid, v in inv_vols.items()}
