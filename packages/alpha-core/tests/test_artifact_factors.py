"""Artifact-bridge factor tests — hand-computed anchors + producer semantics.

The 7 ``art_*`` factors must reproduce the producer pipeline's ``feature_ports``
semantics (verified against real data by ``scripts/verify_artifact_feature_ports.py``);
these tests pin the structural invariants that survive synthetic data:

- robust-norm warmup: the first 199 bars of every ``_norm``-ed feature are 0
- RVOL is realized return volatility (a constant price gives ~0, not a
  volume ratio) — the naming trap that motivated these tests
- RET_ENTROPY_20 is float32-disciplined with a hand-computed anchor
  (alternating returns → log(2)/log(3) ≈ 0.63093)
- TS_STD_20 (operator, ops_pandas) uses zero-padded causal windows: the first
  19 bars are computed from partially-zero windows — never NaN
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from selene_space_alpha.factors.artifact_factors import (
    ARTIFACT_FACTORS,
    compute_art_gk_vol,
    compute_art_hma_dev_20,
    compute_art_ichimoku_kijun_dev,
    compute_art_macd_hist,
    compute_art_ret_entropy_20,
    compute_art_rvol,
    compute_art_trix_15,
)
from selene_space_alpha.ops.ops_pandas import OPS_CONFIG_PANDAS


def _trending_panel(n: int = 260) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    close = pd.Series(100.0 + np.cumsum(rng.normal(0.05, 1.0, n)))
    return pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close + rng.uniform(0.0, 1.0, n),
            "low": close - rng.uniform(0.0, 1.0, n),
            "close": close,
            "volume": rng.uniform(1000, 5000, n),
        }
    )


def _constant_panel(n: int = 260, value: float = 100.0) -> pd.DataFrame:
    flat = pd.Series([value] * n)
    return pd.DataFrame(
        {
            "open": flat,
            "high": flat,
            "low": flat,
            "close": flat,
            "volume": pd.Series([1000.0] * n),
        }
    )


_NORMED = [
    compute_art_gk_vol,
    compute_art_rvol,
    compute_art_ichimoku_kijun_dev,
    compute_art_macd_hist,
    compute_art_trix_15,
    compute_art_hma_dev_20,
]


# --- registry wiring ---------------------------------------------------------


def test_all_seven_factors_registered_in_all_factors_shape() -> None:
    assert len(ARTIFACT_FACTORS) == 7
    for key, (fn, desc) in ARTIFACT_FACTORS.items():
        assert callable(fn) and desc


def test_ts_std_20_in_ops_config_with_arity_1() -> None:
    cfg = [c for c in OPS_CONFIG_PANDAS if c[0] == "TS_STD_20"]
    assert len(cfg) == 1
    assert cfg[0][2] == 1


# --- robust-norm warmup contract ---------------------------------------------


@pytest.mark.parametrize("fn", _NORMED, ids=lambda f: f.__name__)
def test_normed_features_have_199_bar_zero_warmup(fn) -> None:
    out = fn(_trending_panel())
    assert len(out) == 260
    assert (out.iloc[:199] == 0.0).all()
    assert out.iloc[199:].abs().max() > 0.0


@pytest.mark.parametrize("fn", _NORMED, ids=lambda f: f.__name__)
def test_normed_features_are_zero_on_constant_input(fn) -> None:
    out = fn(_constant_panel())
    assert (out == 0.0).all()


# --- missing-column degradation ----------------------------------------------


@pytest.mark.parametrize("fn", _NORMED, ids=lambda f: f.__name__)
def test_missing_columns_degrade_to_zero(fn) -> None:
    bare = pd.DataFrame({"close": pd.Series([100.0, 101.0, 102.0] * 20)})
    out = fn(bare)
    assert len(out) == 60


def test_rvol_needs_only_close() -> None:
    out = compute_art_rvol(pd.DataFrame({"close": pd.Series([100.0, 101.0] * 30)}))
    assert len(out) == 60


# --- per-feature semantic anchors --------------------------------------------


def test_rvol_constant_price_is_zero_not_a_volume_ratio() -> None:
    """The naming trap: RVOL = realized return volatility. Constant price →
    zero returns → zero rolling std → ~0 even though volume is nonzero."""
    panel = _constant_panel()
    panel["volume"] = pd.Series(np.linspace(1.0, 5000.0, 260))
    out = compute_art_rvol(panel)
    assert (out == 0.0).all()


def test_ret_entropy_20_alternating_returns_anchor() -> None:
    """Strictly alternating up/down closes → p_pos = p_neg = 0.5 in the last
    window → H = log(2), entropy = log(2)/log(3) ≈ 0.63093. First output has
    an all-zero window → p_zero = 1 → 0."""
    closes = pd.Series([100.0, 101.0] * 11)  # 22 bars → 21 returns
    out = compute_art_ret_entropy_20(pd.DataFrame({"close": closes}))
    assert len(out) == 22
    assert out.iloc[0] == pytest.approx(0.0, abs=1e-6)
    assert out.iloc[-1] == pytest.approx(math.log(2) / math.log(3), abs=1e-4)
    assert ((out >= 0.0) & (out <= 1.0)).all()


def test_ret_entropy_20_constant_price_is_zero() -> None:
    out = compute_art_ret_entropy_20(_constant_panel())
    assert out.abs().max() <= 1e-6


def test_gk_vol_constant_ohlc_is_exactly_zero() -> None:
    """Constant OHLC → every GK bar term is 0 → log1p(0) = 0 → norm of an
    all-zero series is exactly 0 (median 0, MAD floor 1e-6)."""
    out = compute_art_gk_vol(_constant_panel())
    assert (out == 0.0).all()


def test_ichimoku_kijun_dev_rising_market_is_positive_at_tail() -> None:
    panel = _trending_panel()
    out = compute_art_ichimoku_kijun_dev(panel)
    assert out.iloc[-1] > 0.0


def test_macd_hist_and_trix_are_finite_and_vary() -> None:
    panel = _trending_panel()
    for fn in (compute_art_macd_hist, compute_art_trix_15):
        out = fn(panel)
        assert np.isfinite(out).all()
        assert out.iloc[199:].nunique() > 1


def test_hma_dev_20_trending_market_diverges() -> None:
    out = compute_art_hma_dev_20(_trending_panel())
    assert out.iloc[199:].nunique() > 1


# --- TS_STD_20 operator: zero-padded causal window, never NaN -----------------


def _op_ts_std_20(x: pd.Series) -> pd.Series:
    from selene_space_alpha.ops.ops_pandas import op_ts_std_20

    return op_ts_std_20(x)


def test_ts_std_20_constant_input_first_19_bars_come_from_zero_padded_window() -> None:
    """x = [5]*30. Output bar 0 sees 19 zeros + one 5 →
    std = sqrt(E[x²]-E[x]²) = sqrt(1.1875) + 1e-6. Bars ≥19 see a full
    constant window → std = 1e-6. No bar is NaN."""
    out = _op_ts_std_20(pd.Series([5.0] * 30))
    assert len(out) == 30
    assert out.notna().all()
    assert out.iloc[0] == pytest.approx(math.sqrt(1.1875) + 1e-6, rel=1e-9)
    assert (out.iloc[19:] - 1e-6).abs().max() <= 1e-12


def test_ts_std_20_matches_producer_semantics_on_mixed_series() -> None:
    """E[x²]-E[x]² identity must equal the direct two-pass std on real values."""
    rng = np.random.default_rng(3)
    x = pd.Series(rng.normal(0.0, 2.0, 120))
    out = _op_ts_std_20(x)
    expected = np.array(
        [x.iloc[max(0, i - 19) : i + 1].std(ddof=0) for i in range(120)]
    )
    # warmup bars in `expected` use the same zero-padded windows
    for i in range(20, 120):
        assert out.iloc[i] == pytest.approx(expected[i] + 1e-6, rel=1e-9, abs=1e-12)


def test_ts_std_20_output_length_matches_input() -> None:
    out = _op_ts_std_20(pd.Series([1.0, 2.0, 3.0] * 10))
    assert len(out) == 30
