"""RecordPersistence 的 SQLite 实现（US-007）。

委托 packages/storage 的 AnalysisRecordRepository 落盘 analysis_records 表。
契约对齐 .persistence.RecordPersistence（异步 save → id）。
"""
from __future__ import annotations

from typing import Any

from selene_space_storage.repositories.analysis_records import (
    AnalysisRecordRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager


class SqliteRecordPersistence:
    """把编排器产出的完整记录写入 SQLite，返回可回放 id。"""

    def __init__(self, db: AsyncSQLiteManager) -> None:
        self._repo = AnalysisRecordRepository(db)

    async def save(self, record: dict[str, Any]) -> str:
        return await self._repo.save(record)

    async def get(self, record_id: str) -> dict[str, Any] | None:
        """按 id 取回完整记录（回放 / 增量分析 prev_record）。"""
        return await self._repo.get(record_id)

    async def list_recent(
        self,
        *,
        symbol: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """经验库检索：最近完整记录（倒序，含 meta.record_id），可复用为增量先验。"""
        return await self._repo.list_records(symbol=symbol, limit=limit)


__all__ = ["SqliteRecordPersistence"]
