"""Async access layer over a single SQLite connection.

The manager owns one aiosqlite connection configured for durability
(WAL journal) and referential integrity (foreign keys ON).  On connect it
optionally plays pending migrations — either the caller-supplied runner or
the package-wide :data:`ALL_MIGRATIONS` set.

Rows returned by :meth:`AsyncSQLiteManager.execute` behave like mapping rows:
indexable by column name and by ordinal position alike.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS

DEFAULT_DB_PATH = Path.home() / ".selenespace" / "data" / "selenespace.db"


class ConnectionError(RuntimeError):
    """Raised when the manager is used before connect() or after close()."""


_NO_OWNER = object()


class _ReentrantLock:
    """Async lock a task may take again while it already holds it.

    Needed because ``connection()`` wraps a transaction and repository methods
    inside that block legitimately call back into ``execute``.  A plain
    ``asyncio.Lock`` would deadlock that pattern; letting a different task in
    would restore the interleaving this lock exists to prevent.  Ownership is
    per task, with a depth counter.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: object = _NO_OWNER
        self._depth = 0

    async def __aenter__(self) -> None:
        task = asyncio.current_task()
        if task is not None and task is self._owner:
            self._depth += 1
            return
        await self._lock.acquire()
        self._owner = task if task is not None else _NO_OWNER
        self._depth = 1

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._depth -= 1
        if self._depth == 0:
            self._owner = _NO_OWNER
            self._lock.release()


class AsyncSQLiteManager:
    """Thin async facade over one aiosqlite connection.

    One connection, many coroutines: the API request handlers and the background
    research loops all share this manager.  aiosqlite serialises statements on
    its worker thread, but a single logical operation here is several of them
    (``execute`` -> ``fetchall`` -> ``close`` -> ``commit``), and a step of one
    coroutine may land between two steps of another.  The visible failure is
    ``sqlite3.OperationalError: cannot commit transaction - SQL statements in
    progress`` when a commit overtakes another statement that is still open.
    :attr:`_lock` makes each operation indivisible; it is re-entrant so a
    ``connection()`` block may call back into :meth:`execute`.
    """

    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB_PATH,
        migration_runner: MigrationRunner | None = None,
    ) -> None:
        self._db_path = db_path
        self._runner = migration_runner
        self._conn: aiosqlite.Connection | None = None
        self._lock = _ReentrantLock()

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
        return _ConnectionScope(self._conn, self._lock)

    async def execute(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        if self._conn is None:
            raise ConnectionError(
                f"SQLite manager is not connected (db_path={self._db_path!r}); "
                "call connect() first"
            )
        async with self._lock:
            cursor = await self._conn.execute(sql, params)
            try:
                # aiosqlite 的存根把 fetchall 标成 Iterable,实际返回 list;
                # 显式 list() 让签名与实际返回值一致(mypy return-value)。
                rows: list[aiosqlite.Row] = (
                    list(await cursor.fetchall())
                    if cursor.description is not None
                    else []
                )
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
        async with self._lock:
            await self._conn.executemany(sql, param_list)
            await self._conn.commit()


class _ConnectionScope:
    """Transaction-scoped view of the raw connection.

    Holds the manager's lock for the whole block: the body runs several
    statements that must all land together, and a concurrent ``execute``
    slipping in between them would commit half a transaction.
    """

    def __init__(self, conn: aiosqlite.Connection, lock: "_ReentrantLock") -> None:
        self._conn = conn
        self._lock = lock

    async def __aenter__(self) -> aiosqlite.Connection:
        await self._lock.__aenter__()
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is not None:
                await self._conn.rollback()
            else:
                await self._conn.commit()
        finally:
            await self._lock.__aexit__(exc_type, exc, tb)
