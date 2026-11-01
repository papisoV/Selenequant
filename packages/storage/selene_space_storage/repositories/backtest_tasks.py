"""Persistent task state machine for background backtests (P2-3).

写侧:``create`` 落一行 pending;``mark_running``(仅 pending→running)/
``mark_completed``(仅 running→completed)/``mark_failed``(pending|running→failed)
全部用**条件 UPDATE** 原子迁移,返回 bool 表示是否真的迁移成功——False = 状态已变,
调用方不能继续推进(防并发双跑,思想吸收自 QD 短事务状态机,非照搬其表结构)。
``mark_failed`` 接受 pending 是为了重启恢复:进程重启后未启动的队列任务也能标失败。
读侧:``get`` / ``list_by_user`` / ``count_active`` 支持热路径缓存查不到时
回退 SQLite,以及并发上限双算。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BacktestTaskRow:
    """One row of the ``backtest_tasks`` table."""

    task_id: str
    user_id: str
    strategy_id: str
    strategy_name: str = ""
    symbol: str = ""
    interval: str = "1h"
    status: str = "pending"
    error: str | None = None
    created_at: int = 0
    started_at: int | None = None
    finished_at: int | None = None
    result_id: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "BacktestTaskRow":
        return cls(
            task_id=row["task_id"],
            user_id=row["user_id"],
            strategy_id=row["strategy_id"],
            strategy_name=row["strategy_name"] or "",
            symbol=row["symbol"] or "",
            # Do NOT fall back to "1h" here. An empty interval means the row was
            # written without one (or predates the column carrying a real value);
            # reporting the default would state a candle granularity the user
            # never chose, and the list would show a confident wrong answer.
            interval=row["interval"] or "",
            status=row["status"],
            error=row["error"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            result_id=row["result_id"],
        )


class BacktestTaskRepository:
    """Facade over the ``backtest_tasks`` table."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def create(self, task: BacktestTaskRow) -> None:
        """Insert a task row (caller supplies status, defaults to pending)."""
        await self._db.execute(
            """
            INSERT INTO backtest_tasks (
                task_id, user_id, strategy_id, strategy_name, symbol, interval,
                status, error, created_at, started_at, finished_at, result_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.task_id,
                task.user_id,
                task.strategy_id,
                task.strategy_name,
                task.symbol,
                task.interval,
                task.status,
                task.error,
                task.created_at,
                task.started_at,
                task.finished_at,
                task.result_id,
            ),
        )

    async def mark_running(self, task_id: str, started_at: int) -> bool:
        """pending -> running. False when the task already left pending."""
        return await self._mark_status(
            "UPDATE backtest_tasks SET status = 'running', started_at = ? "
            "WHERE task_id = ? AND status = 'pending'",
            (started_at, task_id),
        )

    async def mark_completed(self, task_id: str, result_id: str) -> bool:
        """running -> completed with the persisted result id."""
        return await self._mark_status(
            "UPDATE backtest_tasks SET status = 'completed', finished_at = ?, result_id = ? "
            "WHERE task_id = ? AND status = 'running'",
            (self._now_ms(), result_id, task_id),
        )

    async def mark_failed(self, task_id: str, error: str) -> bool:
        """pending|running -> failed. Pending included so a queued-but-never-started
        task (e.g. lost to a process restart) can still be marked failed."""
        return await self._mark_status(
            "UPDATE backtest_tasks SET status = 'failed', finished_at = ?, error = ? "
            "WHERE task_id = ? AND status IN ('pending', 'running')",
            (self._now_ms(), error, task_id),
        )

    async def get(
        self, task_id: str, *, user_id: str | None = None
    ) -> BacktestTaskRow | None:
        """Fetch one task; optionally scoped to a user."""
        sql = "SELECT * FROM backtest_tasks WHERE task_id = ?"
        params: list[Any] = [task_id]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        rows = await self._db.execute(sql, tuple(params))
        if not rows:
            return None
        return BacktestTaskRow.from_row(rows[0])

    async def list_by_user(
        self, user_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[BacktestTaskRow]:
        """List a user's tasks, newest first."""
        rows = await self._db.execute(
            "SELECT * FROM backtest_tasks WHERE user_id = ? "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        )
        return [BacktestTaskRow.from_row(r) for r in rows]

    async def count_active(self, user_id: str) -> int:
        """Count pending + running tasks for a user (concurrency cap)."""
        rows = await self._db.execute(
            "SELECT COUNT(*) AS n FROM backtest_tasks "
            "WHERE user_id = ? AND status IN ('pending', 'running')",
            (user_id,),
        )
        return int(rows[0]["n"]) if rows else 0

    async def list_active_for_recovery(self) -> list[BacktestTaskRow]:
        """List every pending/running task across all users (restart recovery).

        Lifespan startup calls this then marks each row failed so in-flight
        work is surfaced as failed instead of silently vanishing.
        """
        rows = await self._db.execute(
            "SELECT * FROM backtest_tasks WHERE status IN ('pending', 'running') "
            "ORDER BY created_at ASC"
        )
        return [BacktestTaskRow.from_row(r) for r in rows]

    async def delete(self, task_id: str) -> bool:
        """Delete a task row; True when a row was actually removed.

        Deleting a backtest must clear this table too, not just ``backtests``:
        the list endpoint merges task rows back in, so a task row left behind
        reappears as an undeletable ghost row.
        """
        before = await self._db.execute(
            "SELECT COUNT(*) AS n FROM backtest_tasks WHERE task_id = ?", (task_id,)
        )
        existed = bool(before and before[0]["n"])
        await self._db.execute(
            "DELETE FROM backtest_tasks WHERE task_id = ?", (task_id,)
        )
        return existed

    async def _mark_status(self, sql: str, params: tuple) -> bool:
        """Run a conditional UPDATE; True only when a row actually changed.

        ``execute()`` drops affected-row counts for UPDATEs, so use the raw
        connection scope to read ``cursor.rowcount`` (same pattern as
        ``ExperimentRepository.mark_verified``).
        """
        async with self._db.connection() as conn:
            cursor = await conn.execute(sql, params)
            try:
                rowcount = int(cursor.rowcount)
            finally:
                await cursor.close()
        return rowcount > 0

    @staticmethod
    def _now_ms() -> int:
        import time

        return int(time.time() * 1000)
