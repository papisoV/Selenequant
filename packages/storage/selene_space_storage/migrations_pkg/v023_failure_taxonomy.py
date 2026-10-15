"""v023 failure taxonomy — 失败归因五分类的数据地基。

两表(共识 plan internal-plan §2.3):
1. research_failure_categories:五分类字典表(id/code UNIQUE/content 释义),
   复刻 v017 research_error_types 两段式,键从 content 换成 code。
   content 是 LLM prompt 与 UI tooltip 的单一事实源。
2. strategy_episode_attributions:实例表,键域 (strategy_id, episode_n)
   沿 user_decisions 自管表先例(research_missions.py),UNIQUE 单主标签
   (伴生因记 rationale)。episode_graph_id 可空桥列留给未来把 replay
   episode 物化进 research_episodes,本期不施工。

三进入路径 suggested_by IN ('llm','rule','director'):落库即确认,
无状态机;建议态不落库(suggest 即算即显),stats 天然只含确认行。

纯新增:不改既有行。
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration

_CATEGORIES_DDL = """
CREATE TABLE IF NOT EXISTS research_failure_categories (
    id               TEXT PRIMARY KEY,
    code             TEXT NOT NULL UNIQUE,
    content          TEXT NOT NULL,
    first_seen_at    TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1
);
"""

_ATTRIBUTIONS_DDL = """
CREATE TABLE IF NOT EXISTS strategy_episode_attributions (
    strategy_id      TEXT NOT NULL,
    episode_n        INTEGER NOT NULL,
    category_code    TEXT NOT NULL REFERENCES research_failure_categories (code),
    suggested_by     TEXT NOT NULL CHECK (suggested_by IN ('llm','rule','director')),
    rationale        TEXT NOT NULL DEFAULT '',
    episode_graph_id TEXT REFERENCES research_episodes (id),
    created_at       TEXT NOT NULL,
    UNIQUE (strategy_id, episode_n)
);
"""

_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_episode_attributions_strategy "
    "ON strategy_episode_attributions (strategy_id, episode_n)",
)

# 字典 seed 五行(五分类封闭集;扩档走未来迁移,不在运行时插)
_CATEGORY_SEEDS: list[tuple[str, str, str]] = [
    (
        "cat_data",
        "data",
        "数据质量/缺失/时区错位等数据面问题导致误判",
    ),
    (
        "cat_assumption",
        "assumption",
        "入场逻辑或统计前提被证伪",
    ),
    (
        "cat_strategy",
        "strategy",
        "策略逻辑本身在该市场状态下失效(排除污染后)",
    ),
    (
        "cat_regime_shift",
        "regime_shift",
        "策略曾有效,市场状态切换后失效",
    ),
    (
        "cat_execution",
        "execution",
        "下单/滑点/延迟/配置漂移等执行面问题",
    ),
]


async def _apply(conn: aiosqlite.Connection) -> None:
    await conn.execute(_CATEGORIES_DDL)
    await conn.execute(_ATTRIBUTIONS_DDL)
    for index in _INDEX_DDL:
        await conn.execute(index)
    for cat_id, code, content in _CATEGORY_SEEDS:
        await conn.execute(
            "INSERT OR IGNORE INTO research_failure_categories "
            "(id, code, content, first_seen_at, occurrence_count) "
            "VALUES (?, ?, ?, ?, 1)",
            (cat_id, code, content, "2026-09-25T00:00:00+00:00"),
        )


migration = Migration(
    id="v023_failure_taxonomy",
    version=23,
    description=(
        "Add research_failure_categories dict + "
        "strategy_episode_attributions for replay failure attribution"
    ),
    apply=_apply,
)
