"""Options data endpoints — Deribit options tickers, instruments, and summaries.

Provides real-time and cached options analytics from Deribit WebSocket
and REST API sources.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from app.core.dependencies import build_response_meta, get_market_service
from selene_space_types.common import APIResponse
from selene_space_types.errors import ErrorCode, SeleneSpaceError

router = APIRouter(prefix="/options", tags=["options"])


def _now_ms() -> int:
    return int(time.time() * 1000)


def _format_expiry(value: Any) -> str:
    """Normalize an expiration value to YYYY-MM-DD (UTC).

    OptionsInstrument.expiration_timestamp is an ms-epoch; formatting here keeps
    axis labels readable and keeps the expiries list lexicographically ordered
    across years (vol_surface-page.tsx trims YYYY-MM-DD to MM-DD for axis labels).
    """
    if isinstance(value, str):
        return value
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return str(value)


def _require_market_service(svc: Any = Depends(get_market_service)) -> Any:
    if svc is None:
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="Market data service is not available",
        )
    return svc


@router.get("/tickers")
async def get_options_tickers(
    request: Request,
    base_currency: str = Query("BTC", description="Base currency: BTC or ETH"),
    svc: Any = Depends(_require_market_service),
) -> APIResponse[dict[str, Any]]:
    """Get current options tickers for all tracked instruments."""
    store = getattr(svc, "_options_ticker_store", None)
    if store is None:
        return APIResponse(
            data={"tickers": [], "count": 0},
            meta=build_response_meta(request),
            error=None,
        )
    tickers = [t.model_dump() for t in store.values() if t.instrument_name.startswith(base_currency)]
    return APIResponse(
        data={"tickers": tickers, "count": len(tickers), "base_currency": base_currency},
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/tickers/{instrument_name}")
async def get_options_ticker(
    instrument_name: str,
    request: Request,
    svc: Any = Depends(_require_market_service),
) -> APIResponse[dict[str, Any]]:
    """Get ticker for a specific options instrument (e.g. BTC-28MAR25-80000-C)."""
    store = getattr(svc, "_options_ticker_store", None)
    if store is None:
        return APIResponse(
            data={"message": "Options data not available"},
            meta=build_response_meta(request),
            error=None,
        )
    ticker = store.get(instrument_name)
    if ticker is None:
        return APIResponse(
            data={"message": f"Instrument {instrument_name} not found"},
            meta=build_response_meta(request),
            error=None,
        )
    return APIResponse(
        data=ticker.model_dump(),
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/summary")
async def get_options_summary(
    request: Request,
    base_currency: str = Query("BTC", description="Base currency: BTC or ETH"),
    svc: Any = Depends(_require_market_service),
) -> APIResponse[dict[str, Any]]:
    """Get aggregated options summary per expiry."""
    store = getattr(svc, "_options_summary_store", None)
    if store is None:
        return APIResponse(
            data={"summaries": [], "count": 0},
            meta=build_response_meta(request),
            error=None,
        )
    summaries = [s.model_dump() for s in store.values() if s.base_currency == base_currency]
    return APIResponse(
        data={"summaries": summaries, "count": len(summaries), "base_currency": base_currency},
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/instruments")
async def get_options_instruments(
    request: Request,
    base_currency: str = Query("BTC", description="Base currency: BTC or ETH"),
    active_only: bool = Query(True, description="Only return active instruments"),
    svc: Any = Depends(_require_market_service),
) -> APIResponse[dict[str, Any]]:
    """Get available options instruments metadata."""
    store = getattr(svc, "_options_instrument_store", None)
    if store is None:
        return APIResponse(
            data={"instruments": [], "count": 0},
            meta=build_response_meta(request),
            error=None,
        )
    instruments = [
        i.model_dump() for i in store.values()
        if i.base_currency == base_currency and (not active_only or i.is_active)
    ]
    return APIResponse(
        data={"instruments": instruments, "count": len(instruments), "base_currency": base_currency},
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/vol-surface")
async def get_options_vol_surface(
    request: Request,
    base_currency: str = Query("BTC", description="Base currency: BTC or ETH"),
    svc: Any = Depends(get_market_service),
) -> APIResponse[dict[str, Any]]:
    """Implied volatility surface aggregated from options instruments (Phase 4).

    Walks the options instrument store, groups by expiry+strike, and emits a
    grid of (expiry, strike, iv) points. IV is pulled from the per-instrument
    ticker store when available.

    Returns an empty surface (with underlying + ts) if no market service is
    attached, so callers can render the grid shell while data loads.
    """
    if svc is None:
        return APIResponse(
            data={
                "underlying": base_currency,
                "ts": _now_ms(),
                "points": [],
                "expiries": [],
                "strikes": [],
            },
            meta=build_response_meta(request),
            error=None,
        )

    instrument_store = getattr(svc, "_options_instrument_store", None) or {}
    ticker_store = getattr(svc, "_options_ticker_store", None) or {}

    instruments = [
        i for i in instrument_store.values()
        if i.base_currency == base_currency and getattr(i, "is_active", True)
    ]

    points: list[dict[str, Any]] = []
    expiries_set: set[str] = set()
    strikes_set: set[float] = set()

    for inst in instruments:
        expiry = getattr(inst, "expiration_timestamp", None)
        strike = getattr(inst, "strike", None)
        if expiry is None or strike is None:
            continue
        expiry_str = _format_expiry(expiry)
        try:
            strike_f = float(strike)
        except (TypeError, ValueError):
            continue

        expiries_set.add(expiry_str)
        strikes_set.add(strike_f)

        # Look up IV from the ticker store, if available.
        ticker = ticker_store.get(getattr(inst, "instrument_name", ""))
        iv = None
        iv_change = None
        volume = None
        if ticker is not None:
            iv = getattr(ticker, "implied_volatility", None)
            iv_change = getattr(ticker, "iv_change", None)
            volume = getattr(ticker, "volume_24h", None)

        points.append({
            "expiry": expiry_str,
            "strike": strike_f,
            "iv": float(iv) if iv is not None else 0.0,
            "iv_change": float(iv_change) if iv_change is not None else None,
            "volume": int(volume) if volume is not None else None,
        })

    result = {
        "underlying": base_currency,
        "ts": _now_ms(),
        "points": points,
        "expiries": sorted(expiries_set),
        "strikes": sorted(strikes_set),
    }
    return APIResponse(
        data=result,
        meta=build_response_meta(request),
        error=None,
    )
