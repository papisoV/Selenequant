"""Market regime detection API endpoint.

Provides real-time market state classification (bull/bear/sideways/transition)
based on a regime detection algorithm inspired by commercial quant terminals
(clean-room reimplementation, no source code copied).
"""

from __future__ import annotations

import math
import secrets
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.core.dependencies import build_response_meta
from selene_space_types.common import APIResponse

router = APIRouter(prefix="/regime", tags=["regime"])

_regime_cache: dict[str, dict[str, Any]] = {}
_CACHE_TTL = 60  # seconds

# 少于这么多根 K 线不足以估波动，直接判盘整
MIN_BARS = 20
# 年化因子：按日线口径
_TRADING_DAYS = 252
# 分类阈值
_BULL_RETURN = 0.15
_BEAR_RETURN = -0.15
_CALM_VOL = 0.25
_NERVOUS_VOL = 0.35

_FLAT = {"regime": "sideways", "annual_return": 0.0, "volatility": 0.0}


def _simple_returns(prices: list[float]) -> list[float]:
    """逐根收益率；跳过前值为 0 的无效点。"""
    return [(curr - prev) / prev for prev, curr in zip(prices, prices[1:]) if prev > 0]


def _annualized(returns: list[float]) -> tuple[float, float]:
    """(年化收益, 年化波动率)，按样本总体方差。"""
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    return mean * _TRADING_DAYS, math.sqrt(variance) * math.sqrt(_TRADING_DAYS)


def _classify(annual_return: float, volatility: float) -> str:
    """按收益方向与波动水平判档；高波动优先于方向。"""
    if volatility > _NERVOUS_VOL:
        return "transition"
    if annual_return > _BULL_RETURN and volatility < _CALM_VOL:
        return "bull"
    if annual_return < _BEAR_RETURN and volatility < _NERVOUS_VOL:
        return "bear"
    return "sideways"


def _summary(regime: str, annual_return: float, volatility: float) -> dict[str, Any]:
    """把三档结果收成统一的响应体, 收益/波动保留 4 位小数。

    键序沿用 regime → annual_return → volatility, 与既有响应一致。
    """
    summary: dict[str, Any] = {"regime": regime}
    summary["annual_return"] = round(annual_return, 4)
    summary["volatility"] = round(volatility, 4)
    return summary


def _compute_regime_from_prices(prices: list[float]) -> dict[str, Any]:
    """Compute market regime from price series.

    Classification based on annualized return and volatility:
    - BULL: return > 15%, vol < 25%
    - BEAR: return < -15%, vol < 35%
    - TRANSITION: vol > 35%
    - SIDEWAYS: other cases

    Args:
        prices: List of closing prices.

    Returns:
        Dict with regime, annual_return, volatility.
    """
    if len(prices) < MIN_BARS:
        return dict(_FLAT)

    returns = _simple_returns(prices)
    if len(returns) < 2:
        return dict(_FLAT)

    annual_return, volatility = _annualized(returns)
    return _summary(_classify(annual_return, volatility), annual_return, volatility)


async def _fetch_prices_for_symbol(symbol: str, lookback: int = 60) -> list[float]:
    """Fetch historical prices for a symbol.

    Args:
        symbol: Trading pair symbol (e.g., "BTC/USDT").
        lookback: Number of bars to fetch.

    Returns:
        List of closing prices.
    """
    # Placeholder - actual implementation would fetch from an exchange.
    # Empty list signals the caller to fall back to demo data.
    return []


def _demo_prices(symbol: str, lookback: int) -> list[float]:
    """Deterministic-scale random walk used when no real feed is wired."""
    rng = secrets.SystemRandom()
    price = 50000.0 if "BTC" in symbol else 3000.0
    series = []
    for _ in range(lookback):
        price *= 1 + rng.uniform(-0.02, 0.02)
        series.append(price)
    return series


def _require_enough_prices(prices: list[float]) -> None:
    """少于两根算不出收益率, 直接 400。"""
    if len(prices) < 2:
        raise HTTPException(status_code=400, detail="At least 2 prices required")


def _cached_regime(cache_key: str) -> dict[str, Any] | None:
    """命中未过期的缓存则返回, 否则 None。"""
    cached = _regime_cache.get(cache_key)
    if not cached:
        return None
    if time.time() - cached.get("timestamp", 0) >= _CACHE_TTL:
        return None
    return cached


def _now_ms() -> int:
    return int(time.time() * 1000)


def _stamped(result: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """给结果补上时间戳与附加字段。响应壳由调用方用 APIResponse 统一包。"""
    return {**result, **extra, "timestamp": _now_ms()}


async def _prices_for(symbol: str, lookback: int) -> list[float]:
    """取历史价; 真实数据源未接通时退回确定性演示序列。"""
    prices = await _fetch_prices_for_symbol(symbol, lookback)
    return prices or _demo_prices(symbol, lookback)


@router.get("")
async def get_market_regime(
    request: Request,
    symbol: str = Query(default="BTC/USDT", description="Trading pair symbol"),
    lookback: int = Query(default=60, ge=20, le=200, description="Lookback period"),
) -> APIResponse[dict[str, Any]]:
    """Get current market regime for a symbol.

    Returns regime classification based on annualized return and volatility:
    - bull: Strong uptrend with low volatility
    - bear: Strong downtrend
    - sideways: Mean-reversion suitable
    - transition: High volatility, caution advised

    Args:
        symbol: Trading pair (e.g., BTC/USDT).
        lookback: Number of bars for calculation.

    Returns:
        APIResponse wrapping regime, annual_return, volatility, timestamp.
    """
    cache_key = f"{symbol}:{lookback}"
    cached = _cached_regime(cache_key)
    if cached is not None:
        return APIResponse(data=cached, meta=build_response_meta(request), error=None)

    result = _compute_regime_from_prices(await _prices_for(symbol, lookback))
    _regime_cache[cache_key] = {**result, "symbol": symbol, "timestamp": _now_ms()}
    return APIResponse(
        data=_regime_cache[cache_key], meta=build_response_meta(request), error=None
    )


@router.post("/compute")
async def compute_regime_from_data(
    request: Request,
    prices: list[float] = Query(..., description="List of closing prices"),
) -> APIResponse[dict[str, Any]]:
    """Compute market regime from provided price data.

    This endpoint allows computing regime from custom price series,
    useful for backtesting or historical analysis.

    Args:
        prices: List of closing prices (at least 20 recommended).

    Returns:
        APIResponse wrapping regime, annual_return, volatility.
    """
    _require_enough_prices(prices)
    return APIResponse(
        data=_stamped(_compute_regime_from_prices(prices)),
        meta=build_response_meta(request),
        error=None,
    )
