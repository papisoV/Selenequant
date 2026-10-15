"""Live-trading persistence: order/fill/position/balance journals plus
execution-mode state.

Five tables track everything a live session produces — orders with their
exchange and client ids, fills tied back to orders, position snapshots,
per-asset balance readings, and a small key/value store remembering the
active execution mode across restarts.  All creation statements are
idempotent.
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration


async def _apply(conn: aiosqlite.Connection) -> None:
    await conn.execute("""CREATE TABLE IF NOT EXISTS live_orders (
        id TEXT PRIMARY KEY,
        exchange_order_id TEXT,
        client_order_id TEXT,
        account_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        order_type TEXT NOT NULL,
        quantity REAL NOT NULL,
        price REAL,
        stop_price REAL,
        leverage INTEGER DEFAULT 1,
        margin_mode TEXT DEFAULT 'cross',
        pos_side TEXT DEFAULT 'net',
        status TEXT NOT NULL DEFAULT 'created',
        filled_quantity REAL DEFAULT 0.0,
        avg_fill_price REAL,
        exchange TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_live_orders_status ON live_orders (status)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_live_orders_symbol ON live_orders (symbol)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_live_orders_exchange_oid ON live_orders (exchange_order_id)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_live_orders_client_oid ON live_orders (client_order_id)"
    )
    await conn.execute("""CREATE TABLE IF NOT EXISTS live_fills (
        id TEXT PRIMARY KEY,
        order_id TEXT NOT NULL,
        price REAL NOT NULL,
        quantity REAL NOT NULL,
        fee REAL DEFAULT 0.0,
        fee_currency TEXT DEFAULT 'USDT',
        realized_pnl REAL DEFAULT 0.0,
        side TEXT NOT NULL,
        pos_side TEXT DEFAULT '',
        symbol TEXT NOT NULL,
        leverage INTEGER DEFAULT 1,
        exchange TEXT NOT NULL,
        timestamp INTEGER NOT NULL,
        FOREIGN KEY (order_id) REFERENCES live_orders (id)
    )""")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_live_fills_order_id ON live_fills (order_id)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_live_fills_timestamp ON live_fills (timestamp)"
    )
    await conn.execute("""CREATE TABLE IF NOT EXISTS live_positions (
        id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        pos_side TEXT DEFAULT 'net',
        quantity REAL NOT NULL,
        quantity_base REAL DEFAULT 0.0,
        avg_entry_price REAL NOT NULL,
        leverage INTEGER DEFAULT 1,
        margin_mode TEXT DEFAULT 'cross',
        unrealized_pnl REAL DEFAULT 0.0,
        realized_pnl REAL DEFAULT 0.0,
        exchange TEXT NOT NULL,
        opened_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_live_positions_symbol ON live_positions (symbol)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_live_positions_account ON live_positions (account_id)"
    )
    await conn.execute("""CREATE TABLE IF NOT EXISTS live_balances (
        id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        asset TEXT NOT NULL,
        free REAL DEFAULT 0.0,
        locked REAL DEFAULT 0.0,
        exchange TEXT NOT NULL,
        timestamp INTEGER NOT NULL
    )""")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_live_balances_account ON live_balances (account_id)"
    )
    await conn.execute("""CREATE TABLE IF NOT EXISTS execution_mode_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at INTEGER NOT NULL
    )""")


migration = Migration(
    id="v007_live_trading",
    version=7,
    description="Add live_orders, live_fills, live_positions, live_balances, execution_mode_state tables",
    apply=_apply,
)
