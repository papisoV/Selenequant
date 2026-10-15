"""BTC background feature family (QD-absorption Phase 2, B2) — factor layer.

Three background features over the BTC relative-strength series built by
:mod:`selene_space_alpha.factors.btc_background`:

- ``BTC_BG_RS_LEVEL``   — smoothed relative-strength level (z-scored over a
  trailing window so the raw unit is dimensionless and stationary-ish)
- ``BTC_BG_RS_SLOPE``   — 5-bar slope of the background level (direction of
  the regime shift)
- ``BTC_BG_RS_EXTREME`` — boolean-ish marker: |z| >= 2 (regime extreme)

Feature-contract note: existing factors are single-asset ``(data) ->
Series``. A background feature needs cross-asset data the incoming OHLCV
frame cannot carry, so each compute function loads the background series
from klines.db and aligns it onto ``data.index`` by exact timestamp, NaN on
gap (never zero-fill, per the US-201 NaN contract). The db path is
overridable via ``BTC_BG_DB_PATH`` for tests.

Domain-analogue discipline (cleanroom): the MT5-domain original used DXY/OIL
as background; the crypto-domain background here is BTC relative strength —
an independent definition (see b2-dominance-deviation.md), not a port.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

from .btc_background import DEFAULT_KLINES_DB_PATH, build_background

#: Environment override for the klines.db location (tests).
BTC_BG_DB_PATH_ENV = "BTC_BG_DB_PATH"

#: Trailing z-score window for the RS level feature.
ZSCORE_WINDOW = 60

#: Slope lookback for the RS direction feature.
SLOPE_WINDOW = 5

#: |z| threshold for the regime-extreme marker.
EXTREME_Z = 2.0


def _resolve_db_path() -> Path:
    env = os.environ.get(BTC_BG_DB_PATH_ENV)
    if env:
        return Path(env)
    return DEFAULT_KLINES_DB_PATH


def _align_background(data: pd.DataFrame) -> pd.Series:
    """Build the background series and align it onto the incoming frame's index.

    Exact-timestamp join; bars absent from the background series are NaN.
    A wholly unavailable background (db missing or background symbols absent)
    degrades to an all-NaN series rather than raising — vocab features must
    stay computable on any OHLCV frame (the VM computes all 103 features
    eagerly; a raising factor would break unrelated token execution). The
    all-NaN result honors the NaN contract: absence is never zero-filled at
    the factor layer.
    """
    try:
        bg = build_background(db_path=_resolve_db_path())
    except (FileNotFoundError, ValueError):
        return pd.Series(np.nan, index=data.index)
    return bg.reindex(data.index)


def _bg_zscore(bg: pd.Series) -> pd.Series:
    """Trailing z-score of the background level (NaN-preserving)."""
    mean = bg.rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW).mean()
    std = bg.rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW).std()
    z = (bg - mean) / std.replace(0.0, np.nan)
    return z


def compute_btc_bg_rs_level(data: pd.DataFrame) -> pd.Series:
    """Background level: trailing z-score of BTC relative strength."""
    bg = _align_background(data)
    return _bg_zscore(bg)


def compute_btc_bg_rs_slope(data: pd.DataFrame) -> pd.Series:
    """Background slope: 5-bar change of the RS level (direction of shift)."""
    bg = _align_background(data)
    return bg.diff(SLOPE_WINDOW)


def compute_btc_bg_rs_extreme(data: pd.Series | pd.DataFrame) -> pd.Series:
    """Regime extreme: 1.0 when |z| >= 2, 0.0 when defined and not extreme, NaN
    when the background is undefined (never fabricates 'not extreme')."""
    bg = _align_background(pd.DataFrame(index=data.index))
    z = _bg_zscore(bg)
    out = pd.Series(np.nan, index=data.index)
    defined = z.notna()
    out[defined & (z.abs() >= EXTREME_Z)] = 1.0
    out[defined & (z.abs() < EXTREME_Z)] = 0.0
    return out


BTC_BG_FACTORS: dict[str, tuple[object, str]] = {
    "btc_bg_rs_level": (compute_btc_bg_rs_level, "BTC rel-strength background z (60)"),
    "btc_bg_rs_slope": (compute_btc_bg_rs_slope, "BTC rel-strength slope (5)"),
    "btc_bg_rs_extreme": (compute_btc_bg_rs_extreme, "BTC bg regime extreme (|z|>=2)"),
}
