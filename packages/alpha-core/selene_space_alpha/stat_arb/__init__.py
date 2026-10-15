"""Statistical arbitrage — cointegration testing and spread analysis."""

from .cointegration import CointResult, PairResult, adf_test, engle_granger, find_cointegrated_pairs
from .spread import (
    compute_hedge_ratio_kalman,
    compute_hedge_ratio_ols,
    compute_spread,
    half_life,
    z_score_spread,
)

__all__ = [
    "CointResult",
    "PairResult",
    "adf_test",
    "compute_hedge_ratio_kalman",
    "compute_hedge_ratio_ols",
    "compute_spread",
    "engle_granger",
    "find_cointegrated_pairs",
    "half_life",
    "z_score_spread",
]
