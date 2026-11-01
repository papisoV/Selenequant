"""A-share deep data endpoints — money flow, 龙虎榜, margin, northbound.

Wraps ``selene_space_ashare.data_fetcher`` research functions as REST
endpoints. Empty upstream data -> 404 NOT_FOUND, data-source failure -> 502
EXCHANGE_ERROR, akshare not installed -> 404 NOT_FOUND with explicit hint
(optional dependency, manual install).

Endpoints are synchronous ``def`` so FastAPI runs them in a threadpool; the
underlying akshare calls are blocking HTTP.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Query, Request

from app.core.dependencies import build_response_meta
from selene_space_types.common import APIResponse
from selene_space_types.errors import ErrorCode, SeleneSpaceError

router = APIRouter(prefix="/ashare", tags=["ashare"])


def _wrap(fn: Any, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Call a data_fetcher function; map errors to SeleneSpaceError.

    Raises:
        SeleneSpaceError: NOT_FOUND on empty/akshare-missing, EXCHANGE_ERROR
        on other upstream failures.
    """
    try:
        df = fn(*args, **kwargs)
    except ImportError as e:
        raise SeleneSpaceError(
            code=ErrorCode.NOT_FOUND,
            message=f"akshare 未安装,无法获取 A 股深度数据(可选依赖,手动安装): {e}",
        ) from e
    except Exception as e:
        raise SeleneSpaceError(
            code=ErrorCode.EXCHANGE_ERROR,
            message=f"数据源请求失败: {e}",
        ) from e
    if df is None or len(df) == 0:
        raise SeleneSpaceError(
            code=ErrorCode.NOT_FOUND,
            message="未找到对应数据(数据源可能无此交易日/股票)",
        )
    return df.to_dict(orient="records")


def _respond(request: Request, data: Any) -> APIResponse[list[dict[str, Any]]]:
    return APIResponse(
        data=data,
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/fund-flow/{symbol}")
def get_fund_flow_endpoint(
    symbol: str,
    request: Request,
    start: str = Query("", description="YYYYMMDD"),
    end: str = Query("", description="YYYYMMDD"),
) -> APIResponse[list[dict[str, Any]]]:
    """Individual stock money-flow detail (主力/超大单/大单/中单/小单)."""
    from selene_space_ashare.data_fetcher import get_fund_flow

    return _respond(request, _wrap(get_fund_flow, symbol, start, end))


@router.get("/lhb")
def get_lhb_endpoint(
    request: Request,
    date: str = Query(..., description="YYYYMMDD 龙虎榜交易日"),
) -> APIResponse[list[dict[str, Any]]]:
    """龙虎榜 (Dragon-Tiger list) for a single trading day."""
    from selene_space_ashare.data_fetcher import get_lhb

    return _respond(request, _wrap(get_lhb, date))


@router.get("/margin/{symbol}")
def get_margin_endpoint(
    symbol: str,
    request: Request,
) -> APIResponse[list[dict[str, Any]]]:
    """Margin trading (融资融券) history for a stock."""
    from selene_space_ashare.data_fetcher import get_margin_data

    return _respond(request, _wrap(get_margin_data, symbol))


@router.get("/northbound")
def get_northbound_endpoint(
    request: Request,
) -> APIResponse[list[dict[str, Any]]]:
    """Northbound (北向资金) daily net flow."""
    from selene_space_ashare.data_fetcher import get_northbound_flow

    return _respond(request, _wrap(get_northbound_flow))


@router.get("/{symbol}/kline")
def get_kline_endpoint(
    request: Request,
    symbol: str = Path(..., pattern=r"^\d{6}$", description="6 位股票代码"),
    start: str = Query("", description="YYYYMMDD"),
    end: str = Query("", description="YYYYMMDD"),
    adjust: str = Query("hfq", description="hfq/qfq/空"),
) -> APIResponse[list[dict[str, Any]]]:
    """Daily OHLCV klines (default 后复权)."""
    from selene_space_ashare.data_fetcher import get_daily_klines

    return _respond(request, _wrap(get_daily_klines, symbol, start, end, adjust))
