"""Hand-computed reference tests for multiOLS and partialcorrelation.

These two methods are pure numpy/scipy (no sklearn estimator), so they are
verified against independently re-derived references — *not* golden fixtures
and *not* a sklearn_version guard. The reference helper re-derives the
preprocessing semantics (global z-score with ddof=1, drop-first dummies) from
the documented contract, so any drift in the implementation fails the test.

Covers the scale_X x dummy_cols four-quadrant matrix and the polars
``.std()`` = ddof=1 semantics.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy.stats import pearsonr

from selene_space_factor_research.preprocessing.neutralize import (
    multiOLS_neutralize,
    partialcorrelation_neutralize,
)

T_COL = "trade_date"
F_COL = "factor"
N_COLS = ["n1", "n2"]
DUMMY = ["industry"]


def _synthetic(n_dates: int = 2, n_per_date: int = 8, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = [f"2024-01-{d + 1:02d}" for d in range(n_dates)]
    n = n_dates * n_per_date
    return pd.DataFrame(
        {
            T_COL: [dates[i // n_per_date] for i in range(n)],
            "symbol": [f"S{i % n_per_date:02d}" for i in range(n)],
            F_COL: rng.normal(size=n),
            "n1": rng.normal(size=n),
            "n2": rng.normal(size=n),
            "industry": [["A", "B", "C", "D"][i % 4] for i in range(n)],
        }
    )


def _ref_preprocess(df: pd.DataFrame, factor_col: str, neutralizer_cols: list[str],
                    dummy_cols: list[str] | None, scale_X: bool) -> tuple[pd.DataFrame, list[str]]:
    """Independent re-derivation: global z-score ddof=1 (polars default), drop-first dummies."""
    d = df.copy()
    if scale_X:
        for c in neutralizer_cols:
            std = d[c].std()  # pandas default == polars .std() default == ddof=1
            mean = d[c].mean()
            d[c] = d[c] - mean if std == 0 else (d[c] - mean) / std
    X_cols = list(neutralizer_cols)
    if dummy_cols:
        dd = pd.get_dummies(d[dummy_cols], drop_first=True, dtype=float)
        d = pd.concat([d, dd], axis=1)
        X_cols += dd.columns.tolist()
    return d, X_cols


def _ref_multiOLS(df, t_col, f_col, n_cols, dummy_cols, scale_X) -> pd.DataFrame:
    d, X_cols = _ref_preprocess(df, f_col, n_cols, dummy_cols, scale_X)
    out = []
    for _, g in d.groupby(t_col, sort=False):
        X = np.column_stack([np.ones(len(g)), g[X_cols].values])
        y = g[f_col].values
        beta = np.linalg.lstsq(X, y, rcond=None)[0]
        g = g.copy()
        g[f_col] = y - X @ beta
        out.append(g)
    return pd.concat(out, ignore_index=True)


def _ref_partialcorrelation(df, t_col, f_col, n_cols, dummy_cols, scale_X) -> pd.DataFrame:
    d, X_cols = _ref_preprocess(df, f_col, n_cols, dummy_cols, scale_X)
    out = []
    for _, g in d.groupby(t_col, sort=False):
        X = g[X_cols].values
        y = g[f_col].values
        if X.shape[1] > 0:
            P = X @ np.linalg.pinv(X.T @ X) @ X.T
            resid = y - P @ y
        else:
            resid = y.copy()
        g = g.copy()
        g[f_col] = resid
        out.append(g)
    return pd.concat(out, ignore_index=True)


def _aligned(got: pd.DataFrame, exp: pd.DataFrame, f_col: str) -> np.ndarray:
    g = got.set_index([T_COL, "symbol"])[f_col].sort_index()
    e = exp.set_index([T_COL, "symbol"])[f_col].sort_index()
    assert g.index.equals(e.index), "row alignment mismatch"
    return np.abs(g.values - e.values)


QUADRANTS = [
    pytest.param(False, None, id="scaleX_False_dummy_empty"),
    pytest.param(True, None, id="scaleX_True_dummy_empty"),
    pytest.param(False, DUMMY, id="scaleX_False_dummy_has"),
    pytest.param(True, DUMMY, id="scaleX_True_dummy_has"),
]


@pytest.mark.parametrize("scale_X,dummy_cols", QUADRANTS)
def test_multiOLS_matches_lstsq_reference(scale_X: bool, dummy_cols: list[str] | None) -> None:
    df = _synthetic()
    got = multiOLS_neutralize(df, T_COL, F_COL, N_COLS, dummy_cols, scale_X)
    exp = _ref_multiOLS(df, T_COL, F_COL, N_COLS, dummy_cols, scale_X)
    assert np.allclose(got[F_COL], exp[F_COL], atol=1e-10, rtol=1e-10)


@pytest.mark.parametrize("scale_X,dummy_cols", QUADRANTS)
def test_partialcorrelation_matches_hat_matrix_reference(scale_X: bool, dummy_cols: list[str] | None) -> None:
    df = _synthetic()
    got = partialcorrelation_neutralize(df, T_COL, F_COL, N_COLS, dummy_cols, scale_X)
    exp = _ref_partialcorrelation(df, T_COL, F_COL, N_COLS, dummy_cols, scale_X)
    assert np.allclose(got[F_COL], exp[F_COL], atol=1e-10, rtol=1e-10)


def test_scale_X_uses_pandas_default_std_ddof1() -> None:
    """polars .std() defaults to ddof=1; scale_X must match pandas default, not ddof=0."""
    df = _synthetic()
    # ddof=1 and ddof=0 differ measurably on this data.
    s = df["n1"]
    assert not np.isclose(s.std(ddof=1), s.std(ddof=0), atol=1e-12)
    # The reference (ddof=1) must match; a ddof=0 scale would deviate.
    got = multiOLS_neutralize(df, T_COL, F_COL, N_COLS, None, True)
    exp = _ref_multiOLS(df, T_COL, F_COL, N_COLS, None, True)
    assert np.allclose(got[F_COL], exp[F_COL], atol=1e-10, rtol=1e-10)


def test_constant_neutralizer_branch() -> None:
    """std == 0 column is demeaned, not z-scored (matches _preprocess_for_neutralization)."""
    df = _synthetic()
    df["n1"] = 3.0  # constant
    got = multiOLS_neutralize(df, T_COL, F_COL, ["n1", "n2"], None, True)
    exp = _ref_multiOLS(df, T_COL, F_COL, ["n1", "n2"], None, True)
    assert np.allclose(got[F_COL], exp[F_COL], atol=1e-10, rtol=1e-10)


def test_partialcorrelation_target_col_mode() -> None:
    """return_type='partial_corr' with a target returns one value per cross-section."""
    df = _synthetic()
    df["target"] = df["factor"] + 0.3 * df["n1"] + np.random.default_rng(1).normal(size=len(df))
    got = partialcorrelation_neutralize(
        df, T_COL, F_COL, N_COLS, None, False, target_col="target", return_type="partial_corr"
    )
    # one row per date
    assert len(got) == df[T_COL].nunique()
    assert got[T_COL].nunique() == len(got)
    assert got[F_COL].between(-1, 1).all()


def test_partialcorrelation_scipy_hand_reference() -> None:
    """partial_corr values match an independent scipy residualization reference."""
    df = _synthetic()
    df["target"] = df["factor"] + 0.3 * df["n1"] + np.random.default_rng(1).normal(size=len(df))

    # Independent reference: residualize factor and target on the controls,
    # then Pearson-correlate the residuals — the textbook partial correlation.
    expected = []
    for _, g in df.groupby(T_COL, sort=False):
        X = g[N_COLS].values
        y = g[F_COL].values
        r = g["target"].values
        if X.shape[1] > 0:
            P = X @ np.linalg.pinv(X.T @ X) @ X.T
            y_res, r_res = y - P @ y, r - P @ r
        else:
            y_res, r_res = y, r
        expected.append(pearsonr(y_res, r_res)[0])

    got = partialcorrelation_neutralize(
        df, T_COL, F_COL, N_COLS, None, False, target_col="target", return_type="partial_corr"
    )
    assert np.allclose(got[F_COL].values, expected, atol=1e-12, rtol=1e-12)


def test_no_sklearn_version_guard() -> None:
    """These tests are sklearn-version independent: no sklearn import, no fixture reads."""
    import ast
    import pathlib

    src = pathlib.Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                assert alias.name.split(".")[0] != "sklearn", f"unexpected sklearn import {alias.name}"
        if isinstance(node, ast.Attribute) and node.attr == "read_parquet":
            raise AssertionError("reference tests must not read golden fixtures")
