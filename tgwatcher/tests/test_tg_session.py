"""Regression tests for WALSQLiteSession — Telethon session with WAL + busy_timeout.

Covers:
- PRAGMAs set on first connection open
- PRAGMAs re-set after close() + reconnect (per-connection, not persistent)
- Existing session file (default SQLiteSession) loads correctly under WALSQLiteSession
- Override fires when Telethon's internal code path calls _cursor() (catches
  a future Telethon 2.x refactor that moves connection creation out of _cursor())

All tests use tmp_path for isolation — no writes to the repo session file.
"""
from __future__ import annotations


import pytest
from telethon.sessions.sqlite import SQLiteSession

from tgwatcher.tg_session import WALSQLiteSession


@pytest.mark.unit
def test_wal_sqlite_session_sets_pragmas(tmp_path):
    """PRAGMAs are set immediately after instantiation."""
    session_path = str(tmp_path / "test_session")
    session = WALSQLiteSession(session_path)
    try:
        # Force a cursor to trigger _cursor() (and the PRAGMA override)
        c = session._cursor()
        try:
            mode = c.execute("PRAGMA journal_mode").fetchone()[0]
            timeout = c.execute("PRAGMA busy_timeout").fetchone()[0]
        finally:
            c.close()
        assert mode == "wal"
        assert timeout == 30000
    finally:
        session.close()


@pytest.mark.unit
def test_wal_sqlite_session_releases_connection(tmp_path):
    """PRAGMAs are re-applied after close() + re-instantiate (reconnect case)."""
    session_path = str(tmp_path / "test_session")
    # First instantiation — creates file + sets PRAGMAs
    s1 = WALSQLiteSession(session_path)
    s1.close()
    # Second instantiation — should re-open and re-apply PRAGMAs
    s2 = WALSQLiteSession(session_path)
    try:
        c = s2._cursor()
        try:
            mode = c.execute("PRAGMA journal_mode").fetchone()[0]
            timeout = c.execute("PRAGMA busy_timeout").fetchone()[0]
        finally:
            c.close()
        assert mode == "wal"
        assert timeout == 30000
    finally:
        s2.close()


@pytest.mark.unit
def test_wal_sqlite_session_loads_existing_file(tmp_path):
    """A session file created by default SQLiteSession loads correctly
    under WALSQLiteSession — no schema change, no data loss."""
    session_path = str(tmp_path / "test_session")
    # Create with the default SQLiteSession (writes schema + version row)
    original = SQLiteSession(session_path)
    original.close()
    # Re-open with WALSQLiteSession — should not error
    wal_session = WALSQLiteSession(session_path)
    try:
        # Schema is preserved — version table exists and has CURRENT_VERSION
        c = wal_session._cursor()
        try:
            row = c.execute("select version from version").fetchone()
        finally:
            c.close()
        assert row is not None
        assert row[0] == 8  # CURRENT_VERSION in telethon 1.x
    finally:
        wal_session.close()


@pytest.mark.unit
def test_wal_sqlite_session_overrides_telethon_connection(tmp_path):
    """The _cursor() override actually intercepts Telethon's internal
    connection-creation path. Without this test, a future Telethon 2.x
    refactor that moves connection creation out of _cursor() would silently
    make our subclass a no-op.

    Methodology: Telethon's __init__ calls self._cursor() during construction
    (telethon/sessions/sqlite.py:48). If our override fires, the connection
    opened during __init__ will already have WAL + busy_timeout set — we
    can read them back via the same _conn.
    """
    session_path = str(tmp_path / "test_session")
    session = WALSQLiteSession(session_path)
    try:
        # __init__ already called _cursor() internally — verify the connection
        # it opened has our PRAGMAs, not Telethon's defaults.
        assert session._conn is not None, "Telethon __init__ did not open a connection via _cursor()"
        c = session._conn.cursor()
        try:
            mode = c.execute("PRAGMA journal_mode").fetchone()[0]
            timeout = c.execute("PRAGMA busy_timeout").fetchone()[0]
        finally:
            c.close()
        assert mode == "wal", (
            f"Override did not fire during __init__ — journal_mode={mode!r} "
            f"(expected 'wal'). Telethon may have moved connection creation "
            f"out of _cursor()."
        )
        assert timeout == 30000, (
            f"Override did not fire during __init__ — busy_timeout={timeout} "
            f"(expected 30000)."
        )
    finally:
        session.close()
