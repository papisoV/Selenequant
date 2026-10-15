"""Commodities (CN futures) & FX research endpoints — AKShare source.

Wraps ``selene_space_commodities_fx.data_fetcher`` as REST. Empty upstream ->
404 NOT_FOUND, data-source failure -> 502 EXCHANGE_ERROR, akshare not
installed -> 404 NOT_FOUND with explicit hint. Sync ``def`` -> threadpool
(the underlying akshare calls are blocking HTTP).

Data source & usage boundary: research-terminal quote/kline only, no trading
signals (与红线5 同类决策记录).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from app.core.dependencies import build_response_meta
from selene_space_types.common import APIResponse
from selene_space_types.errors import ErrorCode, SeleneSpaceError

router = APIRouter(prefix="/commodities-fx", tags=["commodities-fx"])


def _wrap(fn: Any, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    try:
        df = fn(*args, **kwargs)
    except ImportError as e:
        raise SeleneSpaceError(
            code=ErrorCode.NOT_FOUND,
            message=f"akshare 未安装,无法获取大宗/外汇数据(可选依赖,手动安装): {e}",
        ) from e
    except Exception as e:
        raise SeleneSpaceError(
            code=ErrorCode.EXCHANGE_ERROR,
            message=f"数据源请求失败: {e}",
        ) from e
    if df is None or len(df) == 0:
        raise SeleneSpaceError(
            code=ErrorCode.NOT_FOUND,
            message="未找到对应数据(数据源可能无此品种/区间)",
        )
    return df.to_dict(orient="records")


def _respond(request: Request, data: Any) -> APIResponse[list[dict[str, Any]]]:
    return APIResponse(
        data=data,
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/{symbol}/kline")
def get_kline_endpoint(
    symbol: str,
    request: Request,
    period: str = Query("1", description="1/5/15/30/60=分钟,其他=日线"),
    start: str = Query("", description="YYYYMMDD 或 YYYY-MM-DD,过滤下限"),
    end: str = Query("", description="YYYYMMDD 或 YYYY-MM-DD,过滤上限"),
) -> APIResponse[list[dict[str, Any]]]:
    """CN futures K-line (AU0 沪金 / CU0 沪铜 / SC0 原油 …)."""
    from selene_space_commodities_fx.data_fetcher import get_futures_kline

    return _respond(request, _wrap(get_futures_kline, symbol, period, start, end))


@router.get("/fx/quotes")
def get_fx_quotes_endpoint(
    request: Request,
) -> APIResponse[list[dict[str, Any]]]:
    """FX spot quotes (国际对 + 含 USD/CNY)."""
    from selene_space_commodities_fx.data_fetcher import get_fx_quotes

    return _respond(request, _wrap(get_fx_quotes))
