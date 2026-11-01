"""Market regime perception for the agent stack.

Turns a candle series into three numbers an agent can reason about: how
strongly the market trends (ADX proxy), how wild it is (ATR share of price),
and which coarse regime it sits in. Deliberately simple — single timeframe,
three indicators, no magic.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

import numpy as np
import pandas as pd

from selene_space_types.agent import MarketRegime, MarketState
from selene_space_types.market import KlineEvent

_ADX_PERIOD = 14
_ATR_PERIOD = 14
_MOMENTUM_PERIOD = 10

# ADX at/above this reads as a trending market; below 60% of it, ranging.
_TREND_ADX = 25.0
_STRONG_ADX = 40.0
_RANGING_RATIO = 0.6

# ATR as fraction of price: these marks map to qualitative volatility bands.
_VOL_QUIET = 0.01
_VOL_NORMAL = 0.025
_VOL_WILD = 0.05


class InsufficientDataError(Exception):
    """Kline history is too short for the configured indicator periods."""


def klines_to_dataframe(klines: Sequence[KlineEvent]) -> pd.DataFrame:
    """Flatten candles into a timestamp-sorted OHLCV frame."""
    if not klines:
        raise InsufficientDataError("No kline data provided")
    frame = pd.DataFrame(
        [
            {
                "timestamp": k.timestamp,
                "open": k.open,
                "high": k.high,
                "low": k.low,
                "close": k.close,
                "volume": k.volume,
            }
            for k in klines
        ]
    )
    return frame.sort_values("timestamp").reset_index(drop=True)


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    legs = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return legs.max(axis=1)


def compute_atr(df: pd.DataFrame, period: int = _ATR_PERIOD) -> pd.Series:
    """Rolling mean of true range over *period* bars."""
    return _true_range(df).rolling(window=period, min_periods=1).mean()


def compute_adx_proxy(df: pd.DataFrame, period: int = _ADX_PERIOD) -> pd.Series:
    """Directional-strength stand-in for Wilder's ADX, on the same 0-100 scale.

    Smooths up/down swings into DI+/DI-, then averages the resulting DX so
    the number reflects sustained direction rather than one big bar.
    """
    swing_up = df["high"].diff()
    swing_down = -df["low"].diff()

    plus_dm = pd.Series(
        np.where((swing_up > swing_down) & (swing_up > 0), swing_up, 0.0),
        index=df.index,
    )
    minus_dm = pd.Series(
        np.where((swing_down > swing_up) & (swing_down > 0), swing_down, 0.0),
        index=df.index,
    )

    true_range_mean = compute_atr(df, period)
    smooth_plus = plus_dm.rolling(window=period, min_periods=1).mean()
    smooth_minus = minus_dm.rolling(window=period, min_periods=1).mean()

    safe_range = true_range_mean.replace(0, np.nan)
    di_plus = 100.0 * smooth_plus / safe_range
    di_minus = 100.0 * smooth_minus / safe_range

    directional_sum = (di_plus + di_minus).replace(0, np.nan)
    dx = 100.0 * (di_plus - di_minus).abs() / directional_sum

    adx = dx.rolling(window=period, min_periods=1).mean()
    return adx.fillna(0.0)


def compute_momentum(df: pd.DataFrame, period: int = _MOMENTUM_PERIOD) -> pd.Series:
    """Close-to-close percent move across *period* bars, in percent."""
    return df["close"].pct_change(periods=period).fillna(0.0) * 100.0


def classify_regime(adx: float, momentum: float) -> MarketRegime:
    """Pick TRENDING / RANGING / VOLATILE from ADX (momentum reserved for v2)."""
    if adx >= _TREND_ADX:
        return MarketRegime.TRENDING
    if adx < _TREND_ADX * _RANGING_RATIO:
        return MarketRegime.RANGING
    return MarketRegime.VOLATILE


def classify_trend_strength(adx: float) -> float:
    """ADX on a 0-1 leash; values past 100 clamp at the ceiling."""
    return float(min(max(adx / 100.0, 0.0), 1.0))


def classify_volatility(atr_pct: float) -> float:
    """Scale ATR-as-price-fraction onto 0-1 with the 5% mark as saturation."""
    if atr_pct <= 0:
        return 0.0
    return round(min(atr_pct / _VOL_WILD, 1.0), 4)


class MarketStateEngine:
    """Single-timeframe analyzer: candles in, :class:`MarketState` out."""

    def __init__(
        self,
        *,
        adx_period: int = _ADX_PERIOD,
        atr_period: int = _ATR_PERIOD,
        momentum_period: int = _MOMENTUM_PERIOD,
    ) -> None:
        self._adx_period = adx_period
        self._atr_period = atr_period
        self._momentum_period = momentum_period

    def analyze(
        self,
        symbol: str,
        klines: Sequence[KlineEvent] | pd.DataFrame,
    ) -> MarketState:
        """Score the latest bar of *symbol* from its recent candle history.

        Accepts raw events or a ready-made OHLCV frame; raises
        :class:`InsufficientDataError` when history is shorter than the
        longest indicator window needs.
        """
        df = klines.copy() if isinstance(klines, pd.DataFrame) else klines_to_dataframe(klines)

        longest = max(self._adx_period, self._atr_period, self._momentum_period)
        min_bars = longest + 1
        if len(df) < min_bars:
            raise InsufficientDataError(f"Need at least {min_bars} bars, got {len(df)}")

        adx_now = float(compute_adx_proxy(df, self._adx_period).iloc[-1])
        last_close = float(df["close"].iloc[-1])
        atr_pct = (
            float(compute_atr(df, self._atr_period).iloc[-1]) / last_close
            if last_close > 0
            else 0.0
        )

        return MarketState(
            symbol=symbol,
            regime=classify_regime(adx_now, 0.0),
            trend_strength=round(classify_trend_strength(adx_now), 4),
            volatility=classify_volatility(atr_pct),
            timestamp=int(time.time() * 1000),
        )
