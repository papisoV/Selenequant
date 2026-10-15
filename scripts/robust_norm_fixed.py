"""Rolling robust normalization for the _robust_norm look-ahead fix experiment
(plan: internal-plan, D1/D2).

Replacement for selene_space_alpha.factors.pandas_bridge._robust_norm:
same math (median/MAD z-score, clip ±5) but computed over a trailing window
instead of the full sample. Applied via monkeypatch (packages/ stay
byte-identical); arm A never imports this module.
"""

from __future__ import annotations

from contextlib import contextmanager

import pandas as pd

import selene_space_alpha.factors.pandas_bridge as pandas_bridge

# D2: 15m × 30 days matches the V8 rolling-factor scale; min_periods is a
# small warmup floor (aligned with the mainline fix in pandas_bridge).
WINDOW = 2880
MIN_PERIODS = 20
CLIP = 5.0


def rolling_robust_norm(series: pd.Series) -> pd.Series:
    median = series.rolling(WINDOW, min_periods=MIN_PERIODS).median()
    mad = (series - median).abs().rolling(WINDOW, min_periods=MIN_PERIODS).median()
    norm = (series - median) / (mad + 1e-6)
    return norm.clip(-CLIP, CLIP).fillna(0.0)


@contextmanager
def patched_robust_norm():
    """Swap pandas_bridge._robust_norm for the rolling version; restore on exit."""
    original = pandas_bridge._robust_norm
    pandas_bridge._robust_norm = rolling_robust_norm
    try:
        yield
    finally:
        pandas_bridge._robust_norm = original
