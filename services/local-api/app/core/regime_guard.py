"""Market-regime probe for deploy-time guardrails (fail-open).

Fetches fresh klines via the market service and returns the full
MarketState (regime + trend_strength + volatility) — severity grading
in RegimeMismatchDetector needs all three, never just the regime string.
"""

from __future__ import annotations

import logging

from selene_space_types.agent import MarketState

logger = logging.getLogger(__name__)

_REGIME_LOOKBACK = 120


async def get_market_state(
    symbol: str,
    interval: str = "1h",
    exchange: str = "binance",
    market_type: str = "spot",
) -> MarketState | None:
    """Current MarketState for ``symbol``, or None when unavailable.

    Any failure (no service, empty fetch, short history, import error)
    returns None — callers must treat None as "skip the check".
    """
    try:
        from app.core.dependencies import get_market_service
        from selene_space_market.state_engine import MarketStateEngine

        service = get_market_service()
        if service is None:
            return None
        klines = await service.fetch_klines_rest(
            symbol,
            exchange=exchange,
            market_type=market_type,
            interval=interval,
            limit=_REGIME_LOOKBACK,
        )
        if not klines:
            return None
        return MarketStateEngine().analyze(symbol, klines)
    except Exception:
        logger.warning(
            "Regime probe failed for %s (%s/%s %s); failing open",
            symbol,
            exchange,
            market_type,
            interval,
            exc_info=True,
        )
        return None
