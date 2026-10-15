"""Tests for portfolio_rebal — periodic rebalance engine and benchmark metrics.

All fixtures are synthetic small panels; no network, no real data files.
Hand-computed expected values are derived inline where the arithmetic is
part of the assertion.
"""

import numpy as np
import pandas as pd
import pytest
from collections.abc import Callable

from selene_space_ashare.composite import build_composite_panel
from selene_space_ashare.factor_eval import calc_factor_ic_series
from selene_space_ashare.factors import ALL_FACTORS
from selene_space_ashare.portfolio_rebal import (
    align_benchmark_windows,
    backtest_periodic_portfolio,
    calc_benchmark_metrics,
    detect_panel_factors,
    run_param_lab,
    run_portfolio_backtest,
)


def test_periodic_returns_hand_example() -> None:
    """2 windows x 3 stocks: window return = mean fwd_2d of TopN at rebalance date."""
    dates = pd.bdate_range("2025-01-06", periods=6)
    rows = [
        {"date": dates[0], "symbol": "A", "score": 3.0, "fwd_2d": 0.10},
        {"date": dates[0], "symbol": "B", "score": 2.0, "fwd_2d": 0.04},
        {"date": dates[0], "symbol": "C", "score": 1.0, "fwd_2d": -0.02},
        {"date": dates[2], "symbol": "A", "score": 2.0, "fwd_2d": 0.02},
        {"date": dates[2], "symbol": "B", "score": 1.0, "fwd_2d": 0.00},
        {"date": dates[2], "symbol": "C", "score": 3.0, "fwd_2d": 0.08},
    ]
    # Padding rows keep the 6-date grid; NaN score/fwd keeps them invalid.
    for d in (dates[1], dates[3], dates[4], dates[5]):
        for s in "ABC":
            rows.append({"date": d, "symbol": s, "score": np.nan, "fwd_2d": np.nan})
    panel = pd.DataFrame(rows)

    result = backtest_periodic_portfolio(
        panel,
        score_col="score",
        n_long=2,
        rebalance_days=2,
        forward_col="fwd_2d",
    )

    # Grid [d0, d2, d4]; last grid date has no next rebalance date -> 2 windows.
    assert len(result) == 2

    first = result.iloc[0]
    assert first["rebalance_date"] == dates[0]
    assert first["end_date"] == dates[2]
    # Window 1 holds {A, B}: (0.10 + 0.04) / 2.
    assert first["period_return"] == pytest.approx(0.07)
    assert first["turnover"] == pytest.approx(0.0)
    assert first["n_held"] == 2

    second = result.iloc[1]
    assert second["rebalance_date"] == dates[2]
    assert second["end_date"] == dates[4]
    # Window 2 holds {C, A}: (0.08 + 0.02) / 2.
    assert second["period_return"] == pytest.approx(0.05)
    # {A, B} -> {C, A}: 1 added + 1 removed = 2 changed / n_long 2.
    assert second["turnover"] == pytest.approx(1.0)
    assert second["n_held"] == 2


def test_periodic_turnover_cost_once() -> None:
    """Second window swaps 2 of 10 names -> turnover 0.4, cost charged exactly once."""
    dates = pd.bdate_range("2025-01-06", periods=3)
    symbols = [f"S{i:02d}" for i in range(12)]
    score_d0 = {f"S{i:02d}": float(9 - i) for i in range(10)}
    score_d1 = {
        "S10": 11.0,
        "S11": 10.0,
        "S02": 9.0,
        "S03": 8.0,
        "S04": 7.0,
        "S05": 6.0,
        "S06": 5.0,
        "S07": 4.0,
        "S08": 3.0,
        "S09": 2.0,
        "S01": 1.0,
        "S00": 0.0,
    }
    rows = []
    for s in symbols:
        rows.append(
            {
                "date": dates[0],
                "symbol": s,
                "score": score_d0.get(s, np.nan),
                "fwd_1d": 0.01 if s in score_d0 else np.nan,
            }
        )
    for s in symbols:
        rows.append(
            {
                "date": dates[1],
                "symbol": s,
                "score": score_d1[s],
                "fwd_1d": 0.02 if s not in ("S00", "S01") else 0.0,
            }
        )
    for s in symbols:
        rows.append({"date": dates[2], "symbol": s, "score": np.nan, "fwd_1d": np.nan})
    panel = pd.DataFrame(rows)

    result = backtest_periodic_portfolio(
        panel,
        score_col="score",
        n_long=10,
        rebalance_days=1,
        forward_col="fwd_1d",
        cost_bps=50.0,
    )

    assert len(result) == 2

    # First window: prev is None -> zero turnover, zero cost.
    first = result.iloc[0]
    assert first["turnover"] == pytest.approx(0.0)
    assert first["period_return"] == pytest.approx(0.01)

    # Second window: {S00..S09} -> {S02..S11} = 2 added + 2 removed -> 0.4.
    second = result.iloc[1]
    assert second["turnover"] == pytest.approx(0.4)
    # gross 0.02 - 50/1e4 * 0.4 = 0.02 - 0.002, charged once.
    assert second["period_return"] == pytest.approx(0.018)


def test_periodic_rebalance_dates_grid() -> None:
    """Rebalance dates are exactly sorted unique panel dates sliced [::N]."""
    dates = pd.bdate_range("2025-01-06", periods=7)
    symbols = [f"{600000 + i}" for i in range(5)]
    rows = [
        {
            "date": d,
            "symbol": s,
            "score": float(i + j),
            "fwd_2d": 0.01,
        }
        for j, d in enumerate(dates)
        for i, s in enumerate(symbols)
    ]
    panel = pd.DataFrame(rows)

    result = backtest_periodic_portfolio(
        panel,
        score_col="score",
        n_long=3,
        rebalance_days=2,
        forward_col="fwd_2d",
    )

    grid = dates[::2]
    assert list(result["rebalance_date"]) == list(grid[:-1])
    assert list(result["end_date"]) == list(grid[1:])
    assert dates[-1] not in set(result["rebalance_date"])
    assert (result["n_held"] == 3).all()


def test_periodic_skips_insufficient_cross_section() -> None:
    """A window with < n_long valid rows is skipped; prev held carries over."""
    dates = pd.bdate_range("2025-01-06", periods=5)

    def full_rows(d, scores, fwd):
        return [
            {"date": d, "symbol": s, "score": sc, "fwd_1d": fwd}
            for s, sc in scores.items()
        ]

    rows = full_rows(
        dates[0],
        {"A": 5.0, "B": 4.0, "C": 3.0, "D": 2.0, "E": 1.0},
        0.01,
    )
    # Only 2 valid rows at d1 -> window skipped.
    rows += [
        {"date": dates[1], "symbol": "A", "score": 5.0, "fwd_1d": 0.0},
        {"date": dates[1], "symbol": "B", "score": 4.0, "fwd_1d": 0.0},
        {"date": dates[1], "symbol": "C", "score": np.nan, "fwd_1d": 0.0},
        {"date": dates[1], "symbol": "D", "score": np.nan, "fwd_1d": 0.0},
        {"date": dates[1], "symbol": "E", "score": np.nan, "fwd_1d": 0.0},
    ]
    rows += full_rows(
        dates[2],
        {"A": 5.0, "B": 4.0, "D": 3.5, "C": 2.0, "E": 1.0},
        0.02,
    )
    rows += full_rows(
        dates[3],
        {"A": 5.0, "B": 4.0, "D": 3.5, "C": 2.0, "E": 1.0},
        0.03,
    )
    rows += full_rows(dates[4], dict.fromkeys("ABCDE"), np.nan)
    panel = pd.DataFrame(rows)

    result = backtest_periodic_portfolio(
        panel,
        score_col="score",
        n_long=3,
        rebalance_days=1,
        forward_col="fwd_1d",
    )

    assert len(result) == 3
    assert list(result["rebalance_date"]) == [dates[0], dates[2], dates[3]]

    # d0 holds {A, B, C}; d1 skipped; d2 holds {A, B, D} -> turnover vs the
    # carried-over d0 set is (1 added + 1 removed) / 3.
    assert list(result["turnover"].round(6)) == pytest.approx(
        [0.0, round(2 / 3, 6), 0.0]
    )
    assert list(result["period_return"].round(6)) == pytest.approx([0.01, 0.02, 0.03])
    assert (result["n_held"] == 3).all()


def test_walkforward_score_invariant_to_future_rows() -> None:
    """Anti-look-ahead: perturbing all rows after date t must not move t's score."""
    names = ["MOMENTUM_1M", "RSI_14D", "VOLUME_ZSCORE"]
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2025-01-06", periods=45)
    symbols = [f"S{i:02d}" for i in range(12)]

    rows = []
    for d in dates:
        mom = rng.normal(size=12)
        rsi = rng.normal(size=12)
        vol = rng.normal(size=12)
        fwd = 0.02 * mom - 0.01 * rsi + rng.normal(scale=0.01, size=12)
        for j, s in enumerate(symbols):
            rows.append(
                {
                    "date": d,
                    "symbol": s,
                    "MOMENTUM_1M": mom[j],
                    "RSI_14D": rsi[j],
                    "VOLUME_ZSCORE": vol[j],
                    "fwd_5d": fwd[j],
                }
            )
    panel = pd.DataFrame(rows)

    t_pos = 30
    horizon = 5  # fwd_5d
    min_obs = 12

    # Precondition: at t the walk-forward path has >= min_obs valid IC days
    # before its cutoff (cutoff = dates[t_pos - horizon]), so it takes the
    # real IR-weight branch, not the min_obs equal-weight fallback.
    for name in names:
        ic = calc_factor_ic_series(panel, name, "fwd_5d").sort_index()
        hist = ic.iloc[: t_pos - horizon]
        assert len(hist) == t_pos - horizon
        assert int(hist.notna().sum()) >= min_obs

    t = dates[t_pos]
    result, _ = build_composite_panel(
        panel, names, "fwd_5d", weight_method="ir_walkforward"
    )

    tampered = panel.copy()
    future = tampered["date"] > t
    assert int(future.sum()) > 0
    # Reversing the cross-section ordering (not uniform scaling — a uniform
    # x100 leaves z-scores and ranks unchanged, which would be a vacuous
    # perturbation) must move future scores.
    for name in names:
        for d in tampered.loc[future, "date"].unique():
            at_d = future & (tampered["date"] == d)
            vals = tampered.loc[at_d, name].to_numpy()
            tampered.loc[at_d, name] = vals[::-1].copy()
    result_tampered, _ = build_composite_panel(
        tampered, names, "fwd_5d", weight_method="ir_walkforward"
    )

    at_t = result["date"] == t
    at_t_tampered = result_tampered["date"] == t
    assert np.allclose(
        result.loc[at_t, "composite_score"].to_numpy(),
        result_tampered.loc[at_t_tampered, "composite_score"].to_numpy(),
    )

    # Sanity: the perturbation did move future-date scores (not a vacuous compare).
    late = result["date"] == dates[40]
    late_tampered = result_tampered["date"] == dates[40]
    assert not np.allclose(
        result.loc[late, "composite_score"].to_numpy(),
        result_tampered.loc[late_tampered, "composite_score"].to_numpy(),
    )


def test_periodic_uses_only_rebalance_date_rows() -> None:
    """Injected date-offset scores pin each window's holdings to that date's cross-section."""
    dates = pd.bdate_range("2025-01-06", periods=4)
    symbols = [f"{600000 + i}" for i in range(5)]

    def cross_rows(d, scores, fwds):
        return [
            {"date": d, "symbol": s, "score": scores.get(s, np.nan), "fwd_1d": fwds[s]}
            for s in symbols
        ]

    rows = cross_rows(
        dates[0],
        {s: float(i) for i, s in enumerate(symbols)},
        {s: 0.01 * (i + 1) for i, s in enumerate(symbols)},
    )
    # d1: last symbol missing (NaN score) -> holdings must come from d1's rows.
    rows += cross_rows(
        dates[1],
        {s: float(i) + 1000.0 for i, s in enumerate(symbols[:-1])},
        {s: 0.001 + 0.01 * (i + 1) for i, s in enumerate(symbols[:-1])}
        | {symbols[-1]: np.nan},
    )
    rows += cross_rows(
        dates[2],
        {s: float(i) + 2000.0 for i, s in enumerate(symbols)},
        {s: 0.002 + 0.01 * (i + 1) for i, s in enumerate(symbols)},
    )
    rows += cross_rows(
        dates[3],
        {s: np.nan for s in symbols},
        {s: np.nan for s in symbols},
    )
    panel = pd.DataFrame(rows)

    result = backtest_periodic_portfolio(
        panel,
        score_col="score",
        n_long=2,
        rebalance_days=1,
        forward_col="fwd_1d",
    )

    assert len(result) == 3
    assert (result["n_held"] == 2).all()

    # Window returns are the mean fwd of exactly the top-2 rows of that
    # date's cross-section. Any row leakage across dates breaks these values.
    assert list(result["period_return"].round(6)) == pytest.approx(
        [0.045, 0.036, 0.047]
    )


def test_benchmark_metrics_hand_example() -> None:
    """p=[.02,.03,-.01,.04], b=.01 each -> excess [.01,.02,-.02,.03]."""
    portfolio = pd.Series([0.02, 0.03, -0.01, 0.04])
    benchmark = pd.Series([0.01, 0.01, 0.01, 0.01])

    metrics = calc_benchmark_metrics(portfolio, benchmark)

    # std(ddof=1): squared deviations 0 + 1e-4 + 9e-4 + 4e-4 = 1.4e-3 over 3.
    assert metrics["te"] == pytest.approx(np.sqrt(0.0014 / 3 * 252))
    assert metrics["ir"] == pytest.approx(0.01 * np.sqrt(252 * 3 / 0.0014))

    expected_cum = 1.01 * 1.02 * 0.98 * 1.03 - 1
    assert metrics["excess_cumulative"] == pytest.approx(expected_cum)
    assert metrics["excess_annual"] == pytest.approx((1 + expected_cum) ** 63 - 1)

    # Curve 1.01, 1.0302, 1.009596, 1.039884: third point sits 2% below cummax.
    assert metrics["excess_max_drawdown"] == pytest.approx(-0.02)
    assert metrics["excess_win_rate"] == pytest.approx(0.75)


def test_benchmark_metrics_constant_benchmark() -> None:
    """Constant zero benchmark with flat excess: TE=0, IR nan (no raise)."""
    portfolio = pd.Series([0.01, 0.01, 0.01, 0.01])
    benchmark = pd.Series([0.0, 0.0, 0.0, 0.0])

    metrics = calc_benchmark_metrics(portfolio, benchmark)

    assert metrics["te"] == pytest.approx(0.0)
    assert np.isnan(metrics["ir"])
    assert metrics["excess_cumulative"] == pytest.approx(1.01**4 - 1)
    assert metrics["excess_win_rate"] == pytest.approx(1.0)


def test_excess_mdd_hand_example() -> None:
    """Excess [0.1, -0.2, 0.05] -> curve 1.1, 0.88, 0.924 -> MDD = 0.88/1.1 - 1."""
    portfolio = pd.Series([0.10, 0.00, 0.05])
    benchmark = pd.Series([0.00, 0.20, 0.00])

    metrics = calc_benchmark_metrics(portfolio, benchmark)

    assert metrics["excess_max_drawdown"] == pytest.approx(-0.2)
    assert metrics["excess_cumulative"] == pytest.approx(0.924 - 1)


def test_excess_win_rate() -> None:
    """3 positive, 1 negative, 1 zero -> win rate 3/5 (zero does not count)."""
    portfolio = pd.Series([0.01, 0.02, -0.01, 0.005, 0.0])
    benchmark = pd.Series([0.0, 0.0, 0.0, 0.0, 0.0])

    metrics = calc_benchmark_metrics(portfolio, benchmark)

    assert metrics["excess_win_rate"] == pytest.approx(3 / 5)


def test_align_windows_drops_missing_index_dates() -> None:
    """Windows with either endpoint missing from the index calendar are dropped."""
    dates = pd.bdate_range("2025-01-06", periods=9)
    windows = pd.DataFrame(
        {
            "rebalance_date": [dates[0], dates[2], dates[4], dates[6]],
            "end_date": [dates[2], dates[4], dates[6], dates[8]],
        }
    )
    # Index calendar lacks dates[4] (01-10): kills w1 (its end) and w2 (its
    # rebalance date). Dates use the "%Y%m%d" str form get_index_daily emits.
    index_daily = pd.DataFrame(
        {
            "date": ["20250106", "20250108", "20250114", "20250116"],
            "close": [100.0, 102.0, 110.0, 112.0],
        }
    )

    result = align_benchmark_windows(index_daily, windows)

    assert result.name == "benchmark_return"
    assert result.attrs["dropped"] == 2
    assert list(result.index) == [0, 3]
    assert result.iloc[0] == pytest.approx(102.0 / 100.0 - 1)
    assert result.iloc[1] == pytest.approx(112.0 / 110.0 - 1)


def _synthetic_runner_panel(n_dates: int = 40) -> pd.DataFrame:
    """Small panel with 3 real factors + decoy non-factor columns."""
    rng = np.random.default_rng(11)
    dates = pd.bdate_range("2025-01-06", periods=n_dates)
    symbols = [f"S{i:02d}" for i in range(25)]
    rows = []
    for d in dates:
        mom = rng.normal(size=len(symbols))
        vol = rng.normal(size=len(symbols))
        rz = rng.normal(size=len(symbols))
        fwd = 0.02 * mom - 0.01 * rz + rng.normal(scale=0.01, size=len(symbols))
        for j, s in enumerate(symbols):
            rows.append(
                {
                    "date": d,
                    "symbol": s,
                    "MOMENTUM_1M": mom[j],
                    "VOLATILITY_20D": vol[j],
                    "VOLUME_ZSCORE": rz[j],
                    "close": 10.0 + float(j),
                    "volume": 1000.0 + float(j),
                    "industry": "tech",
                    "fwd_5d": fwd[j],
                }
            )
    return pd.DataFrame(rows)


def test_runner_injected_fetcher_no_network() -> None:
    """End-to-end run with a synthetic index_fetcher; zero network."""
    panel = _synthetic_runner_panel()
    dates = sorted(panel["date"].unique())

    def fake_fetcher(symbol: str) -> pd.DataFrame:
        assert symbol == "000905"
        return pd.DataFrame(
            {
                "date": [d.strftime("%Y%m%d") for d in dates],
                "close": np.linspace(100.0, 120.0, len(dates)),
            }
        )

    result = run_portfolio_backtest(
        panel,
        benchmark_index="000905",
        index_fetcher=fake_fetcher,
        n_long=5,
        rebalance_days=5,
        cost_bps=15.0,
    )

    assert set(result) == {
        "portfolio_metrics",
        "benchmark_metrics",
        "excess_metrics",
        "windows",
        "config",
    }
    assert isinstance(result["windows"], pd.DataFrame)
    assert len(result["windows"]) > 0
    cfg = result["config"]
    assert cfg["n_long"] == 5
    assert cfg["rebalance_days"] == 5
    assert cfg["cost_bps"] == 15.0
    assert cfg["forward_col"] == "fwd_5d"
    assert cfg["benchmark_index"] == "000905"
    assert cfg["n_factors_detected"] == 3
    # Non-nan cumulative return proves each metrics section actually computed.
    assert not np.isnan(result["portfolio_metrics"]["cumulative_return"])
    assert not np.isnan(result["benchmark_metrics"]["cumulative_return"])
    assert not np.isnan(result["excess_metrics"]["excess_cumulative"])
    # All three sections share the excess-metric contract keys.
    assert {"te", "ir", "excess_cumulative"} <= set(result["excess_metrics"])


def test_runner_default_factors_whitelist() -> None:
    """factor_names=None detects only ALL_FACTORS members; decoys stay out."""
    panel = _synthetic_runner_panel()

    detected = detect_panel_factors(panel)

    assert set(detected) <= set(ALL_FACTORS)
    assert set(detected) <= set(panel.columns)
    assert "close" not in detected
    assert "volume" not in detected
    assert "industry" not in detected
    assert "fwd_5d" not in detected

    result = run_portfolio_backtest(
        panel,
        factor_names=None,
        index_fetcher=lambda _symbol: pd.DataFrame(
            {
                "date": [d.strftime("%Y%m%d") for d in sorted(panel["date"].unique())],
                "close": 100.0,
            }
        ),
    )
    assert set(result["config"]["factor_names"]) == set(detected)


def test_runner_empty_whitelist_raises() -> None:
    """A panel with no ALL_FACTORS column raises instead of a silent empty run."""
    dates = pd.bdate_range("2025-01-06", periods=10)
    rows = [
        {
            "date": d,
            "symbol": s,
            "close": 10.0,
            "fwd_5d": 0.01,
        }
        for d in dates
        for s in ("A", "B", "C")
    ]
    panel = pd.DataFrame(rows)

    with pytest.raises(ValueError, match="no ALL_FACTORS columns"):
        run_portfolio_backtest(panel, index_fetcher=lambda _s: pd.DataFrame())


def test_walkforward_score_survives_partial_factor_coverage() -> None:
    """Regression: a factor that is NaN on early dates must not poison rows.

    Zero-weight factors are skipped in scoring; a nonzero-weight factor
    with NaN values on a date must only drop that factor's contribution,
    not make the whole composite_score NaN (IEEE NaN propagation through
    w * rank). Before the w == 0.0 guard, MOMENTUM_12M-style sparse
    columns (never enough ICs -> weight 0) zeroed every row score.
    """
    rng = np.random.default_rng(23)
    dates = pd.bdate_range("2025-01-06", periods=45)
    symbols = [f"S{i:02d}" for i in range(12)]
    rows = []
    for k, d in enumerate(dates):
        mom = rng.normal(size=len(symbols))
        rz = rng.normal(size=len(symbols))
        # MOMENTUM_12M exists only on the second half of the panel —
        # its early ICs are all NaN, so its walk-forward weight is 0
        # (below min_obs) while other factors earn real IR weights.
        mom12 = (
            rng.normal(size=len(symbols)) if k >= 20 else np.full(len(symbols), np.nan)
        )
        fwd = 0.02 * mom + rng.normal(scale=0.01, size=len(symbols))
        for j, s in enumerate(symbols):
            rows.append(
                {
                    "date": d,
                    "symbol": s,
                    "MOMENTUM_1M": mom[j],
                    "VOLUME_ZSCORE": rz[j],
                    "MOMENTUM_12M": mom12[j],
                    "fwd_5d": fwd[j],
                }
            )
    panel = pd.DataFrame(rows)

    result, _ = build_composite_panel(
        panel,
        ["MOMENTUM_1M", "VOLUME_ZSCORE", "MOMENTUM_12M"],
        "fwd_5d",
        weight_method="ir_walkforward",
    )

    early = result[result["date"] < dates[20]]
    assert early["composite_score"].notna().all()
    assert (early["composite_score"] != 0.0).any()
    late = result[result["date"] >= dates[20]]
    assert late["composite_score"].notna().all()


import scipy.optimize
from scipy.stats import spearmanr

from selene_space_ashare.portfolio_rebal import (
    make_inverse_vol_weight_fn,
    make_slsqp_weight_fn,
    optimize_weights,
    shrunk_cov,
)


def test_shrunk_cov_delta_zero_matches_manual() -> None:
    """delta=0 reduces to the annualized sample covariance, hand-locked.

    B = 2*A elementwise, so cov(A,B) = 2*var(A) and var(B) = 4*var(A)
    with ddof=1; the module annualizes by x250.
    """
    rets = pd.DataFrame({"A": [0.01, 0.02, 0.03, 0.04], "B": [0.02, 0.04, 0.06, 0.08]})
    var_a = 0.0005 / 3
    expected = np.array(
        [
            [var_a * 250.0, 2.0 * var_a * 250.0],
            [2.0 * var_a * 250.0, 4.0 * var_a * 250.0],
        ]
    )

    result = shrunk_cov(rets, delta=0.0)

    assert np.allclose(result, expected, atol=1e-12)


def test_shrunk_cov_delta_one_is_diagonal() -> None:
    """delta=1 keeps only the diagonal; off-diagonal is exactly zero."""
    rets = pd.DataFrame({"A": [0.01, 0.02, 0.03, 0.04], "B": [0.02, 0.04, 0.06, 0.08]})

    full = shrunk_cov(rets, delta=0.0)
    diag = shrunk_cov(rets, delta=1.0)

    assert diag[0, 1] == 0.0
    assert diag[1, 0] == 0.0
    assert diag[0, 0] == pytest.approx(full[0, 0])
    assert diag[1, 1] == pytest.approx(full[1, 1])


def test_shrunk_cov_empty_frame_returns_empty() -> None:
    """No columns (empty frame or zero columns) yields a (0, 0) array."""
    assert shrunk_cov(pd.DataFrame()).shape == (0, 0)
    assert shrunk_cov(pd.DataFrame(index=[0, 1, 2])).shape == (0, 0)


def test_optimize_weights_greedy_fill_at_zero_lambda() -> None:
    """Zero covariance + ~zero lambda = linear program: greedy vertex fill."""
    scores = pd.Series([5.0, 4.0, 3.0, 2.0, 1.0])
    cov = np.zeros((5, 5))

    w = optimize_weights(scores, cov, max_w=0.5, lam=1e-9)

    assert np.allclose(w, [0.5, 0.5, 0.0, 0.0, 0.0], atol=1e-4)


def test_optimize_weights_high_lambda_tilts_to_lowvar() -> None:
    """Huge lambda converges toward min-variance (not equal weight).

    Stock 0 has the high score but 10x the variance of stock 1. At
    lambda~0 the weights follow the score ordering; at lambda=1e5 the
    ordering flips to the low-variance name, so the rank correlation
    with the score drops from +1 to -1. (1e6 itself stalls SLSQP at the
    equal-weight start with success=True — an extreme-gradient artifact
    of the solver, present in the legacy port too — so 1e5 is the
    largest lambda this assertion can meaningfully probe.)
    """
    cov = np.array([[1.0, 0.0], [0.0, 0.1]])
    scores = pd.Series([1.0, 0.5])

    w_score = optimize_weights(scores, cov, max_w=1.0, lam=1e-9)
    w_risk = optimize_weights(scores, cov, max_w=1.0, lam=1e5)

    assert w_score[0] > w_score[1]
    assert w_risk[1] > w_risk[0]
    assert w_risk[1] > 0.5

    rho_score = float(spearmanr(w_score, scores).statistic)
    rho_risk = float(spearmanr(w_risk, scores).statistic)
    assert rho_score > 0.0
    assert rho_risk < 0.0


def test_optimize_weights_solver_failure_falls_back_equal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both failure paths (raise, success=False) return equal weights."""
    scores = pd.Series([3.0, 2.0, 1.0])
    cov = np.eye(3)
    equal = np.full(3, 1.0 / 3)

    def raise_solver(*args: object, **kwargs: object) -> object:
        raise RuntimeError("solver exploded")

    monkeypatch.setattr(scipy.optimize, "minimize", raise_solver)
    assert np.allclose(optimize_weights(scores, cov), equal)

    class _FailedResult:
        success = False
        x = np.array([0.9, 0.05, 0.05])

    monkeypatch.setattr(
        scipy.optimize, "minimize", lambda *args, **kwargs: _FailedResult()
    )
    assert np.allclose(optimize_weights(scores, cov), equal)


def test_optimize_weights_respects_sum_and_bound() -> None:
    """Solution satisfies sum=1, 0 <= w <= max_w under a real objective."""
    rng = np.random.default_rng(3)
    scores = pd.Series(rng.uniform(0.5, 2.0, size=8))
    cov = np.diag(np.linspace(0.01, 0.08, 8))

    w = optimize_weights(scores, cov, max_w=0.25, lam=2.0)

    assert float(w.sum()) == pytest.approx(1.0, abs=1e-6)
    assert float(w.max()) <= 0.25 + 1e-9
    assert float(w.min()) >= -1e-9


def test_optimize_weights_feasibility_clamp_small_pool() -> None:
    """max_w below 1/n is clamped, leaving all-equal as the only feasible point."""
    scores = pd.Series([5.0, 4.0, 3.0, 2.0, 1.0])

    w = optimize_weights(scores, np.eye(5), max_w=0.06, lam=1e-9)

    assert np.allclose(w, [0.2] * 5, atol=1e-4)


def test_optimize_weights_cov_shape_mismatch_uses_zero_cov() -> None:
    """A cov whose shape disagrees with len(score) is replaced by zeros."""
    scores = pd.Series([3.0, 1.0])

    w = optimize_weights(scores, np.ones((3, 3)), max_w=1.0, lam=1e-9)

    assert np.allclose(w, [1.0, 0.0], atol=1e-4)


def test_optimize_weights_empty_score_returns_empty() -> None:
    """n=0 returns an empty array instead of touching the solver."""
    out = optimize_weights(pd.Series([], dtype=float), np.zeros((0, 0)))

    assert out.shape == (0,)


def test_slsqp_weight_fn_missing_close_raises() -> None:
    """A panel without a close column fails fast at closure construction."""
    panel = pd.DataFrame(
        {
            "date": [pd.Timestamp("2025-01-06")],
            "symbol": ["A"],
            "composite_score": [1.0],
        }
    )

    with pytest.raises(ValueError, match="close"):
        make_slsqp_weight_fn(panel)


def _slsqp_close_panel(
    symbols: tuple[str, ...],
    suspended: tuple[str, ...] = (),
    n_dates: int = 30,
    seed: int = 42,
) -> pd.DataFrame:
    """Synthetic panel: date/symbol/close/composite_score, score by list order.

    Symbols listed earlier carry higher scores; names in `suspended` get
    an all-NaN close (their rows exist but never contribute a return).
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2025-01-06", periods=n_dates)
    rows: list[dict[str, object]] = []
    for d in dates:
        for i, s in enumerate(symbols):
            close = (
                np.nan
                if s in suspended
                else 10.0 + 2.0 * i + float(rng.normal(scale=0.2))
            )
            rows.append(
                {
                    "date": d,
                    "symbol": s,
                    "close": close,
                    "composite_score": float(len(symbols) - i),
                }
            )
    return pd.DataFrame(rows)


def _held_at(panel: pd.DataFrame, t: pd.Timestamp) -> pd.DataFrame:
    """Engine-shaped held slice: rows at t in score-descending order."""
    return (
        panel[panel["date"] == t]
        .sort_values("composite_score", ascending=False)
        .reset_index(drop=True)
    )


def test_slsqp_weight_fn_thin_history_falls_back_equal() -> None:
    """Fewer than min_names valid columns in the window -> equal weight."""
    panel = _slsqp_close_panel(
        symbols=("A", "B", "C", "D", "E", "G"),
        suspended=("D", "E", "G"),
    )
    held = _held_at(panel, panel["date"].max())

    w = make_slsqp_weight_fn(panel)(held)

    assert np.allclose(w, np.full(len(held), 1.0 / len(held)))


def test_slsqp_weight_fn_partial_coverage_subset_semantics() -> None:
    """All-NaN held name gets weight 0; valid names split the full budget.

    Locks the subset semantics: optimization runs on the dropna'd column
    subset only (never on all-NaN columns, which nan_to_num would turn
    into free zero-risk assets), then scatters back over all held rows.
    Verified against a manual dropna -> optimize -> scatter replication.
    """
    panel = _slsqp_close_panel(
        symbols=("F", "A", "B", "C", "D", "E"),
        suspended=("F",),
    )
    t = panel["date"].max()
    held = _held_at(panel, t)
    assert held["symbol"].iloc[0] == "F"

    w = make_slsqp_weight_fn(panel)(held)

    assert w[0] == pytest.approx(0.0)
    assert float(np.sum(w[1:])) == pytest.approx(1.0)

    closes = panel[["date", "symbol", "close"]].copy()
    closes["date"] = pd.to_datetime(closes["date"])
    wide = closes.pivot_table(
        index="date", columns="symbol", values="close", aggfunc="last"
    ).sort_index()
    rets = wide.pct_change(fill_method=None)
    hist = rets.loc[rets.index <= t].tail(250).dropna(axis=1, how="all")
    valid = [str(s) for s in held["symbol"] if s in set(hist.columns)]
    scores = held.set_index("symbol")["composite_score"].reindex(valid)
    sub = optimize_weights(scores, shrunk_cov(hist[valid], 0.5))
    manual = np.zeros(len(held))
    held_syms = [str(s) for s in held["symbol"]]
    for k, name in enumerate(valid):
        manual[held_syms.index(name)] = sub[k]

    assert np.allclose(w, manual)


def _two_stock_engine_panel() -> pd.DataFrame:
    """3-date panel, 2 stocks, fixed fwd_1d values for hand-locked returns."""
    dates = pd.bdate_range("2025-01-06", periods=3)
    rows = [
        {"date": dates[0], "symbol": "A", "score": 2.0, "fwd_1d": 0.10},
        {"date": dates[0], "symbol": "B", "score": 1.0, "fwd_1d": 0.02},
        {"date": dates[1], "symbol": "A", "score": np.nan, "fwd_1d": np.nan},
        {"date": dates[1], "symbol": "B", "score": np.nan, "fwd_1d": np.nan},
        {"date": dates[2], "symbol": "A", "score": np.nan, "fwd_1d": np.nan},
        {"date": dates[2], "symbol": "B", "score": np.nan, "fwd_1d": np.nan},
    ]
    return pd.DataFrame(rows)


def test_engine_weight_fn_changes_window_return() -> None:
    """Fixed [0.75, 0.25] hook beats equal weight: 0.75*a + 0.25*b locked."""
    panel = _two_stock_engine_panel()

    result = backtest_periodic_portfolio(
        panel,
        score_col="score",
        n_long=2,
        rebalance_days=1,
        forward_col="fwd_1d",
        weight_fn=lambda held: np.array([0.75, 0.25]),
    )

    assert len(result) == 1
    # Held rows are score-descending: A first (fwd 0.10), B second (0.02).
    assert result.iloc[0]["period_return"] == pytest.approx(0.75 * 0.10 + 0.25 * 0.02)


def test_engine_weight_fn_invalid_output_falls_back_equal() -> None:
    """NaN / wrong-length / all-zero / raising hooks all degrade to equal."""
    panel = _two_stock_engine_panel()
    equal_expected = (0.10 + 0.02) / 2

    def run(fn: object) -> float:
        return float(
            backtest_periodic_portfolio(
                panel,
                score_col="score",
                n_long=2,
                rebalance_days=1,
                forward_col="fwd_1d",
                weight_fn=fn,  # type: ignore[arg-type]
            ).iloc[0]["period_return"]
        )

    assert run(lambda _h: np.array([np.nan, 0.5])) == pytest.approx(equal_expected)
    assert run(lambda _h: np.array([0.9, 0.05, 0.05])) == pytest.approx(equal_expected)
    assert run(lambda _h: np.array([0.0, 0.0])) == pytest.approx(equal_expected)
    assert run(lambda _h: 1 / 0) == pytest.approx(equal_expected)

    # A negative weight is not a dirty output: it is clipped to 0 and the
    # rest renormalized ([-0.5, 1.5] -> [0, 1]) — all weight on B.
    assert run(lambda _h: np.array([-0.5, 1.5])) == pytest.approx(0.02)


def test_engine_weight_fn_scatter_semantics() -> None:
    """A held name given weight 0 contributes nothing to the window return."""
    panel = _two_stock_engine_panel()

    result = backtest_periodic_portfolio(
        panel,
        score_col="score",
        n_long=2,
        rebalance_days=1,
        forward_col="fwd_1d",
        weight_fn=lambda held: np.array([1.0, 0.0]),
    )

    # All weight on A: B's fwd (0.02) never enters the return.
    assert result.iloc[0]["period_return"] == pytest.approx(0.10)


def test_slsqp_weight_fn_no_lookahead() -> None:
    """Tampering with closes from t+1 onward must not move t's weights.

    End-to-end through the engine: the weight_fn's decision at window t
    uses closes up to and including t only (t row of pct_change is
    realized at the t-close buy), so future close edits leave t-window
    weights unchanged. The t row itself must NOT be touched.
    """
    names = ("A", "B", "C", "D", "E", "F", "G")
    rng = np.random.default_rng(17)
    dates = pd.bdate_range("2025-01-06", periods=12)
    base_rows = []
    for d in dates:
        for i, s in enumerate(names):
            base_rows.append(
                {
                    "date": d,
                    "symbol": s,
                    "close": 10.0 + 2.0 * i + float(rng.normal(scale=0.3)),
                    "composite_score": float(len(names) - i),
                    "fwd_1d": 0.001 * (i + 1),
                }
            )
    panel = pd.DataFrame(base_rows)

    def run(p: pd.DataFrame) -> np.ndarray:
        captures: list[np.ndarray] = []
        fn = make_slsqp_weight_fn(p, score_col="composite_score")

        def capturing(held: pd.DataFrame) -> np.ndarray:
            w = fn(held)
            captures.append(w)
            return w

        backtest_periodic_portfolio(
            p,
            score_col="composite_score",
            n_long=7,
            rebalance_days=1,
            forward_col="fwd_1d",
            weight_fn=capturing,
        )
        assert len(captures) == 11  # 12 grid dates -> 11 windows
        return captures[0]

    w_base = run(panel)

    tampered = panel.copy()
    t0 = dates[0]
    future = tampered["date"] > t0
    assert int(future.sum()) > 0
    tampered.loc[future, "close"] = (
        tampered.loc[future, "close"].to_numpy() * 3.7 + 11.0
    )

    w_tampered = run(tampered)

    assert np.allclose(w_base, w_tampered)


# ---------------------------------------------------------------------------
# run_param_lab: parameter sensitivity grid (batch11 US-3)
# ---------------------------------------------------------------------------


def _param_lab_panel() -> pd.DataFrame:
    """Small panel with two forward horizons + close, for grid tests.

    fwd_5d is only defined on the first 15 dates (the tail is NaN, as a
    forward-looking column would be at the data edge); fwd_2d spans all
    20. Sufficient for the engine to settle windows in both horizons.
    """
    rng = np.random.default_rng(23)
    dates = pd.bdate_range("2025-01-06", periods=20)
    symbols = [f"S{i:02d}" for i in range(10)]
    rows = []
    for k, d in enumerate(dates):
        mom = rng.normal(size=len(symbols))
        for j, s in enumerate(symbols):
            rows.append(
                {
                    "date": d,
                    "symbol": s,
                    "MOMENTUM_1M": mom[j],
                    "close": 10.0 + float(j) + float(rng.normal(scale=0.2)),
                    "fwd_5d": float(0.01 * mom[j]) if k < 15 else np.nan,
                    "fwd_2d": float(0.005 * mom[j]) + float(rng.normal(scale=0.001)),
                }
            )
    return pd.DataFrame(rows)


def _param_lab_fetcher(dates: list[pd.Timestamp]) -> Callable[[str], pd.DataFrame]:
    def fake_fetcher(symbol: str) -> pd.DataFrame:
        assert symbol == "000905"
        return pd.DataFrame(
            {
                "date": [d.strftime("%Y%m%d") for d in dates],
                "close": np.linspace(100.0, 115.0, len(dates)),
            }
        )

    return fake_fetcher


def test_param_lab_grid_completeness() -> None:
    """Default-style grid: every (rebalance, n_long, stock_weight, method) row, unique."""
    panel = _param_lab_panel()
    dates = sorted(panel["date"].unique())

    result = run_param_lab(
        panel,
        factor_names=["MOMENTUM_1M"],
        benchmark_index="000905",
        index_fetcher=_param_lab_fetcher(dates),
        rebalances=(2, 5),
        n_longs=(3, 5, 8),
        stock_weights=("equal", "slsqp"),
        weight_methods=("equal",),
        cost_bps=0.0,
    )

    assert len(result) == 12
    keys = list(
        zip(
            result["rebalance"],
            result["n_long"],
            result["stock_weight"],
            result["weight_method"],
        )
    )
    assert len(set(keys)) == 12
    # Non-degenerate cells settled real windows.
    assert (result["n_windows"] > 0).any()


def test_param_lab_missing_forward_col_raises() -> None:
    """A rebalance with no matching fwd column is a ValueError naming both sides."""
    panel = _param_lab_panel()

    with pytest.raises(ValueError, match="fwd_10d"):
        run_param_lab(
            panel,
            rebalances=(10,),
            stock_weights=("equal",),
            index_fetcher=_param_lab_fetcher(sorted(panel["date"].unique())),
        )
    # The message also lists what IS available.
    with pytest.raises(ValueError, match="fwd_5d"):
        run_param_lab(
            panel,
            rebalances=(10,),
            stock_weights=("equal",),
            index_fetcher=_param_lab_fetcher(sorted(panel["date"].unique())),
        )


def test_param_lab_unknown_weight_method_raises() -> None:
    """An unknown weight_method is rejected up front (compute_ic_weights would
    silently produce zero weights for it)."""
    panel = _param_lab_panel()

    with pytest.raises(ValueError, match="momentum"):
        run_param_lab(
            panel,
            weight_methods=("momentum",),
            stock_weights=("equal",),
            rebalances=(2,),
            index_fetcher=_param_lab_fetcher(sorted(panel["date"].unique())),
        )


def test_param_lab_composite_built_once_per_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_composite_panel runs once per (fwd_col, weight_method) pair."""
    import selene_space_ashare.portfolio_rebal as pr_mod

    panel = _param_lab_panel()
    dates = sorted(panel["date"].unique())

    calls = {"n": 0}
    real_build = pr_mod.build_composite_panel

    def counting_build(*args: object, **kwargs: object) -> object:
        calls["n"] += 1
        return real_build(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pr_mod, "build_composite_panel", counting_build)

    run_param_lab(
        panel,
        factor_names=["MOMENTUM_1M"],
        benchmark_index="000905",
        index_fetcher=_param_lab_fetcher(dates),
        rebalances=(2, 5),
        n_longs=(3, 5),
        stock_weights=("equal", "slsqp"),
        weight_methods=("equal",),
        cost_bps=0.0,
    )

    # 2 rebalances x 1 method = 2 builds, reused across all 8 cells.
    assert calls["n"] == 2


def test_param_lab_row_schema_locked() -> None:
    """Output columns are exactly the 14 pinned ones, in order."""
    panel = _param_lab_panel()
    dates = sorted(panel["date"].unique())

    result = run_param_lab(
        panel,
        factor_names=["MOMENTUM_1M"],
        benchmark_index="000905",
        index_fetcher=_param_lab_fetcher(dates),
        rebalances=(2,),
        n_longs=(5,),
        stock_weights=("equal",),
        weight_methods=("equal",),
        cost_bps=0.0,
    )

    assert list(result.columns) == [
        "rebalance",
        "n_long",
        "stock_weight",
        "weight_method",
        "n_windows",
        "cumulative_return",
        "annual_return",
        "benchmark_annual",
        "excess_annual",
        "te",
        "ir",
        "excess_max_drawdown",
        "excess_win_rate",
        "avg_turnover",
    ]


def test_param_lab_no_network() -> None:
    """End-to-end on a small grid with a synthetic fetcher; metrics finite."""
    panel = _param_lab_panel()
    dates = sorted(panel["date"].unique())

    result = run_param_lab(
        panel,
        factor_names=["MOMENTUM_1M"],
        benchmark_index="000905",
        index_fetcher=_param_lab_fetcher(dates),
        rebalances=(2,),
        n_longs=(5,),
        stock_weights=("equal",),
        weight_methods=("equal",),
        cost_bps=15.0,
    )

    assert len(result) == 1
    row = result.iloc[0]
    assert row["n_windows"] > 0
    assert np.isfinite(row["annual_return"])
    assert np.isfinite(row["excess_annual"])
    assert np.isfinite(row["avg_turnover"])


# ---------------------------------------------------------------------------
# make_inverse_vol_weight_fn + inv_vol param_lab wiring (batch12 US-2)
# ---------------------------------------------------------------------------


def _inv_vol_return_panel(
    return_lists: dict[str, list[float] | None],
    scores: dict[str, float] | None = None,
    start: float = 100.0,
) -> pd.DataFrame:
    """Panel whose per-symbol daily returns equal the given lists.

    A symbol mapped to None gets an all-NaN close column (never trades, so
    its vol is unestimable). composite_score defaults to 1.0 everywhere.
    """
    known = [r for r in return_lists.values() if r is not None]
    n = len(known[0])
    dates = pd.bdate_range("2025-01-06", periods=n + 1)
    rows: list[dict[str, object]] = []
    for sym, rets in return_lists.items():
        score = 1.0 if scores is None else scores[sym]
        if rets is None:
            closes: list[float] = [np.nan] * (n + 1)
        else:
            closes = [start]
            for r in rets:
                closes.append(closes[-1] * (1.0 + r))
        for d, c in zip(dates, closes, strict=True):
            rows.append(
                {"date": d, "symbol": sym, "close": c, "composite_score": score}
            )
    return pd.DataFrame(rows)


def test_inverse_vol_weight_fn_missing_close_raises() -> None:
    """A panel without a close column fails fast at closure construction."""
    panel = pd.DataFrame(
        {
            "date": [pd.Timestamp("2025-01-06")],
            "symbol": ["A"],
            "composite_score": [1.0],
        }
    )

    with pytest.raises(ValueError, match="close"):
        make_inverse_vol_weight_fn(panel)


def test_inverse_vol_weight_fn_two_names_hand_calc() -> None:
    """A's daily-return vol is exactly twice B's -> w = [1/3, 2/3]."""
    panel = _inv_vol_return_panel(
        {"A": [0.02, -0.02] * 15, "B": [0.01, -0.01] * 15},
        scores={"A": 2.0, "B": 1.0},
    )
    held = _held_at(panel, panel["date"].max())
    assert held["symbol"].iloc[0] == "A"

    w = make_inverse_vol_weight_fn(panel, min_names=2)(held)

    assert len(w) == 2
    assert w[0] == pytest.approx(1.0 / 3.0, abs=1e-9)
    assert w[1] == pytest.approx(2.0 / 3.0, abs=1e-9)


def test_inverse_vol_weight_fn_drops_zero_or_nan_vol() -> None:
    """Constant-close and all-NaN names get weight 0; valid names sum to 1."""
    rets: dict[str, list[float] | None] = {
        f"V{i}": [0.02, -0.02] * 15 for i in range(6)
    }
    rets["Z"] = [0.0] * 30  # constant close -> zero vol
    rets["N"] = None  # no close history -> NaN vol
    panel = _inv_vol_return_panel(rets)
    held = _held_at(panel, panel["date"].max())

    w = make_inverse_vol_weight_fn(panel)(held)

    by_name = dict(zip([str(s) for s in held["symbol"]], w, strict=True))
    assert by_name["Z"] == pytest.approx(0.0)
    assert by_name["N"] == pytest.approx(0.0)
    valid_sum = sum(v for k, v in by_name.items() if k.startswith("V"))
    assert valid_sum == pytest.approx(1.0)


def test_inverse_vol_weight_fn_thin_history_falls_back_equal() -> None:
    """Fewer than min_names survivors in the window -> equal weight."""
    panel = _inv_vol_return_panel(
        {s: [0.02, -0.02] * 15 for s in ("A", "B", "C")},
    )
    held = _held_at(panel, panel["date"].max())

    w = make_inverse_vol_weight_fn(panel)(held)

    assert np.allclose(w, np.full(len(held), 1.0 / len(held)))


def test_inverse_vol_weight_fn_no_lookahead() -> None:
    """Tampering with closes after t must not move t's weights.

    End-to-end through the engine on a window with deep enough history that
    the vol estimate is non-degenerate (plain equal-weight fallback would
    make the assertion vacuous).
    """
    names = ("A", "B", "C", "D", "E", "F", "G")
    rng = np.random.default_rng(17)
    dates = pd.bdate_range("2025-01-06", periods=12)
    rows = []
    for d in dates:
        for i, s in enumerate(names):
            rows.append(
                {
                    "date": d,
                    "symbol": s,
                    "close": 10.0 + 2.0 * i + float(rng.normal(scale=0.3)),
                    "composite_score": float(len(names) - i),
                    "fwd_1d": 0.001 * (i + 1),
                }
            )
    panel = pd.DataFrame(rows)
    pivot_idx = 5  # t = dates[5]: 6 closes -> 5 valid returns per name

    def run(p: pd.DataFrame) -> np.ndarray:
        captures: list[np.ndarray] = []
        fn = make_inverse_vol_weight_fn(p)

        def capturing(held: pd.DataFrame) -> np.ndarray:
            w = fn(held)
            captures.append(w)
            return w

        backtest_periodic_portfolio(
            p,
            score_col="composite_score",
            n_long=7,
            rebalance_days=1,
            forward_col="fwd_1d",
            weight_fn=capturing,
        )
        assert len(captures) == 11  # 12 grid dates -> 11 windows
        return captures[pivot_idx]

    w_base = run(panel)
    assert not np.allclose(w_base, np.full(7, 1.0 / 7.0))  # non-degenerate

    tampered = panel.copy()
    future = tampered["date"] > dates[pivot_idx]
    assert int(future.sum()) > 0
    tampered.loc[future, "close"] = (
        tampered.loc[future, "close"].to_numpy() * 3.7 + 11.0
    )

    assert np.allclose(w_base, run(tampered))


def test_inverse_vol_weight_fn_sum_one_and_nonneg() -> None:
    """Any valid panel: weights are non-negative, length-aligned, sum to 1."""
    panel = _param_lab_panel()
    held = panel[panel["date"] == panel["date"].max()].reset_index(drop=True)

    w = make_inverse_vol_weight_fn(panel)(held)

    assert len(w) == len(held)
    assert bool(np.all(w >= 0.0))
    assert float(np.sum(w)) == pytest.approx(1.0)


def test_param_lab_unknown_stock_weight_raises() -> None:
    """An unknown stock_weight is rejected up front rather than as equal."""
    panel = _param_lab_panel()

    with pytest.raises(ValueError, match="invol"):
        run_param_lab(
            panel,
            stock_weights=("invol",),
            rebalances=(2,),
            index_fetcher=_param_lab_fetcher(sorted(panel["date"].unique())),
        )
    # The message lists the valid set, which includes inv_vol.
    with pytest.raises(ValueError, match="inv_vol"):
        run_param_lab(
            panel,
            stock_weights=("invol",),
            rebalances=(2,),
            index_fetcher=_param_lab_fetcher(sorted(panel["date"].unique())),
        )


def test_param_lab_inv_vol_grid() -> None:
    """equal + inv_vol grid: rows/keys correct, inv_vol cells settle windows."""
    panel = _param_lab_panel()
    dates = sorted(panel["date"].unique())

    result = run_param_lab(
        panel,
        factor_names=["MOMENTUM_1M"],
        benchmark_index="000905",
        index_fetcher=_param_lab_fetcher(dates),
        rebalances=(2, 5),
        n_longs=(3, 5),
        stock_weights=("equal", "inv_vol"),
        weight_methods=("equal",),
        cost_bps=0.0,
    )

    assert len(result) == 8  # 2 reb x 2 n_long x 2 sw x 1 method
    keys = list(
        zip(
            result["rebalance"],
            result["n_long"],
            result["stock_weight"],
            result["weight_method"],
        )
    )
    assert len(set(keys)) == 8
    assert set(result["stock_weight"]) == {"equal", "inv_vol"}
    inv = result[result["stock_weight"] == "inv_vol"]
    assert (inv["n_windows"] > 0).any()
    assert np.isfinite(inv["excess_annual"]).any()


def test_param_lab_inv_vol_requires_close() -> None:
    """inv_vol on a panel without a close column is a ValueError naming it."""
    panel = _param_lab_panel().drop(columns=["close"])

    with pytest.raises(ValueError, match="close"):
        run_param_lab(
            panel,
            stock_weights=("inv_vol",),
            rebalances=(2,),
            index_fetcher=_param_lab_fetcher(sorted(panel["date"].unique())),
        )


def test_param_lab_cache_not_inflated_by_inv_vol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adding inv_vol does not add composite builds: still one per (fwd, wm)."""
    import selene_space_ashare.portfolio_rebal as pr_mod

    panel = _param_lab_panel()
    dates = sorted(panel["date"].unique())

    calls = {"n": 0}
    real_build = pr_mod.build_composite_panel

    def counting_build(*args: object, **kwargs: object) -> object:
        calls["n"] += 1
        return real_build(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pr_mod, "build_composite_panel", counting_build)

    run_param_lab(
        panel,
        factor_names=["MOMENTUM_1M"],
        benchmark_index="000905",
        index_fetcher=_param_lab_fetcher(dates),
        rebalances=(2, 5),
        n_longs=(3, 5),
        stock_weights=("equal", "slsqp", "inv_vol"),
        weight_methods=("equal",),
        cost_bps=0.0,
    )

    # 2 rebalances x 1 method = 2 builds, reused across all 12 cells.
    assert calls["n"] == 2
