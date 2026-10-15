"""Registry / config / error tests for the preprocessing toolbox (story P1M1-01).

Method-level behaviour is covered separately per category (winsorize /
standardize / neutralize parity and smoke tests). This file locks the registry
contract: exactly 42 methods (12w + 15n + 15s), 1:1 with defs, no legacy keys
leaking into the registry, and the AlphaPurify bug-① fix (PreprocessConfig is a
real dataclass).
"""
from __future__ import annotations

import pandas as pd
import pytest

from selene_space_factor_research.preprocessing import (
    PreprocessConfig,
    PreprocessMethodError,
    PreprocessParamError,
    apply,
    list_methods,
)


def _df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": ["2024-01-01"] * 4 + ["2024-01-02"] * 4,
            "symbol": ["A", "B", "C", "D"] * 2,
            "factor": [0.5, 1.5, -0.5, 3.0, -1.0, 0.2, 0.8, -2.5],
            "neutralizer": [1.0, 2.0, 3.0, 4.0, 4.0, 3.0, 2.0, 1.0],
        }
    )


def test_list_methods_total_is_42() -> None:
    names = list_methods()
    assert len(names) == 42
    assert len(set(names)) == 42  # unique composite names


def test_list_methods_by_category_counts() -> None:
    assert len(list_methods("winsorize")) == 12
    assert len(list_methods("neutralize")) == 15
    assert len(list_methods("standardize")) == 15


def test_registry_has_no_legacy_keys() -> None:
    names = list_methods()
    # legacy 3 are shim-only, never registered
    assert "neutralize_cluster" not in names
    assert "neutralize_industry" not in names
    # neutralize 'rank' (RANSAC in AlphaPurify) was renamed to 'ransac'
    assert "neutralize_rank" not in list_methods("neutralize")
    assert "neutralize_ransac" in list_methods("neutralize")
    # standardize 'rank' (L2791) is legitimate and stays
    assert "standardize_rank" in list_methods("standardize")


def test_every_method_name_has_category_prefix() -> None:
    for name in list_methods():
        category, _, key = name.partition("_")
        assert category in ("winsorize", "neutralize", "standardize")
        assert key  # non-empty


def test_preprocess_config_is_plain_dataclass() -> None:
    cfg = PreprocessConfig(method="winsorize_mad")
    assert cfg.method == "winsorize_mad"
    assert cfg.params == {}
    cfg2 = PreprocessConfig(method="winsorize_mad", params={"n": 2.0})
    assert cfg2.params == {"n": 2.0}


def test_apply_rejects_unknown_method() -> None:
    with pytest.raises(PreprocessMethodError):
        apply(_df(), [PreprocessConfig(method="winsorize_does_not_exist")])


def test_apply_rejects_ambiguous_bare_key() -> None:
    # 'ransac' exists in both winsorize and neutralize
    with pytest.raises(PreprocessMethodError):
        apply(_df(), [PreprocessConfig(method="ransac", params={"trade_date_col": "trade_date", "factor_col": "factor"})])


def test_apply_missing_required_param_raises() -> None:
    with pytest.raises(PreprocessParamError):
        apply(_df(), [PreprocessConfig(method="winsorize_mad", params={"n": 2.0})])


def test_apply_unknown_param_raises() -> None:
    params = {"trade_date_col": "trade_date", "factor_col": "factor", "bogus": 1}
    with pytest.raises(PreprocessParamError):
        apply(_df(), [PreprocessConfig(method="winsorize_mad", params=params)])


def test_apply_chain_order_preserves_shape() -> None:
    df = _df()
    out = apply(
        df,
        [
            PreprocessConfig(
                method="winsorize_mad",
                params={"trade_date_col": "trade_date", "factor_col": "factor", "n": 1.5},
            ),
            PreprocessConfig(
                method="standardize_zscore",
                params={"trade_date": "trade_date", "factor_col": "factor"},
            ),
        ],
    )
    assert out.shape == df.shape
    assert list(out.columns) == list(df.columns)
    # winsorize then zscore: cross-sectional mean ~ 0 per date
    means = out.groupby("trade_date")["factor"].mean()
    assert means.abs().max() < 1e-6
    # only the factor column was mutated; other columns untouched
    assert out["symbol"].tolist() == df["symbol"].tolist()
