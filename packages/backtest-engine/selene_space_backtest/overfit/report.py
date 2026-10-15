"""OverfitReport — orchestrates DSR, PBO, haircut and MinTRL into a single
verdict on whether a backtest is likely the product of selection bias /
multiple testing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .deflated_sharpe import (
    DSRResult,
    _skew_kurt,
    deflated_sharpe_ratio,
    minimum_track_record_length,
    sharpe_ratio,
)
from .haircut import haircut_sharpe
from .pbo_cscv import probability_of_backtest_overfitting


@dataclass(frozen=True)
class OverfitReport:
    verdict: str
    passed: bool
    observed_sharpe_annual: float
    skew: float
    kurtosis: float
    n_obs: int
    n_trials: int
    deflated_sharpe_ratio: float
    deflation_benchmark_sr0_annual: float
    psr_vs_zero: float
    haircut_sharpe_annual: float
    haircut_pct: float
    minimum_track_record_length: float
    pbo: float | None

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "passed": self.passed,
            "observed_sharpe_annual": self.observed_sharpe_annual,
            "skew": self.skew,
            "kurtosis": self.kurtosis,
            "n_obs": self.n_obs,
            "n_trials": self.n_trials,
            "deflated_sharpe_ratio": self.deflated_sharpe_ratio,
            "deflation_benchmark_sr0_annual": self.deflation_benchmark_sr0_annual,
            "psr_vs_zero": self.psr_vs_zero,
            "haircut_sharpe_annual": self.haircut_sharpe_annual,
            "haircut_pct": self.haircut_pct,
            "minimum_track_record_length": self.minimum_track_record_length,
            "pbo": self.pbo,
        }


def build_overfit_report(
    selected_returns: np.ndarray,
    n_trials: int,
    trials_matrix: np.ndarray | None = None,
    periods_per_year: int = 252,
    dsr_threshold: float = 0.95,
    pbo_threshold: float = 0.50,
    n_blocks: int = 16,
    haircut_method: str = "bonferroni",
) -> OverfitReport:
    """Build a comprehensive overfitting report.

    Parameters
    ----------
    selected_returns : per-period returns of the selected (best) strategy.
    n_trials : number of independent configurations tested.
    trials_matrix : optional (T x N) matrix of all trial returns for PBO.
    periods_per_year : annualization factor.
    dsr_threshold : DSR pass threshold.
    pbo_threshold : PBO fail threshold.
    n_blocks : CSCV block count for PBO.
    haircut_method : 'bonferroni' | 'holm' | 'bhy'.
    """
    r = np.asarray(selected_returns, dtype=float)
    r = r[~np.isnan(r)]
    ann = math.sqrt(periods_per_year)
    sr_pp = sharpe_ratio(r)
    skew, kurt = _skew_kurt(r)

    all_trial_sharpes: list[float] | None = None
    if trials_matrix is not None:
        tm = np.asarray(trials_matrix, dtype=float)
        all_trial_sharpes = [sharpe_ratio(tm[:, j]) for j in range(tm.shape[1])]
        all_trial_sharpes = [s for s in all_trial_sharpes if not math.isnan(s)]
        n_trials = max(n_trials, len(all_trial_sharpes))

    dsr: DSRResult = deflated_sharpe_ratio(
        r, n_trials=n_trials,
        all_trial_sharpes=all_trial_sharpes,
        threshold=dsr_threshold,
    )

    hc = haircut_sharpe(sr_pp, n_obs=r.size, n_tests=n_trials, method=haircut_method)

    mintrl = minimum_track_record_length(sr_pp, 0.0, skew, kurt)

    pbo_val: float | None = None
    if trials_matrix is not None and np.asarray(trials_matrix).shape[1] >= 2:
        pbo_result = probability_of_backtest_overfitting(
            np.asarray(trials_matrix, float), n_blocks=n_blocks,
        )
        pbo_val = pbo_result.pbo

    flags: list[str] = []
    if dsr.deflated_sharpe_ratio < dsr_threshold:
        flags.append(f"DSR {dsr.deflated_sharpe_ratio:.2f} < {dsr_threshold}")
    if pbo_val is not None and pbo_val > pbo_threshold:
        flags.append(f"PBO {pbo_val:.2f} > {pbo_threshold}")
    if hc.adjusted_sharpe * ann < 0.5:
        flags.append(f"haircut Sharpe {hc.adjusted_sharpe * ann:.2f} < 0.5")
    if mintrl > r.size:
        flags.append(f"MinTRL {mintrl:.0f} > sample {r.size}")

    passed = len(flags) == 0
    verdict = (
        "PASS - survives multiple-testing correction"
        if passed else
        "FAIL - likely overfit / selection-biased: " + "; ".join(flags)
    )

    return OverfitReport(
        verdict=verdict,
        passed=passed,
        observed_sharpe_annual=round(sr_pp * ann, 4),
        skew=round(skew, 4),
        kurtosis=round(kurt, 4),
        n_obs=int(r.size),
        n_trials=int(n_trials),
        deflated_sharpe_ratio=round(dsr.deflated_sharpe_ratio, 4),
        deflation_benchmark_sr0_annual=round(dsr.deflated_benchmark_sr0 * ann, 4),
        psr_vs_zero=round(dsr.psr_vs_zero, 4),
        haircut_sharpe_annual=round(hc.adjusted_sharpe * ann, 4),
        haircut_pct=round(hc.haircut, 4),
        minimum_track_record_length=round(mintrl, 1),
        pbo=round(pbo_val, 4) if pbo_val is not None else None,
    )


def render_text(report: OverfitReport) -> str:
    """Render an OverfitReport as a human-readable text block."""
    lines = [
        "=" * 64,
        " BACKTEST OVERFITTING REPORT",
        "=" * 64,
        f" Verdict : {report.verdict}",
        "-" * 64,
        f" Observed Sharpe (annual) : {report.observed_sharpe_annual}",
        f" Trials (multiple tests)  : {report.n_trials}",
        f" Observations             : {report.n_obs}",
        f" Skew / Kurtosis          : {report.skew} / {report.kurtosis}",
        "-" * 64,
        f" Deflated Sharpe Ratio    : {report.deflated_sharpe_ratio} "
        f"(benchmark SR0 {report.deflation_benchmark_sr0_annual} ann.)",
        f" PSR vs 0                 : {report.psr_vs_zero}",
        f" Haircut Sharpe           : {report.haircut_sharpe_annual} "
        f"(-{report.haircut_pct:.0%})",
        f" Min Track Record Length  : {report.minimum_track_record_length} obs",
    ]
    if report.pbo is not None:
        lines.append(f" PBO                      : {report.pbo}")
    lines.append("=" * 64)
    return "\n".join(lines)
