"""Episodes — strategy experience ledger repository (S6).

只读账本:行由回测完成钩子写入,本仓库提供 create / list / get 三原语。
无 UPDATE / DELETE —— Episode 是不可变经验记录,修正走新 Episode。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _dump_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load_json(raw: Any) -> Any:
    if raw is None:
        return None
    return json.loads(raw)


EpisodeList = list[dict[str, Any]]
"""Module-level alias: this class defines a ``list`` method, and mypy resolves
the bare ``list`` token in lazy (PEP 563) annotations against the class scope,
so class-body signatures must not spell ``list`` directly (2026-09-23 failure)."""


class EpisodeRepository:
    """Facade over the ``episodes`` table."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def create(
        self,
        *,
        strategy_kind: str,
        strategy_id: str,
        symbol: str,
        interval: str,
        strategy_name: str | None = None,
        exchange: str | None = None,
        market_type: str | None = None,
        params: dict[str, Any] | None = None,
        backtest_id: str | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> str:
        """Insert one RECORDED episode row.

        Returns:
            The generated episode id (``epi-<hex12>``).
        """
        episode_id = f"epi-{uuid.uuid4().hex[:12]}"
        await self._db.execute(
            """
            INSERT INTO episodes (
                id, strategy_kind, strategy_id, strategy_name, symbol,
                interval, exchange, market_type, params_json, backtest_id,
                metrics_json, provenance, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'RECORDED', ?)
            """,
            (
                episode_id,
                strategy_kind,
                strategy_id,
                strategy_name,
                symbol,
                interval,
                exchange,
                market_type,
                _dump_json(params),
                backtest_id,
                _dump_json(metrics),
                _now(),
            ),
        )
        return episode_id

    async def get(self, episode_id: str) -> dict[str, Any] | None:
        rows = await self._db.execute(
            "SELECT * FROM episodes WHERE id = ?", (episode_id,)
        )
        if not rows:
            return None
        row = dict(rows[0])
        row["params"] = _load_json(row.pop("params_json", None))
        row["metrics"] = _load_json(row.pop("metrics_json", None))
        return row

    async def list(
        self,
        limit: int | None = 50,
        offset: int = 0,
        strategy_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        """Newest-first episode listing, optional strategy_kind filter."""
        clauses: list[str] = []
        params: list[Any] = []
        if strategy_kind is not None:
            clauses.append("strategy_kind = ?")
            params.append(strategy_kind)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        paging = (
            f" LIMIT {int(limit)} OFFSET {int(offset)}"
            if limit is not None and (limit or offset)
            else (f" LIMIT -1 OFFSET {int(offset)}" if offset else "")
        )
        # rowid tiebreak: created_at has second granularity, and id is random
        # hex — rowid is the only monotonic insertion-order key.
        rows = await self._db.execute(
            "SELECT * FROM episodes"
            + where
            + " ORDER BY created_at DESC, rowid DESC"
            + paging,
            tuple(params),
        )
        out = []
        for r in rows:
            row = dict(r)
            row["params"] = _load_json(row.pop("params_json", None))
            row["metrics"] = _load_json(row.pop("metrics_json", None))
            out.append(row)
        return out

    async def get_raw(self, episode_id: str) -> dict[str, Any] | None:
        """Raw row without JSON column decoding (replay reads trades itself)."""
        rows = await self._db.execute(
            "SELECT * FROM episodes WHERE id = ?", (episode_id,)
        )
        return dict(rows[0]) if rows else None

    async def aggregate_by_symbol_interval(self) -> EpisodeList:
        """World Model aggregation: one row per (symbol, interval) group.

        Metrics are extracted from metrics_json inside SQL. json_valid guards
        the extraction: a NaN-bearing json.dumps output is invalid strict JSON
        and yields NULL instead of raising. Group assembly below (kinds split,
        best join) is group-granularity, not per-episode-row.
        """
        rows = await self._db.execute(
            """
            WITH x AS (
                SELECT symbol, interval, strategy_kind, created_at,
                    CASE WHEN metrics_json IS NOT NULL
                          AND json_valid(metrics_json)
                         THEN json_extract(metrics_json, '$.annual_return')
                    END AS annual_return,
                    CASE WHEN metrics_json IS NOT NULL
                          AND json_valid(metrics_json)
                         THEN json_extract(metrics_json, '$.sharpe_ratio')
                    END AS sharpe_ratio
                FROM episodes
            )
            SELECT
                symbol,
                interval,
                COUNT(*) AS episode_count,
                SUM(CASE WHEN annual_return > 0 THEN 1 ELSE 0 END)
                    AS winning_count,
                ROUND(
                    1.0 * SUM(CASE WHEN annual_return > 0 THEN 1 ELSE 0 END)
                    / COUNT(*), 4
                ) AS win_rate,
                GROUP_CONCAT(DISTINCT strategy_kind) AS kinds_raw,
                MAX(annual_return) AS best_annual_return,
                MAX(sharpe_ratio) AS best_sharpe,
                MIN(sharpe_ratio) AS worst_sharpe,
                AVG(sharpe_ratio) AS avg_sharpe,
                MAX(created_at) AS last_episode_at
            FROM x
            GROUP BY symbol, interval
            ORDER BY episode_count DESC, symbol, interval
            """
        )
        best_rows = await self._db.execute(
            """
            WITH x AS (
                SELECT symbol, interval, strategy_id,
                    CASE WHEN metrics_json IS NOT NULL
                          AND json_valid(metrics_json)
                         THEN json_extract(metrics_json, '$.annual_return')
                    END AS annual_return,
                    CASE WHEN metrics_json IS NOT NULL
                          AND json_valid(metrics_json)
                         THEN json_extract(metrics_json, '$.sharpe_ratio')
                    END AS sharpe_ratio
                FROM episodes
            ),
            ranked AS (
                SELECT symbol, interval, strategy_id, annual_return, sharpe_ratio,
                    ROW_NUMBER() OVER (
                        PARTITION BY symbol, interval
                        ORDER BY annual_return DESC
                    ) AS rn
                FROM x
                WHERE annual_return IS NOT NULL
            )
            SELECT symbol, interval, strategy_id, annual_return, sharpe_ratio
            FROM ranked WHERE rn = 1
            """
        )
        best_by_group = {
            (r["symbol"], r["interval"]): {
                "strategy_id": r["strategy_id"],
                "annual_return": r["annual_return"],
                "sharpe_ratio": r["sharpe_ratio"],
            }
            for r in best_rows
        }
        out: list[dict[str, Any]] = []
        for r in rows:
            group = dict(r)
            kinds_raw = group.pop("kinds_raw", None)
            group["kinds"] = sorted(kinds_raw.split(",")) if kinds_raw else []
            group["best"] = best_by_group.get((r["symbol"], r["interval"]))
            out.append(group)
        return out
