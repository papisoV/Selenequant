"""US-102: run_portfolio_backtest signature promotion — defaults lock old behavior.

The pre-US-102 implementation hardcoded weight_method="ir_walkforward"
into build_composite_panel and held stocks equally (no weight_fn). These
tests pin the promoted parameters to that exact behavior via an inline
replication of the old pipeline body (the oracle), a run_param_lab
cross-check on the ir_walkforward + equal cell, and the whitelists
copied from run_param_lab (typos fail fast, never silently degrade).

Equivalence criterion (评审 Minor-1): the runner's config dict is
compared after explicitly EXCLUDING the two keys this batch adds
(stock_weight, weight_method); the added keys are asserted separately.
"""

from collections.abc import Callable

import numpy as np
import pandas as pd
import pytest

from selene_space_ashare.backtest import calc_performance_metrics
from selene_space_ashare.composite import build_composite_panel
from selene_space_ashare.portfolio_rebal import (
    align_benchmark_windows,
    backtest_periodic_portfolio,
    calc_benchmark_metrics,
    detect_panel_factors,
    run_param_lab,
    run_portfolio_backtest,
)


def _signature_panel(n_dates: int = 40) -> pd.DataFrame:
    """Deterministic panel: 3 real factors + close + fwd_5d; no network."""
    rng = np.random.default_rng(102)
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
                    "fwd_5d": fwd[j],
                }
            )
    return pd.DataFrame(rows)


def _index_fetcher(dates: list[pd.Timestamp]) -> Callable[[str], pd.DataFrame]:
    def fetch(symbol: str) -> pd.DataFrame:
        assert symbol == "000905"
        return pd.DataFrame(
            {
                "date": [d.strftime("%Y%m%d") for d in dates],
                "close": np.linspace(100.0, 118.0, len(dates)),
            }
        )

    return fetch


def test_defaults_equal_old_hardcoded_pipeline() -> None:
    """Default-arg run == inline replication of the pre-US-102 body.

    Oracle = the old implementation verbatim: whitelist detection ->
    build_composite_panel(weight_method="ir_walkforward") (the old
    hardcoded value) -> backtest_periodic_portfolio with no weight_fn
    (equal weights) -> align/drop -> calc_*_metrics.
    """
    panel = _signature_panel()
    dates = sorted(panel["date"].unique())
    fetcher = _index_fetcher(dates)

    result = run_portfolio_backtest(
        panel,
        benchmark_index="000905",
        index_fetcher=fetcher,
    )

    detected = detect_panel_factors(panel)
    scored, _cfg = build_composite_panel(
        panel, detected, "fwd_5d", weight_method="ir_walkforward"
    )
    windows_old = backtest_periodic_portfolio(
        scored,
        n_long=20,
        rebalance_days=5,
        forward_col="fwd_5d",
        cost_bps=15.0,
    )
    pr_old = pd.Series(windows_old["period_return"].to_numpy(), dtype=float)
    bench_old = align_benchmark_windows(fetcher("000905"), windows_old)
    if int(bench_old.attrs.get("dropped", 0)) > 0:
        windows_old = windows_old.loc[bench_old.index]
        pr_old = pr_old.loc[bench_old.index]
    freq = 252 // 5
    pm_old = calc_performance_metrics(pr_old, freq=freq)
    bm_old = calc_performance_metrics(bench_old, freq=freq)
    em_old = calc_benchmark_metrics(pr_old, bench_old, freq=freq)

    # windows: frame equality, not `==` (flaky on dtype/NaN).
    pd.testing.assert_frame_equal(
        result["windows"], windows_old, check_dtype=False
    )

    for section, expected in (
        ("portfolio_metrics", pm_old),
        ("benchmark_metrics", bm_old),
        ("excess_metrics", em_old),
    ):
        assert set(result[section]) == set(expected)
        for key, val in expected.items():
            got = result[section][key]
            if isinstance(val, float) and np.isnan(val):
                assert np.isnan(got)
            else:
                assert got == pytest.approx(val), (section, key)

    # Config frozen: new keys explicitly excluded, old keys exact.
    cfg = dict(result["config"])
    cfg.pop("stock_weight")
    cfg.pop("weight_method")
    assert cfg == {
        "n_long": 20,
        "rebalance_days": 5,
        "cost_bps": 15.0,
        "forward_col": "fwd_5d",
        "benchmark_index": "000905",
        "factor_names": detected,
        "n_factors_detected": len(detected),
    }
    # The added keys exist and record the defaults (= old hardcoded values).
    assert result["config"]["weight_method"] == "ir_walkforward"
    assert result["config"]["stock_weight"] == "equal"


def test_defaults_match_param_lab_ir_walkforward_equal_cell() -> None:
    """Runner defaults == run_param_lab's (ir_walkforward, equal) cell."""
    panel = _signature_panel()
    dates = sorted(panel["date"].unique())

    result = run_portfolio_backtest(
        panel,
        benchmark_index="000905",
        index_fetcher=_index_fetcher(dates),
    )
    grid = run_param_lab(
        panel,
        factor_names=None,
        benchmark_index="000905",
        index_fetcher=_index_fetcher(dates),
        rebalances=(5,),
        n_longs=(20,),
        stock_weights=("equal",),
        weight_methods=("ir_walkforward",),
        cost_bps=15.0,
    )
    assert len(grid) == 1
    row = grid.iloc[0]

    assert row["n_windows"] == len(result["windows"])
    pairs = [
        ("cumulative_return", result["portfolio_metrics"]["cumulative_return"]),
        ("annual_return", result["portfolio_metrics"]["annual_return"]),
        ("benchmark_annual", result["benchmark_metrics"]["annual_return"]),
        ("excess_annual", result["excess_metrics"]["excess_annual"]),
        ("te", result["excess_metrics"]["te"]),
        ("ir", result["excess_metrics"]["ir"]),
        ("excess_max_drawdown", result["excess_metrics"]["excess_max_drawdown"]),
        ("excess_win_rate", result["excess_metrics"]["excess_win_rate"]),
    ]
    for col, val in pairs:
        assert row[col] == pytest.approx(val), col


def test_unknown_weight_method_raises() -> None:
    """Typo'd weight_method fails fast — never a silent zero-weight run."""
    panel = _signature_panel(n_dates=12)

    with pytest.raises(ValueError, match="momentum"):
        run_portfolio_backtest(
            panel,
            weight_method="momentum",
            index_fetcher=lambda _s: pd.DataFrame(),
        )


def test_unknown_stock_weight_raises() -> None:
    """Typo'd stock_weight fails fast instead of degrading to equal."""
    panel = _signature_panel(n_dates=12)

    with pytest.raises(ValueError, match="invol"):
        run_portfolio_backtest(
            panel,
            stock_weight="invol",
            index_fetcher=lambda _s: pd.DataFrame(),
        )


def test_stock_weight_risk_scheme_requires_close() -> None:
    """slsqp/inv_vol stock_weight demands a close column, even pre-dispatch."""
    panel = _signature_panel(n_dates=12).drop(columns=["close"])

    with pytest.raises(ValueError, match="close"):
        run_portfolio_backtest(
            panel,
            stock_weight="inv_vol",
            index_fetcher=lambda _s: pd.DataFrame(),
        )


def test_weight_method_parameter_is_threaded() -> None:
    """A non-default weight_method reaches build_composite_panel: the
    equal-weight composite cannot reproduce the ir_walkforward windows."""
    panel = _signature_panel()
    dates = sorted(panel["date"].unique())
    fetcher = _index_fetcher(dates)

    wf = run_portfolio_backtest(
        panel, benchmark_index="000905", index_fetcher=fetcher
    )
    eq = run_portfolio_backtest(
        panel,
        benchmark_index="000905",
        index_fetcher=fetcher,
        weight_method="equal",
    )

    assert eq["config"]["weight_method"] == "equal"
    assert not np.allclose(
        wf["windows"]["period_return"].to_numpy(dtype=float),
        eq["windows"]["period_return"].to_numpy(dtype=float),
    )
