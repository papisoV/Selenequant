"""Version snapshots and deployment records for strategies.

A snapshot freezes the full strategy config at one version; a
deployment records where that version is running.  Both tables are
write-mostly and read per-strategy.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from selene_space_types.strategy import StrategyDeployment, StrategyVersionSnapshot


def _to_millis(raw: Any) -> int:
    """Stored timestamps are ISO text; callers expect epoch millis."""
    if isinstance(raw, int):
        return raw
    try:
        return int(datetime.fromisoformat(str(raw)).timestamp() * 1000)
    except (ValueError, TypeError):
        return 0


class StrategyVersionRepository:
    """Append/list access to ``strategy_versions``."""

    def __init__(self, db: Any) -> None:
        self._db = db

    @staticmethod
    def _row_to_snapshot(row: Any) -> StrategyVersionSnapshot:
        return StrategyVersionSnapshot(
            id=row["id"],
            strategy_id=row["strategy_id"],
            version=row["version"],
            config_snapshot=json.loads(row["config_json"]),
            note=row["note"],
            created_at=_to_millis(row["created_at"]),
        )

    async def save(self, snapshot: StrategyVersionSnapshot) -> str:
        await self._db.execute(
            """
            INSERT INTO strategy_versions
                (id, strategy_id, version, config_json, note)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                version = excluded.version,
                config_json = excluded.config_json,
                note = excluded.note
            """,
            (
                snapshot.id,
                snapshot.strategy_id,
                snapshot.version,
                json.dumps(snapshot.config_snapshot, ensure_ascii=False),
                snapshot.note,
            ),
        )
        return snapshot.id

    async def list_by_strategy(
        self, strategy_id: str, limit: int | None = None
    ) -> list[StrategyVersionSnapshot]:
        query = (
            "SELECT id, strategy_id, version, config_json, note, created_at "
            "FROM strategy_versions WHERE strategy_id = ? ORDER BY rowid DESC"
        )
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        rows = await self._db.execute(query, (strategy_id,))
        return [self._row_to_snapshot(row) for row in rows]


class StrategyDeploymentRepository:
    """Append/list access to ``strategy_deployments``."""

    _COLUMNS = ("id", "strategy_id", "strategy_version", "account_id", "mode", "status")

    def __init__(self, db: Any) -> None:
        self._db = db

    async def save(self, deployment: StrategyDeployment) -> str:
        values = tuple(getattr(deployment, col) for col in self._COLUMNS)
        column_list = ", ".join(self._COLUMNS)
        marks = ", ".join("?" for _ in self._COLUMNS)
        assignments = ", ".join(f"{c} = excluded.{c}" for c in self._COLUMNS[1:])
        await self._db.execute(
            f"INSERT INTO strategy_deployments ({column_list}) VALUES ({marks}) "
            f"ON CONFLICT(id) DO UPDATE SET {assignments}",
            values,
        )
        return deployment.id

    async def list_by_strategy(
        self, strategy_id: str, limit: int | None = None
    ) -> list[StrategyDeployment]:
        wanted = (*self._COLUMNS, "created_at")
        query = (
            f"SELECT {', '.join(wanted)} FROM strategy_deployments "
            "WHERE strategy_id = ? ORDER BY rowid DESC"
        )
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        rows = await self._db.execute(query, (strategy_id,))
        return [
            StrategyDeployment(
                **{col: row[col] for col in self._COLUMNS},
                created_at=_to_millis(row["created_at"]),
            )
            for row in rows
        ]
