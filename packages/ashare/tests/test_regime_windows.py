"""batch13 US-1 tests: regime window join + excess summary.

Covers the frozen plan contracts: PIT consistency (truncated index ->
identical labels), three-columns-die-together drops, duplicate-date fan
out guard, D7 aggregation conventions (arithmetic excess, n_segments
contiguous runs, empty states at n=0, FULL anchor row), and the
misalignment ValueError.

batch14 appends the drawdown view on top: rolling-peak labels, warm-up
NaN handling, the extra_label_cols generalisation (with a pre-extension
snapshot as the equivalence evidence), and the three new view keys.
"""

import numpy as np
import pandas as pd
import pytest

from selene_space_ashare.portfolio_rebal import align_benchmark_windows
from selene_space_ashare.regime import calc_index_regime
from selene_space_ashare.regime_windows import (
    calc_drawdown_regime,
    join_window_regimes,
    regime_excess_summary,
)


def _make_windows(
    rebalance_dates: list[pd.Timestamp],
    period_returns: list[float],
) -> pd.DataFrame:
    idx = pd.RangeIndex(len(rebalance_dates))
    return pd.DataFrame(
        {
            "rebalance_date": rebalance_dates,
            "end_date": [d + pd.Timedelta(days=5) for d in rebalance_dates],
            "period_return": period_returns,
            "turnover": 0.5,
            "n_held": 20,
        },
        index=idx,
    )


def _make_regime_df(
    dates: list,
    trend: list[str],
    vol: list[str],
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": dates,
            "trend_regime": trend,
            "vol_regime": vol,
        }
    )


# ---------------------------------------------------------------------------
# 1. PIT: labels from the full series == labels from the series truncated
#    at t (regime.py rolling windows are backward-looking only).
# ---------------------------------------------------------------------------


def _synthetic_index(n_days: int = 300) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=n_days)
    close = np.concatenate(
        [
            np.linspace(100.0, 160.0, 250),  # steady uptrend
            np.linspace(158.0, 110.0, n_days - 250),  # crash after day 250
        ]
    )
    return pd.DataFrame({"date": dates, "close": close})


@pytest.mark.parametrize("t_idx", [80, 180, 249, 251, 275])
def test_pit_truncated_series_gives_identical_labels(t_idx: int) -> None:
    index_daily = _synthetic_index()
    full = calc_index_regime(index_daily)
    truncated = calc_index_regime(index_daily.iloc[: t_idx + 1])

    t = index_daily["date"].iloc[t_idx]
    row_full = full[full["date"] == t].iloc[0]
    row_trunc = truncated[truncated["date"] == t].iloc[0]
    assert row_full["trend_regime"] == row_trunc["trend_regime"]
    assert row_full["vol_regime"] == row_trunc["vol_regime"]


def test_pit_join_with_truncated_regime_matches_full_join() -> None:
    index_daily = _synthetic_index()
    full = calc_index_regime(index_daily)
    dates = list(index_daily["date"].iloc[[100, 200, 260]])
    windows = _make_windows(dates, [0.01, 0.02, 0.03])
    bench = pd.Series([0.005, 0.005, 0.005], index=windows.index)

    joined_full = join_window_regimes(windows, full, bench)
    trunc_at = index_daily.iloc[:261]
    joined_trunc = join_window_regimes(windows, calc_index_regime(trunc_at), bench)
    pd.testing.assert_frame_equal(joined_full, joined_trunc)


# ---------------------------------------------------------------------------
# 2. Missing regime date: window dropped, counted, benchmark dies with it.
# ---------------------------------------------------------------------------


def test_missing_regime_date_drops_row_and_counts() -> None:
    dates = pd.to_datetime(["2024-01-01", "2024-01-08", "2024-01-15", "2024-01-22"])
    windows = _make_windows(dates, [0.01, 0.02, 0.03, 0.04])
    bench = pd.Series([0.01, 0.01, 0.01, 0.01], index=windows.index)
    # Regime calendar lacks 2024-01-15 (index 2).
    regime = _make_regime_df(
        [dates[0], dates[1], dates[3]],
        ["bull", "bear", "bull"],
        ["low_vol", "high_vol", "normal_vol"],
    )

    joined = join_window_regimes(windows, regime, bench)
    assert joined.attrs["dropped"] == 1
    assert len(joined) == 3
    assert dates[2] not in set(pd.to_datetime(joined["rebalance_date"]))
    # Three columns die together: 3 windows -> 3 benchmark returns.
    assert len(joined["benchmark_return"]) == 3


# ---------------------------------------------------------------------------
# 3. Duplicate regime dates must not fan out window rows (keep="last").
# ---------------------------------------------------------------------------


def test_duplicate_regime_dates_do_not_fan_out() -> None:
    dates = pd.to_datetime(["2024-01-01", "2024-01-08"])
    windows = _make_windows(dates, [0.01, 0.02])
    bench = pd.Series([0.01, 0.01], index=windows.index)
    regime = _make_regime_df(
        [dates[0], dates[0], dates[1]],
        ["bear", "bull", "neutral"],  # 2024-01-01 twice; keep="last" -> bull
        ["low_vol", "low_vol", "normal_vol"],
    )

    joined = join_window_regimes(windows, regime, bench)
    assert len(joined) == 2  # no fan-out
    row0 = joined[joined["rebalance_date"] == dates[0]].iloc[0]
    assert row0["trend_regime"] == "bull"  # keep="last"


# ---------------------------------------------------------------------------
# 4. Hand-calculated aggregation (D7 conventions, trend marginal).
# ---------------------------------------------------------------------------


def test_summary_hand_calc_trend_marginal() -> None:
    dates = pd.bdate_range("2024-01-01", periods=6)
    trend = ["bull", "bull", "bear", "bull", "bear", "neutral"]
    joined = pd.DataFrame(
        {
            "rebalance_date": dates,
            "period_return": [0.03, 0.00, 0.04, 0.05, -0.01, 0.02],
            "benchmark_return": [0.01] * 6,
            "trend_regime": trend,
            "vol_regime": ["low_vol"] * 6,
        }
    )

    out = regime_excess_summary(joined, by=("trend_regime",), label="hand", freq=51)

    # Expected excess = period - bench:
    # [0.02, -0.01, 0.03, 0.04, -0.02, 0.01]
    full = out.iloc[0]
    assert full["def"] == "FULL"
    assert full["label"] == "hand"
    assert full["n_windows"] == 6
    assert full["n_segments"] == 5  # bb b b bn -> 2+2+1
    assert full["mean_window_excess"] == pytest.approx(0.07 / 6)
    expected_cum = (1.02 * 0.99 * 1.03 * 1.04 * 0.98 * 1.01) - 1.0
    assert full["cum_excess"] == pytest.approx(expected_cum)
    assert full["win_rate"] == pytest.approx(4 / 6)
    assert full["excess_annual"] == pytest.approx(
        (1.0 + expected_cum) ** (51 / 6) - 1.0
    )

    by_state = out.set_index("state")
    bull = by_state.loc["bull"]
    assert bull["n_windows"] == 3
    assert bull["n_segments"] == 2
    assert bull["mean_window_excess"] == pytest.approx(0.05 / 3)
    assert bull["cum_excess"] == pytest.approx(1.02 * 0.99 * 1.04 - 1.0)
    assert bull["win_rate"] == pytest.approx(2 / 3)
    assert np.isnan(bull["excess_annual"])  # no state-level annualization

    neutral = by_state.loc["neutral"]
    assert neutral["n_windows"] == 1
    assert neutral["n_segments"] == 1
    assert neutral["mean_window_excess"] == pytest.approx(0.01)
    assert neutral["win_rate"] == 1.0

    bear = by_state.loc["bear"]
    assert bear["n_windows"] == 2
    assert bear["n_segments"] == 2
    assert bear["mean_window_excess"] == pytest.approx(0.005)
    assert bear["cum_excess"] == pytest.approx(1.03 * 0.98 - 1.0)
    assert bear["win_rate"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# 5. Joint trend x vol grid: 9 frozen cells, hand-counted.
# ---------------------------------------------------------------------------


def test_summary_joint_grid_counts() -> None:
    combos = [
        ("bull", "low_vol"),
        ("bull", "normal_vol"),
        ("bull", "high_vol"),
        ("neutral", "low_vol"),
        ("neutral", "normal_vol"),
        ("neutral", "high_vol"),
        ("bear", "low_vol"),
        ("bear", "normal_vol"),
        ("bear", "high_vol"),
    ]
    extra = [("bull", "low_vol")] * 3  # combos already has one -> n=4
    all_combos = combos + extra
    joined = pd.DataFrame(
        {
            "rebalance_date": pd.bdate_range("2024-01-01", periods=len(all_combos)),
            "period_return": [0.02] * len(all_combos),
            "benchmark_return": [0.01] * len(all_combos),
            "trend_regime": [c[0] for c in all_combos],
            "vol_regime": [c[1] for c in all_combos],
        }
    )

    out = regime_excess_summary(joined, by=("trend_regime", "vol_regime"))
    assert len(out) == 10  # FULL + 9 cells
    rows = out[out["def"] == "trend_regime×vol_regime"]
    counts = rows.set_index("state")["n_windows"].to_dict()
    assert counts["bull×low_vol"] == 4
    assert counts["bull×normal_vol"] == 1
    for state, n in counts.items():
        if state != "bull×low_vol":
            assert n == 1, state
    # mean excess identical everywhere by construction
    assert rows["mean_window_excess"].eq(0.01).all()


# ---------------------------------------------------------------------------
# 6. Date type normalization: %Y%m%d str regime side vs Timestamp windows.
# ---------------------------------------------------------------------------


def test_date_type_normalization_str_vs_timestamp() -> None:
    dates = pd.to_datetime(["2024-03-04", "2024-03-11"])
    windows = _make_windows(dates, [0.01, 0.02])
    bench = pd.Series([0.01, 0.01], index=windows.index)
    regime = _make_regime_df(
        ["20240304", "20240311"],
        ["bull", "bear"],
        ["low_vol", "high_vol"],
    )

    joined = join_window_regimes(windows, regime, bench)
    assert joined.attrs["dropped"] == 0
    assert len(joined) == 2
    assert list(joined["trend_regime"]) == ["bull", "bear"]


# ---------------------------------------------------------------------------
# 7. Empty states at n=0; empty input returns the full frozen enum.
# ---------------------------------------------------------------------------


def test_absent_states_reported_at_n_zero() -> None:
    joined = pd.DataFrame(
        {
            "rebalance_date": pd.bdate_range("2024-01-01", periods=2),
            "period_return": [0.02, 0.03],
            "benchmark_return": [0.01, 0.01],
            "trend_regime": ["bull", "bull"],
            "vol_regime": ["low_vol", "low_vol"],
        }
    )
    out = regime_excess_summary(joined, by=("trend_regime",))
    rows = out[out["def"] == "trend_regime"].set_index("state")
    assert rows.loc["bull", "n_windows"] == 2
    assert rows.loc["neutral", "n_windows"] == 0
    assert rows.loc["bear", "n_windows"] == 0
    assert np.isnan(rows.loc["bear", "mean_window_excess"])
    assert np.isnan(rows.loc["neutral", "win_rate"])
    assert rows.loc["neutral", "n_segments"] == 0


def test_empty_input_returns_full_enum_without_raising() -> None:
    empty_joined = pd.DataFrame(
        {
            "rebalance_date": pd.Series(dtype="datetime64[ns]"),
            "period_return": pd.Series(dtype=float),
            "benchmark_return": pd.Series(dtype=float),
            "trend_regime": pd.Series(dtype=object),
            "vol_regime": pd.Series(dtype=object),
        }
    )
    out = regime_excess_summary(empty_joined, by=("trend_regime",), freq=51)
    assert len(out) == 4  # FULL + 3 trend states
    assert (out["n_windows"] == 0).all()
    assert (out["n_segments"] == 0).all()
    assert out["mean_window_excess"].isna().all()


# ---------------------------------------------------------------------------
# 8. Arithmetic excess convention (period - benchmark, not geometric).
# ---------------------------------------------------------------------------


def test_excess_is_arithmetic_difference() -> None:
    joined = pd.DataFrame(
        {
            "rebalance_date": pd.bdate_range("2024-01-01", periods=1),
            "period_return": [0.05],
            "benchmark_return": [0.02],
            "trend_regime": ["bull"],
            "vol_regime": ["normal_vol"],
        }
    )
    out = regime_excess_summary(joined, by=("vol_regime",))
    rows = out[out["def"] == "vol_regime"].set_index("state")
    assert rows.loc["normal_vol", "mean_window_excess"] == pytest.approx(0.03)


# ---------------------------------------------------------------------------
# 9. n_segments counts contiguous runs (bull,bull,bear,bull -> bull=2).
# ---------------------------------------------------------------------------


def test_n_segments_counts_contiguous_runs() -> None:
    joined = pd.DataFrame(
        {
            "rebalance_date": pd.bdate_range("2024-01-01", periods=4),
            "period_return": [0.0] * 4,
            "benchmark_return": [0.0] * 4,
            "trend_regime": ["bull", "bull", "bear", "bull"],
            "vol_regime": ["low_vol"] * 4,
        }
    )
    out = regime_excess_summary(joined, by=("trend_regime",))
    rows = out[out["def"] == "trend_regime"].set_index("state")
    assert rows.loc["bull", "n_segments"] == 2
    assert rows.loc["bear", "n_segments"] == 1
    full = out.iloc[0]
    assert full["n_segments"] == 3


# ---------------------------------------------------------------------------
# 10. Misaligned benchmark index -> ValueError (never silent reindex).
# ---------------------------------------------------------------------------


def test_misaligned_benchmark_index_raises() -> None:
    windows = _make_windows(pd.bdate_range("2024-01-01", periods=3), [0.01] * 3)
    bench = pd.Series([0.01] * 3, index=pd.RangeIndex(1, 4))  # shifted

    with pytest.raises(ValueError, match="does not match windows index"):
        join_window_regimes(
            windows,
            _make_regime_df(
                list(pd.bdate_range("2024-01-01", periods=5)),
                ["bull"] * 5,
                ["low_vol"] * 5,
            ),
            bench,
        )


# ---------------------------------------------------------------------------
# Sanity: the summary works on a real join of engine-shaped outputs.
# (align_benchmark_windows import exercised so callers' pipeline shape
# stays compatible with the summary's input contract.)
# ---------------------------------------------------------------------------


def test_end_to_end_with_align_benchmark_windows_shape() -> None:
    dates = pd.bdate_range("2024-01-01", periods=4)
    windows = _make_windows(list(dates), [0.02, 0.01, 0.03, -0.01])
    # _make_windows end dates are rebalance + 5 calendar days; a calendar
    # of daily timestamps covers every endpoint (align drops windows whose
    # endpoints are missing, so the fixture must be complete).
    cal = pd.date_range("2024-01-01", periods=15, freq="D")
    closes = np.linspace(100.0, 103.0, len(cal))
    index_daily = pd.DataFrame({"date": list(cal), "close": closes})
    bench = align_benchmark_windows(index_daily, windows)
    assert int(bench.attrs["dropped"]) == 0

    regime = _make_regime_df(
        list(dates.strftime("%Y%m%d")),
        ["bull", "bull", "bear", "neutral"],
        ["low_vol", "normal_vol", "high_vol", "low_vol"],
    )
    joined = join_window_regimes(windows, regime, bench)
    out = regime_excess_summary(joined, by=("trend_regime", "vol_regime"), freq=12)
    full = out.iloc[0]
    assert full["n_windows"] == 4
    # bench windows from the linear calendar: closes at the 4 endpoints
    expected_bench = bench.to_numpy()
    expected_excess = np.array([0.02, 0.01, 0.03, -0.01]) - expected_bench
    assert full["mean_window_excess"] == pytest.approx(expected_excess.mean())


# ---------------------------------------------------------------------------
# batch14: drawdown regime + extra_label_cols generalisation.
# ---------------------------------------------------------------------------

# Captured from the PRE-extension code at git rev 37d3bf5 by
# `scratch/tmp/batch14_snapshot_capture.py`, before regime_windows.py was
# touched (batch14 R-2 timing discipline). This is the frozen evidence that
# the default path still behaves exactly as it did in batch13.
_PRE_BATCH14_FIXTURE_DATES = pd.to_datetime(
    ["2024-01-01", "2024-01-08", "2024-01-15", "2024-01-22"]
)
_PRE_BATCH14_EXPECTED_COLUMNS = [
    "rebalance_date",
    "end_date",
    "period_return",
    "turnover",
    "n_held",
    "benchmark_return",
    "trend_regime",
    "vol_regime",
]
_PRE_BATCH14_EXPECTED_DTYPES = {
    "rebalance_date": "datetime64[ns]",
    "end_date": "datetime64[ns]",
    "period_return": "float64",
    "turnover": "float64",
    "n_held": "int64",
    "benchmark_return": "float64",
    "trend_regime": "object",
    "vol_regime": "object",
}
_PRE_BATCH14_EXPECTED_INDEX = [0, 1, 3]
_PRE_BATCH14_EXPECTED_DROPPED = 1
_PRE_BATCH14_EXPECTED_ROWS = [
    [
        pd.Timestamp("2024-01-01 00:00:00"),
        pd.Timestamp("2024-01-06 00:00:00"),
        0.02,
        0.4,
        20,
        0.01,
        "bull",
        "low_vol",
    ],
    [
        pd.Timestamp("2024-01-08 00:00:00"),
        pd.Timestamp("2024-01-13 00:00:00"),
        -0.01,
        0.5,
        21,
        0.005,
        "bear",
        "high_vol",
    ],
    [
        pd.Timestamp("2024-01-22 00:00:00"),
        pd.Timestamp("2024-01-27 00:00:00"),
        0.015,
        0.45,
        20,
        0.008,
        "neutral",
        "normal_vol",
    ],
]


def _pre_batch14_fixture() -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    dates = _PRE_BATCH14_FIXTURE_DATES
    windows = pd.DataFrame(
        {
            "rebalance_date": dates,
            "end_date": dates + pd.Timedelta(days=5),
            "period_return": [0.02, -0.01, 0.03, 0.015],
            "turnover": [0.4, 0.5, 0.6, 0.45],
            "n_held": [20, 21, 19, 20],
        }
    )
    benchmark_return = pd.Series([0.01, 0.005, -0.002, 0.008])
    # 2024-01-15 (position 2) is deliberately absent from the regime calendar.
    regime_df = pd.DataFrame(
        {
            "date": [dates[0], dates[1], dates[3]],
            "trend_regime": ["bull", "bear", "neutral"],
            "vol_regime": ["low_vol", "high_vol", "normal_vol"],
        }
    )
    return windows, benchmark_return, regime_df


def test_default_path_matches_pre_batch14_snapshot() -> None:
    """R-2: default extra_label_cols=() reproduces the old output bit for bit.

    The expected frame is a literal snapshot taken from the pre-extension
    code, not a re-derivation, so this test can actually fail if the
    generalisation changed the default path.
    """
    windows, benchmark_return, regime_df = _pre_batch14_fixture()
    joined = join_window_regimes(windows, regime_df, benchmark_return)

    expected = pd.DataFrame(
        _PRE_BATCH14_EXPECTED_ROWS,
        columns=_PRE_BATCH14_EXPECTED_COLUMNS,
        index=pd.Index(_PRE_BATCH14_EXPECTED_INDEX),
    )
    pd.testing.assert_frame_equal(joined, expected, check_dtype=True)
    assert list(joined.columns) == _PRE_BATCH14_EXPECTED_COLUMNS
    assert {c: str(d) for c, d in joined.dtypes.items()} == _PRE_BATCH14_EXPECTED_DTYPES
    assert joined.attrs["dropped"] == _PRE_BATCH14_EXPECTED_DROPPED


def test_extra_label_cols_carry_through_and_widen_the_keep() -> None:
    """Extra columns ride the same notna keep: NaN in them drops the row."""
    windows, benchmark_return, regime_df = _pre_batch14_fixture()
    regime_df = regime_df.copy()
    # Warm-up style NaN on the FIRST regime date only.
    regime_df["drawdown_regime"] = [np.nan, "deep", "mild"]

    joined = join_window_regimes(
        windows, regime_df, benchmark_return, extra_label_cols=["drawdown_regime"]
    )
    assert "drawdown_regime" in joined.columns
    assert len(joined) == 2  # 2024-01-01 dropped by the widened keep
    assert joined.attrs["dropped"] == 2  # plus the missing 2024-01-15
    assert list(joined["drawdown_regime"]) == ["deep", "mild"]
    # The pre-existing labels die with it (N columns live and die together).
    assert list(joined["trend_regime"]) == ["bear", "neutral"]
    assert list(joined["vol_regime"]) == ["high_vol", "normal_vol"]


def test_drawdown_view_keys_are_registered() -> None:
    """The three batch14 views are accepted; an unregistered one still raises."""
    joined = pd.DataFrame(
        {
            "rebalance_date": pd.bdate_range("2024-01-01", periods=3),
            "period_return": [0.02, -0.01, 0.03],
            "benchmark_return": [0.01, 0.0, 0.01],
            "trend_regime": ["bull", "bear", "neutral"],
            "vol_regime": ["low_vol", "high_vol", "normal_vol"],
            "drawdown_regime": ["mild", "correction", "deep"],
        }
    )
    expected_rows = {
        ("drawdown_regime",): 4,  # FULL + 3 drawdown states
        ("trend_regime", "drawdown_regime"): 10,  # FULL + 9 joint cells
        ("vol_regime", "drawdown_regime"): 10,
    }
    for by, n_rows in expected_rows.items():
        out = regime_excess_summary(joined, by=by)
        assert len(out) == n_rows, by
        assert out.iloc[0]["def"] == "FULL"

    with pytest.raises(ValueError, match="not a pre-registered view"):
        regime_excess_summary(
            joined, by=("trend_regime", "vol_regime", "drawdown_regime")
        )


def test_drawdown_joint_grid_hand_calc() -> None:
    """trend×drawdown enumerates 9 frozen cells in trend-major order."""
    combos = [
        ("bull", "mild"),
        ("bull", "correction"),
        ("bull", "deep"),
        ("bear", "mild"),
        ("bear", "correction"),
        ("bear", "deep"),
        ("neutral", "mild"),
        ("neutral", "correction"),
        ("neutral", "deep"),
    ]
    extra = [("bear", "deep")] * 2  # combos has one -> n=3
    all_combos = combos + extra
    joined = pd.DataFrame(
        {
            "rebalance_date": pd.bdate_range("2024-01-01", periods=len(all_combos)),
            "period_return": [0.02] * len(all_combos),
            "benchmark_return": [0.01] * len(all_combos),
            "trend_regime": [c[0] for c in all_combos],
            "vol_regime": ["low_vol"] * len(all_combos),
            "drawdown_regime": [c[1] for c in all_combos],
        }
    )
    out = regime_excess_summary(joined, by=("trend_regime", "drawdown_regime"))
    assert len(out) == 10  # FULL + 9 cells
    rows = out[out["def"] == "trend_regime×drawdown_regime"]
    assert len(rows) == 9
    counts = rows.set_index("state")["n_windows"].to_dict()
    assert counts["bear×deep"] == 3
    assert counts["bull×mild"] == 1
    assert rows["mean_window_excess"].eq(0.01).all()
    # States absent from the data still get a row at n=0.
    empty = regime_excess_summary(
        pd.DataFrame(
            {
                "rebalance_date": pd.bdate_range("2024-01-01", periods=1),
                "period_return": [0.02],
                "benchmark_return": [0.01],
                "trend_regime": ["bull"],
                "vol_regime": ["low_vol"],
                "drawdown_regime": ["mild"],
            }
        ),
        by=("drawdown_regime",),
    )
    dd_rows = empty[empty["def"] == "drawdown_regime"].set_index("state")
    assert dd_rows.loc["mild", "n_windows"] == 1
    assert dd_rows.loc["deep", "n_windows"] == 0
    assert np.isnan(dd_rows.loc["correction", "mean_window_excess"])


def test_calc_drawdown_regime_hand_calc_with_boundaries() -> None:
    """Rolling peak, dd arithmetic, and boundary attribution (deeper wins)."""
    closes = [100.0] * 5 + [85.0, 70.0]
    index_daily = pd.DataFrame(
        {
            "date": pd.bdate_range("2024-01-01", periods=len(closes)),
            "close": closes,
        }
    )
    out = calc_drawdown_regime(index_daily, peak_window=5, min_periods=5)

    # First window_size-1 rows are warm-up: no peak yet, no fake label.
    assert out["drawdown"].iloc[:4].isna().all()
    assert out["drawdown_regime"].iloc[:4].isna().all()

    assert out["drawdown"].iloc[4] == pytest.approx(0.0)
    assert out["drawdown_regime"].iloc[4] == "mild"
    assert out["drawdown"].iloc[5] == pytest.approx(-0.15)
    assert out["drawdown_regime"].iloc[5] == "correction"
    assert out["drawdown"].iloc[6] == pytest.approx(-0.30)
    assert out["drawdown_regime"].iloc[6] == "deep"

    # Boundary equality falls to the deeper state. Use levels that are exact
    # in binary so the comparison is not decided by float noise.
    peak_then_half = pd.DataFrame(
        {
            "date": pd.bdate_range("2024-01-01", periods=4),
            "close": [100.0, 100.0, 50.0, 25.0],
        }
    )
    b = calc_drawdown_regime(
        peak_then_half, peak_window=3, min_periods=1, levels=(0.5, 0.75)
    )
    assert b["drawdown"].iloc[2] == pytest.approx(-0.5)  # 100 still the peak
    assert b["drawdown_regime"].iloc[2] == "correction"  # exactly -50% is not mild
    assert b["drawdown"].iloc[3] == pytest.approx(-0.75)  # 100 still in window
    assert b["drawdown_regime"].iloc[3] == "deep"  # exactly -75% is not correction


def test_calc_drawdown_regime_pit_truncated_matches_full() -> None:
    """t's label uses only data up to t's close (no lookahead)."""
    rng = np.random.default_rng(7)
    n = 400
    close = 100.0 * np.cumprod(1.0 + rng.normal(0.0, 0.01, n))
    index_daily = pd.DataFrame(
        {"date": pd.bdate_range("2024-01-01", periods=n), "close": close}
    )
    full = calc_drawdown_regime(index_daily)

    # t must be past the full window, otherwise the truncated run is still
    # in warm-up and both sides are trivially NaN (batch14 R-4).
    for t_idx in (300, 350, 399):
        truncated = calc_drawdown_regime(index_daily.iloc[: t_idx + 1])
        t = index_daily["date"].iloc[t_idx]
        row_full = full[full["date"] == t].iloc[0]
        row_trunc = truncated[truncated["date"] == t].iloc[0]
        assert row_full["drawdown"] == pytest.approx(row_trunc["drawdown"])
        assert row_full["drawdown_regime"] == row_trunc["drawdown_regime"]


def test_calc_drawdown_regime_peak_is_rolling_not_full_history() -> None:
    """A peak that scrolled out of the window must stop counting."""
    index_daily = pd.DataFrame(
        {
            "date": pd.bdate_range("2024-01-01", periods=6),
            "close": [200.0, 100.0, 100.0, 100.0, 100.0, 100.0],
        }
    )
    out = calc_drawdown_regime(index_daily, peak_window=3, min_periods=3)

    assert out["drawdown"].iloc[2] == pytest.approx(-0.5)  # 200 still in window
    # 200 scrolled out: a full-history peak would still say -50%.
    assert out["drawdown"].iloc[3] == pytest.approx(0.0)
    assert out["drawdown_regime"].iloc[3] == "mild"


def test_calc_drawdown_regime_warmup_is_nan_not_filled() -> None:
    """Default 252-day window leaves the first 251 rows unlabelled."""
    n = 300
    index_daily = pd.DataFrame(
        {
            "date": pd.bdate_range("2024-01-01", periods=n),
            "close": np.linspace(100.0, 120.0, n),
        }
    )
    out = calc_drawdown_regime(index_daily)
    assert out["drawdown"].iloc[:251].isna().all()
    assert out["drawdown_regime"].iloc[:251].isna().all()
    assert out["drawdown"].iloc[251:].notna().all()
    # NaN must never be coerced into a real state by the np.select default.
    assert set(out["drawdown_regime"].iloc[251:]) <= {"mild", "correction", "deep"}


def test_calc_drawdown_regime_levels_reorder_states() -> None:
    """The sensitivity grid levels genuinely move the boundaries."""
    shallow = pd.DataFrame(  # -12% from a 2-day peak
        {"date": pd.bdate_range("2024-01-01", periods=2), "close": [100.0, 88.0]}
    )
    assert (
        calc_drawdown_regime(shallow, peak_window=2, min_periods=2)[
            "drawdown_regime"
        ].iloc[1]
        == "correction"
    )
    assert (
        calc_drawdown_regime(
            shallow, peak_window=2, min_periods=2, levels=(0.15, 0.25)
        )["drawdown_regime"].iloc[1]
        == "mild"
    )

    deeper = pd.DataFrame(  # -22% from the retained 100 peak
        {
            "date": pd.bdate_range("2024-01-01", periods=3),
            "close": [100.0, 100.0, 78.0],
        }
    )
    assert (
        calc_drawdown_regime(deeper, peak_window=3, min_periods=1)[
            "drawdown_regime"
        ].iloc[2]
        == "deep"
    )
    assert (
        calc_drawdown_regime(deeper, peak_window=3, min_periods=1, levels=(0.15, 0.25))[
            "drawdown_regime"
        ].iloc[2]
        == "correction"
    )


def test_calc_drawdown_regime_rejects_bad_params() -> None:
    index_daily = pd.DataFrame(
        {"date": pd.bdate_range("2024-01-01", periods=3), "close": [1.0, 1.0, 1.0]}
    )
    with pytest.raises(ValueError, match="0 < correction < deep < 1"):
        calc_drawdown_regime(index_daily, levels=(0.20, 0.10))
    with pytest.raises(ValueError, match="min_periods"):
        calc_drawdown_regime(index_daily, peak_window=10, min_periods=20)


def test_calc_drawdown_regime_returns_exactly_the_contract_columns() -> None:
    """D2 contract: [date, drawdown, drawdown_regime], and nothing else.

    ``close`` is an input to the peak arithmetic, not part of the return. Every
    other test here selects the columns it needs, so a regression that let the
    input frame ride along would stay invisible — this one pins the whole shape.
    """
    index_daily = pd.DataFrame(
        {
            "date": pd.bdate_range("2024-01-01", periods=8),
            "close": [100.0] * 5 + [85.0, 70.0, 72.0],
            "volume": np.arange(8, dtype=float),  # unrelated input column
        }
    )
    out = calc_drawdown_regime(index_daily, peak_window=5, min_periods=5)

    assert list(out.columns) == ["date", "drawdown", "drawdown_regime"]
    assert len(out) == len(index_daily)
    assert out["date"].equals(index_daily["date"])
    # Neither the raw close nor any unrelated input column leaks through.
    assert "close" not in out.columns
    assert "volume" not in out.columns
    # The caller's frame is untouched: the subset copy is not an alias.
    assert list(index_daily.columns) == ["date", "close", "volume"]


def test_drawdown_regime_end_to_end_join_and_summary() -> None:
    """calc -> merge into regime_df -> join(extra) -> summary, engine shape."""
    n = 320
    dates = pd.bdate_range("2024-01-01", periods=n)
    closes = np.concatenate(
        [np.linspace(100.0, 140.0, 200), np.linspace(138.0, 112.0, n - 200)]
    )
    index_daily = pd.DataFrame({"date": dates, "close": closes})

    dd = calc_drawdown_regime(index_daily)
    base = calc_index_regime(index_daily)[["date", "trend_regime", "vol_regime"]]
    regime_df = base.merge(dd[["date", "drawdown_regime"]], on="date", how="inner")

    reb_dates = list(dates[[260, 280, 300, 310]])
    windows = _make_windows(reb_dates, [0.02, -0.01, 0.03, 0.01])
    bench = pd.Series([0.01, 0.01, 0.01, 0.01], index=windows.index)

    joined = join_window_regimes(
        windows, regime_df, bench, extra_label_cols=["drawdown_regime"]
    )
    assert joined.attrs["dropped"] == 0
    assert joined["drawdown_regime"].notna().all()

    out = regime_excess_summary(
        joined, by=("trend_regime", "drawdown_regime"), label="e2e", freq=12
    )
    assert out.iloc[0]["n_windows"] == 4
    assert out.iloc[0]["def"] == "FULL"
    for state in out[out["def"] == "trend_regime×drawdown_regime"]["state"]:
        assert isinstance(state, str) and "×" in state
