"""Billing foundation: user accounts + redemption codes (monetize Phase 1.3).

Two tables:
- ``users``: minimal account (email + PBKDF2 hash). Created on register.
- ``redemption_codes``: prepaid codes issued by the operator (爱发电 fulfillment),
  bound to a user on first redeem. Entitlement = user has an unexpired binding.
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration

_USERS_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_CODES_DDL = """
CREATE TABLE IF NOT EXISTS redemption_codes (
    code TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'unused',
    plan TEXT NOT NULL DEFAULT 'basic',
    bound_user_id TEXT,
    bound_at TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_CODES_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_redemption_codes_bound_user
ON redemption_codes (bound_user_id, expires_at)
"""


async def _apply(conn: aiosqlite.Connection) -> None:
    await conn.execute(_USERS_DDL)
    await conn.execute(_CODES_DDL)
    await conn.execute(_CODES_INDEX_DDL)


migration = Migration(
    id="v021_billing",
    version=21,
    description="Add users and redemption_codes tables (monetize Phase 1.3)",
    apply=_apply,
)
