"""Portfolio result model."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PortfolioResult:
    weights: np.ndarray
    objective: str
    success: bool
    message: str
    expected_return: float
    volatility: float
    sharpe_ratio: float
    risk_contributions: np.ndarray
