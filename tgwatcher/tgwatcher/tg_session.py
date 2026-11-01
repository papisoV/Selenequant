"""Telethon SQLiteSession subclass with WAL + busy_timeout PRAGMAs.

Root cause of recurring "database is locked" errors: Telethon's
`SQLiteSession._cursor()` opens `sqlite3.connect(self.filename, check_same_thread=False)`
with the default 5s timeout and no PRAGMAs. When the listener's NewMessage
loop writes to the session file concurrently with crawl_service or auth
routes, the 5s timeout is exceeded and SQLite returns "database is locked".

This subclass overrides `_cursor()` to set:
  - `timeout=30` on `sqlite3.connect` (raises OperationalError after 30s)
  - `PRAGMA journal_mode=WAL` (writer doesn't block readers)
  - `PRAGMA busy_timeout=30000` (per-connection, in addition to timeout=)

Matches the main DB pattern at `tgwatcher/storage/facade.py:53-54` which
sets WAL + 30s busy_timeout. We intentionally do NOT set `synchronous=NORMAL`
to match the main DB (which inherits SQLite's default FULL).

Note: Telethon's `_cursor()` is a non-name-mangled internal. The
`telethon>=1.37,<2.0` pin in `pyproject.toml` guards against a 2.x
breaking change that would silently make this override a no-op.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from telethon.sessions.sqlite import SQLiteSession


class WALSQLiteSession(SQLiteSession):
    """SQLiteSession with WAL + 30s busy_timeout on every connection open.

    Override fires on every `_cursor()` call that finds `self._conn is None`,
    so reconnects after `close()` are covered. Idempotent — safe to call
    repeatedly.
    """

    def _cursor(self) -> Any:  # type: ignore[override]
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.filename,
                timeout=30,
                check_same_thread=False,
            )
            c = self._conn.cursor()
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=30000")
            c.close()
        return self._conn.cursor()
