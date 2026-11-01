"""
Market regime detection using index-level data.

Detects trend regime (bull/bear/neutral) and volatility regime
from CSI 300 or any index daily data. Used to filter trading signals
and avoid holding during unfavorable market conditions.
"""

import numpy as np
import pandas as pd

from selene_space_types.stock import RegimeState, TrendRegime, VolRegime


def calc_index_regime(
    index_daily: pd.DataFrame,
    trend_ma_slow: int = 60,
    trend_ma_fast: int = 20,
    vol_window: int = 20,
    vol_lookback: int = 252,
) -> pd.DataFrame:
    """Calculate regime states from index daily data.

    Args:
        index_daily: DataFrame with [date, close] columns
        trend_ma_slow: slow MA period for trend regime
        trend_ma_fast: fast MA period for trend confirmation
        vol_window: rolling window for realized vol
        vol_lookback: lookback for vol percentile thresholds

    Returns:
        DataFrame with [date, trend_regime, vol_regime, index_close,
                        index_ma20, index_ma60, realized_vol_20d]
    """
    df = index_daily.copy()
    df["index_ma20"] = df["close"].rolling(trend_ma_fast, min_periods=1).mean()
    df["index_ma60"] = df["close"].rolling(trend_ma_slow, min_periods=1).mean()

    conditions = [
        df["close"] >= df["index_ma60"],
        df["close"] <= df["index_ma20"],
    ]
    choices = [TrendRegime.BULL.value, TrendRegime.BEAR.value]
    df["trend_regime"] = np.select(conditions, choices, default=TrendRegime.NEUTRAL.value)

    returns = df["close"].pct_change()
    df["realized_vol_20d"] = returns.rolling(vol_window, min_periods=5).std() * np.sqrt(252)

    vol_p25 = df["realized_vol_20d"].rolling(vol_lookback, min_periods=60).quantile(0.25)
    vol_p75 = df["realized_vol_20d"].rolling(vol_lookback, min_periods=60).quantile(0.75)

    vol_conditions = [
        df["realized_vol_20d"] <= vol_p25,
        df["realized_vol_20d"] >= vol_p75,
    ]
    vol_choices = [VolRegime.LOW_VOL.value, VolRegime.HIGH_VOL.value]
    df["vol_regime"] = np.select(vol_conditions, vol_choices, default=VolRegime.NORMAL_VOL.value)

    return df[[
        "date", "trend_regime", "vol_regime", "close",
        "index_ma20", "index_ma60", "realized_vol_20d",
    ]].rename(columns={"close": "index_close"})


def get_regime_on_date(regime_df: pd.DataFrame, date: str) -> RegimeState:
    """Look up regime state for a specific date."""
    date_str = str(date)
    row = regime_df[regime_df["date"].astype(str) == date_str]
    if row.empty:
        return RegimeState()
    r = row.iloc[0]
    return RegimeState(
        trend=TrendRegime(r["trend_regime"]),
        vol=VolRegime(r["vol_regime"]),
        index_ma20=float(r["index_ma20"]),
        index_ma60=float(r["index_ma60"]),
        index_close=float(r["index_close"]),
        realized_vol_20d=float(r["realized_vol_20d"]),
    )


def should_trade(regime: RegimeState, strategy_mode: str = "trend_follow") -> bool:
    """Determine if the strategy should be active given the regime.

    Args:
        regime: current regime state
        strategy_mode: one of "trend_follow", "mean_reversion", "always"

    Logic:
        - trend_follow: trade in BULL or NEUTRAL, skip BEAR
        - mean_reversion: trade in NEUTRAL or BEAR+LOW_VOL/NORMAL_VOL
        - always: trade regardless (baseline)
    """
    if strategy_mode == "always":
        return True

    if strategy_mode == "trend_follow":
        return regime.trend in (TrendRegime.BULL, TrendRegime.NEUTRAL)

    if strategy_mode == "mean_reversion":
        if regime.trend in (TrendRegime.NEUTRAL, TrendRegime.BULL):
            return True
        return regime.vol != VolRegime.HIGH_VOL

    return True
