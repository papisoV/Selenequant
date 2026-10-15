"""Taiwan (台股) market data endpoints — FinMind open-data API.

Wraps the pure functions in ``selene_space_stock.taiwan_finmind`` as REST
endpoints. Symbol input is normalized to FinMind's bare stock-code semantics
(``2330.TW`` -> ``2330``). Upstream errors map to unified SeleneSpaceError
codes: missing token -> 401 AUTHENTICATION_ERROR, rate limit -> 429
RATE_LIMITED, empty result -> 404 NOT_FOUND, other upstream failure -> 502
EXCHANGE_ERROR.

Endpoints are synchronous ``def`` so FastAPI runs them in a threadpool; the
underlying FinMind calls are blocking stdlib urllib with a 30s timeout.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from app.core.dependencies import build_response_meta
from selene_space_stock import taiwan_finmind
from selene_space_types.common import APIResponse
from selene_space_types.errors import ErrorCode, SeleneSpaceError

router = APIRouter(prefix="/taiwan", tags=["taiwan"])


def _normalize_symbol(symbol: str) -> str:
    """Strip a trailing ``.TW`` suffix (case-insensitive) for FinMind semantics."""
    s = symbol.strip()
    if s.upper().endswith(".TW"):
        s = s[:-3]
    return s.strip()


def _to_iso(yyyymmdd: str) -> str:
    """Normalize a YYYYMMDD query value to FinMind's YYYY-MM-DD."""
    s = yyyymmdd.strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _wrap_finmind(fn: Any, *args: Any) -> Any:
    """Call a taiwan_finmind function, mapping its errors to SeleneSpaceError."""
    try:
        return fn(*args)
    except taiwan_finmind.FinMindTokenError as e:
        raise SeleneSpaceError(
            code=ErrorCode.AUTHENTICATION_ERROR,
            message=str(e),
        ) from e
    except taiwan_finmind.FinMindRateLimitError as e:
        raise SeleneSpaceError(
            code=ErrorCode.RATE_LIMITED,
            message=str(e),
        ) from e
    except taiwan_finmind.FinMindEmptyError as e:
        raise SeleneSpaceError(
            code=ErrorCode.NOT_FOUND,
            message=str(e),
        ) from e
    except taiwan_finmind.FinMindError as e:
        raise SeleneSpaceError(
            code=ErrorCode.EXCHANGE_ERROR,
            message=str(e),
        ) from e


def _respond(request: Request, data: Any) -> APIResponse[dict[str, Any]]:
    return APIResponse(
        data=data,
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/search")
def search_stocks(
    request: Request,
    q: str = Query(..., min_length=1, description="Stock name (traditional Chinese) or code"),
) -> APIResponse[list[dict[str, Any]]]:
    """Search Taiwan stocks by name or code (FinMind TaiwanStockInfo)."""
    return _respond(request, _wrap_finmind(taiwan_finmind.search, q))


@router.get("/{symbol}/quote")
def get_quote(symbol: str, request: Request) -> APIResponse[dict[str, Any]]:
    """Latest quote snapshot + market-cap cross-check."""
    return _respond(request, _wrap_finmind(taiwan_finmind.quote, _normalize_symbol(symbol)))


@router.get("/{symbol}/valuation")
def get_valuation(symbol: str, request: Request) -> APIResponse[dict[str, Any]]:
    """Valuation metrics: PER/PBR/yield + 52-week high/low."""
    return _respond(request, _wrap_finmind(taiwan_finmind.valuation, _normalize_symbol(symbol)))


@router.get("/{symbol}/kline")
def get_kline(
    symbol: str,
    request: Request,
    start: str | None = Query(None, description="YYYYMMDD"),
    end: str | None = Query(None, description="YYYYMMDD"),
) -> APIResponse[list[dict[str, Any]]]:
    """Daily OHLCV bars (v1: daily only)."""
    start_iso = _to_iso(start) if start else None
    end_iso = _to_iso(end) if end else None
    return _respond(
        request,
        _wrap_finmind(taiwan_finmind.kline, _normalize_symbol(symbol), start_iso, end_iso),
    )


@router.get("/{symbol}/financials")
def get_financials(symbol: str, request: Request) -> APIResponse[dict[str, Any]]:
    """~5y annual core financials."""
    return _respond(request, _wrap_finmind(taiwan_finmind.financials, _normalize_symbol(symbol)))


@router.get("/{symbol}/revenue")
def get_revenue(symbol: str, request: Request) -> APIResponse[dict[str, Any]]:
    """~13 months of monthly revenue with YoY."""
    return _respond(request, _wrap_finmind(taiwan_finmind.revenue, _normalize_symbol(symbol)))


@router.get("/{symbol}/dividend")
def get_dividend(symbol: str, request: Request) -> APIResponse[dict[str, Any]]:
    """Recent dividend policy."""
    return _respond(request, _wrap_finmind(taiwan_finmind.dividend, _normalize_symbol(symbol)))
