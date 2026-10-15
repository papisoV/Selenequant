"""Multiple-testing haircut of the Sharpe Ratio.

Reference
---------
Harvey, C. R., & Liu, Y. (2015). "Backtesting." Journal of Portfolio
    Management, 42(1), 13-28.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.stats import norm


def sharpe_to_tstat(sharpe_per_period: float, n_obs: int) -> float:
    """t-stat of a Sharpe ratio under iid normal returns: t = SR * sqrt(n)."""
    return sharpe_per_period * math.sqrt(n_obs)


def tstat_to_sharpe(tstat: float, n_obs: int) -> float:
    return tstat / math.sqrt(n_obs)


def _p_from_t(tstat: float) -> float:
    """Two-sided p-value from a (large-sample normal) t-stat."""
    return 2.0 * norm.sf(abs(tstat))


@dataclass(frozen=True)
class HaircutResult:
    method: str
    observed_sharpe: float
    adjusted_sharpe: float
    haircut: float
    observed_pvalue: float
    adjusted_pvalue: float
    n_tests: int


def _adjusted_p(p: float, n_tests: int, method: str, rank: int = 1) -> float:
    """Multiple-testing adjustment of a single p-value."""
    method = method.lower()
    if method == "bonferroni":
        return min(1.0, p * n_tests)
    if method == "holm":
        return min(1.0, p * (n_tests - rank + 1))
    if method == "bhy":
        c = sum(1.0 / i for i in range(1, n_tests + 1))
        return min(1.0, p * n_tests * c / rank)
    raise ValueError(f"unknown method: {method}")


def haircut_sharpe(
    observed_sharpe_per_period: float,
    n_obs: int,
    n_tests: int,
    method: str = "bonferroni",
    rank: int = 1,
) -> HaircutResult:
    """Compute the multiple-testing-adjusted Sharpe ratio.

    Parameters
    ----------
    observed_sharpe_per_period : per-period Sharpe of the reported strategy.
    n_obs : number of return observations.
    n_tests : total number of strategies/configurations tested.
    method : 'bonferroni' | 'holm' | 'bhy'.
    rank : rank of this strategy among the tests by significance (1 = best).
    """
    t_obs = sharpe_to_tstat(observed_sharpe_per_period, n_obs)
    p_obs = _p_from_t(t_obs)
    p_adj = _adjusted_p(p_obs, n_tests, method, rank)

    p_adj = min(1.0, max(p_adj, 1e-300))
    t_adj = norm.isf(p_adj / 2.0)
    t_adj = math.copysign(t_adj, observed_sharpe_per_period)
    sr_adj = tstat_to_sharpe(t_adj, n_obs)

    haircut = 1.0 - sr_adj / observed_sharpe_per_period if observed_sharpe_per_period else float("nan")
    return HaircutResult(
        method=method,
        observed_sharpe=observed_sharpe_per_period,
        adjusted_sharpe=sr_adj,
        haircut=haircut,
        observed_pvalue=p_obs,
        adjusted_pvalue=p_adj,
        n_tests=n_tests,
    )
