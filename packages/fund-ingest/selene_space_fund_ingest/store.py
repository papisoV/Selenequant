"""SQLite 存储层 — 转债/LOF-ETF/REITs 三域表 + 通用 upsert。

模式复刻 news-ingest news_store.py:单文件 SQLite,data/fund_ingest.db,
每次采集整表快照 upsert(场内工具数据是"最新态"语义,不做历史 append)。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from selene_space_fund_ingest.config import FUND_DB_PATH

_CB_TABLE = """
CREATE TABLE IF NOT EXISTS cb_bonds (
    code TEXT PRIMARY KEY,
    name TEXT,
    price REAL,
    convert_price REAL,
    premium_pct REAL,
    redeem_trigger_price REAL,
    redeem_status TEXT DEFAULT '',
    redeem_days REAL,
    last_trade_date TEXT,
    updated_at TEXT
)
"""

_LOF_ETF_TABLE = """
CREATE TABLE IF NOT EXISTS lof_etf_spot (
    code TEXT PRIMARY KEY,
    name TEXT,
    category TEXT,
    price REAL,
    nav REAL,
    nav_date TEXT,
    premium_pct REAL,
    updated_at TEXT
)
"""

_REITS_TABLE = """
CREATE TABLE IF NOT EXISTS reits_spot (
    code TEXT PRIMARY KEY,
    name TEXT,
    price REAL,
    change_pct REAL,
    updated_at TEXT
)
"""


def _migrate_cb_columns(conn: sqlite3.Connection) -> None:
    """cb_bonds 增列迁移(幂等):旧库无强赎三列时补上。"""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(cb_bonds)")}
    for col, ddl in (
        (
            "redeem_status",
            "ALTER TABLE cb_bonds ADD COLUMN redeem_status TEXT DEFAULT ''",
        ),
        ("redeem_days", "ALTER TABLE cb_bonds ADD COLUMN redeem_days REAL"),
        ("last_trade_date", "ALTER TABLE cb_bonds ADD COLUMN last_trade_date TEXT"),
    ):
        if col not in existing:
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass  # 并发迁移竞态:列已存在


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """打开(必要时创建)fund_ingest.db 并确保三表存在。"""
    path = Path(db_path) if db_path else FUND_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    for ddl in (_CB_TABLE, _LOF_ETF_TABLE, _REITS_TABLE):
        conn.execute(ddl)
    _migrate_cb_columns(conn)
    conn.commit()
    return conn


def upsert_rows(
    conn: sqlite3.Connection,
    table: str,
    rows: list[dict[str, Any]],
) -> int:
    """整表快照 upsert:按主键覆盖写,返回写入行数。rows 为空时清空表并返回 0。"""
    if not rows:
        conn.execute(f"DELETE FROM {table}")
        conn.commit()
        return 0
    cols = list(rows[0].keys())
    placeholders = ",".join("?" for _ in cols)
    col_list = ",".join(cols)
    conn.executemany(
        f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})",
        [tuple(r.get(c) for c in cols) for r in rows],
    )
    conn.commit()
    return len(rows)


def fetch_all(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    cur = conn.execute(f"SELECT * FROM {table}")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]
