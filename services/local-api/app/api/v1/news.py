"""新闻路由 — RSS 新闻、异动检测与统计的 HTTP 面。

来源: 路由结构最初移植自作者本人的既有项目 `binance-quant` 的 news 路由
(同一作者 2026-04 建构、无第三方许可问题)。2026-10-05 cleanroom 重写:
路径/参数/返回契约由前端与 lifespan 锁定, 实现改为统一的「取 store → 丢线程 →
兜底 500」执行器, 上下界提为常量。

错误处理约定: 完整异常只进服务端日志, HTTP 响应返回通用文案, 不泄露内部细节。
并发约定: store 是同步阻塞的, 所有 DB 往返必须经 ``asyncio.to_thread`` 下放。
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Optional, cast

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/news", tags=["news"])

# local-api 进程内可导入 news-ingest 包(不依赖 pip install -e)。
# parents[5]: v1(0) → api(1) → app(2) → local-api(3) → services(4) → repo root(5)
_ROOT = Path(__file__).resolve().parents[5]
if str(_ROOT / "packages" / "news-ingest") not in sys.path:
    sys.path.insert(0, str(_ROOT / "packages" / "news-ingest"))

#: 分页上限 —— 一次请求最多回这么多行; 下界 1 保证不出现"要 0 行"的歧义。
_PAGE_LIMIT = (1, 200)
#: 统计/明细窗口的天数范围 —— 兜底挡掉 0 天与"要 500 天历史"的滥用。
_DAY_WINDOW = (1, 90)


def _get_store() -> Any:
    """取新闻存储单例。延迟导入, 让本模块在包未就绪时也能被 import。"""
    from selene_space_news_ingest.news_store import get_news_store

    return get_news_store()


def _clamp(value: int, low: int, high: int) -> int:
    """把入参夹进 [low, high] —— 越界按边界处理, 不报 422。"""
    return max(low, min(value, high))


def _http_500(context: str, exc: Exception) -> HTTPException:
    """把内部异常转成对外的通用 500(细节只留在服务端日志)。"""
    logger.exception("news api error: %s", context)
    return HTTPException(status_code=500, detail=f"{context}失败,请查看服务端日志")


async def _query(method: str, *args: Any, context: str) -> Any:
    """统一执行一次 store 往返: 取单例 → 丢线程 → 异常兜底。

    每个端点都围着同一套样板(拿 store、把同步调用下放线程、把任何异常
    折成 500), 收在这里, 端点体里只剩它真正关心的那段。
    """
    try:
        store = _get_store()
    except Exception as exc:  # 单例初始化失败也算这一路失败
        raise _http_500(context, exc) from exc
    try:
        return await asyncio.to_thread(getattr(store, method), *args)
    except Exception as exc:
        raise _http_500(context, exc) from exc


@router.get("")
async def list_news(
    symbol: Optional[str] = None,
    importance: Optional[str] = None,
    type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list:
    """分页倒序查询新闻(symbol/importance/type 过滤, 带出异动检测结果字段)。"""
    return cast(
        list,
        await _query(
            "query_news",
            symbol,
            importance,
            type,
            _clamp(limit, *_PAGE_LIMIT),
            max(0, offset),
            context="新闻查询",
        ),
    )


@router.get("/anomalies")
async def list_anomalies(days: int = 7) -> list:
    """查询过去 N 天成交量异动检测明细(仅有效检测行, 倒序)。"""
    return cast(
        list,
        await _query("query_anomalies", _clamp(days, *_DAY_WINDOW), context="异动查询"),
    )


@router.get("/stats")
async def news_stats(days: int = 7) -> dict:
    """过去 N 天 high+breaking 新闻的异动触发率与价格变化分布。"""
    return cast(
        dict,
        await _query("compute_stats", _clamp(days, *_DAY_WINDOW), context="新闻统计"),
    )


@router.get("/status")
async def news_status() -> dict:
    """三后台任务(collector/tagger/detector)最近成功时间与错误计数。"""
    return cast(dict, await _query("get_status", context="任务状态查询"))


@router.post("/poll")
async def manual_poll() -> dict:
    """手动触发一轮 RSS 采集(调试用, 不影响后台循环), 返回新增条数。"""
    try:
        from selene_space_news_ingest.news_collector import poll_once

        return {"new_items": await poll_once()}
    except Exception as exc:
        raise _http_500("手动采集", exc) from exc
