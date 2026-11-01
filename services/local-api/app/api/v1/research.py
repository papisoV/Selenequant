"""Factor research endpoints for quant analysis.

Provides IC/IR calculation for factor evaluation:
- IC (Information Coefficient): correlation between factor value and future return
- IR (Information Ratio): IC_mean / IC_std, measures factor stability
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, List

import numpy as np
import pandas as pd
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.core.dependencies import AuthenticatedUser, build_response_meta, optional_user
from selene_space_core.kline_paths import default_klines_db_path
from selene_space_types.common import APIResponse, ErrorInfo
from selene_space_types.calendars import annualization_factor, resolve_market_domain

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/research", tags=["research"])

# Available factors from strategy engine indicators
AVAILABLE_FACTORS = [
    {"id": "rsi", "name": "RSI", "category": "momentum"},
    {"id": "macd", "name": "MACD", "category": "momentum"},
    {"id": "kdj_k", "name": "KDJ K", "category": "momentum"},
    {"id": "stochastic_k", "name": "Stochastic K", "category": "momentum"},
    {"id": "sma", "name": "SMA", "category": "trend"},
    {"id": "ema", "name": "EMA", "category": "trend"},
    {"id": "adx", "name": "ADX", "category": "trend"},
    {"id": "atr", "name": "ATR", "category": "volatility"},
    {"id": "bollinger_upper", "name": "Bollinger Upper", "category": "volatility"},
    {"id": "bollinger_lower", "name": "Bollinger Lower", "category": "volatility"},
    {"id": "obv", "name": "OBV", "category": "volume"},
    {"id": "cmf", "name": "CMF", "category": "volume"},
    {"id": "mfi", "name": "MFI", "category": "volume"},
    {"id": "tg_event", "name": "TG 事件(±24h)", "category": "event"},
    {"id": "tg_regime", "name": "TG 波动窗口(48h)", "category": "event"},
]


class FactorICIRRequest(BaseModel):
    """Request for IC/IR calculation."""

    symbol: str = Field(..., description="Trading pair (e.g. BTC/USDT)")
    interval: str = Field("1h", description="Kline interval (e.g. 1h, 4h, 1d)")
    factors: List[str] = Field(..., description="Factor IDs to analyze")
    lookback_bars: int = Field(
        500, ge=50, le=2000, description="Number of bars to analyze"
    )
    forward_bars: int = Field(
        1, ge=1, le=10, description="Forward bars for return calculation"
    )
    exchange: str = Field("binance", description="Exchange: binance or okx")
    market_type: str = Field("spot", description="Market type: spot or futures")


class FactorICIRResult(BaseModel):
    """IC/IR result for a single factor."""

    factor: str
    factor_name: str
    category: str
    ic: float  # Information Coefficient (mean correlation)
    ir: float  # Information Ratio (IC_mean / IC_std)
    ic_std: float
    positive_pct: float  # Percentage of positive IC values
    rank: int = 0


class FactorCorrelationResult(BaseModel):
    """Correlation between two factors."""

    factor1: str
    factor2: str
    correlation: float


class LifecycleEpoch(BaseModel):
    """One state-machine cycle over an IC epoch."""

    epoch: int
    rolling_ic: float
    mean_ic: float
    icir: float
    verdict: str
    reason: str
    state_after: str


class LifecycleRow(BaseModel):
    """Per-factor lifecycle verdict row."""

    factor: str
    factor_name: str
    category: str
    state: str
    reason: str
    mean_ic: float
    rolling_ic: float
    baseline_ic: float
    icir: float
    ls_spread: float
    turnover_top_quintile: float
    ic_history: List[float]
    epochs: List[LifecycleEpoch]


@router.get("/factors")
async def list_available_factors(
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[List[dict]]:
    """List available factors for IC/IR analysis."""
    return APIResponse(
        data=AVAILABLE_FACTORS,
        meta=build_response_meta(request),
        error=None,
    )


@router.post("/factors/ic-ir")
async def calculate_factor_ic_ir(
    request: Request,
    body: FactorICIRRequest,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict]:
    """Calculate IC/IR for selected factors.

    IC = correlation(factor_value_t, forward_return_t+N)
    IR = mean(IC) / std(IC)

    Higher IC = better factor predictive power
    Higher IR = more stable factor performance
    """
    try:
        # Load kline data
        kline_df = await _load_klines_for_factor(
            body.symbol,
            body.interval,
            body.lookback_bars,
            body.exchange,
            body.market_type,
        )

        if kline_df.empty or len(kline_df) < 50:
            return APIResponse(
                data=None,
                meta=build_response_meta(request),
                error=ErrorInfo(
                    code="IC_IR_NOT_ENOUGH_DATA",
                    message=f"Not enough data for {body.symbol} (need at least 50 bars)",
                ),
            )

        # Calculate forward returns
        forward_returns = _calculate_forward_returns(kline_df, body.forward_bars)

        # Calculate factor values and IC/IR
        ic_ir_results: List[FactorICIRResult] = []
        factor_series: dict[str, pd.Series] = {}
        scatter_data: dict[str, list[dict]] = {}

        for factor_id in body.factors:
            try:
                factor_values = _calculate_factor_values(
                    kline_df, factor_id, body.symbol
                )
                if factor_values is None or factor_values.empty:
                    continue

                factor_series[factor_id] = factor_values

                # Per-bar (factor_value, forward_return) samples for the frontend scatter plot
                aligned_scatter = pd.DataFrame(
                    {
                        "factor_value": factor_values,
                        "forward_return": forward_returns,
                    }
                ).dropna()
                if len(aligned_scatter) > 0:
                    sampled = aligned_scatter.sample(
                        min(len(aligned_scatter), 300), random_state=42
                    )
                    scatter_data[factor_id] = [
                        {
                            "factor_value": round(float(r.factor_value), 6),
                            "forward_return": round(float(r.forward_return), 6),
                        }
                        for _, r in sampled.iterrows()
                    ]

                # Calculate IC for each bar
                ic_values = _calculate_rolling_ic(factor_values, forward_returns)

                if len(ic_values) > 0:
                    ic_mean = float(np.nanmean(ic_values))
                    ic_std = float(np.nanstd(ic_values))
                    ir_value = ic_mean / ic_std if ic_std > 0 else 0
                    positive_pct = float(np.sum(ic_values > 0) / len(ic_values))

                    factor_info = next(
                        (f for f in AVAILABLE_FACTORS if f["id"] == factor_id),
                        {"name": factor_id, "category": "unknown"},
                    )

                    ic_ir_results.append(
                        FactorICIRResult(
                            factor=factor_id,
                            factor_name=factor_info["name"],
                            category=factor_info["category"],
                            ic=ic_mean,
                            ir=ir_value,
                            ic_std=ic_std,
                            positive_pct=positive_pct,
                        )
                    )

            except Exception as e:
                logger.warning(f"Failed to calculate factor {factor_id}: {e}")
                continue

        # Rank by IC descending
        ic_ir_results.sort(key=lambda x: x.ic, reverse=True)
        for i, r in enumerate(ic_ir_results):
            r.rank = i + 1

        # Calculate factor correlations
        correlations: List[FactorCorrelationResult] = []
        factor_ids = list(factor_series.keys())
        for i, f1 in enumerate(factor_ids):
            for j, f2 in enumerate(factor_ids):
                if j > i:
                    corr = float(factor_series[f1].corr(factor_series[f2]))
                    correlations.append(
                        FactorCorrelationResult(
                            factor1=f1,
                            factor2=f2,
                            correlation=corr,
                        )
                    )

        return APIResponse(
            data={
                "symbol": body.symbol,
                "interval": body.interval,
                "lookback_bars": body.lookback_bars,
                "forward_bars": body.forward_bars,
                "ic_ir_results": [r.model_dump() for r in ic_ir_results],
                "correlations": [c.model_dump() for c in correlations],
                "scatter_samples": scatter_data,
                "total_factors_analyzed": len(ic_ir_results),
                "bar_count": len(kline_df),
            },
            meta=build_response_meta(request),
            error=None,
        )

    except Exception as e:
        logger.error(f"IC/IR calculation failed: {e}", exc_info=True)
        return APIResponse(
            data=None,
            meta=build_response_meta(request),
            error=ErrorInfo(code="IC_IR_CALCULATION_ERROR", message=str(e)),
        )


def _lifecycle_decide(
    rolling_ic: float,
    mean_ic: float,
    baseline_ic: float | None,
    ic_floor: float,
    decay_factor: float,
) -> tuple[str, str]:
    """Temporal-decay verdict mirroring factor_lifecycle._decide.

    Decay = recent IC shrunk below decay_factor of the first-epoch baseline,
    or IC sits below ic_floor outright. Single-symbol adaptation: the IC
    series is the time-series rolling rank-IC (per-bar), so "recent" is the
    epoch's mean of that series.
    """
    ic_shrunk = (
        baseline_ic is not None
        and not np.isnan(rolling_ic)
        and rolling_ic < baseline_ic * decay_factor
    )
    weak_ic = (not np.isnan(rolling_ic) and rolling_ic < ic_floor) or (
        not np.isnan(mean_ic) and mean_ic < ic_floor
    )
    if ic_shrunk and weak_ic:
        return "decay", "recent IC below decay_factor of baseline and below floor"
    if ic_shrunk:
        return (
            "decay",
            f"recent IC {rolling_ic:.4f} below {decay_factor:.0%} of baseline {baseline_ic:.4f}",
        )
    if weak_ic:
        return "decay", "recent IC below floor"
    return "healthy", "recent IC stable vs baseline"


def _lifecycle_for_factor(
    factor_values: pd.Series,
    forward_returns: pd.Series,
    *,
    epoch_bars: int = 100,
    ic_floor: float = 0.02,
    decay_factor: float = 0.6,
) -> dict | None:
    """Run the lifecycle state machine over one factor's IC series.

    Time-series rank-IC per bar (single-symbol pattern from the sweep scripts:
    rank both sides, rolling Pearson), then split into epochs and replay
    ACTIVE→WATCH→DECAYING→RETIRED via the calibrated transitions.
    """
    from selene_space_alpha.factor_lifecycle import FactorState, transition

    aligned = pd.DataFrame({"factor": factor_values, "ret": forward_returns}).dropna()
    if len(aligned) < epoch_bars * 2:
        return None

    f_rank = aligned["factor"].rank(pct=True)
    r_rank = aligned["ret"].rank(pct=True)
    w = min(120, max(20, epoch_bars // 5))

    # rolling Pearson of ranks == rolling spearman; vectorized via covariance identity
    m_f = f_rank.rolling(w).mean()
    m_r = r_rank.rolling(w).mean()
    cov = (f_rank * r_rank).rolling(w).mean() - m_f * m_r
    std_f = f_rank.rolling(w).std()
    std_r = r_rank.rolling(w).std()
    ic_series = cov / (std_f * std_r)
    ic_series = ic_series.dropna()
    if len(ic_series) < epoch_bars:
        return None

    # Sign orientation: a negative-IC factor is an inverted positive-IC factor.
    # The kernel's decay check (rolling < baseline × decay_factor) is only
    # sign-correct for positive IC — without orienting, stable contrarian
    # factors get flagged as decayed (real-data run 0922: all indicators RETIRED).
    first_epoch_mean = float(ic_series.iloc[:epoch_bars].mean())
    if first_epoch_mean < 0:
        ic_series = -ic_series

    epochs_data: list[dict] = []
    baseline_ic: float | None = None
    state = FactorState.ACTIVE
    for i, start in enumerate(range(0, len(ic_series) - epoch_bars + 1, epoch_bars)):
        chunk = ic_series.iloc[start : start + epoch_bars]
        rolling_ic = float(chunk.mean())
        # trailing mean IC over everything seen so far (no look-ahead)
        mean_ic = float(ic_series.iloc[: start + epoch_bars].mean())
        icir = (
            float(mean_ic / ic_series.iloc[: start + epoch_bars].std())
            if ic_series.iloc[: start + epoch_bars].std() > 0
            else 0.0
        )
        if baseline_ic is None and not np.isnan(rolling_ic):
            baseline_ic = rolling_ic
        verdict, reason = _lifecycle_decide(
            rolling_ic, mean_ic, baseline_ic, ic_floor, decay_factor
        )
        state = transition(state, verdict)
        epochs_data.append(
            {
                "epoch": i,
                "rolling_ic": rolling_ic,
                "mean_ic": mean_ic,
                "icir": icir,
                "verdict": verdict,
                "reason": reason,
                "state_after": state.value,
            }
        )

    last = epochs_data[-1]
    ls_spread = _long_short_spread(aligned["factor"], aligned["ret"])
    turnover = _factor_turnover(factor_values.reindex(aligned.index))
    return {
        "state": last["state_after"],
        "reason": last["reason"],
        "mean_ic": last["mean_ic"],
        "rolling_ic": last["rolling_ic"],
        "baseline_ic": baseline_ic if baseline_ic is not None else float("nan"),
        "icir": last["icir"],
        "ls_spread": ls_spread,
        "turnover_top_quintile": turnover,
        "ic_history": [round(float(x), 4) for x in ic_series.tolist()],
        "epochs": epochs_data,
    }


def _long_short_spread(factor: pd.Series, ret: pd.Series, n_groups: int = 5) -> float:
    """Mean return spread between top and bottom factor quintiles (trailing)."""
    try:
        df = pd.DataFrame({"f": factor, "r": ret}).dropna()
        if len(df) < n_groups * 10:
            return float("nan")
        df["bucket"] = pd.qcut(df["f"], n_groups, labels=False, duplicates="drop")
        if df["bucket"].nunique() < n_groups:
            return float("nan")
        top = df[df["bucket"] == df["bucket"].max()]["r"].mean()
        bottom = df[df["bucket"] == df["bucket"].min()]["r"].mean()
        return float(top - bottom)
    except Exception:
        return float("nan")


def _factor_turnover(factor: pd.Series) -> float:
    """Top-quintile membership churn per bar (trailing, no look-ahead)."""
    try:
        f = factor.dropna()
        if len(f) < 40:
            return float("nan")
        top = f.rolling(len(f) // 4).quantile(
            0.8
        )  # ponytail: O(n·w) window, fine at 2k bars
        in_top = (f >= top).astype(int)
        switches = in_top.diff().abs().sum()
        return float(switches / max(len(f) - 1, 1))
    except Exception:
        return float("nan")


@router.get("/lifecycle")
async def get_factor_lifecycle(
    request: Request,
    symbol: str = Query("BTC/USDT", description="Trading pair (e.g. BTC/USDT)"),
    interval: str = Query("1h", description="Kline interval (e.g. 1h, 4h, 1d)"),
    lookback_bars: int = Query(
        2000, ge=200, le=5000, description="Number of bars to analyze"
    ),
    forward_bars: int = Query(
        1, ge=1, le=10, description="Forward bars for return calculation"
    ),
    epoch_bars: int = Query(100, ge=50, le=500, description="Bars per lifecycle epoch"),
    exchange: str = Query("binance", description="Exchange: binance or okx"),
    market_type: str = Query("spot", description="Market type: spot or futures"),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict]:
    """Lifecycle verdict per factor: ACTIVE/WATCH/DECAYING/RETIRED.

    Replays the calibrated factor_lifecycle state machine over per-bar
    time-series rank-IC epochs, so verdicts come from the same kernel as the
    offline monitor (baseline × decay_factor / ic_floor), not new thresholds.
    """
    try:
        kline_df = await _load_klines_for_factor(
            symbol, interval, lookback_bars, exchange, market_type
        )
        if kline_df.empty or len(kline_df) < 200:
            return APIResponse(
                data=None,
                meta=build_response_meta(request),
                error=ErrorInfo(
                    code="LIFECYCLE_NOT_ENOUGH_DATA",
                    message=f"Not enough data for {symbol} (need at least 200 bars)",
                ),
            )

        forward_returns = kline_df["close"].shift(-forward_bars) / kline_df["close"] - 1
        forward_returns = forward_returns.dropna()

        rows: List[LifecycleRow] = []
        for factor_def in AVAILABLE_FACTORS:
            factor_id = factor_def["id"]
            try:
                factor_values = _calculate_factor_values(kline_df, factor_id, symbol)
                if factor_values is None or factor_values.empty:
                    continue
                result = _lifecycle_for_factor(
                    factor_values, forward_returns, epoch_bars=epoch_bars
                )
                if result is None:
                    continue
                rows.append(
                    LifecycleRow(
                        factor=factor_id,
                        factor_name=factor_def["name"],
                        category=factor_def["category"],
                        **result,
                    )
                )
            except Exception as e:
                logger.warning(f"Lifecycle calculation failed for {factor_id}: {e}")
                continue

        state_order = {"DECAYING": 0, "WATCH": 1, "ACTIVE": 2, "RETIRED": 3}
        rows.sort(
            key=lambda r: (
                state_order.get(r.state, 9),
                -(r.rolling_ic if not np.isnan(r.rolling_ic) else -999),
            )
        )

        return APIResponse(
            data={
                "symbol": symbol,
                "interval": interval,
                "lookback_bars": lookback_bars,
                "epoch_bars": epoch_bars,
                "forward_bars": forward_bars,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "rows": [r.model_dump() for r in rows],
                "total_factors": len(rows),
                "bar_count": len(kline_df),
            },
            meta=build_response_meta(request),
            error=None,
        )

    except Exception as e:
        logger.error(f"Lifecycle calculation failed: {e}", exc_info=True)
        return APIResponse(
            data=None,
            meta=build_response_meta(request),
            error=ErrorInfo(code="LIFECYCLE_CALCULATION_ERROR", message=str(e)),
        )


async def _load_klines_for_factor(
    symbol: str,
    interval: str,
    limit: int,
    exchange: str,
    market_type: str,
) -> pd.DataFrame:
    """Load kline data for factor calculation."""
    from app.core.dependencies import get_market_service

    market_svc = get_market_service()
    if market_svc is None:
        # Fallback to demo data
        demo_path = (
            f"demo/data/{symbol.lower().replace('/', '_')}_{interval}_90d.parquet"
        )
        try:
            return pd.read_parquet(demo_path)
        except Exception:
            return pd.DataFrame()

    try:
        klines = await market_svc.fetch_klines_rest(
            symbol=symbol,
            exchange=exchange,
            market_type=market_type,
            interval=interval,
            limit=limit,
        )

        if not klines:
            return pd.DataFrame()

        rows = [
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

        df = pd.DataFrame(rows)
        df.sort_values("timestamp", inplace=True)
        return df

    except Exception as e:
        logger.warning(f"Failed to load klines: {e}")
        return pd.DataFrame()


def _calculate_forward_returns(df: pd.DataFrame, forward_bars: int) -> pd.Series:
    """Calculate forward returns (N bars ahead)."""
    close = df["close"]
    # Forward return = (close_N - close_0) / close_0
    forward_returns = close.shift(-forward_bars) / close - 1
    return forward_returns.dropna()


def _calculate_event_factor_values(
    df: pd.DataFrame,
    factor_id: str,
    symbol: str,
) -> pd.Series | None:
    """Binary event markers for TG factors (bar inside a gated event window = 1).

    Mirrors the validated de-risk windows without copying logic:
    - tg_event: ±24h around gated overlay events (all EVENT_OVERLAY_TYPES)
    - tg_regime: [event_ts, +48h] around whitelisted regime events
    Reads tg_factors.db via the shared loader; direction is NOT used (falsified —
    these markers express event-presence x future-return only, no direction advice).
    """
    try:
        from selene_space_alpha.factors.tg_event_overlay_source import (
            EVENT_OVERLAY_TYPES,
            OVERLAY_WINDOW_AFTER_HOURS,
            OVERLAY_WINDOW_BEFORE_HOURS,
        )
        from selene_space_alpha.factors.tg_vol_regime_source import (
            CONFIDENCE_MIN,
            REGIME_TYPES_BY_SYMBOL,
            REGIME_WINDOW_AFTER_HOURS,
        )
        from selene_space_alpha.factors.tg_spike_source import (
            SYMBOL_SHORT_MAP,
            load_tg_signals,
            map_signal_to_symbols,
        )

        sym_short = symbol.split("/")[0].upper()
        if sym_short not in SYMBOL_SHORT_MAP.values():
            return None

        db = _tg_factors_db_path()
        if not db.exists():
            return None

        events = load_tg_signals(db, filter_noise=True)
        events["mapped_symbols"] = events["symbols_list"].apply(map_signal_to_symbols)

        if factor_id == "tg_regime":
            types = REGIME_TYPES_BY_SYMBOL.get(sym_short, frozenset())
            before_ns, after_ns = 0, int(REGIME_WINDOW_AFTER_HOURS * 3_600_000_000_000)
        else:  # tg_event
            types = EVENT_OVERLAY_TYPES
            before_ns = int(OVERLAY_WINDOW_BEFORE_HOURS * 3_600_000_000_000)
            after_ns = int(OVERLAY_WINDOW_AFTER_HOURS * 3_600_000_000_000)

        gated = events[
            events["mapped_symbols"].apply(lambda lst: sym_short in lst)
            & events["event_type"].isin(types)
            & (events["confidence"] >= CONFIDENCE_MIN)
            & (events["magnitude"] > 0)
        ]
        if gated.empty:
            return pd.Series(0, index=df.index, dtype="float64")

        ts_col = df["timestamp"]
        if pd.api.types.is_datetime64_any_dtype(ts_col):
            bar_ns = ts_col.values.astype("int64")
        elif pd.api.types.is_integer_dtype(ts_col):
            bar_ns = ts_col.values.astype("int64") * 1_000_000  # ms → ns
        else:
            bar_ns = pd.to_datetime(ts_col).values.astype("int64")

        mask = np.zeros(len(df), dtype=bool)
        for ev_ns in gated["ts"].values.astype("int64"):
            mask |= (bar_ns >= ev_ns - before_ns) & (bar_ns <= ev_ns + after_ns)
        return pd.Series(mask.astype("float64"), index=df.index)
    except Exception as e:
        logger.warning(f"Failed to calculate event factor {factor_id}: {e}")
        return None


def _calculate_factor_values(
    df: pd.DataFrame, factor_id: str, symbol: str | None = None
) -> pd.Series | None:
    """Calculate factor values from OHLCV data."""
    if factor_id in ("tg_event", "tg_regime"):
        return _calculate_event_factor_values(df, factor_id, symbol or "")
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    try:
        if factor_id == "rsi":
            # RSI-14
            delta = close.diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss
            return 100 - (100 / (1 + rs))

        elif factor_id == "macd":
            # MACD histogram
            ema12 = close.ewm(span=12).mean()
            ema26 = close.ewm(span=26).mean()
            macd = ema12 - ema26
            signal = macd.ewm(span=9).mean()
            return macd - signal

        elif factor_id == "kdj_k":
            # KDJ K value
            lowest_low = low.rolling(9).min()
            highest_high = high.rolling(9).max()
            rsv = (close - lowest_low) / (highest_high - lowest_low) * 100
            return rsv.ewm(alpha=1 / 3).mean()

        elif factor_id == "stochastic_k":
            # Stochastic K
            lowest_low = low.rolling(14).min()
            highest_high = high.rolling(14).max()
            return (close - lowest_low) / (highest_high - lowest_low) * 100

        elif factor_id == "sma":
            # SMA-20 deviation
            sma20 = close.rolling(20).mean()
            return (close - sma20) / sma20 * 100

        elif factor_id == "ema":
            # EMA-20 deviation
            ema20 = close.ewm(span=20).mean()
            return (close - ema20) / ema20 * 100

        elif factor_id == "adx":
            # ADX-14
            plus_dm = high.diff()
            minus_dm = -low.diff()
            plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
            minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
            tr = (
                (high - low)
                .combine(high - close.shift(), max)
                .combine(close.shift() - low, max)
            )
            atr14 = tr.rolling(14).mean()
            plus_di = 100 * (plus_dm.rolling(14).mean() / atr14)
            minus_di = 100 * (minus_dm.rolling(14).mean() / atr14)
            dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
            return dx.rolling(14).mean()

        elif factor_id == "atr":
            # ATR-14 normalized
            tr = (
                (high - low)
                .combine(high - close.shift(), max)
                .combine(close.shift() - low, max)
            )
            atr14 = tr.rolling(14).mean()
            return atr14 / close * 100

        elif factor_id == "bollinger_upper":
            # Bollinger Upper band deviation
            sma20 = close.rolling(20).mean()
            std20 = close.rolling(20).std()
            upper = sma20 + 2 * std20
            return (close - upper) / upper * 100

        elif factor_id == "bollinger_lower":
            # Bollinger Lower band deviation
            sma20 = close.rolling(20).mean()
            std20 = close.rolling(20).std()
            lower = sma20 - 2 * std20
            return (close - lower) / lower * 100

        elif factor_id == "obv":
            # OBV normalized
            obv = (
                volume
                * (
                    (close > close.shift()).astype(int)
                    - (close < close.shift()).astype(int)
                )
            ).cumsum()
            return obv / obv.rolling(20).mean()

        elif factor_id == "cmf":
            # Chaikin Money Flow
            mf_multiplier = ((close - low) - (high - close)) / (high - low)
            mf_volume = mf_multiplier * volume
            return mf_volume.rolling(20).sum() / volume.rolling(20).sum()

        elif factor_id == "mfi":
            # Money Flow Index
            typical_price = (high + low + close) / 3
            mf = typical_price * volume
            positive_mf = (
                mf.where(typical_price > typical_price.shift(), 0).rolling(14).sum()
            )
            negative_mf = (
                mf.where(typical_price < typical_price.shift(), 0).rolling(14).sum()
            )
            return 100 - (100 / (1 + positive_mf / negative_mf))

        else:
            logger.warning(f"Unknown factor: {factor_id}")
            return None

    except Exception as e:
        logger.warning(f"Failed to calculate {factor_id}: {e}")
        return None


def _calculate_rolling_ic(
    factor_values: pd.Series,
    forward_returns: pd.Series,
    window: int = 20,
) -> np.ndarray:
    """Calculate rolling IC between factor and forward returns."""
    # Align series
    aligned = pd.DataFrame(
        {
            "factor": factor_values,
            "return": forward_returns,
        }
    ).dropna()

    if len(aligned) < window:
        return np.array([])

    ic_values = []
    for i in range(window, len(aligned)):
        subset = aligned.iloc[i - window : i]
        ic = subset["factor"].corr(subset["return"])
        ic_values.append(ic)

    return np.array(ic_values)


# === Tuning Results Endpoints ===
# Project root is 5 levels up from app/api/v1/ directory
import os as _os  # noqa: E402

_current = _os.path.dirname(_os.path.abspath(__file__))  # starts at api/v1
_current = _os.path.dirname(_current)  # api
_current = _os.path.dirname(_current)  # app
_current = _os.path.dirname(_current)  # local-api
_current = _os.path.dirname(_current)  # services
_project_root = _os.path.dirname(_current)  # project root
TUNING_RESULTS_DIR = Path(_project_root) / "data" / "tuning_results"

# Lightweight cache for tuning results list — avoids re-parsing multi-MB JSONs on every request
_tuning_cache: list[TuningResultSummary] = []
_tuning_cache_mtime: float = 0.0


class TuningResultSummary(BaseModel):
    """Summary of a single tuning result."""

    file_name: str
    timestamp: str
    strategy_id: str
    strategy_name: str
    interval: str
    exchange: str = "binance"
    market_type: str = "futures"
    symbol: str = "BTC/USDT"
    best_sharpe: float
    best_params: dict[str, Any] = Field(default_factory=dict)
    total_return: float
    annual_return: float = 0.0
    max_drawdown: float
    win_rate: float
    n_trials: int
    optimization_time: float
    total_trades: int | None = None
    profit_factor: float | None = None
    # OOS 防过拟合门 + DSR 透传（旧 JSON 无键 → None/False）
    oos_sharpe: float | None = None
    oos_rejected: bool = False
    all_rejected: bool = False
    deflated_sharpe_ratio: float | None = None
    # 与 DSR 同源于 OverfitReport。None = 没算出来(旧 JSON 无键 / 交易全平
    # 导致 CSCV 无可用 logit),不是"通过"。
    pbo: float | None = None
    haircut_sharpe_annual: float | None = None
    # 优化器 no-op 修复(20261001)前的历史榜单:best_params 未真正参与回测,仅供参考
    pre_noop_fix: bool = False
    # 本条记录回测与 DSR 共用的 periods_per_year(crypto=365 系、us_stock=252;
    # equity 未知 interval → None,不静默兜底)
    annualization_factor: int | None = None


class TuningResultDetail(BaseModel):
    """Detailed tuning result with best params."""

    file_name: str
    timestamp: str
    config: dict
    strategy_id: str
    strategy_name: str
    interval: str
    exchange: str = "binance"
    market_type: str = "futures"
    symbol: str = "BTC/USDT"
    best_params: dict
    best_sharpe: float
    n_trials: int
    optimization_time: float
    backtest_result: dict


@router.get("/tuning/results")
async def list_tuning_results(
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[List[TuningResultSummary]]:
    """List all tuning results from data/tuning_results/*.json."""
    global _tuning_cache, _tuning_cache_mtime

    if not TUNING_RESULTS_DIR.exists():
        return APIResponse(
            data=[],
            meta=build_response_meta(request),
            error=None,
        )

    # Check if any file changed since last cache
    latest_mtime = max(
        (f.stat().st_mtime for f in TUNING_RESULTS_DIR.glob("*.json")),
        default=0.0,
    )
    if _tuning_cache and latest_mtime == _tuning_cache_mtime:
        return APIResponse(
            data=_tuning_cache,
            meta=build_response_meta(request),
            error=None,
        )

    # Rebuild cache
    results: List[TuningResultSummary] = []
    for file_path in sorted(TUNING_RESULTS_DIR.glob("tuning_*.json"), reverse=True):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Extract timestamp from file name: tuning_20260523_190314.json
            file_name = file_path.name
            timestamp = file_name.replace("tuning_", "").replace(".json", "")

            for result in data.get("results", []):
                backtest = result.get("backtest_result", {})
                # P1-4 年化口径:参数顺序 (market_type, symbol, exchange) —
                # 首参是 kline_market_type,按位传成 exchange 会把 "yahoo"
                # 判成 CRYPTO 兜底,AAPL 静默显示 365 而非 252。
                # equity 域未知 interval raise → None;CRYPTO 域兜底 365 不 raise。
                try:
                    _domain = resolve_market_domain(
                        result.get("market_type", "futures"),
                        result.get("symbol", "BTC/USDT"),
                        result.get("exchange", "binance"),
                    )
                    ann_factor: int | None = annualization_factor(
                        _domain, result.get("interval", "")
                    )
                except ValueError:
                    ann_factor = None
                results.append(
                    TuningResultSummary(
                        file_name=file_name,
                        timestamp=timestamp,
                        strategy_id=result.get("strategy_id", ""),
                        strategy_name=result.get("strategy_name", ""),
                        interval=result.get("interval", ""),
                        exchange=result.get("exchange", "binance"),
                        market_type=result.get("market_type", "futures"),
                        symbol=result.get("symbol", "BTC/USDT"),
                        best_sharpe=result.get("best_sharpe", 0),
                        best_params=result.get("best_params", {}),
                        total_return=backtest.get("total_return", 0),
                        annual_return=backtest.get("annual_return", 0),
                        max_drawdown=backtest.get("max_drawdown", 0),
                        win_rate=backtest.get("win_rate", 0),
                        n_trials=result.get("n_trials", 0),
                        optimization_time=result.get("optimization_time", 0),
                        total_trades=backtest.get("total_trades"),
                        profit_factor=backtest.get("profit_factor"),
                        oos_sharpe=result.get("oos_sharpe"),
                        oos_rejected=bool(result.get("oos_rejected", False)),
                        all_rejected=bool(result.get("all_rejected", False)),
                        deflated_sharpe_ratio=result.get("deflated_sharpe_ratio"),
                        pbo=result.get("pbo"),
                        haircut_sharpe_annual=result.get("haircut_sharpe_annual"),
                        # 文件名是唯一权威创建时间(文件内无可靠顶层 timestamp);
                        # < 20261001 = 优化器采样 no-op 修复前,榜单仅供参考
                        pre_noop_fix=timestamp[:8] < "20261001",
                        annualization_factor=ann_factor,
                    )
                )

        except Exception as e:
            logger.warning(f"Failed to read tuning file {file_path}: {e}")
            continue

    # Update cache
    _tuning_cache = results
    _tuning_cache_mtime = latest_mtime

    return APIResponse(
        data=results,
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/tuning/results/{file_name}")
async def get_tuning_result_detail(
    request: Request,
    file_name: str,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[TuningResultDetail]:
    """Get detailed tuning result for a specific file."""
    file_path = TUNING_RESULTS_DIR / file_name

    if not file_path.exists():
        return APIResponse(
            data=None,
            meta=build_response_meta(request),
            error=f"Tuning result file not found: {file_name}",
        )

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Extract timestamp from file name
        timestamp = file_name.replace("tuning_", "").replace(".json", "")

        # Return the first result (usually one file has one result)
        result = data.get("results", [])[0] if data.get("results") else {}

        return APIResponse(
            data=TuningResultDetail(
                file_name=file_name,
                timestamp=timestamp,
                config=data.get("config", {}),
                strategy_id=result.get("strategy_id", ""),
                strategy_name=result.get("strategy_name", ""),
                interval=result.get("interval", ""),
                exchange=result.get("exchange", "binance"),
                market_type=result.get("market_type", "futures"),
                symbol=result.get("symbol", "BTC/USDT"),
                best_params=result.get("best_params", {}),
                best_sharpe=result.get("best_sharpe", 0),
                n_trials=result.get("n_trials", 0),
                optimization_time=result.get("optimization_time", 0),
                backtest_result=result.get("backtest_result", {}),
            ),
            meta=build_response_meta(request),
            error=None,
        )

    except Exception as e:
        logger.error(f"Failed to read tuning file {file_path}: {e}")
        return APIResponse(
            data=None,
            meta=build_response_meta(request),
            error=str(e),
        )


# === Tuning Task Execution ===

import asyncio  # noqa: E402
import re  # noqa: E402
import uuid  # noqa: E402
from enum import Enum  # noqa: E402


class TuningTaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TuningProgress(BaseModel):
    current: int = 0
    total: int = 0
    current_strategy: str = ""
    current_interval: str = ""
    current_exchange: str = ""
    current_market_type: str = ""
    best_sharpe_so_far: float = 0.0
    elapsed_seconds: float = 0.0
    eta_seconds: float | None = None
    completed_strategies: list[dict[str, Any]] = []


class TuningTaskState(BaseModel):
    task_id: str
    status: TuningTaskStatus = TuningTaskStatus.PENDING
    config: dict[str, Any] = {}
    progress: TuningProgress = TuningProgress()
    result_file: str | None = None
    error: str | None = None
    created_at: int = Field(default_factory=lambda: int(time.time() * 1000))


_tuning_tasks: dict[str, TuningTaskState] = {}
_tuning_subprocesses: dict[str, asyncio.subprocess.Process] = {}


async def _persist_tuning_task(task: TuningTaskState) -> None:
    """调参任务 UPSERT 落库(透明性批2-7)。

    内存字典重启即丢;落库后重启仍可查历史任务。fire-and-forget:
    落库失败只警告,绝不影响调参本身。
    """
    try:
        from app.core.dependencies import get_db_manager

        db = get_db_manager()
        if db is None:
            return
        await db.execute(
            """
            INSERT INTO tuning_tasks
                (task_id, status, config_json, progress_json, result_file,
                 error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                status = excluded.status,
                config_json = excluded.config_json,
                progress_json = excluded.progress_json,
                result_file = excluded.result_file,
                error = excluded.error,
                updated_at = excluded.updated_at
            """,
            (
                task.task_id,
                task.status.value,
                json.dumps(task.config, ensure_ascii=False),
                json.dumps(task.progress.model_dump(), ensure_ascii=False),
                task.result_file,
                task.error,
                task.created_at,
                int(time.time() * 1000),
            ),
        )
    except Exception:
        logger.warning("Failed to persist tuning task %s", task.task_id, exc_info=True)


async def _load_persisted_tuning_task(task_id: str) -> TuningTaskState | None:
    """从库恢复一个不在内存的调参任务(重启后 GET 详情用)。"""
    try:
        from app.core.dependencies import get_db_manager

        db = get_db_manager()
        if db is None:
            return None
        rows = await db.execute(
            "SELECT * FROM tuning_tasks WHERE task_id = ?", (task_id,)
        )
        if not rows:
            return None
        r = rows[0]
        return TuningTaskState(
            task_id=r["task_id"],
            status=TuningTaskStatus(r["status"]),
            config=json.loads(r["config_json"]),
            progress=TuningProgress(**json.loads(r["progress_json"])),
            result_file=r["result_file"],
            error=r["error"],
            created_at=int(r["created_at"]),
        )
    except Exception:
        logger.warning(
            "Failed to load persisted tuning task %s", task_id, exc_info=True
        )
        return None


async def _list_persisted_tuning_tasks() -> list[TuningTaskState]:
    """库中全部任务(列表端点合并内存 + 落库两路)。"""
    try:
        from app.core.dependencies import get_db_manager

        db = get_db_manager()
        if db is None:
            return []
        rows = await db.execute("SELECT * FROM tuning_tasks ORDER BY created_at DESC")
        return [
            TuningTaskState(
                task_id=r["task_id"],
                status=TuningTaskStatus(r["status"]),
                config=json.loads(r["config_json"]),
                progress=TuningProgress(**json.loads(r["progress_json"])),
                result_file=r["result_file"],
                error=r["error"],
                created_at=int(r["created_at"]),
            )
            for r in rows
        ]
    except Exception:
        logger.warning("Failed to list persisted tuning tasks", exc_info=True)
        return []


class StartTuningRequest(BaseModel):
    strategies: list[str] = Field(
        default_factory=lambda: [
            "bb_v3",
            "rsi_ma_delta",
            "bb_atr_trend",
            "dmi_delta",
            "p_signal",
            "stoch_supertrend",
            "vw_ema_cross",
            "ema_triple_cross",
            "calendar_effect",
            "tema_channel",
            "keltner_v2",
            "ma_extended_runs",
            "dca_grid",
        ]
    )
    intervals: list[str] = Field(default=["1d", "4h", "1h"])
    exchange: list[str] = Field(default=["binance"])
    market_type: list[str] = Field(default=["futures"])
    symbol: str = Field("BTC/USDT")
    trials: int = Field(30, ge=5, le=200)


# Regex patterns to parse tuning script output
_RE_PROGRESS = re.compile(r"总进度:\s+(\d+)/(\d+)\s+\[([0-9.:]+)<([0-9.:]+)")
_RE_STRATEGY_LINE = re.compile(r"\[(\S+)/(\S+)\]\s+(.+?):\s+Sharpe=([0-9.]+)")
_RE_SKIP = re.compile(r"跳过\s+(\S+)")
_RE_FAIL = re.compile(r"(\S+):\s+失败")


def _parse_elapsed(s: str) -> float:
    """Parse elapsed time string like '02:30' or '1:02:30' to seconds."""
    parts = s.strip().split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except (ValueError, IndexError):
        return 0.0


async def _run_tuning_subprocess(task: TuningTaskState) -> None:
    """Execute llm_param_tuner.py as subprocess and parse progress from stdout."""
    task.status = TuningTaskStatus.RUNNING

    cfg = task.config
    script_path = Path(_project_root) / "scripts" / "llm_param_tuner.py"
    db_path = default_klines_db_path()

    cmd = [
        sys.executable,
        str(script_path),
        "--db",
        str(db_path),
        "--strategies",
        *cfg.get("strategies", []),
        "--intervals",
        *cfg.get("intervals", []),
        "--exchange",
        *cfg.get("exchange", ["binance"]),
        "--market-type",
        *cfg.get("market_type", ["futures"]),
        "--symbol",
        cfg.get("symbol", "BTC/USDT"),
        "--trials",
        str(cfg.get("trials", 30)),
    ]

    logger.info(f"Starting tuning subprocess: {' '.join(cmd)}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_project_root),
        )
        _tuning_subprocesses[task.task_id] = proc

        start_time = time.time()
        buf = ""

        # Guard: PIPE should always be available, but a degenerate spawn
        # (e.g. fd exhaustion) can leave stdout None. Fail loud rather than
        # crash inside the read loop with an opaque AttributeError.
        if proc.stdout is None:
            raise RuntimeError("tuning subprocess stdout pipe unavailable")

        while True:
            # Bound each read so a wedged subprocess (optuna hang, deadlocked
            # stdout) cannot block forever and leave the task stuck in RUNNING.
            # 60s with no stdout output = subprocess is hung → break + fail.
            try:
                chunk = await asyncio.wait_for(proc.stdout.read(512), timeout=60.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "tuning subprocess %s: stdout idle >60s, treating as hung",
                    task.task_id,
                )
                task.error = "subprocess hung (no stdout for 60s)"
                task.status = TuningTaskStatus.FAILED
                _tuning_subprocesses.pop(task.task_id, None)
                proc.kill()
                task.progress.elapsed_seconds = time.time() - start_time
                await _broadcast_tuning_progress(task)
                return
            if not chunk:
                break

            text = chunk.decode("utf-8", errors="replace")
            buf += text

            # Process complete lines
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue

                # Try to parse progress bar line
                m = _RE_PROGRESS.search(line)
                if m:
                    task.progress.current = int(m.group(1))
                    task.progress.total = int(m.group(2))
                    task.progress.elapsed_seconds = _parse_elapsed(m.group(3))
                    task.progress.eta_seconds = _parse_elapsed(m.group(4))

                # Parse strategy result line
                m = _RE_STRATEGY_LINE.search(line)
                if m:
                    task.progress.current_exchange = m.group(1)
                    task.progress.current_market_type = ""
                    task.progress.current_interval = ""
                    task.progress.current_strategy = m.group(3)
                    sharpe = float(m.group(4))
                    if sharpe > task.progress.best_sharpe_so_far:
                        task.progress.best_sharpe_so_far = sharpe
                    task.progress.completed_strategies.append(
                        {
                            "strategy": m.group(3),
                            "exchange": m.group(1),
                            "sharpe": sharpe,
                        }
                    )

                # Parse skip
                m = _RE_SKIP.search(line)
                if m:
                    task.progress.completed_strategies.append(
                        {
                            "strategy": "",
                            "exchange": "",
                            "skipped": m.group(1),
                        }
                    )

                # Broadcast progress via WS
                await _broadcast_tuning_progress(task)

        # Wait for process to finish
        await proc.wait()
        _tuning_subprocesses.pop(task.task_id, None)

        # Find the latest result file
        if proc.returncode == 0:
            result_files = sorted(
                TUNING_RESULTS_DIR.glob("tuning_*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if result_files:
                task.result_file = result_files[0].name
            task.status = TuningTaskStatus.COMPLETED
        else:
            # Bound stderr read so a wedged pipe can't block the failure path.
            # Guard None for the same degenerate-spawn reason as stdout above.
            error_msg = ""
            if proc.stderr is not None:
                try:
                    stderr = await asyncio.wait_for(proc.stderr.read(), timeout=5.0)
                    error_msg = stderr.decode("utf-8", errors="replace")[:500]
                except asyncio.TimeoutError:
                    error_msg = "subprocess exited non-zero (stderr read timed out)"
            else:
                error_msg = "subprocess exited non-zero (stderr pipe unavailable)"
            task.error = error_msg
            task.status = TuningTaskStatus.FAILED

        task.progress.elapsed_seconds = time.time() - start_time
        await _broadcast_tuning_progress(task)

        # 透明性批2-6: 调参完成 → 该标的开放 mission 的 mission_activity
        if task.status == TuningTaskStatus.COMPLETED:
            from app.api.v1.research_missions import notify_experiment_completed

            await notify_experiment_completed(
                "market",
                cfg.get("symbol", "BTC/USDT"),
                task_id=task.task_id,
                experiment_kind="tuning",
                summary={
                    "symbol": cfg.get("symbol", "BTC/USDT"),
                    "strategies": cfg.get("strategies", []),
                    "intervals": cfg.get("intervals", []),
                    "trials": cfg.get("trials", 30),
                    "result_file": task.result_file,
                },
            )

    except asyncio.CancelledError:
        proc = _tuning_subprocesses.pop(task.task_id, None)  # type: ignore[arg-type]
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
        task.status = TuningTaskStatus.CANCELLED
        await _broadcast_tuning_progress(task)
    except Exception as e:
        task.error = str(e)
        task.status = TuningTaskStatus.FAILED
        _tuning_subprocesses.pop(task.task_id, None)
        await _broadcast_tuning_progress(task)


async def _broadcast_tuning_progress(task: TuningTaskState) -> None:
    """Broadcast tuning progress to WebSocket subscribers + persist to DB.

    所有状态变更点都经过这里(透明性批2-7):落库与广播同路,内存字典重启
    丢失后 GET /tuning/tasks 仍能从库恢复。落库失败只警告不影响广播。
    """
    await _persist_tuning_task(task)
    try:
        from app.api.v1.ws import _research_manager

        event_type = {
            TuningTaskStatus.RUNNING: "tuning_progress",
            TuningTaskStatus.COMPLETED: "tuning_completed",
            TuningTaskStatus.FAILED: "tuning_failed",
            TuningTaskStatus.CANCELLED: "tuning_cancelled",
        }.get(task.status, "tuning_progress")

        await _research_manager.broadcast(
            f"tuning:{task.task_id}",
            {
                "type": event_type,
                "task_id": task.task_id,
                "data": task.model_dump(),
                "timestamp": int(time.time() * 1000),
            },
        )
    except Exception:
        logger.debug("Failed to broadcast tuning progress", exc_info=True)


@router.post("/tuning/start")
async def start_tuning(
    request: Request,
    body: StartTuningRequest,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Start an asynchronous tuning run."""
    # Check for already running tasks
    running = [
        t
        for t in _tuning_tasks.values()
        if t.status in (TuningTaskStatus.PENDING, TuningTaskStatus.RUNNING)
    ]
    if running:
        return APIResponse(
            data=None,
            meta=build_response_meta(request),
            error="A tuning task is already running. Wait for it to complete or cancel it first.",
        )

    task_id = f"tune-{uuid.uuid4().hex[:8]}"
    task = TuningTaskState(
        task_id=task_id,
        config={
            "strategies": body.strategies,
            "intervals": body.intervals,
            "exchange": body.exchange,
            "market_type": body.market_type,
            "symbol": body.symbol,
            "trials": body.trials,
        },
        progress=TuningProgress(
            total=len(body.strategies)
            * len(body.exchange)
            * len(body.market_type)
            * len(body.intervals),
        ),
    )
    _tuning_tasks[task_id] = task
    await _persist_tuning_task(task)

    asyncio.create_task(_run_tuning_subprocess(task))

    return APIResponse(
        data={"task_id": task_id, "status": task.status.value},
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/tuning/tasks")
async def list_tuning_tasks(
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[list[dict[str, Any]]]:
    """List all tuning tasks."""
    persisted = {t.task_id: t for t in await _list_persisted_tuning_tasks()}
    # 内存为准(运行中状态比库新),落库补内存里没有的(重启后历史)
    merged = {**persisted, **_tuning_tasks}
    tasks = sorted(merged.values(), key=lambda t: t.created_at, reverse=True)
    return APIResponse(
        data=[t.model_dump() for t in tasks],
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/tuning/tasks/{task_id}")
async def get_tuning_task(
    request: Request,
    task_id: str,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Get a specific tuning task status."""
    task = _tuning_tasks.get(task_id)
    if task is None:
        task = await _load_persisted_tuning_task(task_id)
    if task is None:
        return APIResponse(
            data=None,
            meta=build_response_meta(request),
            error=f"Tuning task '{task_id}' not found",
        )
    return APIResponse(
        data=task.model_dump(),
        meta=build_response_meta(request),
        error=None,
    )


@router.post("/tuning/tasks/{task_id}/cancel")
async def cancel_tuning_task(
    request: Request,
    task_id: str,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Cancel a running tuning task."""
    task = _tuning_tasks.get(task_id)
    if task is None:
        return APIResponse(
            data=None,
            meta=build_response_meta(request),
            error=f"Tuning task '{task_id}' not found",
        )
    if task.status not in (TuningTaskStatus.PENDING, TuningTaskStatus.RUNNING):
        return APIResponse(
            data=None,
            meta=build_response_meta(request),
            error=f"Task '{task_id}' is not running (status={task.status.value})",
        )

    proc = _tuning_subprocesses.get(task_id)
    if proc and proc.returncode is None:
        proc.terminate()
    task.status = TuningTaskStatus.CANCELLED
    await _broadcast_tuning_progress(task)

    return APIResponse(
        data={"task_id": task_id, "status": task.status.value},
        meta=build_response_meta(request),
        error=None,
    )


# === Smart Tuning (LLM-guided) ===

import random  # noqa: E402
import math  # noqa: E402


class MarketState(BaseModel):
    volatility: str = Field("medium", description="low/medium/high")
    trend_strength: str = Field("moderate", description="weak/moderate/strong")


class SmartTuningRequest(BaseModel):
    market_state: MarketState = Field(default_factory=MarketState)
    symbol: str = Field("BTC/USDT")
    exchange: str = Field("binance")
    market_type: str = Field("futures")
    intervals: list[str] = Field(default=["1d", "4h", "1h"])
    max_params_per_strategy: int = Field(
        5, ge=1, le=20, description="Param combos per strategy"
    )


# Strategy category mapping for market-state-driven selection
_STRATEGY_CATEGORIES: dict[str, list[str]] = {
    "trend": [
        "bb_atr_trend",
        "dmi_delta",
        "vw_ema_cross",
        "ema_triple_cross",
        "tema_channel",
        "keltner_v2",
    ],
    "oscillator": ["bb_v3", "rsi_ma_delta", "p_signal", "stoch_supertrend"],
    "mean_reversion": ["ma_extended_runs", "calendar_effect"],
    "grid": ["dca_grid"],
    "volume": ["vwap_fibo_bands", "pyramid_ma"],
}

_MARK_STATE_STRATEGIES: dict[str, dict[str, int]] = {
    "high_vol_trend": {"trend": 3, "oscillator": 2, "grid": 1},
    "high_vol_range": {"oscillator": 3, "mean_reversion": 2},
    "low_vol_trend": {"trend": 2, "oscillator": 2, "mean_reversion": 1},
    "low_vol_range": {"mean_reversion": 3, "grid": 2},
    "medium_default": {"trend": 2, "oscillator": 2, "mean_reversion": 1, "grid": 1},
}


def _select_strategies_for_market(market_state: MarketState) -> list[str]:
    """Select strategies based on market state."""
    key = f"{market_state.volatility}_vol_{market_state.trend_strength}"
    category_counts = _MARK_STATE_STRATEGIES.get(
        key, _MARK_STATE_STRATEGIES["medium_default"]
    )

    selected: list[str] = []
    for category, count in category_counts.items():
        available = _STRATEGY_CATEGORIES.get(category, [])
        selected.extend(available[:count])
    return selected


def _generate_param_samples(
    param_space: dict[str, Any],
    n_samples: int = 5,
) -> list[dict[str, Any]]:
    """Generate parameter samples from a space definition."""
    if not param_space:
        return [{}]

    param_samples: dict[str, list[Any]] = {}
    for param_name, config in param_space.items():
        if isinstance(config, tuple) and len(config) == 2:
            low, high = config
            if isinstance(low, int) and isinstance(high, int):
                step = max(1, (high - low) // n_samples)
                vals = list(range(low, high + 1, step))[:n_samples]
                if low not in vals:
                    vals.insert(0, low)
                if high not in vals:
                    vals.append(high)
                param_samples[param_name] = vals[: n_samples + 1]
            else:
                step = (high - low) / n_samples
                vals = [round(low + i * step, 4) for i in range(n_samples + 1)]
                param_samples[param_name] = vals
        elif isinstance(config, list):
            param_samples[param_name] = config

    combos: list[dict[str, Any]] = [{}]
    for param_name, vals in param_samples.items():
        new_combos: list[dict[str, Any]] = []
        for combo in combos:
            for val in vals:
                new_combos.append({**combo, param_name: val})
        combos = new_combos
        if len(combos) > n_samples * 3:
            random.shuffle(combos)
            combos = combos[: n_samples * 3]

    return combos[: n_samples * 3]


def _normalize_score(
    sharpe: float, total_return: float, win_rate: float, max_drawdown: float
) -> float:
    """Normalize backtest result to [0, 1] composite score."""

    def sigmoid(x: float) -> float:
        return 1 / (1 + math.exp(-x))

    n_sharpe = sigmoid(sharpe / 2)
    n_return = math.tanh(total_return / 50)
    n_winrate = win_rate
    n_drawdown = 1 - min(max_drawdown / 50, 1)

    return 0.4 * n_sharpe + 0.3 * n_return + 0.2 * n_winrate + 0.1 * n_drawdown


def _get_strategy_registry() -> dict[str, tuple[dict[str, Any], str]]:
    """Return strategy param spaces and names for smart tuning."""
    return {
        "bb_v3": (
            {
                "bb_len": (5, 20),
                "buy_len": (20, 60),
                "buy_mult": (1.5, 4.0),
                "sell_len": (10, 40),
                "sell_mult": (1.5, 4.0),
            },
            "布林带升级版",
        ),
        "rsi_ma_delta": (
            {"rsi_len": (7, 21), "ma_len1": (7, 21), "ma_len2": (3, 14)},
            "RSI偏离均线",
        ),
        "bb_atr_trend": (
            {"bb_len": (10, 30), "atr_len": (7, 21), "rsi_len": (7, 21)},
            "布林ATR趋势",
        ),
        "dmi_delta": ({"dmi_len": (7, 21), "ma_len": (7, 21)}, "DMI差值"),
        "p_signal": ({"n_bars": (5, 15)}, "P信号熵"),
        "stoch_supertrend": (
            {"k_len": (7, 21), "length": (1, 5), "percent": (1.0, 4.0)},
            "随机超级趋势",
        ),
        "vw_ema_cross": ({"vw_period": (10, 30), "ema_len": (5, 15)}, "量加权EMA交叉"),
        "ema_triple_cross": (
            {
                "short_period": (5, 15),
                "mid_period": (20, 40),
                "long_period": (100, 200),
            },
            "三均线交叉",
        ),
        "calendar_effect": ({"buy_day": (20, 28), "sell_day": (1, 10)}, "日历效应"),
        "tema_channel": ({"length": (10, 30), "mult": (1.5, 3.0)}, "TEMA通道"),
        "keltner_v2": (
            {
                "ma_length": (10, 30),
                "atr_length": (10, 30),
                "mult": (1.5, 3.0),
                "adx_threshold": (15, 35),
            },
            "肯特纳增强版",
        ),
        "ma_extended_runs": (
            {"ma_len": (15, 40), "deviation": (5, 20)},
            "均线连续偏离",
        ),
        "dca_grid": (
            {"fast_ma": (5, 15), "slow_ma": (20, 50), "grid_spacing": (0.002, 0.01)},
            "DCA网格",
        ),
    }


def _import_strategy_class(strategy_id: str) -> type | None:
    """Import a strategy class by ID."""
    _IMPORT_MAP = {
        "bb_v3": "selene_space_strategy.templates.presets.bb_v3:BBV3Strategy",
        "rsi_ma_delta": "selene_space_strategy.templates.presets.rsi_ma_delta:RSIMADeltaStrategy",
        "bb_atr_trend": "selene_space_strategy.templates.presets.bb_atr_trend:BBATRTrendStrategy",
        "dmi_delta": "selene_space_strategy.templates.presets.dmi_delta:DMIDeltaStrategy",
        "p_signal": "selene_space_strategy.templates.presets.p_signal:PSignalStrategy",
        "stoch_supertrend": "selene_space_strategy.templates.presets.stoch_supertrend:STOCHSupertrendStrategy",
        "vw_ema_cross": "selene_space_strategy.templates.presets.vw_ema_cross:VWEMACrossoverStrategy",
        "ema_triple_cross": "selene_space_strategy.templates.presets.ema_triple_cross:EMATripleCrossStrategy",
        "calendar_effect": "selene_space_strategy.templates.presets.calendar_effect:CalendarEffectStrategy",
        "intraday_seasonality": "selene_space_strategy.templates.presets.intraday_seasonality:IntradaySeasonalityStrategy",
        "rebalance_premium": "selene_space_strategy.templates.presets.rebalance_premium:RebalancePremiumStrategy",
        "tsmom_vol_target": "selene_space_strategy.templates.presets.tsmom_vol_target:TsmomVolTargetStrategy",
        "cross_sectional_momentum": "selene_space_strategy.templates.presets.cross_sectional_momentum:CrossSectionalMomentumStrategy",
        "january_barometer": "selene_space_strategy.templates.presets.january_barometer:JanuaryBarometerStrategy",
        "tema_channel": "selene_space_strategy.templates.presets.tema_channel:TEMAChannelStrategy",
        "keltner_v2": "selene_space_strategy.templates.presets.keltner_v2:KeltnerChannelV2Strategy",
        "ma_extended_runs": "selene_space_strategy.templates.presets.ma_extended_runs:MAExtendedRunsStrategy",
        "dca_grid": "selene_space_strategy.templates.presets.dca_grid:DCAGridStrategy",
    }
    import_path = _IMPORT_MAP.get(strategy_id)
    if not import_path:
        return None
    module_path, class_name = import_path.rsplit(":", 1)
    try:
        import importlib

        module = importlib.import_module(module_path)
        return getattr(module, class_name, None)
    except (ImportError, AttributeError):
        return None


async def _run_smart_tuning(task: TuningTaskState, body: SmartTuningRequest) -> None:
    """Execute smart tuning: generate params from market state, run backtests, score & rank."""
    task.status = TuningTaskStatus.RUNNING
    start_time = time.time()

    registry = _get_strategy_registry()
    if not registry:
        task.error = "Strategy registry not available"
        task.status = TuningTaskStatus.FAILED
        await _broadcast_tuning_progress(task)
        return

    # Select strategies based on market state
    selected_ids = _select_strategies_for_market(body.market_state)
    selected_ids = [s for s in selected_ids if s in registry]
    if not selected_ids:
        selected_ids = list(registry.keys())

    # Generate all backtest tasks
    all_tasks: list[dict[str, Any]] = []
    for strategy_id in selected_ids:
        param_space, strategy_name = registry[strategy_id]
        param_combos = _generate_param_samples(
            param_space, n_samples=body.max_params_per_strategy
        )
        for combo in param_combos:
            for interval in body.intervals:
                all_tasks.append(
                    {
                        "strategy_id": strategy_id,
                        "strategy_name": strategy_name,
                        "params": combo,
                        "interval": interval,
                    }
                )

    task.progress.total = len(all_tasks)
    task.config = {
        "mode": "smart",
        "market_state": body.market_state.model_dump(),
        "symbol": body.symbol,
        "exchange": body.exchange,
        "market_type": body.market_type,
        "intervals": body.intervals,
        "selected_strategies": selected_ids,
        "total_tasks": len(all_tasks),
    }
    await _broadcast_tuning_progress(task)

    # Execute backtests
    db_path = str(default_klines_db_path())

    for i, bt_task in enumerate(all_tasks):
        if task.status == TuningTaskStatus.CANCELLED:
            return

        task.progress.current = i + 1
        task.progress.current_strategy = bt_task["strategy_name"]
        task.progress.current_interval = bt_task["interval"]
        task.progress.elapsed_seconds = time.time() - start_time

        try:
            from selene_space_market.kline_db import KlineDB
            from selene_space_types.market import KlineEvent

            strategy_class = _import_strategy_class(bt_task["strategy_id"])
            if strategy_class is None:
                continue

            db = KlineDB(db_path)
            df = db.get_klines(
                exchange=body.exchange,
                market_type=body.market_type,
                symbol=body.symbol,
                interval=bt_task["interval"],
            )
            db.close()

            if df.empty:
                continue

            klines = [
                KlineEvent(
                    exchange=body.exchange,
                    market_type=body.market_type,
                    symbol=body.symbol,
                    timestamp=int(row["timestamp"]),
                    interval=bt_task["interval"],
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    closed=True,
                )
                for _, row in df.iterrows()
            ]

            strategy = strategy_class(
                symbol=body.symbol,
                initial_capital=100000.0,
                **bt_task["params"],
            )
            result = strategy.run_backtest(klines)

            if result:
                sharpe = result.sharpe_ratio
                total_return = result.total_return
                win_rate = result.win_rate
                max_dd = result.max_drawdown
                score = _normalize_score(sharpe, total_return, win_rate, max_dd)

                if sharpe > task.progress.best_sharpe_so_far:
                    task.progress.best_sharpe_so_far = sharpe

                task.progress.completed_strategies.append(
                    {
                        "strategy": bt_task["strategy_name"],
                        "exchange": f"{body.exchange}/{body.market_type}",
                        "interval": bt_task["interval"],
                        "sharpe": sharpe,
                        "total_return": total_return,
                        "score": score,
                        "params": bt_task["params"],
                    }
                )

        except Exception as e:
            logger.warning(
                f"Smart tuning backtest failed for {bt_task['strategy_id']}: {e}"
            )

        if (i + 1) % 3 == 0 or i + 1 == len(all_tasks):
            await _broadcast_tuning_progress(task)

    # Score & rank
    all_results = sorted(
        task.progress.completed_strategies,
        key=lambda x: x.get("score", 0),
        reverse=True,
    )

    task.progress.elapsed_seconds = time.time() - start_time
    task.progress.completed_strategies = all_results

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = TUNING_RESULTS_DIR / f"smart_tuning_{timestamp}.json"
    TUNING_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "mode": "smart",
                "market_state": body.market_state.model_dump(),
                "config": task.config,
                "results": all_results[:20],
                "all_results_count": len(all_results),
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    task.result_file = output_file.name
    task.status = TuningTaskStatus.COMPLETED
    await _broadcast_tuning_progress(task)


@router.post("/tuning/smart-start")
async def start_smart_tuning(
    request: Request,
    body: SmartTuningRequest,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Start LLM-guided smart tuning based on market state."""
    running = [
        t
        for t in _tuning_tasks.values()
        if t.status in (TuningTaskStatus.PENDING, TuningTaskStatus.RUNNING)
    ]
    if running:
        return APIResponse(
            data=None,
            meta=build_response_meta(request),
            error="A tuning task is already running. Wait for it to complete or cancel it first.",
        )

    task_id = f"smart-{uuid.uuid4().hex[:8]}"
    task = TuningTaskState(
        task_id=task_id,
        config={"mode": "smart", "market_state": body.market_state.model_dump()},
    )
    _tuning_tasks[task_id] = task

    asyncio.create_task(_run_smart_tuning(task, body))

    return APIResponse(
        data={"task_id": task_id, "status": task.status.value},
        meta=build_response_meta(request),
        error=None,
    )


# === Save Tuning Results as Strategies ===


class SaveTuningResultsRequest(BaseModel):
    top_n: int = Field(10, ge=1, le=50, description="Number of top results to save")


@router.post("/tuning/tasks/{task_id}/save-results")
async def save_tuning_results(
    request: Request,
    task_id: str,
    body: SaveTuningResultsRequest = SaveTuningResultsRequest(),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Save top tuning results as StrategyConfig entries (source='tuned', tag='调参优选')."""
    task = _tuning_tasks.get(task_id)
    if task is None:
        return APIResponse(
            data=None,
            meta=build_response_meta(request),
            error=f"Tuning task '{task_id}' not found",
        )
    if task.status != TuningTaskStatus.COMPLETED:
        return APIResponse(
            data=None,
            meta=build_response_meta(request),
            error=f"Task '{task_id}' is not completed (status={task.status.value})",
        )

    completed = [
        s
        for s in task.progress.completed_strategies
        if not s.get("skipped") and s.get("strategy")
    ]
    if not completed:
        return APIResponse(
            data=None,
            meta=build_response_meta(request),
            error="No completed results to save",
        )

    is_smart = task.config.get("mode") == "smart"
    completed.sort(key=lambda x: x.get("score", x.get("sharpe", 0)), reverse=True)
    top_results = completed[: body.top_n]

    from selene_space_types.strategy import StrategyConfig

    saved_ids: list[str] = []
    for i, result in enumerate(top_results):
        strategy_id = f"tuned-{uuid.uuid4().hex[:6]}"
        strategy_name = result["strategy"]
        interval = result.get("interval", "1d")
        sharpe = result.get("sharpe", 0)
        score = result.get("score", 0)
        total_return = result.get("total_return", 0)
        params = result.get("params", {})

        tags = ["调参优选"]
        if is_smart:
            ms = task.config.get("market_state", {})
            tags.append(f"波动:{ms.get('volatility', '?')}")
            tags.append(f"趋势:{ms.get('trend_strength', '?')}")
        tags.append(f"Sharpe:{sharpe:.2f}")

        config = StrategyConfig(
            id=strategy_id,
            name=f"{strategy_name} #{i + 1}",
            type="custom",
            description=f"调参优选 | Sharpe={sharpe:.3f} | 收益={total_return * 100:.1f}% | Score={score:.3f}",
            symbols=["BTC/USDT"],
            interval=interval,
            parameters=params,
            source="tuned",
            tags=tags,
            lifecycle_state="active",
        )

        from app.api.v1.strategies import _strategies, _persist_save

        _strategies[strategy_id] = config
        try:
            await _persist_save(config, user_id=user.id)
        except Exception as e:
            logger.warning(f"Failed to persist tuned strategy {strategy_id}: {e}")

        saved_ids.append(strategy_id)

    return APIResponse(
        data={
            "saved_count": len(saved_ids),
            "strategy_ids": saved_ids,
        },
        meta=build_response_meta(request),
        error=None,
    )


# === TG Factor Read-Only Endpoints ===
# TG 因子源（tg_spike / tg_event_overlay / tg_vol_regime）直读 tg_factors.db，不走 HTTP。
# 这两个只读端点是 TG 因子在前端研究层的唯一入口（P1 盘点补全，2026-08-15）。


def _tg_factors_db_path() -> Path:
    """Resolve tg_factors.db relative to project root (DEFAULT_TG_DB_PATH is relative)."""
    from selene_space_alpha.factors.tg_spike_source import DEFAULT_TG_DB_PATH

    p = Path(DEFAULT_TG_DB_PATH)
    return p if p.is_absolute() else Path(_project_root) / p


@router.get("/tg-factors/events")
async def list_tg_factor_events(
    request: Request,
    symbol: str | None = None,
    event_type: str | None = None,
    limit: int = Query(200, ge=1, le=2000, description="Max events to return"),
    start: str | None = Query(
        None, description="Filter events on/after this ts (inclusive)"
    ),
    end: str | None = Query(
        None, description="Filter events before this ts (exclusive)"
    ),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[list[dict]]:
    """List TG factor events from tg_factors.db (newest first).

    Read-only, no direction advice — direction/magnitude are shown as event
    attributes only (direction alpha is falsified; TG is a volatility/risk
    signal, not a directional edge).
    """
    try:
        from selene_space_alpha.factors.tg_spike_source import (
            load_tg_signals,
            map_signal_to_symbols,
        )

        db = _tg_factors_db_path()
        if not db.exists():
            return APIResponse(
                data=[],
                meta=build_response_meta(request),
                error=ErrorInfo(
                    code="TG_FACTORS_DB_NOT_FOUND",
                    message=f"tg_factors.db not found: {db}",
                ),
            )
        df = load_tg_signals(db, filter_noise=True)
        df["mapped_symbols"] = df["symbols_list"].apply(map_signal_to_symbols)
        if symbol:
            sym_short = symbol.split("/")[0].upper()
            df = df[df["mapped_symbols"].apply(lambda lst: sym_short in lst)]
        if event_type:
            df = df[df["event_type"] == event_type]
        if start:
            df = df[df["ts"] >= pd.Timestamp(start)]
        if end:
            df = df[df["ts"] < pd.Timestamp(end)]
        df = df.tail(limit).iloc[::-1]

        rows = []
        for _, r in df.iterrows():
            rows.append(
                {
                    "msg_id": int(r["msg_id"]),
                    "ts": r["ts"].isoformat(),
                    "event_type": str(r["event_type"]),
                    "direction": float(r["direction"] or 0),
                    "magnitude": float(r["magnitude"] or 0),
                    "confidence": float(r["confidence"] or 0),
                    "urgency": float(r["urgency"] or 0),
                    "halflife_min": int(r["halflife_min"] or 0),
                    "symbols": list(r["symbols_list"]),
                    "mapped_symbols": list(r["mapped_symbols"]),
                }
            )
        return APIResponse(data=rows, meta=build_response_meta(request), error=None)
    except Exception as e:
        logger.error(f"List TG factor events failed: {e}", exc_info=True)
        return APIResponse(
            data=None,
            meta=build_response_meta(request),
            error=ErrorInfo(code="TG_FACTORS_EVENTS_ERROR", message=str(e)),
        )


@router.get("/tg-factors/regime")
async def get_tg_factor_regime(
    request: Request,
    symbol: str | None = None,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[list[dict]]:
    """TG volatility-regime whitelist window coverage (48h persistence de-risk).

    Reuses TGVolRegimeSource.check(now_ts) — the live EventGuard interface —
    plus REGIME_* pre-registered constants. No logic copied.
    """
    try:
        from selene_space_alpha.factors.tg_spike_source import SYMBOL_SHORT_MAP
        from selene_space_alpha.factors.tg_vol_regime_source import (
            REGIME_MULT,
            REGIME_TYPES_BY_SYMBOL,
            REGIME_WINDOW_AFTER_HOURS,
            TGVolRegimeSource,
        )

        db = _tg_factors_db_path()
        if not db.exists():
            return APIResponse(
                data=[],
                meta=build_response_meta(request),
                error=ErrorInfo(
                    code="TG_FACTORS_DB_NOT_FOUND",
                    message=f"tg_factors.db not found: {db}",
                ),
            )
        now_ms = int(time.time() * 1000)
        symbols = [symbol] if symbol else list(SYMBOL_SHORT_MAP.values())

        out = []
        for sym_short in symbols:
            full = next(
                (k for k, v in SYMBOL_SHORT_MAP.items() if v == sym_short), None
            )
            if full is None:
                continue
            src = TGVolRegimeSource(tg_db_path=db, symbol=full)
            alerts = src.check(now_ms)
            out.append(
                {
                    "symbol": sym_short,
                    "full_symbol": full,
                    "whitelisted_types": sorted(
                        REGIME_TYPES_BY_SYMBOL.get(sym_short, [])
                    ),
                    "regime_mult": REGIME_MULT,
                    "window_after_hours": REGIME_WINDOW_AFTER_HOURS,
                    "active": len(alerts) > 0,
                    "active_windows": [
                        {
                            "event_ts": int(a.start_ts),
                            "end_ts": int(a.end_ts),
                            "event_type": a.event_type,
                            "multiplier": a.multiplier,
                        }
                        for a in alerts
                    ],
                    "now_ms": now_ms,
                }
            )
        return APIResponse(data=out, meta=build_response_meta(request), error=None)
    except Exception as e:
        logger.error(f"TG regime coverage failed: {e}", exc_info=True)
        return APIResponse(
            data=None,
            meta=build_response_meta(request),
            error=ErrorInfo(code="TG_FACTORS_REGIME_ERROR", message=str(e)),
        )


# === Alpha Mining (deterministic formula search) Artifact Endpoints ===
# The T3 runner + T4 validator write these under internal-archive/ (report.py's
# DEFAULT_RESULTS_DIR). The frontend /research/alpha-mining page reads them via
# these endpoints; missing artifacts degrade to data=null (empty state), never a crash.
ALPHA_MINING_DIR = Path(_project_root) / "实验" / "alpha-mining"


def _load_alpha_mining_artifact(request: Request, file_name: str) -> APIResponse[dict]:
    path = ALPHA_MINING_DIR / file_name
    if not path.exists():
        # data=null → frontend fetcherSilent returns null → empty state (no crash)
        return APIResponse(data=None, meta=build_response_meta(request), error=None)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return APIResponse(data=None, meta=build_response_meta(request), error=None)
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


@router.get("/alpha-mining/top-formulas")
async def get_alpha_mining_top_formulas(
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict]:
    """Serve the deterministic search output (internal-archive/top_formulas.json)."""
    return _load_alpha_mining_artifact(request, "top_formulas.json")


@router.get("/alpha-mining/validation")
async def get_alpha_mining_validation(
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict]:
    """Serve the validation report (internal-archive/validation_report.json)."""
    return _load_alpha_mining_artifact(request, "validation_report.json")
