"""Persistence for :class:`StrategyConfig` objects.

The whole model is serialized to JSON and kept in the ``config_json``
column; a handful of hot columns (name, type, user_id) are lifted out for
indexing and filtering.  Writes are full-row upserts keyed by ``id``.
"""

from __future__ import annotations

import json
from typing import Any

from selene_space_types.strategy import StrategyConfig


class StrategyRepository:
    """CRUD facade over the ``strategies`` table."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def save(self, config: StrategyConfig, *, user_id: str = "local") -> str:
        payload = json.dumps(config.model_dump(mode="json"), ensure_ascii=False)
        await self._db.execute(
            """
            INSERT INTO strategies (id, name, type, config_json, updated_at, user_id)
            VALUES (?, ?, ?, ?, datetime('now'), ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                type = excluded.type,
                config_json = excluded.config_json,
                updated_at = excluded.updated_at,
                user_id = excluded.user_id
            """,
            (
                config.id,
                config.name,
                config.type.value
                if hasattr(config.type, "value")
                else str(config.type),
                payload,
                user_id,
            ),
        )
        return config.id

    async def get(
        self, strategy_id: str, *, user_id: str | None = None
    ) -> StrategyConfig | None:
        sql = "SELECT config_json FROM strategies WHERE id = ?"
        params: list[Any] = [strategy_id]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        rows = await self._db.execute(sql, tuple(params))
        if not rows:
            return None
        return StrategyConfig.model_validate(json.loads(rows[0]["config_json"]))

    async def list(
        self,
        limit: int | None = None,
        offset: int = 0,
        tags: list[str] | None = None,
        source: str | None = None,
        *,
        user_id: str | None = None,
    ) -> list[StrategyConfig]:
        clauses: list[str] = []
        params: list[Any] = []
        if tags:
            # match any of the requested tags inside the JSON array
            alternatives = " OR ".join(["instr(config_json, ?) > 0"] * len(tags))
            clauses.append(f"({alternatives})")
            params.extend(f'"{tag}"' for tag in tags)
        if source is not None:
            clauses.append("config_json LIKE ?")
            params.append(f'%"{source}"%')
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        paging = ""
        if limit is not None or offset:
            paging += f" LIMIT {int(limit) if limit is not None else -1}"
            paging += f" OFFSET {int(offset)}"
        rows = await self._db.execute(
            "SELECT config_json FROM strategies" + where + paging, tuple(params)
        )
        return [
            StrategyConfig.model_validate(json.loads(r["config_json"])) for r in rows
        ]

    async def delete(self, strategy_id: str, *, user_id: str | None = None) -> bool:
        sql = "DELETE FROM strategies WHERE id = ?"
        params: list[Any] = [strategy_id]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        before = await self._db.execute(
            "SELECT COUNT(*) AS n FROM strategies WHERE id = ?", (strategy_id,)
        )
        existed = bool(before and before[0]["n"])
        await self._db.execute(sql, tuple(params))
        return existed
