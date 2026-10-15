"""Worker heartbeats repository — liveness tracking for long-running tasks.

写侧:``beat`` UPSERT 一行(幂等),``mark_stopped`` 标记退出。
读侧:``list_stale`` 按 ISO-8601 UTC 字符串比较捞出失联行(字典序=时间序,
SQLite 无需日期函数),``list`` 全量倒序。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any


class WorkerHeartbeatRepository:
    """Facade over the ``worker_heartbeats`` table."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def beat(
        self,
        worker_id: str,
        *,
        role: str,
        status: str = "running",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """UPSERT 一行心跳,同一 worker_id 幂等,heartbeat_at 刷新为 now。

        Args:
            worker_id: 长驻任务唯一标识(如 ``"scheduler"``)。
            role: 任务角色(scheduler / repair / paper / ...)。
            status: ``running`` | ``stopped`` | ``failed``,默认 running。
            metadata: 可选附加字段,如 running_strategies 计数,序列化存
                ``metadata_json``。
        """
        now_iso = datetime.now(UTC).isoformat()
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        await self._db.execute(
            """
            INSERT INTO worker_heartbeats (worker_id, role, status, heartbeat_at, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(worker_id) DO UPDATE SET
                role = excluded.role,
                status = excluded.status,
                heartbeat_at = excluded.heartbeat_at,
                metadata_json = excluded.metadata_json
            """,
            (worker_id, role, status, now_iso, metadata_json),
        )

    async def mark_stopped(self, worker_id: str) -> None:
        """置状态为 stopped,之后不再被 list_stale 捞出。"""
        await self._db.execute(
            "UPDATE worker_heartbeats SET status = 'stopped' WHERE worker_id = ?",
            (worker_id,),
        )

    async def list_stale(
        self, now_ms: int, stale_secs: int = 45
    ) -> list[dict[str, Any]]:
        """捞出失联行:status=running 且心跳早于 now - stale_secs,最老优先。

        ``heartbeat_at < iso(now - stale_secs)`` 用 ISO-8601 UTC 字符串比较,
        字典序即时间序,与 SQLite 现有 ISO 存储惯例一致。

        Args:
            now_ms: 当前 epoch 毫秒。
            stale_secs: stale 阈值(秒),默认 45。

        Returns:
            失联行 dict 列表,按心跳时间从老到新。
        """
        cutoff = datetime.fromtimestamp(now_ms / 1000.0, tz=UTC) - timedelta(
            seconds=stale_secs
        )
        rows = await self._db.execute(
            "SELECT * FROM worker_heartbeats "
            "WHERE status = 'running' AND heartbeat_at < ? "
            "ORDER BY heartbeat_at ASC",
            (cutoff.isoformat(),),
        )
        return [self._decode(row) for row in rows]

    async def list(self) -> list[dict[str, Any]]:
        """全量心跳行,最新在前。"""
        rows = await self._db.execute(
            "SELECT * FROM worker_heartbeats ORDER BY heartbeat_at DESC"
        )
        return [self._decode(row) for row in rows]

    @staticmethod
    def _decode(row: Any) -> dict[str, Any]:
        out = dict(row)
        raw = out.get("metadata_json")
        out["metadata_json"] = json.loads(raw) if raw else None
        return out
