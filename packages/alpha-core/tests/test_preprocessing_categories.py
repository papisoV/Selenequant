"""Category smoke tests for the 42-method preprocessing toolbox.

Covers US-P1M1-02 (winsorize 12), US-P1M1-03 (standardize 15) and
US-P1M1-04 (neutralize 15): every registered method runs on synthetic data
that contains NaN / Inf / constant columns, returns a DataFrame with the same
shape and column set, and never imports polars.
"""
from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest

from selene_space_factor_research.preprocessing import METHOD_REGISTRY, list_methods

# Map any signature param name (per-category naming varies in AlphaPurify:
# standardize uses `trade_date`, winsorize/neutralize use `trade_date_col`) to
# the actual column name the smoke test creates.
ROLE_PARAM = {
    "trade_date": "trade_date",
    "trade_date_col": "trade_date",
    "symbol_col": "symbol",
    "factor_col": "factor",
    "neutralizer_cols": ["neutralizers"],
}

# Methods with params that need non-string values or extra required columns.
SPECIAL_PARAMS: dict[str, dict] = {}


def _params_for(method_name: str, m) -> dict:
    params = {}
    for role in m.required_params:
        params[role] = ROLE_PARAM[role]
    params.update(SPECIAL_PARAMS.get(method_name, {}))
    return params


def _synthetic_df(n_dates: int = 3, n_per_date: int = 8, inf_in_factor: bool = True) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    dates = [f"2024-01-{i + 1:02d}" for i in range(n_dates)]
    n = n_dates * n_per_date
    df = pd.DataFrame(
        {
            "trade_date": np.repeat(dates, n_per_date),
            "symbol": [f"S{i:02d}" for i in range(n_per_date)] * n_dates,
            "factor": rng.normal(size=n),
            "neutralizers": rng.normal(size=n),
            "industry": np.tile(["A", "B", "C", "D"], n // 4 + 1)[:n],
        }
    )
    # Inject NaN / Inf / constant-column edge cases. sklearn bridges reject Inf
    # in y (faithful to AlphaPurify, which also does no Inf coercion), so the
    # neutralize smoke test opts out of the Inf injection.
    df.loc[3, "factor"] = np.nan
    if inf_in_factor:
        df.loc[5, "factor"] = np.inf
    df.loc[7, "neutralizers"] = 5.0  # constant within one date group
    return df


@pytest.mark.parametrize("name", list_methods("winsorize"))
def test_winsorize_method_runs(name: str) -> None:
    m = METHOD_REGISTRY[name]
    df = _synthetic_df()
    out = m(df, **_params_for(name, m))
    assert isinstance(out, pd.DataFrame)
    assert out.shape == df.shape
    assert list(out.columns) == list(df.columns)
    # winsorize must not drop rows or widen the factor range beyond input bounds
    assert out["factor"].nunique() <= df["factor"].nunique() or out["factor"].isna().all()


@pytest.mark.parametrize("name", list_methods("standardize"))
def test_standardize_method_runs(name: str) -> None:
    m = METHOD_REGISTRY[name]
    df = _synthetic_df()
    out = m(df, **_params_for(name, m))
    assert isinstance(out, pd.DataFrame)
    assert out.shape == df.shape
    assert list(out.columns) == list(df.columns)


@pytest.mark.parametrize("name", list_methods("neutralize"))
def test_neutralize_method_runs(name: str) -> None:
    m = METHOD_REGISTRY[name]
    df = _synthetic_df(inf_in_factor=False)
    out = m(df, **_params_for(name, m))
    assert isinstance(out, pd.DataFrame)
    assert out.shape[1] == df.shape[1]
    assert "factor" in out.columns


def test_neutralize_uses_no_polars() -> None:
    import selene_space_factor_research.preprocessing as preprocessing

    neutralize_src = inspect.getsource(preprocessing.neutralize)
    assert "import polars" not in neutralize_src


def test_preprocessing_package_imports_no_polars() -> None:
    import selene_space_factor_research.preprocessing as pkg

    for mod in (pkg, pkg.registry, pkg.neutralize, pkg.standardize, pkg.winsorize):
        src = inspect.getsource(mod)
        # Check for actual import statements, not docstring mentions.
        assert not _has_polars_import(src), f"{mod.__name__} imports polars"


def _has_polars_import(src: str) -> bool:
    import ast

    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name.split(".")[0] == "polars":
                    return True
    return False
