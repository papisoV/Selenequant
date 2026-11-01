"""Favorites API — persist and sync favorite trading symbols."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter

from app.core.dependencies import (
    get_kline_manager,
    get_market_service,
    get_settings_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/favorites", tags=["favorites"])

MAX_FAVORITES = 30


@router.get("")
async def get_favorites() -> dict[str, Any]:
    svc = get_settings_service()
    if svc is None:
        return {"data": {"symbols": []}, "error": None}
    symbols = svc.get_favorite_symbols()
    return {"data": {"symbols": symbols}, "error": None}


@router.put("")
async def replace_favorites(body: dict[str, Any]) -> dict[str, Any]:
    svc = get_settings_service()
    if svc is None:
        return {"data": None, "error": "Settings service not available"}
    symbols = body.get("symbols", [])
    if not isinstance(symbols, list):
        return {"data": None, "error": "symbols must be a list"}
    cleaned = svc.set_favorite_symbols(symbols)
    _maybe_sync_new_symbols(cleaned)
    return {"data": {"symbols": cleaned}, "error": None}


@router.post("/toggle")
async def toggle_favorite(body: dict[str, Any]) -> dict[str, Any]:
    svc = get_settings_service()
    if svc is None:
        return {"data": None, "error": "Settings service not available"}
    symbol = body.get("symbol", "")
    if not symbol or not isinstance(symbol, str):
        return {"data": None, "error": "symbol is required"}

    # Storage form is bare for A-shares; a caller sending "600519.SH" must
    # toggle the same row "600519" rather than adding a twin.
    from app.core.source_routing import to_favorite_storage

    symbol = to_favorite_storage(symbol)

    current = svc.get_favorite_symbols()
    if symbol in current:
        updated = [s for s in current if s != symbol]
    else:
        updated = [symbol, *[s for s in current if s != symbol]][:MAX_FAVORITES]

    cleaned = svc.set_favorite_symbols(updated)

    # Start K-line sync for newly added symbol
    if symbol not in current and symbol in cleaned:
        _maybe_sync_new_symbols([symbol])

    return {"data": {"symbols": cleaned}, "error": None}


def _maybe_sync_new_symbols(symbols: list[str]) -> None:
    """Fire-and-forget background K-line sync for new favorite symbols."""
    kline_mgr = get_kline_manager()
    market_svc = get_market_service()
    if kline_mgr is None or market_svc is None:
        return

    import os

    _intervals_raw = (
        os.environ.get("SELENE_KLINE_INTERVALS")
        or os.environ.get("SELENE_DEFAULT_INTERVAL")
        or "30m,1h"
    )
    intervals = [i.strip() for i in _intervals_raw.split(",") if i.strip()]

    for sym in symbols:
        asyncio.create_task(
            _sync_symbol_klines(sym, kline_mgr, market_svc, intervals),
            name=f"favorite-sync-{sym}",
        )


async def _sync_symbol_klines(
    symbol: str,
    manager: Any,
    market_svc: Any,
    intervals: list[str],
) -> None:
    """Subscribe symbol on the sources for its market, then sync K-lines.

    Routing goes through ``source_routing`` so a favorite added at runtime is
    handled exactly like one present at startup: an A-share code reaches the
    astock sources, a US ticker the Yahoo source, and neither is fired at a
    crypto venue. The previous hardcoded crypto list sent every added symbol to
    Binance/OKX (a live A-share 400) and never subscribed the equity feed.

    The stored form is bare (``"600519"``) but the feeds and the KlineDB
    partition are keyed suffixed, so the conversion happens here — the
    consumer boundary — not in storage.
    """
    from app.core.source_routing import (
        intervals_for,
        sources_for_symbol,
        to_ashare_symbol,
    )

    symbol = to_ashare_symbol(symbol)
    targets = sources_for_symbol(symbol, list(market_svc.sources))
    if not targets:
        logger.warning(
            "No data source registered for %r; skipping favorite subscribe "
            "(add the source or remove the symbol from favorites)",
            symbol,
        )
        return

    for ex, mt in targets:
        try:
            await market_svc.add_symbol(symbol, exchange=ex, market_type=mt)
            logger.info("Favorite subscribe: %s on %s/%s", symbol, ex, mt)
        except Exception:
            logger.warning(
                "Favorite subscribe failed: %s on %s/%s", symbol, ex, mt, exc_info=True
            )
        await asyncio.sleep(0.3)

    for ex, mt in targets:
        for ivl in intervals_for(ex, mt, intervals):
            try:
                count = await manager.sync_incremental(ex, mt, symbol, ivl, days=365)
                if count:
                    logger.info(
                        "Favorite sync: %s %s/%s %s +%d", symbol, ex, mt, ivl, count
                    )
            except Exception:
                logger.warning(
                    "Favorite sync failed: %s %s/%s %s",
                    symbol,
                    ex,
                    mt,
                    ivl,
                    exc_info=True,
                )
            await asyncio.sleep(0.5)
