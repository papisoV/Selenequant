"""Strategy episode attribution repository — 失败归因五分类实例面(v021)。

共识 plan(internal-plan §2.3/§2.4):
- 落库即确认(suggested_by 是唯一 provenance 字段),无状态机;建议态
  (suggest 端点)不落库,stats 天然只含确认行。
- 键域 (strategy_id, episode_n) UNIQUE 单主标签;改判 = upsert(overwrite=True)
  的 ON CONFLICT DO UPDATE,created_at 不动(快照列语义)。
- 连接源裁定(R2-2):构造接 AsyncSQLiteManager(get_db_manager()),FK 在
  manager 连接上真实强制;禁止用裸连接(FK OFF)写本表。

方法面:upsert / rule_batch / for_episode / stats(+ list_categories 字典读)。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

ATTRIBUTION_SOURCES: tuple[str, ...] = ("llm", "rule", "director")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class StrategyAttributionRepository:
    """Facade over ``research_failure_categories`` + ``strategy_episode_attributions``."""

    def __init__(self, db: Any) -> None:
        self._db = db

    # -- dictionary -------------------------------------------------------------

    async def list_categories(self) -> list[dict[str, Any]]:
        """五分类字典全行(UI 下拉 + LLM prompt 共用单一事实源)。"""
        rows = await self._db.execute(
            "SELECT * FROM research_failure_categories ORDER BY id ASC"
        )
        return [dict(r) for r in rows]

    # -- instance rows ------------------------------------------------------------

    async def upsert(
        self,
        strategy_id: str,
        episode_n: int,
        category_code: str,
        suggested_by: str,
        *,
        rationale: str = "",
        episode_graph_id: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """写一行归因。

        overwrite=False(POST 直插):UNIQUE 冲突原样抛 IntegrityError,
        语义(API 层裁定 409)交调用方;overwrite=True(PUT 改判):
        ON CONFLICT DO UPDATE 覆盖内容列,created_at 保持首插值。

        FK 违反(坏 category_code / 坏 episode_graph_id)在 manager 连接上
        真实抛 IntegrityError(FK per-connection,sqlite.py:51)。
        """
        if suggested_by not in ATTRIBUTION_SOURCES:
            raise ValueError(
                f"unknown suggested_by: {suggested_by!r} (expected {ATTRIBUTION_SOURCES})"
            )
        params = (
            strategy_id,
            episode_n,
            category_code,
            suggested_by,
            rationale,
            episode_graph_id,
            _now(),
        )
        if overwrite:
            await self._db.execute(
                """
                INSERT INTO strategy_episode_attributions (
                    strategy_id, episode_n, category_code, suggested_by,
                    rationale, episode_graph_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (strategy_id, episode_n)
                DO UPDATE SET category_code = excluded.category_code,
                              suggested_by = excluded.suggested_by,
                              rationale = excluded.rationale,
                              episode_graph_id = excluded.episode_graph_id
                """,
                params,
            )
        else:
            await self._db.execute(
                """
                INSERT INTO strategy_episode_attributions (
                    strategy_id, episode_n, category_code, suggested_by,
                    rationale, episode_graph_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                params,
            )
        row = await self.for_episode(strategy_id, episode_n)
        assert row is not None
        return row

    async def rule_batch(
        self,
        strategy_id: str,
        episode_ns: list[int],
        *,
        category_code: str = "execution",
        rationale: str = "",
    ) -> list[int]:
        """drift bundle 硬规则幂等批插:已存在键跳过,返回本次实际新增的 episode_n。

        check-then-insert(单用户本地库,无并发窗口);INSERT 用平插不走
        OR IGNORE,坏 category_code 的 FK 违反不被静默吞掉。
        """
        existing_rows = await self._db.execute(
            "SELECT episode_n FROM strategy_episode_attributions WHERE strategy_id = ?",
            (strategy_id,),
        )
        existing = {r["episode_n"] for r in existing_rows}
        inserted: list[int] = []
        for n in episode_ns:
            if n in existing:
                continue
            await self._db.execute(
                """
                INSERT INTO strategy_episode_attributions (
                    strategy_id, episode_n, category_code, suggested_by,
                    rationale, episode_graph_id, created_at
                ) VALUES (?, ?, ?, 'rule', ?, NULL, ?)
                """,
                (strategy_id, n, category_code, rationale, _now()),
            )
            inserted.append(n)
        return inserted

    async def for_episode(
        self, strategy_id: str, episode_n: int
    ) -> dict[str, Any] | None:
        rows = await self._db.execute(
            "SELECT * FROM strategy_episode_attributions "
            "WHERE strategy_id = ? AND episode_n = ?",
            (strategy_id, episode_n),
        )
        return dict(rows[0]) if rows else None

    async def stats(self) -> dict[str, Any]:
        """分档计数 + 逐条明细(plan §8.2:两格都可复述出哪笔归到哪格)。"""
        count_rows = await self._db.execute(
            "SELECT category_code, COUNT(*) AS n FROM strategy_episode_attributions "
            "GROUP BY category_code"
        )
        counts = {r["category_code"]: r["n"] for r in count_rows}
        detail_rows = await self._db.execute(
            "SELECT strategy_id, episode_n, category_code, suggested_by, created_at "
            "FROM strategy_episode_attributions "
            "ORDER BY created_at ASC, strategy_id ASC, episode_n ASC"
        )
        episodes = [dict(r) for r in detail_rows]
        return {"counts": counts, "total": len(episodes), "episodes": episodes}
