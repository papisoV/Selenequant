"""记录持久化 Protocol + 测试桩（市场无关）。

US-007 将基于 packages/storage 提供 SQLite 实现；此处先定义契约，
供编排器依赖注入与单测使用。
"""
from __future__ import annotations

from typing import Protocol


class RecordPersistence(Protocol):
    async def save(self, record: dict) -> str:
        """持久化一条记录，返回可定位的 id / 路径。

        异步契约与 packages/storage 的 AsyncSQLiteManager 对齐（US-007）。
        """
        ...


class InMemoryPersistence:
    """测试桩：记录保存在内存列表，可回放 / 断言。"""

    def __init__(self) -> None:
        self.records: list[dict] = []

    async def save(self, record: dict) -> str:
        self.records.append(record)
        return f"memory://{len(self.records)}"


__all__ = ["RecordPersistence", "InMemoryPersistence"]
