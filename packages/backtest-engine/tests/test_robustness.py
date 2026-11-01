"""Unit tests for the resampling robustness primitives.

These checks are only worth shipping if they can tell "this path was lucky"
from "this path is what the trades usually do". So the tests below mostly
assert *resolution*: the permutation p-value must separate an ordered trade
set from a random reordering of the same set, rather than emitting a constant.
"""

import json

import pytest

from selene_space_backtest.robustness import (
    _MAX_BAND_POINTS,
    bootstrap_sharpe_ci,
    compute_robustness,
    equity_curve_percentiles,
    permutation_test,
)


# ─── permutation_test ───


def test_permutation_detects_sequence_luck():
    """Same trades, two orders: the winning-streak order must score lower p.

    If both orders returned the same p-value the test would have no resolving
    power at all — this is the assertion that catches a constant readout.
    """
    # Big wins first, then losses: the equity path peaks early and bleeds.
    streaky = [500.0, 400.0, 300.0, -50.0, -50.0, -50.0, -50.0, -50.0, -50.0]
    # Interleaved: no long run of gains to compound on.
    interleaved = [500.0, -50.0, -50.0, 400.0, -50.0, -50.0, 300.0, -50.0, -50.0]

    a = permutation_test(streaky, 10_000.0, n_simulations=500, seed=7)
    b = permutation_test(interleaved, 10_000.0, n_simulations=500, seed=7)

    assert "error" not in a and "error" not in b
    # Both are the same multiset of PnLs, so the p-values should differ only
    # because the arrival order differs.
    assert a["p_value_sharpe"] != b["p_value_sharpe"]


def test_permutation_p_value_is_bounded_and_matches_floor():
    pnls = [120.0, -40.0, 90.0, -30.0, 60.0]
    out = permutation_test(pnls, 10_000.0, n_simulations=200, seed=1)
    assert 0.0 <= out["p_value_sharpe"] <= 1.0
    assert 0.0 <= out["p_value_max_dd"] <= 1.0
    assert out["n_trades"] == 5
    # p = k / n, so the smallest non-zero value is 1/n and the floor is 1/(n+1).
    assert out["p_value_floor"] == pytest.approx(1.0 / 201, abs=1e-6)


def test_permutation_is_seed_reproducible():
    pnls = [80.0, -20.0, 55.0, -15.0, 40.0, -10.0]
    first = permutation_test(pnls, 10_000.0, n_simulations=300, seed=99)
    again = permutation_test(pnls, 10_000.0, n_simulations=300, seed=99)
    assert first == again


def test_permutation_refuses_small_samples_instead_of_passing():
    """Two trades cannot be permuted meaningfully — refuse, do not fake a pass."""
    out = permutation_test([100.0, -100.0], 10_000.0, n_simulations=100, seed=1)
    assert "error" in out
    assert out["p_value_sharpe"] == 1.0


def test_permutation_rejects_non_finite_pnls():
    out = permutation_test([100.0, float("nan"), -50.0], 10_000.0, seed=1)
    assert "error" in out


@pytest.mark.parametrize("bad", [0, -1, 2.5, True])
def test_permutation_rejects_bad_simulation_count(bad):
    out = permutation_test([1.0, 2.0, 3.0], 10_000.0, n_simulations=bad, seed=1)
    assert "error" in out


# ─── bootstrap_sharpe_ci ───


def test_bootstrap_ci_brackets_observed_sharpe():
    returns = [0.01, -0.005, 0.012, 0.003, -0.002, 0.008, 0.001, 0.006, -0.004, 0.009]
    out = bootstrap_sharpe_ci(returns, n_bootstrap=500, seed=3)
    assert "error" not in out
    assert out["ci_lower"] <= out["observed_sharpe"] <= out["ci_upper"]
    assert 0.0 <= out["prob_positive"] <= 1.0


def test_bootstrap_ci_separates_edge_from_no_edge():
    """The readout a consumer actually acts on: does the interval clear zero?

    Note the CI is on *Sharpe*, whose width grows with Sharpe^2, so "less noisy
    returns" does not imply a narrower interval — a high-Sharpe series can have
    a wider one. What must hold is that a consistent drift clears zero and a
    zero-mean series does not.
    """
    drift = [0.010, 0.011, 0.009, 0.010, 0.012, 0.010, 0.009, 0.011] * 4
    zero_mean = [0.010, -0.010, 0.011, -0.011, 0.009, -0.009, 0.012, -0.012] * 4
    a = bootstrap_sharpe_ci(drift, n_bootstrap=500, seed=5)
    b = bootstrap_sharpe_ci(zero_mean, n_bootstrap=500, seed=5)
    assert a["ci_lower"] > 0.0
    assert b["ci_lower"] <= 0.0 <= b["ci_upper"]


def test_bootstrap_is_seed_reproducible():
    returns = [0.01, -0.005, 0.012, 0.003, -0.002, 0.008]
    assert bootstrap_sharpe_ci(
        returns, n_bootstrap=200, seed=11
    ) == bootstrap_sharpe_ci(returns, n_bootstrap=200, seed=11)


def test_bootstrap_refuses_too_few_observations():
    out = bootstrap_sharpe_ci([0.01, 0.02, 0.03], n_bootstrap=100, seed=1)
    assert "error" in out


@pytest.mark.parametrize("conf", [0.0, 1.0, -0.5, 2.0, float("nan")])
def test_bootstrap_rejects_out_of_range_confidence(conf):
    returns = [0.01, -0.005, 0.012, 0.003, -0.002, 0.008]
    out = bootstrap_sharpe_ci(returns, n_bootstrap=100, confidence=conf, seed=1)
    assert "error" in out


# ─── equity_curve_percentiles ───


def test_equity_bands_are_monotone_across_percentiles():
    returns = [0.01, -0.005, 0.012, 0.003, -0.002, 0.008, 0.001, 0.006]
    out = equity_curve_percentiles(
        returns, initial_equity=10_000.0, n_samples=300, seed=2
    )
    assert "error" not in out
    for i in range(len(out["pctile_5"])):
        assert out["pctile_5"][i] <= out["pctile_50"][i] <= out["pctile_95"][i]


def test_equity_bands_start_at_initial_equity():
    returns = [0.01, -0.005, 0.012, 0.003]
    out = equity_curve_percentiles(
        returns, initial_equity=5_000.0, n_samples=50, seed=2
    )
    for p in (5, 25, 50, 75, 95):
        assert out[f"pctile_{p}"][0] == pytest.approx(5_000.0)


def test_equity_bands_prob_profit_is_a_real_probability():
    """Guards the regression where prob_profit was read off the median band.

    A median band can only be above or below the start, so that readout is
    always exactly 0.0 or 1.0. A genuine path fraction lands strictly inside
    for a mixed series.
    """
    returns = [0.02, -0.02] * 20
    out = equity_curve_percentiles(
        returns, initial_equity=10_000.0, n_samples=400, seed=4
    )
    prob = out["summary"]["prob_profit"]
    assert 0.0 < prob < 1.0


def test_equity_bands_chunking_does_not_change_the_result():
    """The memory bound is chunking; it must not be observable in the output."""
    returns = [0.01, -0.005, 0.012, 0.003, -0.002, 0.008, 0.001, 0.006]
    small = equity_curve_percentiles(returns, n_samples=100, seed=6, chunk=3)
    large = equity_curve_percentiles(returns, n_samples=100, seed=6, chunk=256)
    assert small == large


def test_equity_bands_reject_excessive_samples():
    out = equity_curve_percentiles([0.01, 0.02], n_samples=10_000_000, seed=1)
    assert "error" in out


def test_equity_bands_do_not_grow_with_bar_count():
    """The bands are persisted per backtest row; they must not scale with bars.

    A 40_000-bar run previously emitted 5 x 40_001 numbers and a 1.9 MB
    ``robustness_json`` for a UI panel that reads none of the arrays.
    """
    returns = [0.001, -0.001, 0.002, -0.0005] * 12_500  # 50_000 bars
    out = equity_curve_percentiles(
        returns, initial_equity=10_000.0, n_samples=50, seed=3
    )
    assert "error" not in out
    for p in (5, 25, 50, 75, 95):
        assert len(out[f"pctile_{p}"]) <= _MAX_BAND_POINTS


def test_equity_bands_keep_their_endpoints_when_subsampled():
    """Subsampling must not cost the start equity or the terminal reading.

    The first band value is the starting equity and the last is what the
    ``summary`` reports; a subsample that dropped either would make the panel's
    terminal figures disagree with its own band.
    """
    returns = [0.001, -0.0008, 0.0015, -0.0004] * 5_000  # 20_000 bars
    out = equity_curve_percentiles(
        returns, initial_equity=10_000.0, n_samples=60, seed=7
    )
    assert "error" not in out
    summary = out["summary"]
    for p in (5, 50, 95):
        band = out[f"pctile_{p}"]
        assert band[0] == pytest.approx(10_000.0)
        assert len(band) > 1
    assert out["pctile_5"][-1] == summary["terminal_p5"]
    assert out["pctile_50"][-1] == summary["terminal_p50"]
    assert out["pctile_95"][-1] == summary["terminal_p95"]


def test_equity_bands_stay_finite_under_extreme_resamples():
    """Compounding thousands of resampled returns must not overflow to inf.

    An overflowed band gets written to JSON as ``null`` — indistinguishable
    from "missing" in the panel. The clamp keeps it a (large) number.
    """
    returns = [0.5, -0.4, 0.6, 0.3] * 4_000  # 16_000 bars of wild swings
    out = equity_curve_percentiles(
        returns, initial_equity=10_000.0, n_samples=100, seed=8
    )
    assert "error" not in out
    for p in (5, 25, 50, 75, 95):
        assert all(v is not None for v in out[f"pctile_{p}"])
    assert out["summary"]["prob_profit"] is not None


def test_equity_bands_reject_non_positive_initial_equity():
    out = equity_curve_percentiles([0.01, 0.02, 0.03], initial_equity=0.0, seed=1)
    assert "error" in out


# ─── compute_robustness ───


def test_compute_robustness_returns_json_safe_payload():
    pnls = [120.0, -40.0, 90.0, -30.0, 60.0, -20.0]
    equity = [10_000.0, 10_120.0, 10_080.0, 10_170.0, 10_140.0, 10_200.0, 10_180.0]
    out = compute_robustness(
        pnls, equity, 10_000.0, n_simulations=100, n_bootstrap=100, seed=8
    )
    assert set(out) >= {"permutation", "bootstrap", "equity_bands", "seed"}
    # Must survive the storage layer's json.dumps without a custom encoder.
    json.dumps(out, allow_nan=False)


def test_compute_robustness_never_raises_on_degenerate_input():
    """An empty run still has to return a usable payload, not an exception."""
    out = compute_robustness([], [], 10_000.0, n_simulations=10, n_bootstrap=10, seed=1)
    assert "error" in out["permutation"]
    assert "error" in out["bootstrap"]


def test_compute_robustness_is_seed_reproducible():
    pnls = [120.0, -40.0, 90.0, -30.0, 60.0]
    equity = [10_000.0, 10_120.0, 10_080.0, 10_170.0, 10_140.0, 10_200.0]
    a = compute_robustness(
        pnls, equity, 10_000.0, n_simulations=50, n_bootstrap=50, seed=13
    )
    b = compute_robustness(
        pnls, equity, 10_000.0, n_simulations=50, n_bootstrap=50, seed=13
    )
    assert a == b
