"""
Strategy orchestration: composite scoring + regime filter + position sizing.

Combines improvements into backtestable strategies:
1. Composite scoring — diversifies signal across low-correlation factors
2. Regime filter — avoids holding during bear markets
3. Position sizing — inverse-volatility weighting reduces drawdown
4. Stop-loss — caps per-position loss to reduce max DD
5. Adaptive n_long — fewer positions in high-vol regime
6. Confluence entry — requires multi-signal confirmation
"""

import logging

import numpy as np
import pandas as pd

from selene_space_ashare.composite import (
    CompositeConfig,
    calc_composite_score,
    MOMENTUM_QUALITY_FACTORS,
    MEAN_REVERSION_FACTORS,
)
from selene_space_ashare.regime import (
    RegimeState,
    TrendRegime,
    VolRegime,
    should_trade,
)
from selene_space_types.stock import StrategyConfigAshare

logger = logging.getLogger(__name__)


def calc_position_sizes(
    selected_stocks: pd.DataFrame,
    vol_target: float = 0.15,
    max_position_pct: float = 0.30,
    vol_col: str = "VOLATILITY_20D",
) -> pd.Series:
    """Calculate inverse-volatility position sizes.

    Assigns more capital to low-vol stocks, less to high-vol.
    Caps any single position at max_position_pct.
    """
    if selected_stocks.empty:
        return pd.Series(dtype=float)
    if vol_col not in selected_stocks.columns:
        return pd.Series(1.0 / len(selected_stocks), index=selected_stocks.index)

    vols = selected_stocks[vol_col].copy()
    median_vol = vols[vols > 0].median()
    vols = vols.where(vols > 0, median_vol if not pd.isna(median_vol) else 0.20)

    inv_vol = 1.0 / vols
    weights = inv_vol / inv_vol.sum()

    capped = weights.clip(upper=max_position_pct)
    capped = capped / capped.sum()

    return capped


def _apply_confluence_filter(
    group: pd.DataFrame,
    config: StrategyConfigAshare,
) -> pd.DataFrame:
    """Apply multi-signal confluence entry filters."""
    valid = group.dropna(subset=["composite_score"])
    if valid.empty:
        return valid

    threshold = valid["composite_score"].quantile(1.0 - config.min_composite_percentile)
    mask = valid["composite_score"] >= threshold

    if config.require_price_above_ma20 and "MA_DEVIATION" in valid.columns:
        mask = mask & (valid["MA_DEVIATION"] > 0)

    if config.require_volume_above_avg and "VOLUME_ZSCORE" in valid.columns:
        mask = mask & (valid["VOLUME_ZSCORE"] > 0)

    return valid[mask]


def _get_adaptive_n_long(regime: RegimeState, config: StrategyConfigAshare) -> int:
    """Reduce position count in high-vol regime."""
    if regime.vol == VolRegime.HIGH_VOL:
        return int(config.n_long_high_vol)
    return int(config.n_long)


def _apply_stop_loss(
    returns: pd.Series,
    stop_loss: float,
) -> pd.Series:
    """Cap per-position returns at stop_loss level."""
    if stop_loss >= 0 or returns.empty:
        return returns
    return returns.clip(lower=stop_loss)


def run_strategy(
    cross_section: pd.DataFrame,
    regime_df: pd.DataFrame,
    config: StrategyConfigAshare,
    forward_col: str = "fwd_1d",
    cost_bps: float = 0.0,
    *,
    limit_gates: bool = True,
) -> pd.DataFrame:
    """Run a full strategy backtest with regime filter, position sizing, stop-loss, and confluence entry.

    Per date:
    1. Look up regime state (missing dates forward-fill the last known
       regime; a miss before any known regime defaults to cash/unknown)
    2. If should_trade is False -> portfolio_return = cash_return
    3. If should_trade is True:
       a. Apply confluence entry filters
       b. Get adaptive n_long based on vol regime
       c. Rank stocks by composite_score, select top n_long
       d. Apply per-position stop-loss
       e. Calculate inverse-vol position sizes
       f. Portfolio return = sum(weight_i * capped_return_i)

    cost_bps: one-way transaction cost charged on position changes
    (changed stock count / n_positions). 0 = backward compatible.

    limit_gates: drop rows sealed at limit-up from each day's selection pool
    (needs ``limit_up_sealed`` from backtest_gates.attach_limit_flags; without
    the column the gate is inert). The limit-down sell-side gate lives in the
    forward column itself (defer_limit_down_exits) — applied upstream.

    Returns:
        DataFrame with [date, portfolio_return, n_positions, trend_regime,
                        vol_regime, is_invested]
    """
    if limit_gates:
        from selene_space_ashare.backtest_gates import mask_limit_up_entries

        cross_section = mask_limit_up_entries(cross_section)

    if "composite_score" not in cross_section.columns:
        cross_section = cross_section.copy()
        cross_section["composite_score"] = calc_composite_score(
            cross_section, config.composite_config
        )

    regime_map: dict[str, RegimeState] = {}
    for _, row in regime_df.iterrows():
        regime_map[str(row["date"])] = RegimeState(
            trend=TrendRegime(row["trend_regime"]),
            vol=VolRegime(row["vol_regime"]),
            index_ma20=float(row["index_ma20"]),
            index_ma60=float(row["index_ma60"]),
            index_close=float(row["index_close"]),
            realized_vol_20d=float(row["realized_vol_20d"]),
        )

    results = []
    prev_symbols: set | None = None
    last_regime: RegimeState | None = None
    for date, group in cross_section.groupby("date"):
        date_str = str(date)
        regime = regime_map.get(date_str)

        if regime is None:
            # Data gap, not strategy semantics: forward-fill the last known
            # regime so a stale index file doesn't force everything to cash.
            if last_regime is None:
                logger.warning(
                    "regime missing for %s, no prior regime, defaulting to cash",
                    date_str,
                )
                prev_symbols = None
                results.append(
                    {
                        "date": date,
                        "portfolio_return": config.cash_return,
                        "n_positions": 0,
                        "trend_regime": "unknown",
                        "vol_regime": "unknown",
                        "is_invested": False,
                    }
                )
                continue
            logger.warning(
                "regime missing for %s, forward-filling from last known regime",
                date_str,
            )
            regime = last_regime
        else:
            last_regime = regime

        if not should_trade(regime, config.strategy_mode):
            prev_symbols = None
            results.append(
                {
                    "date": date,
                    "portfolio_return": config.cash_return,
                    "n_positions": 0,
                    "trend_regime": regime.trend.value,
                    "vol_regime": regime.vol.value,
                    "is_invested": False,
                }
            )
            continue

        filtered = _apply_confluence_filter(group, config)
        n_long = _get_adaptive_n_long(regime, config)

        valid = filtered.dropna(subset=["composite_score", forward_col])
        if len(valid) < n_long:
            prev_symbols = None
            results.append(
                {
                    "date": date,
                    "portfolio_return": config.cash_return,
                    "n_positions": 0,
                    "trend_regime": regime.trend.value,
                    "vol_regime": regime.vol.value,
                    "is_invested": False,
                }
            )
            continue

        ranked = valid.sort_values("composite_score", ascending=False)
        selected = ranked.head(n_long)

        capped_returns = _apply_stop_loss(selected[forward_col], config.stop_loss)

        weights = calc_position_sizes(
            selected, config.vol_target, config.max_position_pct
        )

        portfolio_ret = (weights * capped_returns).sum()

        turnover = 0.0
        if cost_bps > 0:
            cur_symbols = set(selected["symbol"])
            if prev_symbols is not None:
                turnover = len(cur_symbols ^ prev_symbols) / max(1, len(selected))
                portfolio_ret -= cost_bps / 10000.0 * turnover
            prev_symbols = cur_symbols

        results.append(
            {
                "date": date,
                "portfolio_return": portfolio_ret,
                "n_positions": len(selected),
                "trend_regime": regime.trend.value,
                "vol_regime": regime.vol.value,
                "is_invested": True,
                "turnover": turnover,
            }
        )

    return pd.DataFrame(results)


def strategy_performance_metrics(
    daily_returns: pd.Series,
    is_invested: pd.Series,
    freq: int = 252,
) -> dict:
    """Enhanced performance metrics including regime-conditional stats."""
    invested_mask = is_invested.astype(bool)
    invested_returns = daily_returns[invested_mask]

    if invested_returns.empty:
        return {
            "cumulative_return": np.nan,
            "annual_return": np.nan,
            "sharpe": np.nan,
            "max_drawdown": np.nan,
            "win_rate": np.nan,
            "volatility": np.nan,
            "time_in_market": 0.0,
            "invested_win_rate": np.nan,
            "calmar_ratio": np.nan,
            "sortino_ratio": np.nan,
            "max_consecutive_losses": np.nan,
        }

    cum_ret = (1 + daily_returns).prod() - 1
    n_periods = len(daily_returns)
    annual_ret = (1 + cum_ret) ** (freq / n_periods) - 1 if n_periods > 0 else np.nan

    vol = invested_returns.std() * np.sqrt(freq)
    sharpe = (
        (invested_returns.mean() / invested_returns.std()) * np.sqrt(freq)
        if invested_returns.std() > 0
        else np.nan
    )

    cum_series = (1 + daily_returns).cumprod()
    running_max = cum_series.cummax()
    drawdown = cum_series / running_max - 1
    max_dd = drawdown.min()

    win_rate = (invested_returns > 0).mean()
    downside = invested_returns[invested_returns < 0]
    downside_dev = (
        np.sqrt((downside**2).mean()) * np.sqrt(freq) if len(downside) > 0 else np.nan
    )
    sortino = (
        (invested_returns.mean() * freq) / downside_dev
        if downside_dev and downside_dev > 0
        else np.nan
    )

    calmar = annual_ret / abs(max_dd) if max_dd != 0 else np.nan

    losses = (invested_returns < 0).astype(int)
    max_streak = 0
    current = 0
    for val in losses:
        current = current + 1 if val else 0
        max_streak = max(max_streak, current)

    return {
        "cumulative_return": cum_ret,
        "annual_return": annual_ret,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "win_rate": win_rate,
        "volatility": vol,
        "time_in_market": invested_mask.mean(),
        "invested_win_rate": win_rate,
        "calmar_ratio": calmar,
        "sortino_ratio": sortino,
        "max_consecutive_losses": max_streak,
    }


def make_trend_follow_strategy(
    n_long: int = 5,
    n_long_high_vol: int = 3,
    holding_period: int = 1,
    stop_loss: float = -0.02,
    vol_target: float = 0.15,
) -> tuple[StrategyConfigAshare, list[str]]:
    """Trend-following: momentum-quality composite, skip BEAR regime, with stop-loss + confluence."""
    composite = CompositeConfig(
        name="momentum_quality",
        factor_weights={
            f: 1.0 / len(MOMENTUM_QUALITY_FACTORS) for f in MOMENTUM_QUALITY_FACTORS
        },
        direction=1,
    )
    config = StrategyConfigAshare(
        name="trend_follow",
        composite_config=composite,
        strategy_mode="trend_follow",
        n_long=n_long,
        n_long_high_vol=n_long_high_vol,
        holding_period=holding_period,
        stop_loss=stop_loss,
        vol_target=vol_target,
    )
    return config, MOMENTUM_QUALITY_FACTORS


def make_mean_reversion_strategy(
    n_long: int = 5,
    n_long_high_vol: int = 3,
    holding_period: int = 1,
    stop_loss: float = -0.02,
    vol_target: float = 0.15,
) -> tuple[StrategyConfigAshare, list[str]]:
    """Mean-reversion: MR composite, skip BEAR+HIGH_VOL regime, with stop-loss + confluence."""
    composite = CompositeConfig(
        name="mean_reversion",
        factor_weights={
            f: 1.0 / len(MEAN_REVERSION_FACTORS) for f in MEAN_REVERSION_FACTORS
        },
        direction=1,
    )
    config = StrategyConfigAshare(
        name="mean_reversion",
        composite_config=composite,
        strategy_mode="mean_reversion",
        n_long=n_long,
        n_long_high_vol=n_long_high_vol,
        holding_period=holding_period,
        stop_loss=stop_loss,
        vol_target=vol_target,
    )
    return config, MEAN_REVERSION_FACTORS
