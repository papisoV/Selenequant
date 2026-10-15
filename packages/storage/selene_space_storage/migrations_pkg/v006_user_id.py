"""Multi-user isolation: stamp every business row with its owner.

Adds a ``user_id`` column (defaulting to the Community single-user id
``'local'``, which also backfills existing rows) to the five directly
user-owned tables.  Orders, positions, and equity history inherit their
ownership through the parent account.
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration

_TARGET_TABLES = (
    "strategies",
    "backtests",
    "paper_accounts",
    "chat_sessions",
    "audit_logs",
)

_USER_ID_DDL = "ALTER TABLE {table} ADD COLUMN user_id TEXT NOT NULL DEFAULT 'local'"

# (index name, indexed table, column list) — applied after the column adds
_USER_ID_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("idx_strategies_user_id", "strategies", "user_id"),
    ("idx_strategies_user_name", "strategies", "user_id, name"),
    ("idx_backtests_user_id", "backtests", "user_id, created_at"),
    ("idx_paper_accounts_user_id", "paper_accounts", "user_id, status"),
    ("idx_chat_sessions_user_id", "chat_sessions", "user_id, updated_at"),
    ("idx_audit_logs_user_id", "audit_logs", "user_id"),
)


async def _apply(conn: aiosqlite.Connection) -> None:
    for table in _TARGET_TABLES:
        cursor = await conn.execute(f"PRAGMA table_info({table})")
        known = {row[1] for row in await cursor.fetchall()}
        if "user_id" not in known:
            await conn.execute(_USER_ID_DDL.format(table=table))
    for index_name, table, columns in _USER_ID_INDEXES:
        await conn.execute(
            f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({columns})"
        )


migration = Migration(
    id="v006_user_id",
    version=6,
    description="Add user_id column to strategies, backtests, paper_accounts, chat_sessions, audit_logs",
    apply=_apply,
)
