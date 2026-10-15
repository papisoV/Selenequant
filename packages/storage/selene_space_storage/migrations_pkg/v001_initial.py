"""Baseline schema: the six v0.1 tables plus per-account equity history.

Everything here is created with ``IF NOT EXISTS`` so replaying the
migration against an initialized database is a no-op.  Columns are laid
out so later revisions can extend rows with plain
``ALTER TABLE ... ADD COLUMN`` statements instead of table rebuilds.
"""

from __future__ import annotations

from collections.abc import Iterator

import aiosqlite

from selene_space_storage.migrations import Migration

_STRATEGIES_DDL = """
    CREATE TABLE IF NOT EXISTS strategies (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        type        TEXT NOT NULL,
        config_json TEXT NOT NULL DEFAULT '{}',
        created_at  TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
    )
"""

_BACKTESTS_DDL = """
    CREATE TABLE IF NOT EXISTS backtests (
        id                TEXT PRIMARY KEY,
        strategy_id       TEXT NOT NULL,
        start_date        TEXT NOT NULL,
        end_date          TEXT NOT NULL,
        metrics_json      TEXT NOT NULL DEFAULT '{}',
        equity_curve_json TEXT NOT NULL DEFAULT '[]',
        trades_count      INTEGER NOT NULL DEFAULT 0,
        created_at        TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (strategy_id) REFERENCES strategies (id) ON DELETE CASCADE
    )
"""

_PAPER_ACCOUNTS_DDL = """
    CREATE TABLE IF NOT EXISTS paper_accounts (
        id              TEXT PRIMARY KEY,
        name            TEXT NOT NULL,
        initial_balance REAL NOT NULL DEFAULT 10000.0,
        current_balance REAL NOT NULL DEFAULT 10000.0,
        status          TEXT NOT NULL DEFAULT 'active',
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
    )
"""

_PAPER_ORDERS_DDL = """
    CREATE TABLE IF NOT EXISTS paper_orders (
        id              TEXT PRIMARY KEY,
        account_id      TEXT NOT NULL,
        symbol          TEXT NOT NULL,
        side            TEXT NOT NULL,
        type            TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'created',
        quantity        REAL NOT NULL,
        price           REAL,
        filled_quantity REAL NOT NULL DEFAULT 0.0,
        avg_fill_price  REAL,
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (account_id) REFERENCES paper_accounts (id) ON DELETE CASCADE
    )
"""

_PAPER_POSITIONS_DDL = """
    CREATE TABLE IF NOT EXISTS paper_positions (
        id              TEXT PRIMARY KEY,
        account_id      TEXT NOT NULL,
        symbol          TEXT NOT NULL,
        side            TEXT NOT NULL,
        quantity        REAL NOT NULL DEFAULT 0.0,
        avg_entry_price REAL NOT NULL DEFAULT 0.0,
        unrealized_pnl  REAL NOT NULL DEFAULT 0.0,
        realized_pnl    REAL NOT NULL DEFAULT 0.0,
        updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (account_id) REFERENCES paper_accounts (id) ON DELETE CASCADE
    )
"""

_AUDIT_LOGS_DDL = """
    CREATE TABLE IF NOT EXISTS audit_logs (
        id           TEXT PRIMARY KEY,
        timestamp    TEXT NOT NULL DEFAULT (datetime('now')),
        event_type   TEXT NOT NULL,
        severity     TEXT NOT NULL DEFAULT 'info',
        actor        TEXT NOT NULL DEFAULT '',
        action       TEXT NOT NULL DEFAULT '',
        resource     TEXT NOT NULL DEFAULT '',
        outcome      TEXT NOT NULL DEFAULT '',
        details_json TEXT NOT NULL DEFAULT '{}'
    )
"""

_PAPER_EQUITY_HISTORY_DDL = """
    CREATE TABLE IF NOT EXISTS paper_equity_history (
        id          TEXT PRIMARY KEY,
        account_id  TEXT NOT NULL,
        timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
        equity      REAL NOT NULL,
        FOREIGN KEY (account_id) REFERENCES paper_accounts (id) ON DELETE CASCADE
    )
"""

#: (table name, table DDL, [(index name, indexed expression), ...]).
#: 表的顺序即依赖顺序 —— 外键引用的表必须排在引用者之前, 索引紧跟其表。
_SCHEMA: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    (
        "strategies",
        _STRATEGIES_DDL,
        (("idx_strategies_name", "name"), ("idx_strategies_type", "type")),
    ),
    (
        "backtests",
        _BACKTESTS_DDL,
        (
            ("idx_backtests_strategy_id", "strategy_id"),
            ("idx_backtests_created_at", "created_at"),
        ),
    ),
    (
        "paper_accounts",
        _PAPER_ACCOUNTS_DDL,
        (("idx_paper_accounts_status", "status"),),
    ),
    (
        "paper_orders",
        _PAPER_ORDERS_DDL,
        (
            ("idx_paper_orders_account_id", "account_id"),
            ("idx_paper_orders_status", "status"),
            ("idx_paper_orders_symbol", "symbol"),
        ),
    ),
    (
        "paper_positions",
        _PAPER_POSITIONS_DDL,
        (
            ("idx_paper_positions_account_id", "account_id"),
            ("idx_paper_positions_symbol", "symbol"),
        ),
    ),
    (
        "audit_logs",
        _AUDIT_LOGS_DDL,
        (
            ("idx_audit_logs_timestamp", "timestamp"),
            ("idx_audit_logs_event_type", "event_type"),
            ("idx_audit_logs_severity", "severity"),
        ),
    ),
    (
        "paper_equity_history",
        _PAPER_EQUITY_HISTORY_DDL,
        (
            ("idx_paper_equity_history_account_id", "account_id"),
            ("idx_paper_equity_history_timestamp", "timestamp"),
        ),
    ),
)


def _baseline_statements() -> Iterator[str]:
    """按依赖顺序铺开建表语句及其索引。"""
    for table, ddl, indexes in _SCHEMA:
        yield ddl
        for index_name, target in indexes:
            yield f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({target})"


async def _apply(conn: aiosqlite.Connection) -> None:
    """Play every baseline DDL statement in order."""
    for statement in _baseline_statements():
        await conn.execute(statement)


#: 迁移身份。``id`` 一经落库便按它记账, 改了就等同于新增一次迁移。
_MIGRATION_ID = "v001_initial"
_MIGRATION_VERSION = 1
_MIGRATION_DESCRIPTION = (
    "SeleneQuant v0.1 baseline: strategies, backtests, paper accounts/orders/"
    "positions, audit logs, and equity history"
)


migration = Migration(
    id=_MIGRATION_ID,
    version=_MIGRATION_VERSION,
    description=_MIGRATION_DESCRIPTION,
    apply=_apply,
)
