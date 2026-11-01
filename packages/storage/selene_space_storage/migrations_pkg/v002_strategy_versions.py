"""Version snapshots and deployment records for strategies.

Extends ``strategies`` with version/lifecycle columns, binds backtest
rows to a strategy version, and introduces the two tracking tables.
Column adds tolerate a pre-existing column (replay safety).
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration

#: 表级新增列: 点号写法 "表.列" 便于在 PRAGMA 里判重。
_COLUMN_ADDS: tuple[tuple[str, str], ...] = (
    (
        "strategies.version",
        "ALTER TABLE strategies ADD COLUMN version INTEGER NOT NULL DEFAULT 1",
    ),
    (
        "strategies.lifecycle_state",
        "ALTER TABLE strategies ADD COLUMN lifecycle_state TEXT NOT NULL DEFAULT 'draft'",
    ),
    (
        "backtests.strategy_version",
        "ALTER TABLE backtests ADD COLUMN strategy_version INTEGER NOT NULL DEFAULT 1",
    ),
)

_STRATEGY_VERSIONS_DDL = """
    CREATE TABLE IF NOT EXISTS strategy_versions (
        id                TEXT PRIMARY KEY,
        strategy_id       TEXT NOT NULL,
        version           INTEGER NOT NULL,
        config_json       TEXT NOT NULL DEFAULT '{}',
        note              TEXT NOT NULL DEFAULT '',
        created_at        TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (strategy_id) REFERENCES strategies (id) ON DELETE CASCADE
    )
"""

_STRATEGY_DEPLOYMENTS_DDL = """
    CREATE TABLE IF NOT EXISTS strategy_deployments (
        id                TEXT PRIMARY KEY,
        strategy_id       TEXT NOT NULL,
        strategy_version  INTEGER NOT NULL DEFAULT 1,
        account_id        TEXT NOT NULL,
        mode              TEXT NOT NULL DEFAULT 'paper',
        status            TEXT NOT NULL DEFAULT 'running',
        created_at        TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (strategy_id) REFERENCES strategies (id) ON DELETE CASCADE
    )
"""

#: (表名, 建表 DDL, ((索引名, 索引列), ...))。
_NEW_TABLES: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    (
        "strategy_versions",
        _STRATEGY_VERSIONS_DDL,
        (
            ("idx_strategy_versions_strategy_id", "strategy_id"),
            ("idx_strategy_versions_version", "strategy_id, version"),
        ),
    ),
    (
        "strategy_deployments",
        _STRATEGY_DEPLOYMENTS_DDL,
        (("idx_strategy_deployments_strategy_id", "strategy_id"),),
    ),
)

#: 迁移身份。``id`` 一经落库便按它记账, 改了就等同于新增一次迁移。
_MIGRATION_ID = "v002_strategy_versions"
_MIGRATION_VERSION = 2


async def _existing_columns(conn: aiosqlite.Connection, table: str) -> set[str]:
    cursor = await conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in await cursor.fetchall()}


async def _add_missing_columns(conn: aiosqlite.Connection) -> None:
    """逐列补齐: 已存在的列跳过(重放安全)。"""
    for table_dot_column, ddl in _COLUMN_ADDS:
        table, column = table_dot_column.split(".")
        if column in await _existing_columns(conn, table):
            continue
        await conn.execute(ddl)


async def _create_new_tables(conn: aiosqlite.Connection) -> None:
    """建两张跟踪表, 索引跟随各自的表。"""
    for table, ddl, indexes in _NEW_TABLES:
        await conn.execute(ddl)
        for index_name, target in indexes:
            await conn.execute(
                f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({target})"
            )


async def _apply(conn: aiosqlite.Connection) -> None:
    await _add_missing_columns(conn)
    await _create_new_tables(conn)


migration = Migration(
    id=_MIGRATION_ID,
    version=_MIGRATION_VERSION,
    description="Add strategy versions, deployments, and backtest version binding",
    apply=_apply,
)
