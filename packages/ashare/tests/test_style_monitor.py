"""Tests for style_monitor — spread sign convention + NaN safety."""

import numpy as np
import pandas as pd
import pytest

from selene_space_ashare.style_monitor import (
    full_style_report,
    style_regime_summary,
    style_spread_by_day,
    style_spread_series,
)


@pytest.fixture
def size_monotone_panel() -> pd.DataFrame:
    """10 days x 9 stocks. High SIZE (big cap) earns low fwd returns →
    small-cap wins → SIZE spread (direction=-1) must be POSITIVE."""
    rng = np.random.default_rng(7)
    sizes = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0])
    fwd = -sizes * 0.001  # bigger size, worse return
    rows = []
    for d in pd.date_range("2025-01-01", periods=10, freq="B"):
        rows.append(
            pd.DataFrame(
                {
                    "date": d,
                    "symbol": [f"S{i}" for i in range(9)],
                    "SIZE": sizes + rng.normal(scale=0.01, size=9),
                    "fwd_5d": fwd + rng.normal(scale=1e-5, size=9),
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


def test_size_spread_positive_when_small_wins(size_monotone_panel):
    spread = style_spread_series(size_monotone_panel, "SIZE", direction=-1)
    assert spread.iloc[0] > 0


def test_value_spread_positive_when_high_wins(size_monotone_panel):
    # Same data as BP with direction=1: high quantile (high col = low return
    # here) loses → spread negative
    panel = size_monotone_panel.rename(columns={"SIZE": "BP"})
    spread = style_spread_series(panel, "BP", direction=1)
    assert spread.iloc[0] < 0


def test_by_day_series_length(size_monotone_panel):
    by_day = style_spread_by_day(size_monotone_panel, "SIZE", direction=-1)
    assert len(by_day) == 10
    assert (by_day > 0).all()


def test_regime_summary_leading(size_monotone_panel):
    by_day = style_spread_by_day(size_monotone_panel, "SIZE", direction=-1)
    summary = style_regime_summary(by_day, lookback=20)
    assert summary["regime"] == "leading"
    assert summary["cum_spread"] > 0
    assert summary["n_days"] == 10


def test_regime_summary_empty():
    empty = pd.Series(dtype=float)
    summary = style_regime_summary(empty)
    assert summary["regime"] == "no_data"
    assert summary["cum_spread"] is None


def test_full_report_nan_safe(size_monotone_panel):
    panel = size_monotone_panel.copy()
    panel["BP"] = np.nan  # style factor entirely missing
    panel["YOY_NI"] = np.nan
    report = full_style_report(panel)
    assert report["styles"]["SIZE"]["regime"] == "leading"
    assert report["styles"]["BP"]["regime"] == "factor_absent"
    assert report["styles"]["YOY_NI"]["regime"] == "factor_absent"
