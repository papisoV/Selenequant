"""Declarative schema-evolution primitives.

A :class:`Migration` pairs a monotonically increasing ``version`` with an
async ``apply`` callable that mutates the database.  A :class:`MigrationRunner`
plays those migrations against a connection, journalling each success in the
``_migrations`` ledger table so repeated runs are no-ops and interrupted runs
resume cleanly.

Atomicity: every migration is wrapped in an explicit transaction.  If the
apply callable raises, its statements (including DDL) are rolled back and no
ledger entry survives, leaving the database at the previous version.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

import aiosqlite

ApplyHook = Callable[[aiosqlite.Connection], Awaitable[None]]

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS _migrations (
    id          TEXT PRIMARY KEY,
    version     INTEGER NOT NULL,
    description TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class Migration:
    """One versioned, forward-only schema change."""

    id: str
    version: int
    description: str
    apply: ApplyHook


class MigrationRunner:
    """Applies pending migrations in ascending version order."""

    def __init__(self, migrations: Iterable[Migration] | None = None) -> None:
        self._known: list[Migration] = (
            list(migrations) if migrations is not None else []
        )

    def register(self, migration: Migration) -> None:
        """Add one more migration to the queue (before run_pending)."""
        self._known.append(migration)

    async def run_pending(self, conn: aiosqlite.Connection) -> list[str]:
        """Execute every not-yet-applied migration; return their descriptions."""
        await conn.execute(_LEDGER_DDL)
        finished = await self._finished_ids(conn)

        executed: list[str] = []
        for candidate in sorted(self._known, key=lambda m: m.version):
            if candidate.id in finished:
                continue
            await conn.execute("BEGIN")
            try:
                await candidate.apply(conn)
                await conn.execute(
                    "INSERT INTO _migrations (id, version, description) VALUES (?, ?, ?)",
                    (candidate.id, candidate.version, candidate.description),
                )
            except BaseException:
                await conn.rollback()
                raise
            await conn.commit()
            executed.append(candidate.description)
        return executed

    async def _finished_ids(self, conn: aiosqlite.Connection) -> set[str]:
        cursor = await conn.execute("SELECT id FROM _migrations")
        return {row[0] for row in await cursor.fetchall()}
