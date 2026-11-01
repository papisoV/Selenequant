"""Research experiments — the "Git for Quant Research" core object.

An experiment freezes one "AI spotted a setup -> setup backtest" cycle:
hypothesis, dataset reference, setup parameters, optional backtest
results, and evidence links.  Forks form a lineage via the
``parent_experiment`` column; ancestor walks are cycle-guarded.
"""

from __future__ import annotations

import builtins
import copy
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

_MAX_ANCESTOR_DEPTH = 20


class ExperimentRepository:
    """Facade over the ``experiments`` table."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def create(self, data: dict[str, Any]) -> str:
        """Insert a new experiment row with a generated id.

        Args:
            data: Field dict; recognized keys are ``name``, ``hypothesis``,
                ``symbol``, ``timeframe``, ``dataset``, ``strategy_version``,
                ``setup_params``, ``backtest_result``, ``evidence_refs``, and
                ``parent_experiment``. Unknown keys are ignored; JSON-typed
                fields accept dicts/lists and are serialized here.

        Returns:
            The generated experiment id (``exp-<hex12>``).
        """
        experiment_id = f"exp-{uuid.uuid4().hex[:12]}"
        await self._db.execute(
            """
            INSERT INTO experiments (
                id, name, hypothesis, symbol, timeframe, dataset,
                strategy_version, setup_params, backtest_result,
                evidence_refs, parent_experiment
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                experiment_id,
                data.get("name", ""),
                data.get("hypothesis"),
                data.get("symbol"),
                data.get("timeframe"),
                data.get("dataset"),
                data.get("strategy_version"),
                self._dump_json(data.get("setup_params")),
                self._dump_json(data.get("backtest_result")),
                self._dump_json(data.get("evidence_refs")),
                data.get("parent_experiment"),
            ),
        )
        return experiment_id

    async def get(self, experiment_id: str) -> dict[str, Any] | None:
        rows = await self._db.execute(
            "SELECT * FROM experiments WHERE id = ?", (experiment_id,)
        )
        if not rows:
            return None
        row = dict(rows[0])
        row["setup_params"] = self._load_json(row.get("setup_params"))
        row["backtest_result"] = self._load_json(row.get("backtest_result"))
        row["evidence_refs"] = self._load_json(row.get("evidence_refs"))
        return row

    async def list(
        self,
        symbol: str | None = None,
        limit: int | None = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        paging = (
            f" LIMIT {int(limit)} OFFSET {int(offset)}"
            if limit is not None and (limit or offset)
            else (f" LIMIT -1 OFFSET {int(offset)}" if offset else "")
        )
        rows = await self._db.execute(
            "SELECT id FROM experiments"
            + where
            + " ORDER BY created_at DESC, id DESC"
            + paging,
            tuple(params),
        )
        out = [await self.get(r["id"]) for r in rows]
        return [item for item in out if item is not None]

    async def fork(self, parent_id: str, overrides: dict[str, Any]) -> str:
        """Clone an experiment into a child, recording the lineage link.

        All inheritable fields are deep-copied from the parent; values in
        ``overrides`` win. The child's ``parent_experiment`` always points
        at ``parent_id`` regardless of overrides.

        Args:
            parent_id: Experiment to clone.
            overrides: Field replacements applied on top of the parent's
                data (deep-copied so caller mutations cannot leak in).

        Returns:
            The new child experiment id.

        Raises:
            KeyError: No experiment exists under ``parent_id``.
        """
        parent = await self.get(parent_id)
        if parent is None:
            raise KeyError(f"experiment not found: {parent_id}")
        child_data = {
            key: parent.get(key)
            for key in (
                "name",
                "hypothesis",
                "symbol",
                "timeframe",
                "dataset",
                "strategy_version",
                "setup_params",
                "backtest_result",
                "evidence_refs",
            )
        }
        child_data["parent_experiment"] = parent_id
        child_data.update(copy.deepcopy(overrides))
        return await self.create(child_data)

    async def get_ancestors(self, experiment_id: str) -> builtins.list[dict[str, Any]]:
        """Walk the ``parent_experiment`` chain from nearest to root.

        Cycle-guarded via visited-id set; stops after
        ``_MAX_ANCESTOR_DEPTH`` hops or when a parent is missing.

        Returns:
            Ancestor experiments ordered nearest-parent first (empty list
            for a root experiment).
        """
        chain: list[dict[str, Any]] = []
        seen: set[str] = {experiment_id}
        cursor_id = experiment_id
        for _ in range(_MAX_ANCESTOR_DEPTH):
            current = await self.get(cursor_id)
            if current is None:
                break
            parent_id = current.get("parent_experiment")
            if not parent_id or parent_id in seen:
                break
            parent = await self.get(parent_id)
            if parent is None:
                break
            chain.append(parent)
            seen.add(parent_id)
            cursor_id = parent_id
        return chain

    async def list_children(self, parent_id: str) -> builtins.list[dict[str, Any]]:
        rows = await self._db.execute(
            "SELECT id FROM experiments WHERE parent_experiment = ? ORDER BY created_at DESC, id DESC",
            (parent_id,),
        )
        out = [await self.get(r["id"]) for r in rows]
        return [item for item in out if item is not None]

    async def list_due(
        self,
        now_ms: int,
        horizon_days: int = 7,
        limit: int = 100,
    ) -> builtins.list[dict[str, Any]]:
        """超期未验证的实验,最老的先验。

        created_at 距 ``now_ms`` 已超过 ``horizon_days`` 且 verified_at
        仍为空的行。已验证/未到期的永不出现。
        """
        cutoff_iso = (
            datetime.fromtimestamp(now_ms / 1000, tz=UTC) - timedelta(days=horizon_days)
        ).isoformat()
        rows = await self._db.execute(
            """
            SELECT id FROM experiments
            WHERE created_at <= ? AND verified_at IS NULL
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,
            (cutoff_iso, int(limit)),
        )
        out = [await self.get(r["id"]) for r in rows]
        return [item for item in out if item is not None]

    async def record_verification(
        self,
        experiment_id: str,
        *,
        actual_return_pct: float,
        atr_pct: float,
        was_correct: bool,
        code_hash: str | None,
        engine_version: str | None,
        verified_at_ms: int,
    ) -> bool:
        """落一条验证结论;已验证过的行拒绝重复写(幂等保护)。

        Returns:
            True 写入成功;False 该行已被验证过(本次调用无效果)。
        """
        sql = """
            UPDATE experiments SET
                verified_at = ?, was_correct = ?, actual_return_pct = ?,
                atr_pct_at_analysis = ?, code_hash = ?, engine_version = ?
            WHERE id = ? AND verified_at IS NULL
            """
        params = (
            datetime.fromtimestamp(verified_at_ms / 1000, tz=UTC).isoformat(),
            1 if was_correct else 0,
            float(actual_return_pct),
            float(atr_pct),
            code_hash,
            engine_version,
            experiment_id,
        )
        # 用 connection() 作用域拿原始 cursor.rowcount——execute() 对 UPDATE
        # 只回行列表、丢弃受影响行数,幂等判断必须依赖 rowcount。
        async with self._db.connection() as conn:
            cursor = await conn.execute(sql, params)
            try:
                rowcount = int(cursor.rowcount)
            finally:
                await cursor.close()
        return rowcount > 0

    @staticmethod
    def _dump_json(value: Any) -> str | None:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _load_json(raw: Any) -> Any:
        if raw is None:
            return None
        return json.loads(raw)
