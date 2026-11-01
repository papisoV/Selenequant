"""Scoring kernels for the quantile-kernel A/B experiment (plan: internal-plan).

New files only (D1 zero-mutation): the zscore branches replicate
v9_exit_opt.compute_sigmoid_scores / v7_trend_filter_backtest.compute_signals
exactly; the quantile branches replace rolling-60 z-score with rolling-60
percentile rank. Both kernels live in one file so the two call sites (v10
imports both) stay in lockstep by construction.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from selene_space_alpha.vocab import FormulaVocab
from selene_space_alpha.vm import PandasStackVM


def _zscore_kernel(scores: np.ndarray, window: int, min_periods: int) -> np.ndarray:
    """v9_exit_opt.py:336-339 exact: rolling z-score → clip ±10 → sigmoid."""
    rolling_mean = (
        pd.Series(scores).rolling(window, min_periods=min_periods).mean().values
    )
    rolling_std = (
        pd.Series(scores).rolling(window, min_periods=min_periods).std().values + 1e-8
    )
    score_norm = np.nan_to_num((scores - rolling_mean) / rolling_std, 0.0).clip(-10, 10)
    return 1.0 / (1.0 + np.exp(-score_norm))


def _quantile_kernel(scores: np.ndarray, window: int, min_periods: int) -> np.ndarray:
    """Rolling percentile rank; warmup/NaN → neutral 0.5."""
    pct = pd.Series(scores).rolling(window, min_periods=min_periods).rank(pct=True)
    return pct.fillna(0.5).clip(0.0, 1.0).values


_KERNELS = {"zscore": _zscore_kernel, "quantile": _quantile_kernel}


def compute_sigmoid_scores(
    tokens: list[int],
    vocab: FormulaVocab,
    df: pd.DataFrame,
    features: dict[int, pd.Series],
    kernel: str = "zscore",
    window: int = 60,
    min_periods: int = 10,
    vm: PandasStackVM | None = None,
) -> np.ndarray:
    """Normalize raw formula scores to [0, 1] via the selected kernel.

    vm is injectable for tests; production builds PandasStackVM(vocab) exactly
    like the v9/v7 originals.
    """
    if vm is None:
        vm = PandasStackVM(vocab)
    score_series = vm.execute(tokens, df, precomputed_features=features)
    if score_series is None:
        return np.full(len(df), 0.5)

    try:
        norm_fn = _KERNELS[kernel]
    except KeyError:
        raise ValueError(
            f"unknown kernel: {kernel!r} (expected 'zscore' or 'quantile')"
        ) from None
    return norm_fn(score_series.values.astype(float), window, min_periods)


def compute_signals(
    tokens: list[int],
    vocab: FormulaVocab,
    df: pd.DataFrame,
    features: dict[int, pd.Series],
    buy_th: float,
    sell_th: float,
    kernel: str = "zscore",
    window: int = 60,
    min_periods: int = 10,
    vm: PandasStackVM | None = None,
) -> np.ndarray:
    """Discrete ±1/0 signals from the selected kernel (mirrors v7:246-252)."""
    scores = compute_sigmoid_scores(
        tokens,
        vocab,
        df,
        features,
        kernel=kernel,
        window=window,
        min_periods=min_periods,
        vm=vm,
    )
    return np.where(scores > buy_th, 1, np.where(scores < sell_th, -1, 0))
