"""Tests for batch12 US-1: compute_ic_weights rejects unknown weight methods.

Zero network. All data synthetic. Self-contained cross_section builder
(no fixture import from test_ashare.py).
"""

import numpy as np
import pandas as pd
import pytest

from selene_space_ashare.composite import (
    build_composite_panel,
    compute_ic_weights,
)
from selene_space_types.stock import CompositeConfig


def _make_cross_section() -> pd.DataFrame:
    """Synthetic cross-sectional data mirroring test_ashare.py construction."""
    np.random.seed(7)
    dates = pd.date_range("2024-06-01", periods=60, freq="B")
    symbols = [f"S{i:06d}" for i in range(30)]
    rows = []
    for d in dates:
        for s in symbols:
            rows.append(
                {
                    "date": d,
                    "symbol": s,
                    "MOMENTUM_1M": np.random.randn(),
                    "VOLATILITY_20D": abs(np.random.randn()) * 0.3,
                    "MA_DEVIATION": np.random.randn() * 0.05,
                    "HIGH_52W_PROXIMITY": np.random.rand(),
                    "RSI_14D": np.random.rand() * 100,
                    "BB_WIDTH": abs(np.random.randn()) * 0.1,
                    "VOLUME_CLIMAX": np.random.randint(0, 2),
                    "fwd_5d": np.random.randn() * 0.03,
                    "fwd_1d": np.random.randn() * 0.01,
                }
            )
    return pd.DataFrame(rows)


FACTORS = ["MOMENTUM_1M", "VOLATILITY_20D"]


def test_compute_ic_weights_unknown_method_raises():
    cs = _make_cross_section()
    with pytest.raises(ValueError, match="momentum"):
        compute_ic_weights(cs, FACTORS, method="momentum")
    # valid set must be listed so callers can self-correct
    with pytest.raises(ValueError, match="'ir'"):
        compute_ic_weights(cs, FACTORS, method="momentum")


def test_compute_ic_weights_equal_unchanged():
    cs = _make_cross_section()
    weights = compute_ic_weights(cs, FACTORS, method="equal")
    assert set(weights.keys()) == set(FACTORS)
    # equal weight = direction / n
    for name in FACTORS:
        assert abs(weights[name]) - 1.0 / len(FACTORS) < 1e-9
    assert abs(sum(abs(w) for w in weights.values()) - 1.0) < 1e-6


def test_compute_ic_weights_ir_ic_unchanged():
    cs = _make_cross_section()
    ir_weights = compute_ic_weights(cs, FACTORS, method="ir")
    ic_weights = compute_ic_weights(cs, FACTORS, method="ic")
    # both normalized to sum of |weights| == 1 (unless all-zero fallback)
    assert abs(sum(abs(w) for w in ir_weights.values()) - 1.0) < 1e-6
    assert abs(sum(abs(w) for w in ic_weights.values()) - 1.0) < 1e-6
    # ir and ic must differ (IR uses volatility-adjusted, IC uses raw mean)
    assert ir_weights != ic_weights


def test_build_composite_panel_unknown_method_raises():
    cs = _make_cross_section()
    with pytest.raises(ValueError, match="momentum"):
        build_composite_panel(cs, FACTORS, weight_method="momentum")


def test_build_composite_panel_ir_walkforward_still_ok():
    cs = _make_cross_section()
    result = build_composite_panel(cs, FACTORS, weight_method="ir_walkforward")
    assert isinstance(result, tuple) and len(result) == 2
    panel, config = result
    assert "composite_score" in panel.columns
    assert isinstance(config, CompositeConfig)
    # not a silently all-zero panel
    assert not panel["composite_score"].abs().eq(0.0).all()
