"""Tests for orthogonalize_greedy — synthetic redundant/unique factor panel."""

import numpy as np
import pandas as pd
import pytest

from selene_space_ashare.factor_eval import orthogonalize_greedy
from selene_space_ashare.factor_report import generate_factor_report


@pytest.fixture
def orthogonal_panel() -> pd.DataFrame:
    """20 days x 50 stocks. FA drives returns; FB is FA-redundant; FC unique."""
    rng = np.random.default_rng(42)
    dates = pd.date_range("2025-01-01", periods=20, freq="B")
    symbols = [f"{600000 + i}" for i in range(50)]
    rows = []
    for d in dates:
        fa = rng.normal(size=50)
        fb = fa * 0.9 + rng.normal(scale=0.3, size=50)
        fc = rng.normal(size=50)
        fwd = 1.0 * fa + 0.4 * fc + rng.normal(scale=0.3, size=50)
        rows.append(
            pd.DataFrame(
                {
                    "date": d,
                    "symbol": symbols,
                    "FA": fa,
                    "FB": fb,
                    "FC": fc,
                    "fwd_5d": fwd,
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


def test_structure_keys(orthogonal_panel):
    result = orthogonalize_greedy(
        orthogonal_panel, ["FA", "FB", "FC"], forward_col="fwd_5d"
    )
    assert set(result.keys()) == {"kept", "dropped", "order", "params"}
    assert set(result["params"].keys()) == {"min_abs_t", "max_factors"}
    assert set(result["order"]) == {"FA", "FB", "FC"}
    for row in result["kept"]:
        assert set(row.keys()) == {
            "factor",
            "IC_mean",
            "IC_std",
            "IR",
            "IC_pos_rate",
            "t_stat",
        }
    for row in result["dropped"]:
        assert set(row.keys()) == {"factor", "reason"}


def test_first_factor_kept_stats_match_raw(orthogonal_panel):
    from selene_space_ashare.factor_eval import factor_evaluation_report

    raw = factor_evaluation_report(
        orthogonal_panel, ["FA", "FB", "FC"], forward_col="fwd_5d"
    )
    raw_by_factor = {r["factor"]: r for _, r in raw.iterrows()}

    result = orthogonalize_greedy(
        orthogonal_panel, ["FA", "FB", "FC"], forward_col="fwd_5d"
    )
    assert result["kept"], "no factor kept"
    first = result["kept"][0]
    assert first["factor"] == "FA"  # highest raw |t_stat|
    for key in ("IC_mean", "IC_std", "IR", "IC_pos_rate", "t_stat"):
        # kept stats are round(4) — compare against rounded raw
        assert first[key] == pytest.approx(
            round(float(raw_by_factor["FA"][key]), 4), abs=1e-8
        )


def test_redundant_factor_dropped_with_reason(orthogonal_panel):
    result = orthogonalize_greedy(
        orthogonal_panel, ["FA", "FB", "FC"], forward_col="fwd_5d"
    )
    kept_names = [r["factor"] for r in result["kept"]]
    dropped_names = [r["factor"] for r in result["dropped"]]
    assert "FB" in dropped_names
    assert "FB" not in kept_names
    fb_reason = next(r["reason"] for r in result["dropped"] if r["factor"] == "FB")
    assert "FA" in fb_reason  # max-corr kept factor named in reason
    assert "orthogonal t=" in fb_reason


def test_unique_factor_kept_and_significant(orthogonal_panel):
    result = orthogonalize_greedy(
        orthogonal_panel, ["FA", "FB", "FC"], forward_col="fwd_5d"
    )
    kept = {r["factor"]: r for r in result["kept"]}
    # FC drives returns independently of FA → survives orthogonalization
    assert "FC" in kept
    assert abs(kept["FC"]["t_stat"]) >= 2.0
    assert "FA" in kept


def test_max_factors_cap(orthogonal_panel):
    result = orthogonalize_greedy(
        orthogonal_panel,
        ["FA", "FB", "FC"],
        forward_col="fwd_5d",
        max_factors=1,
    )
    kept_names = [r["factor"] for r in result["kept"]]
    assert len(kept_names) == 1
    assert kept_names == ["FA"]
    dropped_names = [r["factor"] for r in result["dropped"]]
    assert "FB" in dropped_names or "FC" in dropped_names
    for row in result["dropped"]:
        if row["factor"] != "FB":  # FB may drop for corr before cap hits
            assert (
                "max_factors reached" in row["reason"]
                or "orthogonal t=" in row["reason"]
            )


def test_report_orthogonal_none_when_lt3_factors():
    rng = np.random.default_rng(42)
    dates = pd.date_range("2025-01-01", periods=15, freq="B")
    symbols = [f"{600000 + i}" for i in range(12)]
    rows = []
    for d in dates:
        a = rng.normal(size=12)
        fwd = 0.5 * a + rng.normal(scale=0.2, size=12)
        rows.append(
            pd.DataFrame(
                {
                    "date": d,
                    "symbol": symbols,
                    "A": a,
                    "B": rng.normal(size=12),
                    "fwd_5d": fwd,
                }
            )
        )
    panel = pd.concat(rows, ignore_index=True)
    report = generate_factor_report(panel, factors=["A", "B"], forward_col="fwd_5d")
    assert report["orthogonal"] is None
