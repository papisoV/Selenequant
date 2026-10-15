# Cleanroom implementation: Selene-original leak-check suite (US-203). No code
# derived from rd-agent (MIT) or skill-quant-factor-volume-stat-alpha (GPL-3.0).
"""US-203 leak-check suite — five independent future-information audits.

Companion to :mod:`validation_protocol` (T4 walk-forward verdicts): while the
protocol audits *robustness* of OOS evidence, this module audits *integrity* of
the factor panel itself — the leak classes that a walk-forward cannot see
because they contaminate every window uniformly. Each function is independent
and importable standalone; none reuses ``validation_protocol`` internals.

Design:

- **Panel contract** — every factor/return argument is normalized by
  :func:`_to_wide` to a wide ``(datetime x symbol)`` DataFrame. Accepted
  inputs: a wide DataFrame (DatetimeIndex, symbol columns), a long DataFrame
  with ``symbol`` + a date column (``trade_date``/``date``/``datetime``) plus a
  value column (``factor``/``value``/``fut_ret_1``/``ret``/``return`` or the
  single remaining column), or a Series (single symbol, or MultiIndex
  ``(date, symbol)``). The same conventions as ``validation_protocol``'s
  long panels (``trade_date`` / ``symbol``) round-trip through this.
- **Lead cross-correlation** (:func:`check_lead_cross_corr`) — the check
  rd-agent's GT-corr could never perform: correlate ``factor_t`` with
  ``return_{t+h}`` for positive horizons (future — leakage indicator) and
  compare against the contemporaneous and lagged correlations (past — the
  legitimate-prediction baseline). A factor whose value at ``t`` tracks future
  returns far better than past ones is looking ahead. It is a computation on
  values, not a prompt sentence.
- **Shift invariance** (:func:`check_shift_invariance`) — lag-1 autocorrelation
  per symbol, averaged. Super-smooth factors (full-sample zscore backfill,
  full-period ranks broadcast across dates) are near-perfectly autocorrelated
  while honest factors at the same horizon are not. A HEURISTIC flag, not a
  verdict: smoothness is suspicious, not proof. Zero-variance symbols
  (a backfilled constant is exactly this) score 1.0 — a time-invariant series
  cannot carry more time-series information than a constant.
- **NaN position audit** (:func:`check_nan_positions`) — position-wise NaN
  mask comparison on the aligned ``(datetime x symbol)`` rectangle, not total
  counts (counting is rd-agent's known weakness; a leak that preserves the
  count while moving the holes escapes it). Survivorship-bias selection —
  dropping names using final-period information — leaves a hole pattern the
  ground truth does not have.
- **Threshold calibration** (:func:`calibrate_threshold`) — the ``(1-alpha)``
  quantile of |null ICs| from a permutation/null distribution, replacing
  arbitrary constants (rd-agent's flat 0.99). Callers generate ``null_ics``;
  this function only calibrates.
- **Cost parity disclosure** (:func:`report_cost_parity`) — side-by-side
  ``with_cost`` / ``without_cost`` metrics plus per-key drag. Pure data
  shaping; rd-agent reported only the without-cost leg.

Determinism: no randomness, no input mutation. Same inputs => identical dict.
Dependencies: pandas + numpy only.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

__all__ = [
    "check_lead_cross_corr",
    "check_shift_invariance",
    "check_nan_positions",
    "calibrate_threshold",
    "report_cost_parity",
    "to_wide",
]

#: Minimum finite paired observations for a per-symbol correlation to count.
_MIN_OBS: int = 30
#: Accepted date-column names in long-format panels.
_DATE_KEYS: tuple[str, ...] = ("trade_date", "date", "datetime")
#: Preferred value-column names in long-format panels.
_VALUE_KEYS: tuple[str, ...] = ("factor", "value", "fut_ret_1", "ret", "return")
#: How many mismatch positions :func:`check_nan_positions` lists in detail.
_MISMATCH_DETAIL: int = 10


# ---------------------------------------------------------------------------
# Panel normalization.
# ---------------------------------------------------------------------------
def to_wide(panel: pd.DataFrame | pd.Series) -> pd.DataFrame:
    """Normalize a factor/return panel to a wide ``(datetime x symbol)`` frame.

    See the module docstring for the accepted shapes. Index and column labels
    are coerced (datetime index, ``str`` symbol columns) and sorted; duplicate
    dates are a contract violation.
    """
    if isinstance(panel, pd.Series):
        if isinstance(panel.index, pd.MultiIndex):
            wide = panel.unstack()
        else:
            wide = panel.to_frame(name=str(panel.name) if panel.name else "symbol_0")
    elif isinstance(panel, pd.DataFrame):
        if "symbol" in panel.columns:
            date_col = next((c for c in _DATE_KEYS if c in panel.columns), None)
            if date_col is None:
                raise ValueError(
                    "long-format panel needs a date column, one of "
                    f"{_DATE_KEYS}; got columns {list(panel.columns)}"
                )
            value_col = next((c for c in _VALUE_KEYS if c in panel.columns), None)
            if value_col is None:
                non_keys = [c for c in panel.columns if c not in ("symbol", date_col)]
                if len(non_keys) != 1:
                    raise ValueError(
                        f"ambiguous long-format value column; candidates {non_keys}"
                    )
                value_col = non_keys[0]
            wide = panel.pivot(index=date_col, columns="symbol", values=value_col)
        else:
            wide = panel.copy()
    else:
        raise TypeError(
            f"panel must be a pandas DataFrame or Series, got {type(panel).__name__}"
        )
    if wide.index.has_duplicates:
        raise ValueError("panel index has duplicate dates")
    wide.index = pd.to_datetime(wide.index)
    wide.columns = [str(c) for c in wide.columns]
    return wide.sort_index().sort_index(axis=1)


def _pearson_n(x: np.ndarray, y: np.ndarray) -> tuple[float, int]:
    """Pearson correlation over the finite intersection, with pair count.

    Returns ``(nan, n)`` when fewer than ``_MIN_OBS`` finite pairs exist or
    either side is constant — such a pair carries no correlation evidence.
    """
    m = np.isfinite(x) & np.isfinite(y)
    x, y = np.asarray(x, dtype=float)[m], np.asarray(y, dtype=float)[m]
    n = int(x.size)
    if n < _MIN_OBS or np.ptp(x) == 0.0 or np.ptp(y) == 0.0:
        return math.nan, n
    return float(np.corrcoef(x, y)[0, 1]), n


def _mean_finite(values: list[float]) -> tuple[float, int]:
    """Mean of finite values (count of finite contributions)."""
    fin = [v for v in values if math.isfinite(v)]
    if not fin:
        return math.nan, 0
    return float(np.mean(fin)), len(fin)


# ---------------------------------------------------------------------------
# 1. Lead cross-correlation.
# ---------------------------------------------------------------------------
def check_lead_cross_corr(
    factor: pd.DataFrame | pd.Series,
    returns: pd.DataFrame | pd.Series,
    horizons: tuple[int, ...] = (1, 5, 10),
    threshold: float = 0.05,
) -> dict:
    """Correlate ``factor_t`` with ``return_{t+h}`` and flag lead leakage.

    For every horizon ``h`` the per-symbol Pearson correlation is computed in
    two directions — **lead** (``factor_t`` vs ``return_{t+h}``, the future
    side) and **lag** (``factor_t`` vs ``return_{t-h}``, the past side) — plus
    the contemporaneous ``h=0`` correlation, then averaged across symbols.
    Verdict rule: flag when the maximum |lead| correlation exceeds
    ``threshold`` AND the past-side baseline (|lag| and |h=0|). The first
    clause alone would false-flag a genuinely predictive factor; the second
    encodes the leak signature — information flowing backward in time must
    dominate the factor's honest forward edge.

    Args:
        factor: factor panel (wide or long; see :func:`to_wide`).
        returns: per-period return panel, same symbols preferred.
        horizons: positive lead horizons to probe.
        threshold: |lead| correlation above which leakage is suspected.

    Returns:
        Dict with per-horizon lead/lag correlations, the contemporaneous
        correlation, ``lead_abs_max``, ``baseline_abs_max``, the sampling
        noise band ``1/sqrt(n_obs)``, and the verdict
        ``PASS`` / ``LEAD_LEAK_SUSPECT``.
    """
    if not horizons or any(int(h) <= 0 for h in horizons):
        raise ValueError(f"horizons must be positive integers, got {horizons}")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"threshold must be in [0, 1], got {threshold}")

    f_wide = to_wide(factor)
    r_wide = to_wide(returns)
    symbols = [c for c in f_wide.columns if c in r_wide.columns]
    if not symbols:
        raise ValueError("no shared symbols between factor and returns panels")
    dates = f_wide.index.intersection(r_wide.index).sort_values()
    max_h = max(int(h) for h in horizons)
    if len(dates) < _MIN_OBS + max_h:
        raise ValueError(
            f"only {len(dates)} aligned dates; need >= {_MIN_OBS + max_h} "
            f"for the largest horizon {max_h}"
        )
    fv = f_wide.loc[dates, symbols].to_numpy(dtype=float)
    rv = r_wide.loc[dates, symbols].to_numpy(dtype=float)
    n_dates = len(dates)

    correlations: dict[str, dict[str, float]] = {}
    lead_candidates: list[float] = []
    baseline_candidates: list[float] = []
    n_obs: int | None = None
    for h in sorted({int(h) for h in horizons}):
        lead_vals: list[float] = []
        lag_vals: list[float] = []
        if h < n_dates:
            for s in range(len(symbols)):
                c, n = _pearson_n(fv[:-h, s], rv[h:, s])
                lead_vals.append(c)
                if n > 0:
                    n_obs = n if n_obs is None else min(n_obs, n)
                c, _ = _pearson_n(fv[h:, s], rv[:-h, s])
                lag_vals.append(c)
        lead_m, _ = _mean_finite(lead_vals)
        lag_m, _ = _mean_finite(lag_vals)
        correlations[str(h)] = {"lead": lead_m, "lag": lag_m}
        if math.isfinite(lead_m):
            lead_candidates.append(abs(lead_m))
        if math.isfinite(lag_m):
            baseline_candidates.append(abs(lag_m))

    contemporaneous_m, _ = _mean_finite(
        [_pearson_n(fv[:, s], rv[:, s])[0] for s in range(len(symbols))]
    )
    if math.isfinite(contemporaneous_m):
        baseline_candidates.append(abs(contemporaneous_m))

    lead_abs_max = max(lead_candidates) if lead_candidates else math.nan
    baseline_abs_max = max(baseline_candidates) if baseline_candidates else math.nan
    noise_band = 1.0 / math.sqrt(max(n_obs or 0, 4))
    flagged = (
        math.isfinite(lead_abs_max)
        and math.isfinite(baseline_abs_max)
        and lead_abs_max > threshold
        and lead_abs_max > baseline_abs_max
    )
    verdict = "LEAD_LEAK_SUSPECT" if flagged else "PASS"
    reason = (
        f"max |lead corr| {lead_abs_max:.4f} exceeds threshold {threshold} "
        f"and past-side baseline {baseline_abs_max:.4f}"
        if flagged
        else (
            f"max |lead corr| {lead_abs_max:.4f} does not dominate "
            f"baseline {baseline_abs_max:.4f} beyond threshold {threshold}"
        )
    )
    return {
        "verdict": verdict,
        "reason": reason,
        "horizons": correlations,
        "contemporaneous": contemporaneous_m,
        "lead_abs_max": lead_abs_max,
        "baseline_abs_max": baseline_abs_max,
        "threshold": float(threshold),
        "noise_band": noise_band,
        "n_obs": int(n_obs or 0),
        "n_symbols": len(symbols),
        "n_dates": n_dates,
    }


# ---------------------------------------------------------------------------
# 2. Shift invariance.
# ---------------------------------------------------------------------------
def check_shift_invariance(
    factor: pd.DataFrame | pd.Series,
    threshold: float = 0.999,
) -> dict:
    """Flag super-smooth factors via mean lag-1 autocorrelation.

    Per symbol, correlates ``factor_t`` with ``factor_{t+1}`` (the pairing
    ``corr(factor, factor.shift(-1))``) and averages across symbols. Full-sample
    backfills and full-period ranks score near 1.0; honest factors at the same
    lag do not. A zero-variance symbol (backfilled constant) scores exactly
    1.0 and is reported separately in ``n_constant``.

    This is a HEURISTIC flag, not a blocker: smoothness has legitimate causes
    (slow fundamentals, heavy smoothing). Verdict is ``SUSPECT`` when the mean
    exceeds ``threshold``, else ``PASS``.
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"threshold must be in [0, 1], got {threshold}")
    wide = to_wide(factor)
    per_symbol: dict[str, float] = {}
    n_constant = 0
    n_insufficient = 0
    for sym in wide.columns:
        x = wide[sym].to_numpy(dtype=float)
        a, b = x[:-1], x[1:]
        m = np.isfinite(a) & np.isfinite(b)
        a, b = a[m], b[m]
        if a.size < _MIN_OBS:
            per_symbol[sym] = math.nan
            n_insufficient += 1
        elif np.ptp(a) == 0.0 and np.ptp(b) == 0.0:
            # Time-invariant series: maximally smooth by construction.
            per_symbol[sym] = 1.0
            n_constant += 1
        else:
            per_symbol[sym] = _pearson_n(a, b)[0]
    mean_corr, n_evaluated = _mean_finite(list(per_symbol.values()))
    suspect = math.isfinite(mean_corr) and mean_corr > threshold
    return {
        "verdict": "SUSPECT" if suspect else "PASS",
        "reason": (
            f"mean lag-1 autocorr {mean_corr:.6f} exceeds {threshold} — "
            "super-smooth factor (full-sample backfill / full-period rank?)"
            if suspect
            else f"mean lag-1 autocorr {mean_corr:.6f} within normal band "
            f"(threshold {threshold})"
        ),
        "mean_autocorr_lag1": mean_corr,
        "per_symbol_autocorr_lag1": per_symbol,
        "threshold": float(threshold),
        "n_symbols": int(len(wide.columns)),
        "n_evaluated": int(n_evaluated),
        "n_constant": int(n_constant),
        "n_insufficient": int(n_insufficient),
    }


# ---------------------------------------------------------------------------
# 3. NaN position audit.
# ---------------------------------------------------------------------------
def check_nan_positions(
    gen_factor: pd.DataFrame | pd.Series,
    gt_factor: pd.DataFrame | pd.Series,
) -> dict:
    """Compare NaN masks position-by-position on the aligned panel rectangle.

    Total NaN counts can match while the holes sit in different places — the
    survivorship-bias signature (names dropped using final-period information
    shift the hole pattern). Both panels are normalized wide, intersected on
    dates and symbols, and the boolean NaN masks are compared elementwise.

    Returns:
        Dict with ``n_mismatch``, the first :data:`_MISMATCH_DETAIL` mismatch
        positions as ``(date, symbol)`` tuples, per-symbol mismatch counts,
        and verdict ``MATCH`` / ``MISMATCH``.
    """
    gen = to_wide(gen_factor)
    gt = to_wide(gt_factor)
    dates = gen.index.intersection(gt.index).sort_values()
    symbols = [c for c in gen.columns if c in gt.columns]
    if dates.empty or not symbols:
        raise ValueError(
            "gen_factor and gt_factor share no aligned (date, symbol) cells"
        )
    gen_al = gen.loc[dates, symbols]
    gt_al = gt.loc[dates, symbols]
    mask_gen = gen_al.isna().to_numpy()
    mask_gt = gt_al.isna().to_numpy()
    mismatch = mask_gen ^ mask_gt
    n_mismatch = int(mismatch.sum())
    rows, cols = np.nonzero(mismatch)
    positions = [(pd.Timestamp(dates[i]), symbols[j]) for i, j in zip(rows, cols)][
        :_MISMATCH_DETAIL
    ]
    per_symbol = {
        sym: int(mismatch[:, j].sum())
        for j, sym in enumerate(symbols)
        if mismatch[:, j].any()
    }
    return {
        "verdict": "MISMATCH" if n_mismatch else "MATCH",
        "reason": (
            f"{n_mismatch} of {mismatch.size} aligned cells have divergent NaN "
            f"patterns across {len(per_symbol)} symbols"
            if n_mismatch
            else f"NaN masks identical across all {mismatch.size} aligned cells"
        ),
        "n_mismatch": n_mismatch,
        "n_aligned_obs": int(mismatch.size),
        "n_aligned_dates": int(len(dates)),
        "n_aligned_symbols": len(symbols),
        "mismatch_positions": positions,
        "mismatch_per_symbol": per_symbol,
    }


# ---------------------------------------------------------------------------
# 4. Threshold calibration.
# ---------------------------------------------------------------------------
def calibrate_threshold(
    null_ics: "np.ndarray | list[float]", alpha: float = 0.05
) -> float:
    """``(1-alpha)`` quantile of the |null IC| distribution.

    Replaces arbitrary pass constants: a factor's |IC| must beat the level
    that a no-signal (permutation/null) process reaches only ``alpha`` of the
    time. ``null_ics`` comes from the caller's null distribution (e.g.
    permuted factor panels); this function only calibrates the cut.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    v = np.asarray(null_ics, dtype=float).ravel()
    v = v[np.isfinite(v)]
    if v.size == 0:
        raise ValueError("null_ics must contain at least one finite value")
    return float(np.quantile(np.abs(v), 1.0 - alpha))


# ---------------------------------------------------------------------------
# 5. Cost parity disclosure.
# ---------------------------------------------------------------------------
def report_cost_parity(
    metrics_with_cost: dict,
    metrics_without_cost: dict,
) -> dict:
    """Side-by-side with/without-cost disclosure plus per-metric cost drag.

    Pure data shaping, no trading logic: echoes both metric dicts verbatim and
    computes ``with - without`` for every SHARED key with numeric (non-bool)
    values on both sides. Non-numeric and one-sided keys are echoed but
    excluded from ``cost_drag``. Non-finite differences become ``None``
    (JSON-safe, mirroring ``validation_protocol._jsonable``).
    """
    with_cost = dict(metrics_with_cost or {})
    without_cost = dict(metrics_without_cost or {})
    drag: dict[str, float | None] = {}
    for key in with_cost.keys() & without_cost.keys():
        a, b = with_cost[key], without_cost[key]
        numeric = (
            isinstance(a, (int, float))
            and not isinstance(a, bool)
            and isinstance(b, (int, float))
            and not isinstance(b, bool)
        )
        if not numeric:
            continue
        d = float(a) - float(b)
        drag[key] = d if math.isfinite(d) else None
    return {
        "with_cost": with_cost,
        "without_cost": without_cost,
        "cost_drag": drag,
    }
