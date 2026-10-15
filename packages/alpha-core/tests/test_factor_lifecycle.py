"""Factor lifecycle: state machine, full-cycle chain, decay escalation, alerts.

Tests :class:`~selene_space_alpha.factor_lifecycle.FactorLifecycle` — the
ACTIVE/WATCH/DECAYING/RETIRED machine, the per-cycle pipeline
(clean → backtest → attribution → IC monitor → verdict), and the pluggable
``on_alert`` callback.
"""

from __future__ import annotations

import ast
import pathlib

import numpy as np
import pandas as pd
import pytest

from selene_space_alpha.factor_lifecycle import (
    FactorLifecycle,
    FactorState,
    TRANSITIONS,
    transition,
)

_MODULE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "selene_space_alpha"
    / "factor_lifecycle.py"
)

N_DATES = 120
N_SYMBOLS = 6

pytestmark = pytest.mark.filterwarnings(
    "ignore:invalid value encountered in scalar power"
)


def _panel(seed: int = 0, decayed: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Panel whose per-symbol factor is a random-walk level (healthy) or white
    noise (decayed). In both cases ret_t = 0.5 * factor_{t-1} + noise, so the
    decayed factor predicts only the next bar while the healthy level stays
    predictive across lags.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=N_DATES)
    Fs, Rs = [], []
    for _ in range(N_SYMBOLS):
        if decayed:
            fac = rng.normal(0.0, 1.0, N_DATES)
        else:
            fac = np.cumsum(rng.normal(0.0, 1.0, N_DATES))
        prev = np.concatenate([[0.0], fac[:-1]])
        ret = 0.5 * prev + 0.3 * rng.normal(0.0, 1.0, N_DATES)
        Fs.append(fac)
        Rs.append(ret)
    factor_df = pd.DataFrame(
        {
            "trade_date": np.repeat(dates, N_SYMBOLS),
            "symbol": np.tile([f"S{i}" for i in range(N_SYMBOLS)], N_DATES),
            "factor": np.array(Fs).T.ravel(),
            "size": rng.lognormal(0.0, 1.0, N_DATES * N_SYMBOLS),
        }
    )
    ret_df = pd.DataFrame(
        {
            "trade_date": np.repeat(dates, N_SYMBOLS),
            "symbol": np.tile([f"S{i}" for i in range(N_SYMBOLS)], N_DATES),
            "fut_ret_1": np.array(Rs).T.ravel(),
        }
    )
    return factor_df, ret_df


WIN_STEP = {
    "method": "winsorize_mad",
    "params": {"trade_date_col": "trade_date", "factor_col": "factor", "n": 3.0},
}


# --------------------------------------------------------------------------
# State machine (pure transition rules)
# --------------------------------------------------------------------------
def test_initial_state_active() -> None:
    lc = FactorLifecycle()
    assert lc.state == FactorState.ACTIVE
    assert lc.n_cycles == 0
    assert lc.history == []


def test_transition_rules() -> None:
    cases = [
        (FactorState.ACTIVE, "healthy", FactorState.ACTIVE),
        (FactorState.ACTIVE, "decay", FactorState.WATCH),
        (FactorState.WATCH, "healthy", FactorState.ACTIVE),
        (FactorState.WATCH, "decay", FactorState.DECAYING),
        (FactorState.DECAYING, "healthy", FactorState.WATCH),
        (FactorState.DECAYING, "decay", FactorState.RETIRED),
        (FactorState.RETIRED, "healthy", FactorState.RETIRED),
        (FactorState.RETIRED, "decay", FactorState.RETIRED),
    ]
    for before, verdict, expected in cases:
        assert transition(before, verdict) == expected, f"{before} {verdict}"
    assert TRANSITIONS[FactorState.RETIRED] == {
        "healthy": FactorState.RETIRED,
        "decay": FactorState.RETIRED,
    }


def test_transition_bad_verdict_raises() -> None:
    with pytest.raises(ValueError, match="verdict"):
        transition(FactorState.ACTIVE, "sideways")


# --------------------------------------------------------------------------
# Single-cycle behavior
# --------------------------------------------------------------------------
def test_healthy_cycle_stays_active() -> None:
    lc = FactorLifecycle(rolling_window=10, max_lag=10)
    factor_df, ret_df = _panel(decayed=False)
    r = lc.cycle(factor_df, ret_df)
    assert r.decision == "healthy"
    assert r.state_before == FactorState.ACTIVE
    assert r.state_after == FactorState.ACTIVE
    assert r.mean_ic > 0.3  # persistent level keeps cross-sectional IC high
    assert lc.state == FactorState.ACTIVE
    assert lc.n_cycles == 1


def test_decayed_cycle_enters_watch() -> None:
    lc = FactorLifecycle(rolling_window=10, max_lag=10)
    factor_df, ret_df = _panel(decayed=True)
    r = lc.cycle(factor_df, ret_df)
    assert r.decision == "decay"
    assert r.state_after == FactorState.WATCH
    assert r.half_life is not None
    assert lc.state == FactorState.WATCH


def test_report_fields_populated() -> None:
    lc = FactorLifecycle(rolling_window=10, max_lag=10)
    factor_df, ret_df = _panel(decayed=False)
    r = lc.cycle(factor_df, ret_df)
    assert len(r.trace_hash) == 64
    assert {"Ann. Return", "Ann. Sharpe", "Max Drawdown", "Mean Turnover"} <= set(
        r.backtest
    )
    assert {"factor_share", "specific_share", "exposure_r2"} <= set(r.attribution)
    assert 0.0 <= r.attribution["factor_share"] <= 1.0
    assert r.baseline_ic is None or r.baseline_ic == r.rolling_ic
    assert -1.0 <= r.icir <= 1e9  # ICIR may exceed 1 for near-deterministic IC
    assert not np.isnan(r.long_short_spread)
    assert 0.0 <= r.turnover_top_quintile <= 1.0
    assert r.cycle == 1


def test_baseline_ic_learned() -> None:
    lc = FactorLifecycle(rolling_window=10, max_lag=10)
    factor_df, ret_df = _panel(decayed=True)
    r1 = lc.cycle(factor_df, ret_df)
    assert lc.baseline_ic == r1.rolling_ic
    lc.cycle(factor_df, ret_df)
    assert lc.baseline_ic == r1.rolling_ic  # not overwritten


def test_explicit_baseline_wins() -> None:
    lc = FactorLifecycle(baseline_ic=0.5, rolling_window=10, max_lag=10)
    factor_df, ret_df = _panel(decayed=True)
    r = lc.cycle(factor_df, ret_df)
    assert r.baseline_ic == 0.5


def _short_horizon_panel(seed: int = 7) -> tuple[pd.DataFrame, pd.DataFrame]:
    """White-noise factor that predicts its ALIGNED forward return strongly
    (ret_t = 0.5 * fac_t + noise) but has no multi-bar persistence — a
    healthy short-horizon factor. Its lag profile decays instantly, which the
    old cross-lag criterion (half-life / is_decayed) misread as decay.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=N_DATES)
    Fs, Rs = [], []
    for _ in range(N_SYMBOLS):
        fac = rng.normal(0.0, 1.0, N_DATES)
        ret = 0.5 * fac + 0.3 * rng.normal(0.0, 1.0, N_DATES)
        Fs.append(fac)
        Rs.append(ret)
    factor_df = pd.DataFrame(
        {
            "trade_date": np.repeat(dates, N_SYMBOLS),
            "symbol": np.tile([f"S{i}" for i in range(N_SYMBOLS)], N_DATES),
            "factor": np.array(Fs).T.ravel(),
            "size": rng.lognormal(0.0, 1.0, N_DATES * N_SYMBOLS),
        }
    )
    ret_df = pd.DataFrame(
        {
            "trade_date": np.repeat(dates, N_SYMBOLS),
            "symbol": np.tile([f"S{i}" for i in range(N_SYMBOLS)], N_DATES),
            "fut_ret_1": np.array(Rs).T.ravel(),
        }
    )
    return factor_df, ret_df


def test_short_horizon_factor_not_decayed_by_lag_profile() -> None:
    """A factor that predicts only the next bar must NOT be flagged by the
    cross-lag decay profile (half-life / is_decayed); the verdict depends on
    the temporal IC level vs baseline only."""
    factor_df, ret_df = _short_horizon_panel(seed=7)
    lc = FactorLifecycle(rolling_window=10, max_lag=10)
    r1 = lc.cycle(factor_df, ret_df)
    # Cycle 2 on identical data: same recent IC, so no temporal shrink —
    # healthy even though the lag profile decays to ~0 within a few lags.
    r2 = lc.cycle(factor_df, ret_df)
    assert r1.rolling_ic == pytest.approx(r2.rolling_ic)
    assert r2.decision == "healthy"
    assert r2.state_after == FactorState.ACTIVE
    assert r2.reason == "recent IC stable vs baseline"


def test_ic_collapse_triggers_decay() -> None:
    """Genuinely weaker IC on cycle 2 must trip the temporal decay criterion."""
    good, good_ret = _panel(seed=8, decayed=False)
    bad, bad_ret = _panel(seed=8, decayed=True)
    lc = FactorLifecycle(rolling_window=10, max_lag=10)
    r1 = lc.cycle(good, good_ret)
    assert r1.decision == "healthy"
    assert lc.baseline_ic is not None and lc.baseline_ic > 0.2
    r2 = lc.cycle(bad, bad_ret)
    assert r2.decision == "decay"
    assert r2.state_after == FactorState.WATCH
    assert "baseline" in r2.reason


# --------------------------------------------------------------------------
# Escalation / recovery / terminal
# --------------------------------------------------------------------------
def test_escalation_active_to_retired() -> None:
    lc = FactorLifecycle(rolling_window=10, max_lag=10)
    factor_df, ret_df = _panel(decayed=True)
    lc.cycle(factor_df, ret_df)  # ACTIVE -> WATCH
    lc.cycle(factor_df, ret_df)  # WATCH -> DECAYING
    lc.cycle(factor_df, ret_df)  # DECAYING -> RETIRED
    assert lc.state == FactorState.RETIRED
    assert [(b, a) for _, b, a, _ in lc.history] == [
        (FactorState.ACTIVE, FactorState.WATCH),
        (FactorState.WATCH, FactorState.DECAYING),
        (FactorState.DECAYING, FactorState.RETIRED),
    ]


def test_recovery_from_watch() -> None:
    lc = FactorLifecycle(rolling_window=10, max_lag=10)
    bad, bad_ret = _panel(seed=1, decayed=True)
    good, good_ret = _panel(seed=1, decayed=False)
    lc.cycle(bad, bad_ret)  # -> WATCH
    assert lc.state == FactorState.WATCH
    lc.cycle(good, good_ret)  # -> ACTIVE
    assert lc.state == FactorState.ACTIVE
    assert lc.history[-1][2] == FactorState.ACTIVE


def test_retired_is_terminal() -> None:
    lc = FactorLifecycle(rolling_window=10, max_lag=10)
    bad, bad_ret = _panel(seed=2, decayed=True)
    good, good_ret2 = _panel(seed=2, decayed=False)
    for _ in range(3):
        lc.cycle(bad, bad_ret)
    assert lc.state == FactorState.RETIRED
    lc.cycle(good, good_ret2)
    assert lc.state == FactorState.RETIRED


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------
def test_on_alert_fires_only_on_degrading_transition() -> None:
    alerts: list[tuple[str, str]] = []
    lc = FactorLifecycle(
        rolling_window=10,
        max_lag=10,
        on_alert=lambda level, msg, report: alerts.append((level, msg)),
    )
    good, good_ret = _panel(seed=3, decayed=False)
    bad, bad_ret = _panel(seed=3, decayed=True)
    lc.cycle(good, good_ret)  # healthy -> no alert
    assert alerts == []
    lc.cycle(bad, bad_ret)  # -> WATCH -> warning
    assert len(alerts) == 1
    assert alerts[0][0] == "warning"
    assert "WATCH" in alerts[0][1]
    lc.cycle(bad, bad_ret)  # -> DECAYING -> critical
    assert alerts[-1][0] == "critical"


# --------------------------------------------------------------------------
# Full-chain knobs
# --------------------------------------------------------------------------
def test_preprocessing_pipeline_applied() -> None:
    lc_raw = FactorLifecycle(rolling_window=10, max_lag=10)
    lc_clean = FactorLifecycle(
        preprocessing_config=[WIN_STEP], rolling_window=10, max_lag=10
    )
    factor_df, ret_df = _panel(seed=4, decayed=False)
    raw = lc_raw.cycle(factor_df, ret_df)
    clean = lc_clean.cycle(factor_df, ret_df)
    assert clean.trace_hash != raw.trace_hash  # pipeline changed the factor


def test_attribution_with_price_factors() -> None:
    lc = FactorLifecycle(rolling_window=10, max_lag=10)
    factor_df, ret_df = _panel(seed=5, decayed=False)
    dates = factor_df["trade_date"].unique()
    rng = np.random.default_rng(5)
    price = pd.DataFrame(
        {
            "BTC": 100 * np.cumprod(1 + rng.normal(0, 0.01, len(dates))),
            "ETH": 100 * np.cumprod(1 + rng.normal(0, 0.01, len(dates))),
        },
        index=dates,
    )
    r = lc.cycle(factor_df, ret_df, price_df=price)
    assert 0.0 <= r.attribution["factor_share"] <= 1.0
    assert r.attribution["exposure_r2"] >= 0.0


def test_attribution_with_explicit_factor_returns() -> None:
    lc = FactorLifecycle(rolling_window=10, max_lag=10)
    factor_df, ret_df = _panel(seed=6, decayed=False)
    dates = factor_df["trade_date"].unique()
    rng = np.random.default_rng(6)
    fr = pd.DataFrame({"f0": rng.normal(0, 0.01, len(dates))}, index=dates)
    r = lc.cycle(factor_df, ret_df, factor_returns=fr)
    assert {"factor_share", "specific_share", "exposure_r2"} <= set(r.attribution)


def test_port_module_imports_no_torch_or_polars() -> None:
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root not in ("torch", "polars"), f"banned import {alias.name}"
