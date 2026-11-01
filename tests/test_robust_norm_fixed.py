"""Tests for scripts/robust_norm_fixed.py (robust-norm fix experiment, plan P2).

Load-bearing test 1: no-future-data (prefix invariance) — the rolling
normalization of a series prefix must equal the prefix of the full-series
normalization, past the min_periods warmup. This is the property the
full-sample _robust_norm violates.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from robust_norm_fixed import (
    MIN_PERIODS,
    WINDOW,
    patched_robust_norm,
    rolling_robust_norm,
)


def _series(n: int, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(0, 1, n).cumsum())


def test_prefix_invariance_no_future_data() -> None:
    # norm(series[:k]) == norm(series)[:k] for k past warmup+2 — the exact
    # property full-sample _robust_norm violates (look-ahead).
    full = _series(1200)
    norm_full = rolling_robust_norm(full)
    k = 900
    norm_prefix = rolling_robust_norm(full.iloc[:k])
    tail = slice(MIN_PERIODS + 2, k)
    np.testing.assert_allclose(
        norm_prefix.values[tail], norm_full.values[tail], atol=1e-12
    )


def test_full_sample_violates_prefix_invariance() -> None:
    # Sanity check on the test itself: full-sample median normalization
    # must FAIL the same property (proves test 1 can go red).
    full = _series(1200)

    def full_sample_norm(s: pd.Series) -> pd.Series:
        median = s.median()
        mad = (s - median).abs().median() + 1e-6
        return ((s - median) / mad).clip(-5.0, 5.0)

    norm_full = full_sample_norm(full)
    norm_prefix = full_sample_norm(full.iloc[:900])
    tail = slice(MIN_PERIODS + 2, 900)
    assert not np.allclose(norm_prefix.values[tail], norm_full.values[tail], atol=1e-12)


def test_warmup_fills_neutral_zero() -> None:
    s = _series(600)
    norm = rolling_robust_norm(s)
    assert int(norm.iloc[: MIN_PERIODS - 1].eq(0.0).sum()) == MIN_PERIODS - 1
    assert int(norm.iloc[WINDOW:].ne(0.0).all()) == 1  # fully warmed region is live


def test_clip_bounds_hold() -> None:
    # One extreme outlier must be clipped inside ±5.
    s = _series(3000)
    s.iloc[-1] = s.iloc[-1] + 1e6
    norm = rolling_robust_norm(s)
    assert float(norm.abs().max()) <= 5.0


def test_constant_series_no_nan_no_explosion() -> None:
    # MAD → 0 stretch: 1e-6 divisor keeps values finite (clip bounds hold).
    s = pd.Series([3.0] * 500)
    norm = rolling_robust_norm(s)
    assert bool(np.isfinite(norm).all())
    assert float(norm.abs().max()) <= 5.0


def test_patch_context_manager_restores() -> None:
    # Since the rolling fix landed in pandas_bridge._robust_norm (mainline),
    # the patch is now rolling->rolling: output is identical and the test's
    # real job is just to verify the context manager restores cleanly.
    import selene_space_alpha.factors.pandas_bridge as pb

    df = pd.DataFrame({"TF_close": _series(400), "close": _series(400)})
    original = pb._robust_norm
    v_before = pb.compute_tf_trend(df)

    with patched_robust_norm():
        assert pb._robust_norm is not original
        v_patched = pb.compute_tf_trend(df)

    assert pb._robust_norm is original
    v_after = pb.compute_tf_trend(df)

    pd.testing.assert_series_equal(v_before, v_patched)
    # ...and restoration is byte-identical.
    pd.testing.assert_series_equal(v_before, v_after)


def test_patch_propagates_through_vm_precompute() -> None:
    # End-to-end wiring: the V52 precompute path must run on the trailing
    # (no-look-ahead) normalization. Since the rolling fix landed mainline,
    # the patch no longer changes output — instead assert the mainline
    # property directly: precompute of a series prefix equals the prefix of
    # precompute of the full series (past the min_periods warmup).
    sys.path.insert(0, str(PROJECT_ROOT / "packages" / "alpha-core"))
    sys.path.insert(0, str(PROJECT_ROOT / "packages" / "strategy-engine"))
    from selene_space_alpha.real_data_loader import CEX_FEATURE_NAMES_V8
    from selene_space_alpha.vocab import FormulaVocab
    from selene_space_alpha.vm import PandasStackVM
    from selene_space_alpha.ops.ops_pandas import OPS_CONFIG_PANDAS
    from optuna_formula_search_v4 import precompute_features

    v4_op_names = tuple(cfg[0] for cfg in OPS_CONFIG_PANDAS if cfg[0] != "GATE")
    vocab = FormulaVocab(
        feature_names=tuple(CEX_FEATURE_NAMES_V8), operator_names=v4_op_names
    )
    vm = PandasStackVM(vocab)

    n = 400
    rng = np.random.default_rng(7)
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, n))), index=idx)
    df = pd.DataFrame(
        {
            "close": close,
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close * 1.01,
            "low": close * 0.99,
            "volume": rng.uniform(1, 10, n),
            "TF_close": close,
            "TF_open": close.shift(1).fillna(close.iloc[0]),
            "TF_high": close * 1.01,
            "TF_low": close * 0.99,
        }
    )

    feats_full = precompute_features(df, vocab, vm)
    feats_prefix = precompute_features(df.iloc[:300], vocab, vm)

    # Warmup is 288 bars: past bar 290, prefix values must equal full values
    # (identical output on the overlap proves the pipeline never peeks ahead).
    diffs = sum(
        1
        for key in feats_full
        if not np.allclose(
            feats_prefix[key].fillna(0).iloc[290:300],
            feats_full[key].fillna(0).iloc[290:300],
            atol=1e-12,
        )
    )
    assert diffs == 0
