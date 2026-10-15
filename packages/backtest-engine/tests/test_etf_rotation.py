"""Behavior lock for the dual-momentum ETF rotation engine.

Absorbed from daily_stock_analysis ``tests/test_etf_rotation.py`` (MIT,
Copyright (c) 2026 ZhuLinsen) at commit ``ce364e45``.  Upstream ships the
engine and a data-loading service in one file; only the engine (pure logic,
no I/O) is ported here, so the loader/report-rendering tests that depend on
upstream's ``data_provider`` / ``Config`` / trading calendar are intentionally
dropped.  The tests that remain pin the algorithmic invariants:

- Momentum is relative (rank by lookback return) AND absolute (negative
  lookback return is never eligible).
- The switch buffer keeps an eligible incumbent within ``switch_buffer_pct``
  of the weakest selected slot, to reduce churn.
- Orders fill one bar after the signal (``EXECUTION_LAG_DAYS``); a jump on
  the signal bar itself is never earned (no lookahead).
- The cash leg is not a trade: cash -> asset turnover is 1.0, not 2.0.
- Unchanged holdings are not re-traded back to exact weights (no drift
  re-equalisation).
- The strategy and the equal-weight benchmark start on the same execution bar.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from selene_space_backtest.etf_rotation import (
    CASH,
    EXECUTION_LAG_DAYS,
    RotationParams,
    TRADING_DAYS_PER_YEAR,
    annual_returns,
    holdings_to_weights,
    is_rebalance_day,
    latest_ranking,
    momentum_metrics,
    parameter_sweep,
    rebalance_dates,
    run_backtest,
    select_holdings,
)


def _dates(n: int, start: str = "2020-01-01") -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=n)


def _path(daily_returns: np.ndarray, start: float = 1.0) -> np.ndarray:
    return start * np.cumprod(1.0 + daily_returns)


def _regime_closes(n: int = 400) -> pd.DataFrame:
    """A rises then falls, B falls then rises, SAFE grows slowly."""
    half = n // 2
    a = np.r_[np.full(half, 0.004), np.full(n - half, -0.004)]
    b = -a
    return pd.DataFrame(
        {"A": _path(a), "B": _path(b), "SAFE": _path(np.full(n, 0.0001))},
        index=_dates(n),
    )


# --- params ----------------------------------------------------------------------


def test_params_reject_invalid_values():
    with pytest.raises(ValueError):
        RotationParams(rebalance="daily")
    with pytest.raises(ValueError):
        RotationParams(top_n=0)
    with pytest.raises(ValueError):
        RotationParams(cost_bps=-1)
    with pytest.raises(ValueError):
        RotationParams(lookback_days=0)
    with pytest.raises(ValueError):
        RotationParams(switch_buffer_pct=-0.1)


def test_params_are_frozen():
    params = RotationParams()
    with pytest.raises(Exception):
        params.lookback_days = 10  # type: ignore[misc]


# --- rebalance calendar -----------------------------------------------------------


def test_is_rebalance_day_compares_with_next_session():
    friday, monday = pd.Timestamp("2024-01-05"), pd.Timestamp("2024-01-08")
    wednesday, thursday = pd.Timestamp("2024-01-03"), pd.Timestamp("2024-01-04")
    assert is_rebalance_day(friday, monday, "weekly")
    assert not is_rebalance_day(wednesday, thursday, "weekly")
    assert not is_rebalance_day(friday, monday, "monthly")
    assert is_rebalance_day(
        pd.Timestamp("2024-01-31"), pd.Timestamp("2024-02-01"), "monthly"
    )


def test_weekly_rebalance_dates_are_last_trading_day_of_each_week():
    index = _dates(10, "2024-01-01")  # Mon 1st .. Fri 12th
    result = rebalance_dates(index, "weekly")
    assert list(result) == [pd.Timestamp("2024-01-05"), pd.Timestamp("2024-01-12")]


def test_monthly_rebalance_dates_include_partial_last_month():
    index = _dates(30, "2024-01-15")
    result = rebalance_dates(index, "monthly")
    assert list(result) == [pd.Timestamp("2024-01-31"), index[-1]]


def test_rebalance_dates_empty_index():
    assert len(rebalance_dates(pd.DatetimeIndex([]), "weekly")) == 0


# --- selection --------------------------------------------------------------------


def test_select_holdings_goes_defensive_when_nothing_is_positive():
    scores = pd.Series({"A": -0.01, "B": -0.2, "C": np.nan})
    assert select_holdings(scores, (), RotationParams()) == ()


def test_select_holdings_picks_strongest_positive():
    scores = pd.Series({"A": 0.05, "B": 0.10, "C": np.nan})
    assert select_holdings(scores, (), RotationParams()) == ("B",)


def test_switch_buffer_keeps_incumbent_within_buffer():
    scores = pd.Series({"A": 0.090, "B": 0.105})
    params = RotationParams(switch_buffer_pct=2.0)
    assert select_holdings(scores, ("A",), params) == ("A",)


def test_switch_buffer_replaces_incumbent_beyond_buffer():
    scores = pd.Series({"A": 0.07, "B": 0.105})
    params = RotationParams(switch_buffer_pct=2.0)
    assert select_holdings(scores, ("A",), params) == ("B",)


def test_incumbent_dropped_when_momentum_turns_negative():
    scores = pd.Series({"A": -0.001, "B": 0.001})
    params = RotationParams(switch_buffer_pct=50.0)
    assert select_holdings(scores, ("A",), params) == ("B",)


def test_top_n_fills_only_eligible_slots():
    scores = pd.Series({"A": 0.05, "B": -0.02, "C": 0.01})
    assert select_holdings(scores, (), RotationParams(top_n=3)) == ("A", "C")


def test_unfilled_slots_go_to_safe_asset_or_cash():
    assert holdings_to_weights(("A",), 2, "SAFE") == {"A": 0.5, "SAFE": 0.5}
    assert holdings_to_weights((), 1, None) == {CASH: 1.0}
    assert holdings_to_weights(("A", "B"), 2, "SAFE") == {"A": 0.5, "B": 0.5}


# --- backtest ---------------------------------------------------------------------


def test_backtest_follows_regimes_and_uses_safe_asset():
    closes = _regime_closes()
    result = run_backtest(
        closes, ["A", "B"], "SAFE", RotationParams(lookback_days=20, cost_bps=0)
    )

    held = [tuple(sorted(t.to_weights)) for t in result.trades]
    assert held[0] == ("A",)
    assert ("B",) in held
    assert result.equity.iloc[-1] > 1.0
    assert result.final_holdings == ("B",)


def test_trades_execute_one_bar_after_signal_on_rebalance_days():
    closes = _regime_closes()
    params = RotationParams(lookback_days=20, rebalance="weekly", cost_bps=0)
    result = run_backtest(closes, ["A", "B"], "SAFE", params)

    index = closes.index
    rebal = set(rebalance_dates(index, "weekly"))
    for trade in result.trades:
        assert trade.signal_date in rebal
        assert (
            index.get_loc(trade.exec_date) - index.get_loc(trade.signal_date)
            == EXECUTION_LAG_DAYS
        )


def test_no_lookahead_signal_day_jump_is_not_captured():
    """A jump on the signal bar must not be earned: the order fills at the next close."""
    index = _dates(60, "2024-01-01")
    signal_day = rebalance_dates(index, "weekly")[5]
    pos = index.get_loc(signal_day)
    returns = np.zeros(60)
    returns[pos] = 0.10  # makes momentum positive exactly on the signal bar
    closes = pd.DataFrame({"A": _path(returns)}, index=index)

    result = run_backtest(
        closes, ["A"], None, RotationParams(lookback_days=5, cost_bps=0)
    )

    assert result.trades and result.trades[0].signal_date == signal_day
    assert result.equity.iloc[-1] == pytest.approx(1.0)


def test_costs_reduce_equity_by_turnover():
    closes = _regime_closes()
    free = run_backtest(
        closes, ["A", "B"], "SAFE", RotationParams(lookback_days=20, cost_bps=0)
    )
    costly = run_backtest(
        closes, ["A", "B"], "SAFE", RotationParams(lookback_days=20, cost_bps=100)
    )

    assert len(free.trades) == len(costly.trades) > 0
    assert costly.equity.iloc[-1] < free.equity.iloc[-1]


def test_entry_cost_is_exactly_turnover_times_rate():
    closes = _regime_closes()
    free = run_backtest(
        closes, ["A"], None, RotationParams(lookback_days=20, cost_bps=0)
    )
    costly = run_backtest(
        closes, ["A"], None, RotationParams(lookback_days=20, cost_bps=100)
    )

    assert costly.trades[0].turnover == pytest.approx(1.0)
    assert costly.equity.iloc[0] == free.equity.iloc[0] == pytest.approx(1.0)
    assert costly.equity.iloc[1] / free.equity.iloc[1] == pytest.approx(0.99)


def test_strategy_and_benchmark_start_together_at_first_fill():
    closes = _regime_closes()
    result = run_backtest(closes, ["A", "B"], "SAFE", RotationParams(lookback_days=20))

    assert result.equity.index[0] == result.trades[0].exec_date
    assert result.benchmark_equity.index.equals(result.equity.index)
    assert result.equity.iloc[0] == pytest.approx(1.0)
    assert result.benchmark_equity.iloc[0] == pytest.approx(1.0)


def test_first_signal_fully_defensive_still_starts_curves_at_fill():
    n = 120
    closes = pd.DataFrame({"A": _path(np.full(n, -0.002))}, index=_dates(n))
    result = run_backtest(closes, ["A"], None, RotationParams(lookback_days=10))

    assert result.trades == []  # cash -> cash is not a trade
    assert result.final_weights == {CASH: 1.0}
    assert result.equity.index[0] > closes.index[10]
    assert (result.equity == 1.0).all()


def test_cash_leg_is_not_counted_as_turnover():
    closes = _regime_closes()
    result = run_backtest(
        closes, ["A"], None, RotationParams(lookback_days=20, cost_bps=0)
    )

    assert result.trades[0].from_weights == {CASH: 1.0}
    assert result.trades[0].turnover == pytest.approx(1.0)  # cash -> A
    exit_trade = next(t for t in result.trades if t.to_weights == {CASH: 1.0})
    assert exit_trade.turnover == pytest.approx(1.0)  # A -> cash


def test_unchanged_holdings_are_not_retraded_to_exact_weights():
    n = 200
    closes = pd.DataFrame(
        {"A": _path(np.full(n, 0.003)), "B": _path(np.full(n, 0.001))},
        index=_dates(n),
    )
    result = run_backtest(
        closes, ["A", "B"], None, RotationParams(lookback_days=20, top_n=2)
    )

    assert len(result.trades) == 1  # initial entry only, no weekly drift re-balancing


def test_all_negative_momentum_parks_in_safe_asset():
    n = 120
    closes = pd.DataFrame(
        {"A": _path(np.full(n, -0.002)), "SAFE": _path(np.full(n, 0.001))},
        index=_dates(n),
    )
    result = run_backtest(
        closes, ["A"], "SAFE", RotationParams(lookback_days=10, cost_bps=0)
    )

    assert result.final_weights == {"SAFE": 1.0}
    assert result.equity.iloc[-1] > 1.0


def test_pre_listing_asset_is_ignored_until_it_has_history():
    closes = _regime_closes()
    closes.loc[closes.index[:250], "B"] = np.nan  # B lists late
    result = run_backtest(
        closes, ["A", "B"], "SAFE", RotationParams(lookback_days=20, cost_bps=0)
    )

    first_b = next(t for t in result.trades if "B" in t.to_weights)
    assert first_b.signal_date >= closes.index[250 + 20]


def test_held_asset_recovery_books_full_loss_and_never_trades_missing_quotes():
    closes = pd.DataFrame(
        {"A": _path(np.full(80, 0.002))}, index=_dates(80, "2024-01-01")
    )
    closes.iloc[20:28, 0] = np.nan
    closes.iloc[28:, 0] *= 0.8
    result = run_backtest(
        closes, ["A"], None, RotationParams(lookback_days=5, cost_bps=0)
    )
    assert result.equity.loc[closes.index[28]] / result.equity.loc[
        closes.index[19]
    ] == pytest.approx(
        closes.iloc[28, 0] / closes.iloc[19, 0],
    )
    for trade in result.trades:
        for code in (set(trade.from_weights) | set(trade.to_weights)) - {CASH}:
            assert pd.notna(closes.loc[trade.exec_date, code])


def test_defensive_asset_is_cash_until_it_has_an_execution_quote():
    closes = pd.DataFrame(
        {"A": _path(np.full(90, -0.002)), "SAFE": 1.0}, index=_dates(90)
    )
    closes.loc[closes.index[:40], "SAFE"] = np.nan
    result = run_backtest(
        closes, ["A"], "SAFE", RotationParams(lookback_days=5, cost_bps=0)
    )
    safe_trades = [trade for trade in result.trades if "SAFE" in trade.to_weights]
    assert safe_trades
    assert all(trade.exec_date >= closes.index[40] for trade in safe_trades)
    assert (result.equity.loc[: closes.index[39]] == 1.0).all()


def test_backtest_requires_enough_history():
    closes = pd.DataFrame({"A": [1.0, 1.1, 1.2]}, index=_dates(3))
    with pytest.raises(ValueError):
        run_backtest(closes, ["A"], None, RotationParams(lookback_days=10))


def test_backtest_requires_a_risk_asset_with_data():
    closes = pd.DataFrame({"A": [1.0, 1.1, 1.2]}, index=_dates(3))
    with pytest.raises(ValueError):
        run_backtest(closes, ["MISSING"], None, RotationParams(lookback_days=1))


# --- metrics ----------------------------------------------------------------------


def test_momentum_metrics_drawdown_and_return():
    equity = pd.Series(
        [1.0, 2.0, 1.0, 3.0],
        index=pd.to_datetime(["2020-01-01", "2020-06-01", "2020-09-01", "2021-01-01"]),
    )
    metrics = momentum_metrics(equity)
    assert metrics["total_return"] == pytest.approx(2.0)
    assert metrics["max_drawdown"] == pytest.approx(-0.5)
    assert metrics["cagr"] == pytest.approx(2.0, rel=0.01)


def test_momentum_metrics_empty_and_single_point_are_nan():
    assert all(np.isnan(v) for v in momentum_metrics(pd.Series(dtype=float)).values())
    single = pd.Series([1.0], index=pd.to_datetime(["2020-01-01"]))
    assert all(np.isnan(v) for v in momentum_metrics(single).values())


def test_momentum_metrics_annualization_days_override_changes_vol_and_sharpe():
    rets = np.array([0.01, -0.005, 0.008, -0.002, 0.004])
    equity = pd.Series(_path(rets), index=_dates(len(rets)))
    a_share = momentum_metrics(equity)
    us = momentum_metrics(equity, annualization_days=252)
    assert TRADING_DAYS_PER_YEAR == 244
    assert a_share["total_return"] == pytest.approx(us["total_return"])
    assert a_share["max_drawdown"] == pytest.approx(us["max_drawdown"])
    assert us["ann_vol"] > a_share["ann_vol"]  # 252 > 244


def test_annual_returns_chain_across_years():
    equity = pd.Series(
        [1.0, 1.1, 1.21],
        index=pd.to_datetime(["2020-01-02", "2020-12-31", "2021-12-31"]),
    )
    result = annual_returns(equity)
    assert result[2020] == pytest.approx(0.1)
    assert result[2021] == pytest.approx(0.1)


# --- parameter sweep --------------------------------------------------------------


def test_parameter_sweep_matches_backtest_with_post_listing_long_gap():
    closes = pd.DataFrame(
        {"A": _path(np.full(600, 0.002)), "B": 1.0}, index=_dates(600)
    )
    closes.loc[closes.index[300:307], "A"] = np.nan
    closes.loc[closes.index[307:], "A"] *= 0.9
    params = RotationParams(lookback_days=20, cost_bps=0)
    main = run_backtest(closes, ["A", "B"], None, params)
    swept = dict(parameter_sweep(closes, ["A", "B"], None, params, lookbacks=(20,)))
    assert swept[20] == pytest.approx(momentum_metrics(main.equity), nan_ok=True)


def test_parameter_sweep_skips_lookbacks_longer_than_history():
    closes = _regime_closes(300)
    rows = parameter_sweep(
        closes, ["A", "B"], "SAFE", RotationParams(), lookbacks=(20, 60, 1000)
    )
    assert [lookback for lookback, _ in rows] == [20, 60]
    assert all(not np.isnan(m["cagr"]) for _, m in rows)


def test_parameter_sweep_uses_common_window_when_safe_asset_has_longer_history():
    closes = _regime_closes(600)
    closes.loc[closes.index[:300], ["A", "B"]] = (
        np.nan
    )  # risk pool lists late, SAFE does not
    rows = dict(
        parameter_sweep(
            closes, ["A", "B"], "SAFE", RotationParams(cost_bps=0), lookbacks=(20, 120)
        )
    )

    runs = {
        lookback: run_backtest(
            closes,
            ["A", "B"],
            "SAFE",
            RotationParams(lookback_days=lookback, cost_bps=0),
        )
        for lookback in rows
    }
    common_start = max(run.equity.index[0] for run in runs.values())
    assert (
        common_start > closes.index[300 + 120]
    )  # set by the longest lookback, not by SAFE
    for lookback, run in runs.items():
        assert rows[lookback] == momentum_metrics(run.equity.loc[common_start:])


def test_parameter_sweep_always_includes_configured_lookback():
    rows = parameter_sweep(
        _regime_closes(300),
        ["A", "B"],
        "SAFE",
        RotationParams(lookback_days=75),
        lookbacks=(20, 60),
    )
    assert [lookback for lookback, _ in rows] == [20, 60, 75]


def test_parameter_sweep_drops_lookbacks_that_leave_too_short_a_window():
    closes = _regime_closes(400)  # about 1.5 years
    rows = parameter_sweep(
        closes,
        ["A", "B"],
        "SAFE",
        RotationParams(lookback_days=20),
        lookbacks=(20, 200),
        min_years=1.0,
    )
    assert [lookback for lookback, _ in rows] == [20]


def test_parameter_sweep_returns_empty_without_a_risk_asset():
    closes = _regime_closes(120)
    assert parameter_sweep(closes, ["MISSING"], "SAFE", RotationParams()) == []


# --- latest ranking ---------------------------------------------------------------


def test_latest_ranking_sorts_descending_and_masks_late_listings():
    n = 300
    closes = pd.DataFrame(
        {
            "A": _path(np.full(n, 0.003)),
            "B": _path(np.full(n, 0.001)),
            "C": _path(np.full(n, 0.002)),
        },
        index=_dates(n),
    )
    closes.loc[closes.index[-1], "A"] = np.nan  # latest quote missing
    as_of, ranking = latest_ranking(
        closes, ["A", "B", "C"], RotationParams(lookback_days=20)
    )

    assert as_of == closes.index[-1]
    assert pd.isna(ranking["A"])  # missing latest quote is not scored
    assert ranking.dropna().index.tolist() == ["C", "B"]  # 0.002 > 0.001
