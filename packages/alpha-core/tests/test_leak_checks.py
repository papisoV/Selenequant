"""US-203 leak-check suite: synthetic-panel fixtures from the US-104 scenarios.

Exercises :mod:`selene_space_factor_research.mining.leak_checks` on random-walk
price panels (fixed seed). The leak fixtures reproduce the US-104 §3 regression
scenarios: L1 (``close.shift(-1)/close`` as factor), U1 (full-sample value
backfill — super-smooth constant per symbol) and U2 (survivorship selection —
top final-period performers kept, the rest NaN'd). Each check gets at least one
PASS case and one flag case.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from selene_space_factor_research.mining.leak_checks import (
    calibrate_threshold,
    check_lead_cross_corr,
    check_nan_positions,
    check_shift_invariance,
    report_cost_parity,
)

N_DATES = 120
N_SYMBOLS = 10


def _rw_panel(
    n_dates: int = N_DATES, n_symbols: int = N_SYMBOLS, seed: int = 42
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Random-walk closes and their per-period simple returns (date x symbol)."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0, 0.02, size=(n_dates, n_symbols))
    close = 100.0 * np.cumprod(1.0 + rets, axis=0)
    idx = pd.bdate_range("2024-01-01", periods=n_dates)
    cols = [f"S{i:03d}" for i in range(n_symbols)]
    return (
        pd.DataFrame(close, index=idx, columns=cols),
        pd.DataFrame(rets, index=idx, columns=cols),
    )


def _momentum(close: pd.DataFrame, k: int = 5) -> pd.DataFrame:
    """Honest past-looking k-day momentum (NaN warmup on the first k rows)."""
    return close / close.shift(k) - 1.0


# ---------------------------------------------------------------------------
# check_lead_cross_corr — US-104 L1 (direct future price) vs honest momentum.
# ---------------------------------------------------------------------------
def test_lead_cross_corr_catches_future_return_factor():
    close, rets = _rw_panel()
    # L1 fixture: tomorrow's return presented as today's factor value.
    leaky = close.shift(-1) / close - 1.0
    res = check_lead_cross_corr(leaky, rets, horizons=(1, 5, 10), threshold=0.05)
    assert res["verdict"] == "LEAD_LEAK_SUSPECT"
    assert res["horizons"]["1"]["lead"] > 0.9  # corr(f_t, r_{t+1}) = corr(r, r)
    assert res["lead_abs_max"] > res["baseline_abs_max"]
    assert res["lead_abs_max"] > res["threshold"]


def test_lead_cross_corr_passes_honest_momentum():
    close, rets = _rw_panel()
    res = check_lead_cross_corr(_momentum(close), rets, horizons=(1, 5, 10))
    assert res["verdict"] == "PASS"  # lead corr stays inside the noise band
    assert res["lead_abs_max"] <= res["baseline_abs_max"]
    # Momentum's edge lives in the contemporaneous/past side, not the future.
    assert res["contemporaneous"] > res["lead_abs_max"]


def test_lead_cross_corr_accepts_long_panel_convention():
    close, rets = _rw_panel()
    mom = _momentum(close)
    # validation_protocol's long convention: trade_date / symbol / factor.
    long_factor = (
        mom.rename_axis("trade_date")
        .reset_index()
        .melt(id_vars="trade_date", var_name="symbol", value_name="factor")
    )
    long_rets = (
        rets.rename_axis("trade_date")
        .reset_index()
        .melt(id_vars="trade_date", var_name="symbol", value_name="ret")
    )
    res_long = check_lead_cross_corr(long_factor, long_rets, horizons=(1, 5))
    res_wide = check_lead_cross_corr(mom, rets, horizons=(1, 5))
    assert res_long["verdict"] == res_wide["verdict"] == "PASS"
    for h in ("1", "5"):
        assert res_long["horizons"][h]["lead"] == pytest.approx(
            res_wide["horizons"][h]["lead"]
        )


def test_lead_cross_corr_input_validation():
    close, rets = _rw_panel()
    with pytest.raises(ValueError):
        check_lead_cross_corr(_momentum(close), rets, horizons=(0, 5))
    with pytest.raises(ValueError):
        check_lead_cross_corr(_momentum(close), rets, threshold=1.5)
    short_close, short_rets = _rw_panel(n_dates=20)
    with pytest.raises(ValueError):
        check_lead_cross_corr(_momentum(short_close), short_rets)


# ---------------------------------------------------------------------------
# check_shift_invariance — US-104 U1 (full-sample backfill) vs normal momentum.
# ---------------------------------------------------------------------------
def test_shift_invariance_flags_full_sample_backfill():
    close, _ = _rw_panel()
    mom = _momentum(close)
    # U1 variant: the factor value is computed ONCE with end-of-sample data
    # (final-bar momentum, full-sample cross-sectional zscore) and backfilled
    # to every date — a constant per symbol, maximally smooth.
    final_row = mom.iloc[-1]
    zscored = (final_row - final_row.mean()) / final_row.std()
    leaky = pd.DataFrame(
        np.repeat(zscored.to_numpy()[None, :], len(mom), axis=0),
        index=mom.index,
        columns=mom.columns,
    )
    res = check_shift_invariance(leaky, threshold=0.999)
    assert res["verdict"] == "SUSPECT"
    assert res["mean_autocorr_lag1"] > 0.999
    assert res["n_constant"] == N_SYMBOLS


def test_shift_invariance_passes_normal_momentum():
    close, _ = _rw_panel()
    res = check_shift_invariance(_momentum(close), threshold=0.999)
    assert res["verdict"] == "PASS"
    assert res["n_constant"] == 0
    # Overlapping 5-day momentum is autocorrelated but nowhere near 0.999.
    assert res["mean_autocorr_lag1"] < 0.95


def test_shift_invariance_threshold_validation():
    close, _ = _rw_panel()
    with pytest.raises(ValueError):
        check_shift_invariance(_momentum(close), threshold=-0.1)


# ---------------------------------------------------------------------------
# check_nan_positions — US-104 U2 (survivorship selection).
# ---------------------------------------------------------------------------
def test_nan_positions_detects_survivorship_selection():
    close, _ = _rw_panel()
    gt = _momentum(close)
    # U2: keep only the top-20% final-period performers, NaN the rest.
    total_ret = close.iloc[-1] / close.iloc[0] - 1.0
    kept = total_ret.nlargest(max(1, N_SYMBOLS // 5)).index
    gen = gt.copy()
    gen[[c for c in gt.columns if c not in kept]] = np.nan
    res = check_nan_positions(gen, gt)
    assert res["verdict"] == "MISMATCH"
    assert res["n_mismatch"] > 0
    assert len(res["mismatch_positions"]) == 10
    assert set(res["mismatch_per_symbol"]) == set(gt.columns) - set(kept)


def test_nan_positions_matches_when_only_values_differ():
    close, _ = _rw_panel()
    gt = _momentum(close)
    gen = gt * 2.0 + 1.0  # values differ everywhere, NaN positions identical
    res = check_nan_positions(gen, gt)
    assert res["verdict"] == "MATCH"
    assert res["n_mismatch"] == 0


def test_nan_positions_no_overlap_raises():
    _, rets = _rw_panel()
    with pytest.raises(ValueError):
        check_nan_positions(
            rets.iloc[:, :2], rets.iloc[:, 2:].rename(columns=lambda c: c + "_x")
        )


# ---------------------------------------------------------------------------
# calibrate_threshold.
# ---------------------------------------------------------------------------
def test_calibrate_threshold_returns_sensible_quantile():
    rng = np.random.default_rng(7)
    null_ics = rng.normal(0.0, 0.05, size=1000)
    t = calibrate_threshold(null_ics, alpha=0.05)
    assert 0.05 < t < 0.15
    assert t == pytest.approx(np.quantile(np.abs(null_ics), 0.95))
    assert t > calibrate_threshold(null_ics, alpha=0.5)  # monotone in 1-alpha
    assert calibrate_threshold(list(null_ics)) == pytest.approx(t)  # list input


def test_calibrate_threshold_validation():
    with pytest.raises(ValueError):
        calibrate_threshold([0.1, 0.2], alpha=0.0)
    with pytest.raises(ValueError):
        calibrate_threshold([0.1, 0.2], alpha=1.0)
    with pytest.raises(ValueError):
        calibrate_threshold([np.nan, np.inf])


# ---------------------------------------------------------------------------
# report_cost_parity.
# ---------------------------------------------------------------------------
def test_report_cost_parity_computes_drag_on_shared_numeric_keys():
    res = report_cost_parity(
        {"sharpe": 1.2, "ann_ret": 0.15, "label": "net"},
        {"sharpe": 1.5, "ann_ret": 0.20, "extra": 9},
    )
    assert res["with_cost"] == {"sharpe": 1.2, "ann_ret": 0.15, "label": "net"}
    assert res["without_cost"]["sharpe"] == 1.5
    assert res["cost_drag"]["sharpe"] == pytest.approx(-0.3)
    assert res["cost_drag"]["ann_ret"] == pytest.approx(-0.05)
    assert "label" not in res["cost_drag"]
    assert "extra" not in res["cost_drag"]


def test_report_cost_parity_non_numeric_and_nonfinite():
    res = report_cost_parity(
        {"flag": True, "sharpe": float("nan"), "n": 3},
        {"flag": False, "sharpe": 1.0, "n": 3},
    )
    assert "flag" not in res["cost_drag"]  # bools are not draggable metrics
    assert res["cost_drag"]["sharpe"] is None  # non-finite diff -> None
    assert res["cost_drag"]["n"] == 0.0


# ---------------------------------------------------------------------------
# Determinism.
# ---------------------------------------------------------------------------
def test_deterministic():
    close, rets = _rw_panel()
    mom = _momentum(close)
    a = check_lead_cross_corr(mom, rets, horizons=(1, 5))
    b = check_lead_cross_corr(mom, rets, horizons=(1, 5))
    assert a == b
    assert check_shift_invariance(mom) == check_shift_invariance(mom)
