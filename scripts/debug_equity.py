"""打印 paper_equity_history 表的规模与最新一行 — 排查权益曲线问题的便利脚本。

来源: 最初移植自作者本人的既有项目 binance-quant(无第三方许可问题)。
2026-10-05 cleanroom 重写: 上游用 asyncpg 的 `db.pool.acquire()` / `conn.fetchval()`,
而本仓的 AsyncSQLiteManager 是 **SQLite**(aiosqlite),没有 pool ——
该脚本自移植后从未能运行,本次改为走本仓的 `connection()` 上下文。

用法: call activate.bat && python scripts/debug_equity.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.append(os.getcwd())


async def _fetch_one(db, sql: str, args: tuple = ()) -> object:
    """跑一条单值查询, 返回首列首行(无行则 None)。"""
    async with db.connection() as conn:
        async with conn.execute(sql, args) as cursor:
            row = await cursor.fetchone()
    return None if row is None else row[0]


async def _fetch_row(db, sql: str, args: tuple = ()) -> dict | None:
    """跑一条返回单行的查询, 按游标列名转 dict。"""
    async with db.connection() as conn:
        async with conn.execute(sql, args) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            names = [col[0] for col in cursor.description]
    return dict(zip(names, row))


async def check_equity_data() -> None:
    from app.core.dependencies import get_db_manager

    db = get_db_manager()
    if db is None:
        print("Error: DB manager not found")
        return
    if not db.is_connected:
        await db.connect()

    count = await _fetch_one(db, "SELECT COUNT(*) FROM paper_equity_history")
    print(f"Total equity points: {count}")

    if count:
        latest = await _fetch_row(
            db,
            "SELECT * FROM paper_equity_history ORDER BY timestamp DESC LIMIT 1",
        )
        print(f"Latest record: {latest}")


if __name__ == "__main__":
    asyncio.run(check_equity_data())
