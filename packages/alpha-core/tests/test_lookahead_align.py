"""Look-ahead alignment (pure pandas): shift-forward, backward as-of, parity, AST independence.

Tests ``selene_space_factor_research.mining.lookahead_align`` — the pure-pandas
anti-look-ahead alignment ported from AlphaPurify ``DataBase``. Loaded by file
path through a synthetic package.

The AlphaPurify semantics are re-derived here with an *independent* numpy
reference (per-symbol positional ``shift(-shift_n)`` rolls plus a hand-rolled
backward as-of via ``searchsorted``) and asserted to agree with the port — a
semantic parity check that never imports polars or AlphaPurify.

Torch/polars independence: an AST pass asserts neither the port nor this test
imports ``torch`` / ``polars`` / ``alphapurify``.
"""
from __future__ import annotations

import ast
import pathlib
import types

import numpy as np
import pandas as pd

from _module_loader import MINING_DIR as _MINING, load_module

# --------------------------------------------------------------------------
# Torch/polars-independent loading of the port module.
# --------------------------------------------------------------------------
def _load(name: str, src_dir: pathlib.Path = _MINING) -> types.ModuleType:
    return load_module(name, src_dir, package="selene_align")


la = _load("lookahead_align")
align_continuous = la.align_continuous
align_discrete = la.align_discrete
align_panel = la.align_panel


# --------------------------------------------------------------------------
# Synthetic data helpers.
# --------------------------------------------------------------------------
def _base(dates: list[str], symbols: list[str], base_val: float = 1.0) -> pd.DataFrame:
    rows = []
    for d in dates:
        for s in symbols:
            rows.append({"trade_date": pd.Timestamp(d), "symbol": s, "close": base_val})
    return pd.DataFrame(rows)


def _feature(dates: list[str], symbols: list[str], start: int = 100, step: int = 10) -> pd.DataFrame:
    """factor = start + date_index * step (deterministic per bar, same across symbols)."""
    rows = []
    for i, d in enumerate(dates):
        for s in symbols:
            rows.append({"trade_date": pd.Timestamp(d), "symbol": s, "factor": start + i * step})
    return pd.DataFrame(rows)


def _discrete(symbol_values: dict[str, tuple[str, float]], date: str = "2024-01-02") -> pd.DataFrame:
    rows = [{"trade_date": pd.Timestamp(date), "symbol": s, "sector": v, "score": sc}
            for s, (v, sc) in symbol_values.items()]
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Independence guarantees.
# --------------------------------------------------------------------------
def test_port_imports_no_torch_or_polars() -> None:
    tree = ast.parse((_MINING / "lookahead_align.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root not in ("torch", "polars"), f"banned import {alias.name}"


def test_testfile_imports_no_polars_torch_alphapurify() -> None:
    tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root not in ("polars", "torch", "alphapurify"), f"banned import {alias.name}"


# --------------------------------------------------------------------------
# Continuous: shift-forward semantics.
# --------------------------------------------------------------------------
def test_continuous_shifts_feature_forward_by_shift_n() -> None:
    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    out = align_continuous(
        _feature(dates, ["S0", "S1"]), _base(dates, ["S0", "S1"]),
        date_col="trade_date", symbol_col="symbol", shift_n=1,
    )
    # Feature computed at T0 (factor 100) is only usable at T1; T1's (110) at T2.
    assert out.loc[out["trade_date"] == pd.Timestamp("2024-01-03"), "factor"].tolist() == [100.0, 100.0]
    assert out.loc[out["trade_date"] == pd.Timestamp("2024-01-04"), "factor"].tolist() == [110.0, 110.0]
    # Base first bar keeps NaN factor: no look-ahead.
    assert out.loc[out["trade_date"] == pd.Timestamp("2024-01-02"), "factor"].isna().all()


def test_continuous_shift_n_zero_does_not_move() -> None:
    dates = ["2024-01-02", "2024-01-03"]
    out = align_continuous(
        _feature(dates, ["S0"]), _base(dates, ["S0"]),
        date_col="trade_date", symbol_col="symbol", shift_n=0,
    )
    assert out.loc[out["trade_date"] == pd.Timestamp("2024-01-02"), "factor"].tolist() == [100.0]
    assert out.loc[out["trade_date"] == pd.Timestamp("2024-01-03"), "factor"].tolist() == [110.0]


def test_continuous_single_symbol() -> None:
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    out = align_continuous(
        _feature(dates, ["S0"]), _base(dates, ["S0"]),
        date_col="trade_date", symbol_col="symbol", shift_n=2,
    )
    assert out.loc[out["trade_date"] == pd.Timestamp("2024-01-04"), "factor"].tolist() == [100.0]
    assert out.loc[out["trade_date"] == pd.Timestamp("2024-01-05"), "factor"].tolist() == [110.0]
    assert out.loc[out["trade_date"] <= pd.Timestamp("2024-01-03"), "factor"].isna().all()


def test_continuous_empty_feature_does_not_raise() -> None:
    dates = ["2024-01-02", "2024-01-03"]
    empty = pd.DataFrame(columns=["trade_date", "symbol", "factor"])
    out = align_continuous(
        empty, _base(dates, ["S0"]),
        date_col="trade_date", symbol_col="symbol", shift_n=1,
    )
    assert len(out) == len(_base(dates, ["S0"]))
    assert out["factor"].isna().all()


def test_continuous_no_matching_base_rows_gives_nan() -> None:
    # Feature exists on a date the base never has: no match -> NaN.
    base = _base(["2024-01-02"], ["S0"])
    feat = _feature(["2024-01-03"], ["S0"])
    out = align_continuous(feat, base, date_col="trade_date", symbol_col="symbol", shift_n=1)
    assert out["factor"].isna().all()


# --------------------------------------------------------------------------
# Discrete: backward as-of forward-fill.
# --------------------------------------------------------------------------
def test_discrete_backward_asof_forward_fills() -> None:
    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    out = align_discrete(
        _discrete({"S0": ("Tech", 1.0)}), _base(dates, ["S0"]),
        date_col="trade_date", symbol_col="symbol",
    )
    # Latest discrete at-or-before each bar: T0 hits directly, T1/T2 forward-fill.
    assert out["sector"].tolist() == ["Tech", "Tech", "Tech"]
    assert out["score"].tolist() == [1.0, 1.0, 1.0]


def test_discrete_uses_most_recent_at_or_before() -> None:
    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    disc = pd.concat([
        _discrete({"S0": ("Tech", 1.0)}),
        _discrete({"S0": ("Energy", 2.0)}, date="2024-01-04"),
    ], ignore_index=True)
    out = align_discrete(disc, _base(dates, ["S0"]), date_col="trade_date", symbol_col="symbol")
    assert out["sector"].tolist() == ["Tech", "Tech", "Energy"]
    assert out["score"].tolist() == [1.0, 1.0, 2.0]


def test_discrete_before_first_observation_is_nan() -> None:
    dates = ["2024-01-02", "2024-01-03"]
    out = align_discrete(
        _discrete({"S0": ("Tech", 1.0)}, date="2024-01-03"), _base(dates, ["S0"]),
        date_col="trade_date", symbol_col="symbol",
    )
    assert pd.isna(out.loc[0, "sector"])
    assert out.loc[1, "sector"] == "Tech"


def test_discrete_empty_is_noop() -> None:
    dates = ["2024-01-02"]
    base = _base(dates, ["S0"])
    out = align_discrete(pd.DataFrame(), base, date_col="trade_date", symbol_col="symbol")
    assert list(out.columns) == ["trade_date", "symbol", "close"]
    assert len(out) == len(base)


# --------------------------------------------------------------------------
# align_panel composition.
# --------------------------------------------------------------------------
def test_align_panel_composes_continuous_then_discrete() -> None:
    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    factor1 = _feature(dates, ["S0"], start=100, step=10)
    factor2 = _feature(dates, ["S0"], start=1000, step=10).rename(columns={"factor": "factor2"})
    out = align_panel(
        [factor1, factor2], _discrete({"S0": ("Tech", 1.0)}), _base(dates, ["S0"]),
        date_col="trade_date", symbol_col="symbol", shift_n=1,
    )
    # factor1 shifted: T1<-100, T2<-110; factor2 shifted: T1<-1000, T2<-1010.
    row_t1 = out.loc[out["trade_date"] == pd.Timestamp("2024-01-03")]
    row_t2 = out.loc[out["trade_date"] == pd.Timestamp("2024-01-04")]
    assert row_t1["factor"].tolist() == [100.0]
    assert row_t1["factor2"].tolist() == [1000.0]
    assert row_t2["factor"].tolist() == [110.0]
    assert row_t2["factor2"].tolist() == [1010.0]
    # Discrete forward-filled on every bar.
    assert out["sector"].tolist() == ["Tech", "Tech", "Tech"]


# --------------------------------------------------------------------------
# Semantic parity with an independent numpy reference (no polars, no AlphaPurify).
# --------------------------------------------------------------------------
def _ref_continuous(feature_df: pd.DataFrame, base_df: pd.DataFrame, shift_n: int) -> pd.DataFrame:
    """Independent numpy positional shift, grouped per symbol (the port's design intent).

    The source's *global* ``shift(-shift_n)`` leaks dates across symbol
    boundaries within a bar block; the port groups by symbol to fix that. This
    reference pins the grouped semantics with numpy rolls (not pandas
    ``groupby.shift`` / ``merge``), so parity is a genuine cross-implementation
    check.
    """
    feat = feature_df.sort_values(["symbol", "trade_date"]).copy()
    dts: list[np.ndarray] = []
    syms: list[np.ndarray] = []
    vals: list[np.ndarray] = []
    for sym, grp in feat.groupby("symbol"):
        gd = grp["trade_date"].to_numpy()
        shifted = np.full(len(gd), np.datetime64("NaT"), dtype=gd.dtype)
        shifted[: len(gd) - shift_n] = gd[shift_n:]
        keep = ~pd.isna(shifted)
        dts.append(shifted[keep])
        syms.append(np.full(int(keep.sum()), sym, dtype=object))
        vals.append(grp["factor"].to_numpy()[keep])
    shifted_feat = pd.DataFrame(
        {
            "trade_date": np.concatenate(dts),
            "symbol": np.concatenate(syms),
            "factor": np.concatenate(vals),
        }
    )
    merged = base_df.merge(shifted_feat, on=["trade_date", "symbol"], how="left")
    return merged.sort_values(["symbol", "trade_date"]).reset_index(drop=True)


def _ref_discrete(discrete_df: pd.DataFrame, base_df: pd.DataFrame) -> pd.DataFrame:
    """Hand-rolled backward as-of via searchsorted (per symbol)."""
    out = base_df.copy()
    out["sector"] = pd.Series([np.nan] * len(out), dtype=object)
    out["score"] = np.nan
    for sym, grp in discrete_df.sort_values("trade_date").groupby("symbol"):
        disc_dates = grp["trade_date"].to_numpy()
        idx = out["symbol"].to_numpy() == sym
        base_dates = out.loc[idx, "trade_date"].to_numpy()
        pos = np.searchsorted(disc_dates, base_dates, side="right") - 1
        good = pos >= 0
        sectors = np.full(len(pos), np.nan, dtype=object)
        sectors[good] = grp["sector"].to_numpy()[pos[good]]
        scores = np.full(len(pos), np.nan)
        scores[good] = grp["score"].to_numpy()[pos[good]]
        out.loc[idx, "sector"] = sectors
        out.loc[idx, "score"] = scores
    return out.sort_values(["symbol", "trade_date"]).reset_index(drop=True)


def test_continuous_parity_with_reference() -> None:
    dates = [f"2024-01-{d:02d}" for d in range(2, 12)]  # 10 bars, regular calendar
    symbols = ["S0", "S1", "S2"]
    base = _base(dates, symbols)
    feat = _feature(dates, symbols, start=50, step=7)
    for n in (0, 1, 3):
        got = align_continuous(feat, base, date_col="trade_date", symbol_col="symbol", shift_n=n)
        ref = _ref_continuous(feat, base, n)
        pd.testing.assert_frame_equal(
            got.sort_values(["symbol", "trade_date"]).reset_index(drop=True),
            ref,
        )


def test_discrete_parity_with_reference() -> None:
    dates = [f"2024-01-{d:02d}" for d in range(2, 8)]
    symbols = ["S0", "S1"]
    disc = pd.concat([
        _discrete({"S0": ("Tech", 1.0)}),
        _discrete({"S1": ("Fin", 3.0)}, date="2024-01-03"),
        _discrete({"S0": ("Energy", 2.0)}, date="2024-01-05"),
    ], ignore_index=True)
    got = align_discrete(disc, _base(dates, symbols), date_col="trade_date", symbol_col="symbol")
    ref = _ref_discrete(disc, _base(dates, symbols))
    pd.testing.assert_frame_equal(got, ref)
