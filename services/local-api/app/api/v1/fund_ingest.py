"""fund-ingest 路由 — 转债/LOF-ETF 折溢价/REITs 场内工具 API。

L3 吸收自 <local-path-redacted> selene_space_fund_ingest)。
路由结构复刻 news.py(news-ingest 先例):sys.path 进程内导入包 +
asyncio.to_thread 阻塞隔离 + 通用 500 文案不泄露内部细节。
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/fund-ingest", tags=["fund-ingest"])

# local-api 进程内可导入 fund-ingest 包(不依赖 pip install -e)
# parents[5]: v1(0) → api(1) → app(2) → local-api(3) → services(4) → repo root(5)
sys.path.insert(
    0, str(Path(__file__).resolve().parents[5] / "packages" / "fund-ingest")
)


def _http_500(context: str, exc: Exception) -> HTTPException:
    logger.exception("fund-ingest api error: %s", context)
    return HTTPException(status_code=500, detail=f"{context}失败,请查看服务端日志")


def _fetch_rows(table: str) -> list[dict]:
    """connect→fetch→close 全在一个线程内(sqlite3 对象跨线程即 ProgrammingError)。"""
    from selene_space_fund_ingest import store

    conn = store.connect()
    try:
        return store.fetch_all(conn, table)
    finally:
        conn.close()


@router.get("/cb")
async def list_cb_bonds(
    sort: Optional[str] = None,
    top: int = 50,
):
    """转债全表;sort=double_low 时按双低升序取前 top(缺值行排除)。"""
    try:
        rows = await asyncio.to_thread(_fetch_rows, "cb_bonds")
        if sort == "double_low":
            from selene_space_fund_ingest import cb_derived

            rows = await asyncio.to_thread(cb_derived.rank_by_double_low, rows, top)
        return rows
    except Exception as e:
        raise _http_500("转债查询", e)


@router.get("/lof-premium")
async def list_lof_premium(
    category: Optional[str] = None,
    limit: int = 50,
):
    """LOF/ETF 折溢价(nav 已配对行;按 |premium_pct| 降序,可按 category 过滤)。"""
    try:
        rows = await asyncio.to_thread(_fetch_rows, "lof_etf_spot")
        if category:
            rows = [r for r in rows if r.get("category") == category]
        with_premium = [r for r in rows if r.get("premium_pct") is not None]
        with_premium.sort(key=lambda r: abs(r["premium_pct"]), reverse=True)
        return with_premium[: max(1, limit)]
    except Exception as e:
        raise _http_500("LOF折溢价查询", e)


@router.get("/etf-spot")
async def list_etf_spot(limit: int = 50):
    """ETF 场内快照(price/nav/premium,nav 未配对行 premium 为 null)。"""
    try:
        rows = await asyncio.to_thread(_fetch_rows, "lof_etf_spot")
        etfs = [r for r in rows if r.get("category") == "ETF"]
        return etfs[: max(1, limit)]
    except Exception as e:
        raise _http_500("ETF快照查询", e)


@router.get("/reits")
async def list_reits(limit: int = 50):
    """REITs 实时快照(按涨跌幅降序)。"""
    try:
        rows = await asyncio.to_thread(_fetch_rows, "reits_spot")
        rows.sort(key=lambda r: (r.get("change_pct") or 0), reverse=True)
        return rows[: max(1, limit)]
    except Exception as e:
        raise _http_500("REITs查询", e)
