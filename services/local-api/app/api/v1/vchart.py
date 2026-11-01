"""vchart endpoint — professional chart data for design-draft page `vchart`.

Returns close-price series + 4 indicator cards (RSI/MACD/ATR/EMA200)
matching the design-draft `_temp/final/demo-aa-scene-layout.html` vchart renderer
(lines 4485-4530). Data sourced from the existing MarketDataService kline API
so vchart stays in sync with the main K-line page.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from app.api.v1.markets import _ensure_subscribed, _normalize_symbol, _resolve_source
from app.core.dependencies import (
    build_response_meta,
    get_market_service,
    get_settings_service,
)
from app.core.settings_service import SettingsService
from selene_space_types.common import APIResponse
from selene_space_types.errors import ErrorCode, NotFoundError, SeleneSpaceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vchart", tags=["vchart"])


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2.0 / (period + 1)
    ema = [values[0]]
    for v in values[1:]:
        ema.append(v * k + ema[-1] * (1 - k))
    return ema


def _macd(closes: list[float]) -> tuple[float, float, float]:
    if len(closes) < 35:
        return 0.0, 0.0, 0.0
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = [a - b for a, b in zip(ema12[-len(ema26) :], ema26)]
    signal_line = _ema(macd_line, 9)
    macd_val = macd_line[-1] if macd_line else 0.0
    signal_val = signal_line[-1] if signal_line else 0.0
    hist_val = macd_val - signal_val
    return macd_val, signal_val, hist_val


def _atr(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 0.0
    trs = [abs(closes[i] - closes[i - 1]) for i in range(1, period + 1)]
    return sum(trs) / period


def _ema200(closes: list[float]) -> float:
    if not closes:
        return 0.0
    if len(closes) >= 200:
        return _ema(closes, 200)[-1]
    return _ema(closes, len(closes))[-1]


@router.get("")
async def get_vchart(
    request: Request,
    symbol: str = Query(..., description="Symbol, e.g. BTC-USDT-SWAP"),
    interval: str = Query("1h", description="Kline interval"),
    limit: int = Query(200, ge=30, le=1500, description="Number of closes to return"),
    exchange: str | None = Query(None),
    market_type: str | None = Query(None),
    svc: Any = Depends(get_market_service),
    settings_service: SettingsService = Depends(get_settings_service),
) -> APIResponse[dict[str, Any]]:
    """Professional chart data for the `vchart` page.

    Returns close-price series + RSI(14) / MACD / ATR(14) / EMA200 indicators.
    """
    if svc is None:
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="Market data service is not available",
        )
    sym = _normalize_symbol(symbol)
    ex, mt = _resolve_source(exchange, market_type, settings_service)
    await _ensure_subscribed(svc, sym, ex, mt)

    klines = svc.get_klines(sym, ex, mt, limit=limit)
    if not klines:
        raise NotFoundError(f"No kline data for symbol '{sym}' on {ex}/{mt}")

    closes = [float(k.close) for k in klines][-limit:]
    rsi_val = _rsi(closes)
    macd_val, signal_val, hist_val = _macd(closes)
    atr_val = _atr(closes)
    ema200_val = _ema200(closes)

    # Full OHLCV per bar. The candle renderer previously had to invent
    # high/low from the close series (close*1.001 / close*0.999) because this
    # payload only carried `closes` — the wicks were fabricated. `t` stays in
    # milliseconds to match the sibling `close_times` field.
    bars = [
        {
            "t": int(k.timestamp),
            "o": float(k.open),
            "h": float(k.high),
            "l": float(k.low),
            "c": float(k.close),
            "v": float(k.volume),
        }
        for k in klines
    ][-limit:]

    return APIResponse(
        data={
            "symbol": sym,
            "exchange": ex,
            "market_type": mt,
            "interval": interval,
            "bars": bars,
            "closes": closes,
            "close_times": [int(k.timestamp) for k in klines][-limit:],
            "indicators": {
                "rsi14": round(rsi_val, 2),
                "macd": round(macd_val, 4),
                "macd_signal": round(signal_val, 4),
                "macd_hist": round(hist_val, 4),
                "atr14": round(atr_val, 4),
                "ema200": round(ema200_val, 4),
            },
            "last_price": closes[-1] if closes else 0.0,
        },
        meta=build_response_meta(request),
        error=None,
    )
