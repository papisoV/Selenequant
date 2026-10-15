"""Async access layer over a single SQLite connection.

The manager owns one aiosqlite connection configured for durability
(WAL journal) and referential integrity (foreign keys ON).  On connect it
optionally plays pending migrations — either the caller-supplied runner or
the package-wide :data:`ALL_MIGRATIONS` set.

Rows returned by :meth:`AsyncSQLiteManager.execute` behave like mapping rows:
indexable by column name and by ordinal position alike.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS

DEFAULT_DB_PATH = Path.home() / ".selenespace" / "data" / "selenespace.db"


class ConnectionError(RuntimeError):
    """Raised when the manager is used before connect() or after close()."""


class AsyncSQLiteManager:
    """Thin async facade over one aiosqlite connection."""

    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB_PATH,
        migration_runner: MigrationRunner | None = None,
    ) -> None:
        self._db_path = db_path
        self._runner = migration_runner
        self._conn: aiosqlite.Connection | None = None

    @property
    def is_connected(self) -> bool:
        return self._conn is not None

    async def connect(self) -> None:
        if self._conn is not None:
            return
        conn = await aiosqlite.connect(self._db_path)
        try:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA foreign_keys=ON")
            runner = (
                self._runner
                if self._runner is not None
                else MigrationRunner(ALL_MIGRATIONS)
            )
            await runner.run_pending(conn)
            await conn.commit()
        except BaseException:
            await conn.close()
            raise
        self._conn = conn

    async def close(self) -> None:
        if self._conn is None:
            return
        await self._conn.close()
        self._conn = None

    async def __aenter__(self) -> AsyncSQLiteManager:
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    def connection(self) -> _ConnectionScope:
        """Context manager yielding the raw connection; commits on success,
        rolls back when the body raises.

        Raises:
            ConnectionError: The manager has not been connected yet.
        """
        if self._conn is None:
            raise ConnectionError(
                f"SQLite manager is not connected (db_path={self._db_path!r}); "
                "call connect() first"
            )
        return _ConnectionScope(self._conn)

    async def execute(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        if self._conn is None:
            raise ConnectionError(
                f"SQLite manager is not connected (db_path={self._db_path!r}); "
                "call connect() first"
            )
        cursor = await self._conn.execute(sql, params)
        try:
            if cursor.description is not None:
                rows = await cursor.fetchall()
            else:
                rows = []
        finally:
            await cursor.close()
        await self._conn.commit()
        return rows

    async def execute_many(self, sql: str, param_list: list[tuple]) -> None:
        if self._conn is None:
            raise ConnectionError(
                f"SQLite manager is not connected (db_path={self._db_path!r}); "
                "call connect() first"
            )
        await self._conn.executemany(sql, param_list)
        await self._conn.commit()


class _ConnectionScope:
    """Transaction-scoped view of the raw connection."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def __aenter__(self) -> aiosqlite.Connection:
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            await self._conn.rollback()
        else:
            await self._conn.commit()
