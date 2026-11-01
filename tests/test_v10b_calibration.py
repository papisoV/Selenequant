"""Calibration tests for v10b (plan P3): synthetic distributions with known
trigger rates must recover thresholds hitting the target within ±0.5% absolute
(plan acceptance) and classify unreachable targets as D2 hard-stop.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from v10b_quantile_kernel_validation import calibrate_thresholds


def test_uniform_distribution_recovers_target_rates() -> None:
    # Uniform scores in [0,1]: threshold th gives trigger rate 1-th exactly.
    rng = np.random.default_rng(42)
    scores = rng.uniform(0.0, 1.0, 50_000)

    res = calibrate_thresholds(scores, target_buy_rate=0.30, target_sell_rate=0.30)
    assert res["status"] == "calibrated"
    assert abs(res["buy_achieved_rate"] - 0.30) <= 0.005
    assert abs(res["sell_achieved_rate"] - 0.30) <= 0.005
    assert res["buy_within_tolerance"] and res["sell_within_tolerance"]


def test_skewed_distribution_recovers_tail_rates() -> None:
    # Right-skewed: low sell-th should capture a small tail.
    rng = np.random.default_rng(7)
    scores = rng.beta(2.0, 5.0, 50_000)

    res = calibrate_thresholds(scores, target_buy_rate=0.15, target_sell_rate=0.10)
    assert res["status"] == "calibrated"
    assert abs(res["buy_achieved_rate"] - 0.15) <= 0.005
    assert abs(res["sell_achieved_rate"] - 0.10) <= 0.005


def test_degenerate_distribution_halts() -> None:
    # All-identical scores: no grid, D2 hard-stop.
    scores = np.full(5_000, 0.5)
    res = calibrate_thresholds(scores, 0.3, 0.3)
    assert res["status"] == "halted"
    assert "degenerate" in res["reason"]


def test_unreachable_target_halts() -> None:
    # Discrete two-level distribution: degenerate-grid hard-stop fires before
    # any threshold sweep (D2 forbids extrapolating between observed levels).
    scores = np.concatenate([np.full(4_000, 0.2), np.full(6_000, 0.8)])
    res = calibrate_thresholds(scores, target_buy_rate=0.001, target_sell_rate=0.001)
    assert res["status"] == "halted"
    assert res["reason"]


def test_tolerance_boundary_recorded() -> None:
    rng = np.random.default_rng(3)
    scores = rng.uniform(0.0, 1.0, 20_000)
    # Small grid (few distinct levels) + zero tolerance: no grid point can
    # hit an arbitrary target exactly → guaranteed hard-stop path exercised.
    scores = np.round(scores * 10) / 10  # 11 distinct levels
    res = calibrate_thresholds(scores, 0.3137, 0.2921, tolerance=0.0)
    assert res["status"] == "halted"
    assert res["buy_within_tolerance"] is False or res["sell_within_tolerance"] is False
