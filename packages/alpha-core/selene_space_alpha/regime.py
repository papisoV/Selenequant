"""Vectorized regime classifier for adaptive strategy routing.

Classifies each bar in a DataFrame as TRENDING / RANGING / VOLATILE
using Wilder-smoothed ADX and ATR. Returns a full Series of regime
labels suitable for vectorized backtest loops.

Reuses the production ADX indicator from strategy-engine rather than
reimplementing Wilder smoothing.

Publishes RegimeChangedEvent when regime transitions are detected.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
_sys_path_added = str(_PROJECT_ROOT / "packages" / "strategy-engine")
if _sys_path_added not in sys.path:
    sys.path.insert(0, _sys_path_added)

from selene_space_strategy.indicators.adx import ADX


@dataclass(frozen=True)
class RegimeConfig:
    adx_period: int = 14
    adx_smoothing: int | None = None
    atr_period: int = 14
    trending_threshold: float = 25.0
    ranging_threshold: float = 15.0


def classify_regime_series(
    df: pd.DataFrame,
    config: RegimeConfig = RegimeConfig(),
    symbol: str = "",
) -> pd.Series:
    """Classify regime for every bar in a DataFrame.

    Args:
        df: DataFrame with 'high', 'low', 'close' columns.
        config: Thresholds and periods.
        symbol: Optional symbol for event publishing.

    Returns:
        Series of string labels: "trending", "ranging", or "volatile".
    """
    adx_ind = ADX(period=config.adx_period, adx_period=config.adx_smoothing)
    result = adx_ind.calculate_full(df)

    adx = result.adx.fillna(0.0)
    regimes = np.where(
        adx >= config.trending_threshold,
        "trending",
        np.where(adx < config.ranging_threshold, "ranging", "volatile"),
    )
    regime_series = pd.Series(regimes, index=df.index, name="regime")

    # Publish RegimeChangedEvent for the last transition (if any)
    if symbol and len(regime_series) >= 2:
        _publish_last_regime_change(regime_series, symbol, df)

    return regime_series


def _publish_last_regime_change(regime_series: pd.Series, symbol: str, df: pd.DataFrame) -> None:
    """Publish a RegimeChangedEvent if the last bar changed regime."""
    try:
        from selene_space_core.events import get_event_bus, RegimeChangedEvent

        last = regime_series.iloc[-1]
        prev = regime_series.iloc[-2]
        if last != prev:
            ts_col = "timestamp" if "timestamp" in df.columns else df.index
            ts = int(df.iloc[-1].get(ts_col, 0)) if isinstance(df.iloc[-1], dict) else int(df.index[-1]) if hasattr(df.index, '__getitem__') else 0
            get_event_bus().publish(RegimeChangedEvent(
                symbol=symbol,
                old_regime=str(prev),
                new_regime=str(last),
                timestamp=ts,
            ))
    except Exception:
        pass  # Event publishing must not break regime classification


def compute_trend_direction(df: pd.DataFrame, config: RegimeConfig = RegimeConfig()) -> pd.Series:
    """Return +1 (uptrend) / -1 (downtrend) / 0 (neutral) per bar.

    Uses DI+ vs DI- from the ADX calculation.
    """
    adx_ind = ADX(period=config.adx_period, adx_period=config.adx_smoothing)
    result = adx_ind.calculate_full(df)

    plus_di = result.plus_di.fillna(0.0)
    minus_di = result.minus_di.fillna(0.0)
    diff = plus_di - minus_di

    direction = np.where(diff > 5, 1, np.where(diff < -5, -1, 0))
    return pd.Series(direction, index=df.index, name="trend_direction")


def compute_atr_pct(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR as percentage of close price."""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = tr.rolling(window=period, min_periods=1).mean()
    return (atr / (close + 1e-9)).fillna(0.0)
