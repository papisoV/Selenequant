"""Unit tests for scripts/scoring_kernels.py (quantile-kernel experiment, plan P2).

Golden test 1 is load-bearing: the zscore branch must be behaviorally
byte-equivalent to v9_exit_opt.compute_sigmoid_scores.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from scoring_kernels import compute_sigmoid_scores, compute_signals


# ---------------------------------------------------------------------------
# VM-free harness: both kernels' only consumer of the VM is the raw score
# series, so tests drive the normalization math through a stub VM + tiny vocab
# stand-in instead of a real formula run.
# ---------------------------------------------------------------------------


class _StubVM:
    """Returns the precomputed raw score series passed via features slot 0."""

    def __init__(self, raw: pd.Series) -> None:
        self._raw = raw

    def execute(self, tokens, df, precomputed_features=None):
        if self._raw is None:
            return None
        return self._raw


class _StubVocab:
    token_names: list[str] = []
    operator_names: list[str] = []
    operator_offset: int = 0
    feature_names: list[str] = []


def _run_kernel(raw: pd.Series, kernel: str, window: int = 60, min_periods: int = 10):
    df = pd.DataFrame({"close": np.arange(len(raw), dtype=float)})
    stub_vm = _StubVM(raw)
    return compute_sigmoid_scores(
        [0],
        _StubVocab(),
        df,
        {0: raw},
        kernel=kernel,
        window=window,
        min_periods=min_periods,
        vm=stub_vm,
    )


# ---------------------------------------------------------------------------
# 1. Golden equivalence: zscore branch math == v9:336-339 reference math
#    (v9's function builds its own VM internally, so equivalence is asserted
#    against an inline transcription of v9:336-339 applied to identical raw
#    scores; the production wiring itself is proven by the Phase-3
#    cross-harness gate where v10b --kernel zscore must exactly match v10.)
# ---------------------------------------------------------------------------


def test_golden_equivalence_with_v9_math() -> None:
    rng = np.random.default_rng(42)
    for _ in range(5):
        n = int(rng.integers(80, 400))
        raw = pd.Series(rng.normal(0, 1, n).cumsum())
        df = pd.DataFrame({"close": np.arange(n, dtype=float)})

        ours = compute_sigmoid_scores(
            [0], _StubVocab(), df, {0: raw}, kernel="zscore", vm=_StubVM(raw)
        )

        # Inline transcription of v9_exit_opt.py:336-339
        scores = raw.values.astype(float)
        rolling_mean = pd.Series(scores).rolling(60, min_periods=10).mean().values
        rolling_std = pd.Series(scores).rolling(60, min_periods=10).std().values + 1e-8
        score_norm = np.nan_to_num((scores - rolling_mean) / rolling_std, 0.0).clip(
            -10, 10
        )
        reference = 1.0 / (1.0 + np.exp(-score_norm))

        np.testing.assert_allclose(ours, reference, rtol=0, atol=1e-12)


# ---------------------------------------------------------------------------
# 2. Quantile bounds: [0,1], warmup bars == 0.5
# ---------------------------------------------------------------------------


def test_quantile_bounds_and_warmup() -> None:
    rng = np.random.default_rng(7)
    raw = pd.Series(rng.normal(0, 1, 300).cumsum())
    out = _run_kernel(raw, "quantile", window=60, min_periods=10)

    assert out.shape == (300,)
    assert np.all(out >= 0.0) and np.all(out <= 1.0)
    # bars before min_periods are warmup -> neutral 0.5
    assert np.all(out[:9] == 0.5)


# ---------------------------------------------------------------------------
# 3. Monotone invariance: rank branch invariant to monotone transforms
# ---------------------------------------------------------------------------


def test_quantile_monotone_invariance() -> None:
    rng = np.random.default_rng(11)
    raw = pd.Series(rng.normal(0, 1, 200).cumsum())

    a = _run_kernel(raw, "quantile")
    b = _run_kernel(np.exp(raw), "quantile")  # strictly increasing
    c = _run_kernel(raw * 3 + 17.5, "quantile")  # affine increasing

    np.testing.assert_allclose(a, b, atol=1e-12)
    np.testing.assert_allclose(a, c, atol=1e-12)


# ---------------------------------------------------------------------------
# 4. Constant raw-score stretch → 0.5 (degenerate-case diagnostic)
# ---------------------------------------------------------------------------


def test_quantile_constant_series_flatline() -> None:
    # Degeneracy diagnostic (plan pre-mortem #2): a constant raw-score stretch
    # produces ZERO output variance — pandas average-rank of tied values gives
    # (n+1)/2n ≈ 0.508 (window 60), i.e. near-neutral and frozen. No signal
    # can cross a realistic threshold during flatline.
    raw = pd.Series([5.0] * 200)
    out = _run_kernel(raw, "quantile")
    stretch = out[60:]  # post-warmup constant region
    assert np.all(stretch == stretch[0])  # frozen: zero variance
    assert abs(stretch[0] - 0.5) < 0.02  # near-neutral (average rank of 60 ties)


# ---------------------------------------------------------------------------
# 5. NaN raw scores → 0.5 (matches zscore branch's nan_to_num(z, 0) → sigmoid 0.5)
# ---------------------------------------------------------------------------


def test_quantile_nan_scores_neutral() -> None:
    vals = np.arange(100, dtype=float)
    vals[40:60] = np.nan
    raw = pd.Series(vals)
    out = _run_kernel(raw, "quantile")

    # NaN inputs propagate through rank as NaN → fillna(0.5)
    assert np.all(out[40:60] == 0.5)
    assert np.all(np.isfinite(out))


# ---------------------------------------------------------------------------
# Signals path: quantile compute_signals mirrors v7 ±1/0 mapping
# ---------------------------------------------------------------------------


def test_compute_signals_threshold_mapping() -> None:
    rng = np.random.default_rng(3)
    raw = pd.Series(rng.normal(0, 1, 300).cumsum())
    df = pd.DataFrame({"close": np.arange(300, dtype=float)})
    stub_vm = _StubVM(raw)

    sig = compute_signals(
        [0],
        _StubVocab(),
        df,
        {0: raw},
        buy_th=0.7,
        sell_th=0.3,
        kernel="quantile",
        vm=stub_vm,
    )
    scores = compute_sigmoid_scores(
        [0], _StubVocab(), df, {0: raw}, kernel="quantile", vm=stub_vm
    )

    expected = np.where(scores > 0.7, 1, np.where(scores < 0.3, -1, 0))
    np.testing.assert_array_equal(sig, expected)
    assert set(np.unique(sig)) <= {-1, 0, 1}


def test_unknown_kernel_rejected() -> None:
    raw = pd.Series(np.arange(50, dtype=float))
    df = pd.DataFrame({"close": np.arange(50, dtype=float)})
    with pytest.raises(ValueError, match="unknown kernel"):
        compute_sigmoid_scores(
            [0], _StubVocab(), df, {0: raw}, kernel="bogus", vm=_StubVM(raw)
        )


def test_none_score_series_returns_neutral() -> None:
    # v9/v7 contract: VM returning None → all-neutral 0.5 scores
    df = pd.DataFrame({"close": np.arange(30, dtype=float)})
    out = compute_sigmoid_scores(
        [0],
        _StubVocab(),
        df,
        {0: pd.Series(dtype=float)},
        kernel="quantile",
        vm=_StubVM(None),
    )
    assert out.shape == (30,)
    assert np.all(out == 0.5)
