"""Paper simulation plans derived from experiment setups.

A plan records an experiment's entry/SL/TP/direction for manual paper
tracking.  Status follows a small state machine — planned -> active ->
completed, with planned -> cancelled allowed as an early exit; terminal
states accept no further transitions.
"""

from __future__ import annotations

import uuid
from typing import Any, List

_PLAN_FIELDS = (
    "experiment_id",
    "name",
    "symbol",
    "timeframe",
    "direction",
    "entry",
    "stop_loss",
    "take_profit",
    "quantity",
    "account_id",
    "note",
)

_TRANSITIONS: dict[str, set[str]] = {
    "planned": {"active", "cancelled"},
    "active": {"completed"},
    "completed": set(),
    "cancelled": set(),
}


class PaperPlanRepository:
    """Facade over the ``paper_plans`` table."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def create(self, data: dict[str, Any]) -> str:
        plan_id = f"plan-{uuid.uuid4().hex[:12]}"
        cols = {field: data.get(field) for field in _PLAN_FIELDS if field in data}
        status = data.get("status", "planned")
        names = ", ".join(cols)
        placeholders = ", ".join("?" for _ in cols)
        await self._db.execute(
            f"INSERT INTO paper_plans (id, status, {names}) VALUES (?, ?, {placeholders})",
            (plan_id, status, *cols.values()),
        )
        return plan_id

    async def get(self, plan_id: str) -> dict[str, Any] | None:
        rows = await self._db.execute(
            "SELECT * FROM paper_plans WHERE id = ?", (plan_id,)
        )
        return dict(rows[0]) if rows else None

    # 返回注解用 typing.List: 同类作用域里 ``def list`` 方法把内置 ``list``
    # 遮蔽掉了, ``list[dict]`` 会被 mypy 当成方法引用。
    async def list(
        self,
        status: str | None = None,
        limit: int | None = 50,
        offset: int = 0,
    ) -> List[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        paging = (
            f" LIMIT {int(limit)} OFFSET {int(offset)}"
            if limit is not None and (limit or offset)
            else (f" LIMIT -1 OFFSET {int(offset)}" if offset else "")
        )
        rows = await self._db.execute(
            "SELECT id FROM paper_plans"
            + where
            + " ORDER BY created_at DESC, id DESC"
            + paging,
            tuple(params),
        )
        out = [await self.get(r["id"]) for r in rows]
        return [item for item in out if item is not None]

    async def list_by_experiment(self, experiment_id: str) -> List[dict[str, Any]]:
        rows = await self._db.execute(
            "SELECT id FROM paper_plans WHERE experiment_id = ? ORDER BY created_at DESC, id DESC",
            (experiment_id,),
        )
        out = [await self.get(r["id"]) for r in rows]
        return [item for item in out if item is not None]

    async def transition(self, plan_id: str, new_status: str) -> dict[str, Any] | None:
        current = await self.get(plan_id)
        if current is None or new_status not in _TRANSITIONS:
            return None
        from_status = current["status"]
        if new_status not in _TRANSITIONS.get(from_status, set()):
            return None
        # Guarded transition: WHERE carries the status we read, so a concurrent
        # transition between read and write is a no-op instead of an overwrite.
        await self._db.execute(
            "UPDATE paper_plans SET status = ?, updated_at = datetime('now') "
            "WHERE id = ? AND status = ?",
            (new_status, plan_id, from_status),
        )
        updated = await self.get(plan_id)
        if updated is None or updated["status"] != new_status:
            return None  # lost the race; caller re-reads and retries
        return updated
