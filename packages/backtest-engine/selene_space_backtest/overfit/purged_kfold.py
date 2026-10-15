"""Purged & Embargoed K-Fold cross-validation.

Reference
---------
Lopez de Prado, M. (2018). "Advances in Financial Machine Learning",
    Chapter 7: Cross-Validation in Finance.
"""
from __future__ import annotations

from typing import Callable, Iterator

import numpy as np
import pandas as pd


class PurgedKFold:
    """K-Fold splitter that purges overlapping samples and applies an embargo.

    Parameters
    ----------
    n_splits : Number of folds.
    label_times : Index = sample start time (t0). Values = sample end time (t1).
        Must be sorted by index.
    embargo_pct : Fraction of observations to embargo after each test fold.
    """

    def __init__(self, n_splits: int, label_times: pd.Series, embargo_pct: float = 0.0) -> None:
        if not label_times.index.is_monotonic_increasing:
            raise ValueError("label_times index must be sorted ascending")
        self.n_splits = int(n_splits)
        self.label_times = label_times
        self.embargo_pct = float(embargo_pct)

    def split(self, X: np.ndarray | None = None) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        t1 = self.label_times
        n = t1.shape[0]
        indices = np.arange(n)
        embargo = int(n * self.embargo_pct)

        test_starts = [(i[0], i[-1] + 1) for i in np.array_split(indices, self.n_splits)]

        for start, end in test_starts:
            test_idx = indices[start:end]
            t0_test = t1.index[start]
            t1_test_max = t1.iloc[start:end].max()

            train_mask = np.ones(n, dtype=bool)
            train_mask[start:end] = False
            overlap = (t1.values >= t0_test) & (t1.index.values <= t1_test_max)
            train_mask &= ~overlap

            if embargo > 0 and end < n:
                embargo_end = min(end + embargo, n)
                train_mask[end:embargo_end] = False

            train_idx = indices[train_mask]
            yield train_idx, test_idx


def cross_val_score_purged(
    estimator: object,
    X: np.ndarray,
    y: np.ndarray,
    label_times: pd.Series,
    n_splits: int = 6,
    embargo_pct: float = 0.01,
    scorer: Callable | None = None,
) -> np.ndarray:
    """Convenience wrapper: run a purged & embargoed CV and return fold scores."""
    cv = PurgedKFold(n_splits=n_splits, label_times=label_times, embargo_pct=embargo_pct)
    X = np.asarray(X)
    y = np.asarray(y)
    scores: list[float] = []
    for train_idx, test_idx in cv.split(X):
        if len(train_idx) == 0 or len(test_idx) == 0:
            continue
        estimator.fit(X[train_idx], y[train_idx])  # type: ignore[attr-defined]
        if scorer is None:
            scores.append(float(estimator.score(X[test_idx], y[test_idx])))  # type: ignore[attr-defined]
        else:
            scores.append(float(scorer(estimator, X[test_idx], y[test_idx])))
    return np.array(scores)
