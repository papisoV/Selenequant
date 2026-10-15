# Ported from AlphaPurify (MIT)
"""Look-ahead-safe alignment of feature / discrete data onto a base bar frame.

Design-intent port of AlphaPurify ``DataBase`` alignment (``DataBase.py``):
the feature date column is shifted forward by ``shift_n`` bars
(``DataBase.py:366`` ``shift(-shift_n)``) so a feature computed at close of bar
T is only usable from bar ``T+shift_n`` onward — closing the look-ahead window
between feature computation and tradeable bar; continuous features are then
left-joined onto the base frame (``DataBase.py:385``); discrete features are
backward as-of merged so each bar carries the most recent known discrete value
(``DataBase.py:413-418`` ``join_asof(..., strategy="backward")``).

Two deliberate divergences from the source:

- The source applies a *global* ``shift(-shift_n)`` to a date-sorted frame,
  which shifts across symbol boundaries when bars are interleaved. This port
  groups by symbol, so each symbol's feature stream is shifted within itself.
- The source's ``shift_duration`` / ``multiply_duration`` window-extension
  helpers are not ported (no caller needs window extension yet).

This module imports no torch/polars; it is pure pandas and torch-free.
"""

from __future__ import annotations

from typing import Iterable

import pandas as pd

__all__ = ["align_continuous", "align_discrete", "align_panel"]


def align_continuous(
    feature_df: pd.DataFrame,
    base_df: pd.DataFrame,
    *,
    date_col: str,
    symbol_col: str,
    shift_n: int = 1,
) -> pd.DataFrame:
    """Shift a continuous feature frame forward by ``shift_n`` bars and left-join it onto ``base_df``.

    Within each symbol the feature's ``date_col`` value is moved up ``shift_n``
    rows (``groupby(...).shift(-shift_n)``), so the value computed at bar T is
    labeled bar ``T+shift_n``; rows whose shifted date falls past the end of a
    symbol's series are dropped. The shifted frame is then left-joined to
    ``base_df`` on ``(date_col, symbol_col)`` and returned sorted by
    ``(symbol_col, date_col)``. Base rows whose date has no matching shifted
    feature (the first ``shift_n`` bars per symbol) keep a NaN feature — the
    intended no-look-ahead gap. ``shift_n == 0`` leaves dates untouched.
    """
    base = base_df.copy()
    feat = feature_df.sort_values([symbol_col, date_col]).copy()
    if shift_n > 0:
        feat[date_col] = feat.groupby(symbol_col)[date_col].shift(-shift_n)
        feat = feat.dropna(subset=[date_col])
    return (
        base.merge(feat, on=[date_col, symbol_col], how="left")
        .sort_values([symbol_col, date_col])
        .reset_index(drop=True)
    )


def align_discrete(
    discrete_df: pd.DataFrame,
    base_df: pd.DataFrame,
    *,
    date_col: str,
    symbol_col: str,
) -> pd.DataFrame:
    """Backward as-of fill discrete features onto ``base_df`` (``join_asof ... strategy="backward"``).

    Each ``base_df`` bar receives the most recent discrete row at or before its
    date within the same symbol (forward-fill of the last known discrete value).
    Bars before a symbol's first discrete observation keep a NaN discrete value.
    The result is sorted by ``(symbol_col, date_col)``.
    """
    base = base_df.copy()
    if discrete_df is None or len(discrete_df) == 0 or len(base) == 0:
        return base
    cont = base.sort_values(date_col)
    disc = discrete_df.sort_values(date_col)
    merged = pd.merge_asof(
        cont, disc, on=date_col, by=symbol_col, direction="backward"
    )
    return merged.sort_values([symbol_col, date_col]).reset_index(drop=True)


def align_panel(
    continuous_dfs: Iterable[pd.DataFrame],
    discrete_df: pd.DataFrame | None,
    base_df: pd.DataFrame,
    *,
    date_col: str,
    symbol_col: str,
    shift_n: int = 1,
) -> pd.DataFrame:
    """Compose ``align_continuous`` + ``align_discrete`` over a full panel.

    Starts from ``base_df``, left-joins each continuous frame (shifted by
    ``shift_n``), then backward as-of fills the discrete frame if given, and
    returns the panel sorted by ``(symbol_col, date_col)`` (mirrors the
    ``read_and_merge_dfs`` + ``fill_to_full_df`` flow).
    """
    out = base_df.copy()
    for df in continuous_dfs:
        out = align_continuous(
            df, out, date_col=date_col, symbol_col=symbol_col, shift_n=shift_n
        )
    if discrete_df is not None and len(discrete_df) > 0:
        out = align_discrete(
            discrete_df, out, date_col=date_col, symbol_col=symbol_col
        )
    return out.sort_values([symbol_col, date_col]).reset_index(drop=True)
