"""Momentum-tilted risk parity allocation model.

Combines inverse-volatility risk parity (``RiskParityAllocator``) with a
momentum tilt: assets with positive recent momentum are scaled up and negative
momentum scaled down relative to the risk-parity base. Implements the
backtest-engine ``AllocationModel`` protocol so it plugs directly into
``MultiAssetEngine`` periodic rebalancing.
"""

from __future__ import annotations

import numpy as np

from selene_space_alpha.allocator.risk_parity_allocator import RiskParityAllocator


class MomentumRiskParityAllocation:
    """Risk-parity base weights tilted by trailing-return momentum.

    At each ``compute_weights`` call the model accumulates the per-symbol close
    prices handed to it by the engine, so the volatility and momentum estimates
    grow as the backtest progresses. Before enough history exists it falls back
    to equal weights (a safe warm-up), then shifts toward the momentum-tilted
    risk-parity weights.

    Args:
        vol_lookback: Most-recent bars of returns used for per-symbol volatility.
        momentum_lookback: Bars of returns behind the momentum signal.
        tilt_strength: How strongly momentum scales risk-parity weights.
            ``w_final ∝ w_rp * max(0, 1 + tilt_strength * clip(momentum, -1, 1))``
            (long-only: the multiplier is floored at zero); ``0`` yields pure
            risk parity.
        periods_per_year: Annualization factor for volatility (252 for daily).
    """

    def __init__(
        self,
        vol_lookback: int = 60,
        momentum_lookback: int = 126,
        tilt_strength: float = 1.0,
        periods_per_year: int = 252,
    ) -> None:
        self._vol_lookback = max(2, int(vol_lookback))
        self._momentum_lookback = max(1, int(momentum_lookback))
        self._tilt_strength = float(tilt_strength)
        self._periods_per_year = int(periods_per_year)
        self._history: dict[str, list[float]] = {}
        self._rp = RiskParityAllocator()

    def compute_weights(
        self,
        symbols: list[str],
        equity: float,
        prices: dict[str, float] | None = None,
    ) -> dict[str, float]:
        if not symbols:
            return {}
        if prices:
            # Keep only what the estimators can use (max lookback + 1 closes),
            # so a long backtest doesn't accumulate unbounded history per symbol.
            keep = max(self._vol_lookback, self._momentum_lookback) + 1
            for sym in symbols:
                if sym in prices:
                    hist = self._history.setdefault(sym, [])
                    hist.append(prices[sym])
                    if len(hist) > keep:
                        del hist[:-keep]

        base = self._rp.allocate(
            {sym: {"volatility": self._volatility(sym)} for sym in symbols}
        )
        if sum(base.values()) <= 0:
            base = {sym: 1.0 / len(symbols) for sym in symbols}

        raw: dict[str, float] = {}
        for sym in symbols:
            tilt = float(np.clip(self._momentum(sym), -1.0, 1.0))
            multiplier = max(0.0, 1.0 + self._tilt_strength * tilt)
            raw[sym] = base.get(sym, 0.0) * multiplier

        total = sum(raw.values())
        if total <= 0:
            return {sym: 0.0 for sym in symbols}
        return {sym: round(v / total, 8) for sym, v in raw.items()}

    def _volatility(self, sym: str) -> float:
        """Annualized realized volatility over the most recent vol_lookback returns."""
        closes = self._history.get(sym, [])
        if len(closes) < 2:
            return 0.0
        closes_arr = np.asarray(closes, dtype=np.float64)
        returns = closes_arr[1:] / closes_arr[:-1] - 1.0
        window = returns[-self._vol_lookback:]
        if len(window) < 2:
            return 0.0
        return float(np.std(window) * np.sqrt(self._periods_per_year))

    def _momentum(self, sym: str) -> float:
        """Trailing total return over up to momentum_lookback bars."""
        closes = self._history.get(sym, [])
        if len(closes) < 2:
            return 0.0
        n = min(self._momentum_lookback, len(closes) - 1)
        prev = closes[-n - 1] if len(closes) > n else closes[0]
        if prev <= 0:
            return 0.0
        return float(closes[-1] / prev - 1.0)
