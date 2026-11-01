"""IC decay analysis — measure how quickly factor/signal predictive power fades.

Computes Information Coefficient (rank correlation between signal and
forward return) across multiple lags, then derives decay metrics:

  - IC by lag:   how predictive power decreases as holding period extends
  - Half-life:    lag at which IC drops to 50% of its peak
  - Turnover:     signal stability between consecutive bars
  - Turnover-adjusted IC:  IC penalized by signal instability

Usage:
    from selene_space_alpha.mining.ic_decay import compute_ic_decay, ICDdecayResult

    result = compute_ic_decay(signal, returns, max_lag=20)
    print(f"IC half-life: {result.half_life} bars")
    print(f"Peak IC at lag {result.peak_lag}: {result.peak_ic:.4f}")
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class ICDecayResult:
    """Results of IC decay analysis.

    Attributes:
        ic_by_lag: Series of IC values indexed by lag (1..max_lag).
        half_life: Lag at which IC drops to 50% of peak. None if never decays.
        peak_ic: Maximum absolute IC value across all lags.
        peak_lag: Lag at which peak IC occurs.
        turnover: Average absolute change in signal rank between consecutive bars.
        turnover_adjusted_ic: Peak IC penalized by turnover (IC * (1 - turnover)).
        is_decayed: True if IC at max_lag < 50% of peak IC.
    """

    ic_by_lag: pd.Series
    half_life: float | None
    peak_ic: float
    peak_lag: int
    turnover: float
    turnover_adjusted_ic: float
    is_decayed: bool


def compute_ic_decay(
    signal: pd.Series,
    returns: pd.Series,
    max_lag: int = 20,
    method: str = "spearman",
) -> ICDecayResult:
    """Compute IC decay across forward return lags.

    Args:
        signal: Factor/signal values (one per bar).
        returns: Forward returns aligned with signal. If these are
            bar-by-bar returns, the function internally shifts to compute
            cumulative returns at each lag.
        max_lag: Maximum forward lag to compute IC for.
        method: Correlation method — "spearman" (rank, default) or "pearson".

    Returns:
        ICDecayResult with decay metrics.
    """
    if len(signal) != len(returns):
        raise ValueError(
            f"signal and returns must have same length, "
            f"got {len(signal)} vs {len(returns)}"
        )
    if max_lag < 1:
        raise ValueError(f"max_lag must be >= 1, got {max_lag}")

    # Align indices
    common_idx = signal.index.intersection(returns.index)
    signal = signal.reindex(common_idx)
    returns = returns.reindex(common_idx)

    # Compute cumulative forward returns for each lag
    ic_values: dict[int, float] = {}
    for lag in range(1, max_lag + 1):
        fwd_ret = returns.shift(-lag).rolling(window=lag).sum()
        corr = _rank_corr(signal, fwd_ret, method=method)
        ic_values[lag] = corr

    ic_series = pd.Series(ic_values, name="ic")
    ic_series.index.name = "lag"

    # Turnover (independent of IC values)
    turnover = _compute_turnover(signal)

    # Peak IC (handle all-NaN case from constant signal)
    if ic_series.isna().all():
        return ICDecayResult(
            ic_by_lag=ic_series.fillna(0.0),
            half_life=None,
            peak_ic=0.0,
            peak_lag=1,
            turnover=turnover,
            turnover_adjusted_ic=0.0,
            is_decayed=True,
        )

    abs_ics = ic_series.abs()
    peak_lag = int(abs_ics.idxmax())
    peak_ic = float(ic_series.iloc[peak_lag - 1])

    # Half-life
    half_life = _compute_half_life(ic_series, peak_ic)

    # Turnover-adjusted IC
    turnover_adjusted_ic = peak_ic * (1.0 - turnover)

    # Is decayed?
    final_ic = abs(float(ic_series.iloc[-1]))
    is_decayed = final_ic < 0.5 * abs(peak_ic) if abs(peak_ic) > 1e-8 else True

    return ICDecayResult(
        ic_by_lag=ic_series,
        half_life=half_life,
        peak_ic=peak_ic,
        peak_lag=peak_lag,
        turnover=turnover,
        turnover_adjusted_ic=turnover_adjusted_ic,
        is_decayed=is_decayed,
    )


def compute_ic_by_lag(
    signal: pd.Series,
    returns: pd.Series,
    lags: list[int] | None = None,
    method: str = "spearman",
) -> pd.Series:
    """Compute IC at specific lags without full decay analysis.

    Args:
        signal: Factor/signal values.
        returns: Bar-by-bar returns.
        lags: List of forward lags. Default [1, 2, 3, 5, 10, 20].
        method: "spearman" or "pearson".

    Returns:
        Series of IC values indexed by lag.
    """
    if lags is None:
        lags = [1, 2, 3, 5, 10, 20]

    common_idx = signal.index.intersection(returns.index)
    signal = signal.reindex(common_idx)
    returns = returns.reindex(common_idx)

    ic_values: dict[int, float] = {}
    for lag in lags:
        fwd_ret = returns.shift(-lag).rolling(window=lag).sum()
        ic_values[lag] = _rank_corr(signal, fwd_ret, method=method)

    return pd.Series(ic_values, name="ic")


def _rank_corr(
    x: pd.Series,
    y: pd.Series,
    method: str = "spearman",
) -> float:
    """Compute rank correlation between two series, dropping NaN."""
    valid = x.notna() & y.notna()
    xv = x[valid]
    yv = y[valid]
    if len(xv) < 10:
        return 0.0

    if method == "spearman":
        return float(xv.corr(yv, method="spearman"))
    elif method == "pearson":
        return float(xv.corr(yv, method="pearson"))
    else:
        raise ValueError(f"method must be 'spearman' or 'pearson', got {method!r}")


def _compute_half_life(ic_series: pd.Series, peak_ic: float) -> float | None:
    """Find the lag at which |IC| drops to 50% of |peak_ic|.

    Returns None if IC never drops below 50% of peak.
    """
    abs_ics = ic_series.abs()
    threshold = abs(peak_ic) * 0.5

    for lag in range(1, len(abs_ics) + 1):
        if abs_ics.iloc[lag - 1] < threshold:
            return float(lag)

    return None


def _compute_turnover(signal: pd.Series) -> float:
    """Compute average absolute change in signal rank between consecutive bars.

    Turnover ∈ [0, 1]. Low turnover = stable signal. High turnover = noisy.
    """
    ranks = signal.rank(pct=True)
    rank_changes = ranks.diff().abs().dropna()
    if len(rank_changes) == 0:
        return 0.0
    return float(rank_changes.mean())
