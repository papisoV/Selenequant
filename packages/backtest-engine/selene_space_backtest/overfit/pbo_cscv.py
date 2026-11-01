"""Probability of Backtest Overfitting (PBO) via Combinatorially-Symmetric
Cross-Validation (CSCV).

Reference
---------
Bailey, D. H., Borwein, J., Lopez de Prado, M., & Zhu, Q. J. (2017).
    "The Probability of Backtest Overfitting." Journal of Computational
     Finance, 20(4), 39-69.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Callable

import numpy as np


def _sharpe_cols(block: np.ndarray) -> np.ndarray:
    """Per-period Sharpe of each column of a (rows x N) block."""
    mu = np.nanmean(block, axis=0)
    sd = np.nanstd(block, axis=0, ddof=1)
    sd = np.where(sd == 0, np.nan, sd)
    return mu / sd


@dataclass(frozen=True)
class PBOResult:
    pbo: float
    n_splits: int
    n_strategies: int
    n_blocks: int
    logits: tuple[float, ...] = ()
    oos_ranks: tuple[float, ...] = ()
    is_best_oos_perf: tuple[float, ...] = ()

    def summary(self) -> dict:
        return {
            "pbo": self.pbo,
            "n_splits": self.n_splits,
            "n_strategies": self.n_strategies,
            "n_blocks": self.n_blocks,
            "median_logit": float(np.median(self.logits)) if self.logits else float("nan"),
        }


def probability_of_backtest_overfitting(
    perf_matrix: np.ndarray,
    n_blocks: int = 16,
    metric: Callable[[np.ndarray], np.ndarray] = _sharpe_cols,
) -> PBOResult:
    """Compute PBO via CSCV.

    Parameters
    ----------
    perf_matrix : shape (T, N). Per-period PnL / returns.
        Columns are competing strategy configs, rows are time.
    n_blocks : Number of contiguous blocks S (must be even).
    metric : Maps a (rows x N) block to an array of N performance scores.
    """
    M = np.asarray(perf_matrix, dtype=float)
    if M.ndim != 2:
        raise ValueError("perf_matrix must be 2D (T x N)")
    T, N = M.shape
    if N < 2:
        raise ValueError("Need at least 2 strategy configurations to assess PBO")
    if n_blocks % 2 != 0:
        raise ValueError("n_blocks (S) must be even")
    if n_blocks > T:
        raise ValueError("n_blocks cannot exceed number of observations")

    block_idx = np.array_split(np.arange(T), n_blocks)
    blocks = list(range(n_blocks))

    logits: list[float] = []
    oos_ranks: list[float] = []
    is_best_oos_perf: list[float] = []

    for is_blocks in combinations(blocks, n_blocks // 2):
        is_set = set(is_blocks)
        is_rows = np.concatenate([block_idx[b] for b in blocks if b in is_set])
        oos_rows = np.concatenate([block_idx[b] for b in blocks if b not in is_set])

        is_perf = metric(M[is_rows])
        oos_perf = metric(M[oos_rows])

        if np.all(np.isnan(is_perf)):
            continue
        n_star = int(np.nanargmax(is_perf))

        valid = ~np.isnan(oos_perf)
        rank = float(np.sum(oos_perf[valid] <= oos_perf[n_star]))
        w = rank / (np.sum(valid) + 1.0)
        w = min(max(w, 1e-6), 1.0 - 1e-6)
        logits.append(float(np.log(w / (1.0 - w))))
        oos_ranks.append(w)
        is_best_oos_perf.append(float(oos_perf[n_star]))

    pbo = float(np.mean([1.0 if lam <= 0 else 0.0 for lam in logits])) if logits else float("nan")
    return PBOResult(
        pbo=pbo,
        n_splits=len(logits),
        n_strategies=N,
        n_blocks=n_blocks,
        logits=tuple(logits),
        oos_ranks=tuple(oos_ranks),
        is_best_oos_perf=tuple(is_best_oos_perf),
    )
