"""Deflated Sharpe Ratio (DSR), Probabilistic Sharpe Ratio (PSR) and
Minimum Track Record Length (MinTRL).

References
----------
Bailey, D. H., & Lopez de Prado, M. (2014).
    "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest
     Overfitting and Non-Normality." Journal of Portfolio Management, 40(5).
Bailey, D. H., & Lopez de Prado, M. (2012).
    "The Sharpe Ratio Efficient Frontier." Journal of Risk, 15(2).

All Sharpe ratios in this module are expressed in *per-observation* units
(i.e. NOT annualised).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np
from scipy.stats import norm

EULER_MASCHERONI = 0.5772156649015328606


def sharpe_ratio(returns: np.ndarray, benchmark: float = 0.0) -> float:
    """Per-period Sharpe ratio of a return series (ddof=1)."""
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    if r.size < 2:
        return float("nan")
    sd = r.std(ddof=1)
    if sd == 0:
        return float("nan")
    return float((r.mean() - benchmark) / sd)


def _skew_kurt(returns: np.ndarray) -> tuple[float, float]:
    """Sample skewness (g1) and *non-excess* kurtosis (g2, normal == 3)."""
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    n = r.size
    if n < 4:
        return 0.0, 3.0
    m = r.mean()
    s = r.std(ddof=0)
    if s == 0:
        return 0.0, 3.0
    skew = float(np.mean(((r - m) / s) ** 3))
    kurt = float(np.mean(((r - m) / s) ** 4))
    return skew, kurt


def annualised_to_per_period(sharpe_annual: float, periods_per_year: int = 252) -> float:
    return sharpe_annual / math.sqrt(periods_per_year)


def probabilistic_sharpe_ratio(
    observed_sr: float,
    benchmark_sr: float,
    n_obs: int,
    skew: float,
    kurtosis: float,
) -> float:
    """PSR(SR*) = P[ true SR > SR* ] given the estimation error of the observed
    Sharpe, adjusted for non-normality (skew / kurtosis)."""
    if n_obs < 2 or math.isnan(observed_sr):
        return float("nan")
    denom = 1.0 - skew * observed_sr + ((kurtosis - 1.0) / 4.0) * observed_sr ** 2
    denom = max(denom, 1e-12)
    se = math.sqrt(denom / (n_obs - 1))
    z = (observed_sr - benchmark_sr) / se
    return float(norm.cdf(z))


def expected_max_sharpe(sr_variance_across_trials: float, n_trials: int) -> float:
    """Expected maximum Sharpe Ratio across N *independent* trials whose true SR
    is zero (the deflation benchmark SR0)."""
    if n_trials < 1:
        return 0.0
    if n_trials == 1:
        return 0.0
    v = max(sr_variance_across_trials, 0.0)
    g = EULER_MASCHERONI
    z1 = norm.ppf(1.0 - 1.0 / n_trials)
    z2 = norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    return float(math.sqrt(v) * ((1.0 - g) * z1 + g * z2))


@dataclass(frozen=True)
class DSRResult:
    observed_sharpe: float
    deflated_benchmark_sr0: float
    psr_vs_zero: float
    deflated_sharpe_ratio: float
    n_obs: int
    n_trials: int
    skew: float
    kurtosis: float
    passed: bool

    def to_dict(self) -> dict:
        return {
            "observed_sharpe": self.observed_sharpe,
            "deflated_benchmark_sr0": self.deflated_benchmark_sr0,
            "psr_vs_zero": self.psr_vs_zero,
            "deflated_sharpe_ratio": self.deflated_sharpe_ratio,
            "n_obs": self.n_obs,
            "n_trials": self.n_trials,
            "skew": self.skew,
            "kurtosis": self.kurtosis,
            "passed": self.passed,
        }


def deflated_sharpe_ratio(
    strategy_returns: np.ndarray,
    n_trials: int,
    sr_variance_across_trials: float | None = None,
    all_trial_sharpes: Sequence[float] | None = None,
    threshold: float = 0.95,
) -> DSRResult:
    """Compute the Deflated Sharpe Ratio for the *selected* (best) strategy.

    Parameters
    ----------
    strategy_returns : per-period returns of the selected strategy.
    n_trials : number of independent strategy configurations that were tried.
    sr_variance_across_trials : Var of the per-period Sharpe estimates across
        all trials. If None, estimated from ``all_trial_sharpes``.
    all_trial_sharpes : optional list of every trial's per-period Sharpe.
    threshold : DSR pass threshold (default 0.95 -> 95% confidence).
    """
    r = np.asarray(strategy_returns, dtype=float)
    r = r[~np.isnan(r)]
    n = r.size
    sr = sharpe_ratio(r)
    skew, kurt = _skew_kurt(r)

    if sr_variance_across_trials is None:
        if all_trial_sharpes is not None and len(all_trial_sharpes) > 1:
            sr_variance_across_trials = float(np.var(np.asarray(all_trial_sharpes, float), ddof=1))
        else:
            denom = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr ** 2
            sr_variance_across_trials = max(denom, 1e-12) / max(n - 1, 1)

    sr0 = expected_max_sharpe(sr_variance_across_trials, n_trials)
    psr0 = probabilistic_sharpe_ratio(sr, sr0, n, skew, kurt)
    psr_zero = probabilistic_sharpe_ratio(sr, 0.0, n, skew, kurt)

    return DSRResult(
        observed_sharpe=sr,
        deflated_benchmark_sr0=sr0,
        psr_vs_zero=psr_zero,
        deflated_sharpe_ratio=psr0,
        n_obs=n,
        n_trials=n_trials,
        skew=skew,
        kurtosis=kurt,
        passed=bool(psr0 >= threshold),
    )


def minimum_track_record_length(
    observed_sr: float,
    benchmark_sr: float,
    skew: float,
    kurtosis: float,
    confidence: float = 0.95,
) -> float:
    """Minimum number of observations needed for the observed Sharpe to be
    statistically greater than ``benchmark_sr`` at the given confidence."""
    if observed_sr <= benchmark_sr:
        return float("inf")
    z = norm.ppf(confidence)
    num = 1.0 - skew * observed_sr + ((kurtosis - 1.0) / 4.0) * observed_sr ** 2
    return float(1.0 + max(num, 1e-12) * (z / (observed_sr - benchmark_sr)) ** 2)
