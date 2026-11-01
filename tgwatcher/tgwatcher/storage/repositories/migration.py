"""Schema migration runner for the Storage layer.

Encapsulates all vN->vN+1 migration logic and schema-version bookkeeping.
Extracted from the monolithic `Storage` class during Phase 1A refactor
(plan: ticklish-cooking-glade.md, Phase 1A).

Contract: `MigrationRunner(engine).run_migrations(from_version)` runs every
migration with version > from_version, in order. Each migration is responsible
for calling `set_schema_version(N)` on success. Returns the new version.
"""
from __future__ import annotations

import logging
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.engine import Engine

from tgwatcher.models import Base, SignalFactor, SignalOutcome, Digest

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 11


class MigrationRunner:
    """Runs schema migrations from a given version up to SCHEMA_VERSION."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get_schema_version(self) -> int:
        with self.engine.connect() as conn:
            try:
                result = conn.execute(text("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"))
                row = result.fetchone()
                return row[0] if row else 1
            except Exception:
                return 1

    def set_schema_version(self, version: int) -> None:
        with self.engine.connect() as conn:
            conn.execute(text("DELETE FROM schema_version"))
            conn.execute(text("INSERT INTO schema_version (version) VALUES (:v)"), {"v": version})
            conn.commit()

    def run_migrations(self, from_version: int) -> None:
        logger.info(
            "Schema migration starting",
            extra={"from_version": from_version, "to_version": SCHEMA_VERSION, "action": "migrate_start"},
        )
        if from_version < 2:
            self._migrate_v1_to_v2()
        if from_version < 3:
            self._migrate_v2_to_v3()
        if from_version < 4:
            self._migrate_v3_to_v4()
        if from_version < 5:
            self._migrate_v4_to_v5()
        if from_version < 6:
            self._migrate_v5_to_v6()
        if from_version < 7:
            self._migrate_v6_to_v7()
        if from_version < 8:
            self._migrate_v7_to_v8()
        if from_version < 9:
            self._migrate_v8_to_v9()
        if from_version < 10:
            self._migrate_v9_to_v10()
        if from_version < 11:
            self._migrate_v10_to_v11()
        logger.info(
            "Schema migration complete",
            extra={"from_version": from_version, "to_version": SCHEMA_VERSION, "action": "migrate_complete"},
        )

    def _migrate_v1_to_v2(self) -> None:
        logger.info("Migrating schema v1 -> v2 ...")
        new_columns = [
            ("is_edited", "BOOLEAN DEFAULT 0"),
            ("edited_at", "DATETIME"),
            ("is_deleted", "BOOLEAN DEFAULT 0"),
            ("media_type", "VARCHAR(32)"),
            ("media_id", "VARCHAR(256)"),
        ]
        with self.engine.connect() as conn:
            for col_name, col_type in new_columns:
                try:
                    conn.execute(text(f"ALTER TABLE messages ADD COLUMN {col_name} {col_type}"))
                except OperationalError:
                    pass  # Column already exists

            # Backfill chats
            conn.execute(text(
                "INSERT OR IGNORE INTO chats (chat_id, chat_title, updated_at) "
                "SELECT DISTINCT chat_id, chat_title, datetime('now') "
                "FROM messages WHERE chat_id IS NOT NULL"
            ))

            # Backfill senders (pick most recent name per sender_id)
            conn.execute(text(
                "INSERT OR IGNORE INTO senders (sender_id, sender_name, sender_username, updated_at) "
                "SELECT m.sender_id, "
                "(SELECT m2.sender_name FROM messages m2 WHERE m2.sender_id = m.sender_id "
                " AND m2.sender_name IS NOT NULL ORDER BY m2.id DESC LIMIT 1), "
                "(SELECT m3.sender_username FROM messages m3 WHERE m3.sender_id = m.sender_id "
                " AND m3.sender_username IS NOT NULL ORDER BY m3.id DESC LIMIT 1), "
                "datetime('now') "
                "FROM messages m WHERE m.sender_id IS NOT NULL GROUP BY m.sender_id"
            ))

            # New indexes
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_messages_is_deleted ON messages (is_deleted)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_messages_media_type ON messages (media_type)"))
            conn.commit()

        self.set_schema_version(2)
        logger.info("Migration v1 -> v2 complete")

    def _migrate_v2_to_v3(self) -> None:
        logger.info("Migrating schema v2 -> v3 (server defaults) ...")
        with self.engine.connect() as conn:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_messages_chat_id_date ON messages (chat_id, date)"))
            conn.commit()
        self.set_schema_version(3)
        logger.info("Migration v2 -> v3 complete")

    def _migrate_v3_to_v4(self) -> None:
        logger.info("Migrating schema v3 -> v4 (signal_factors table) ...")
        Base.metadata.create_all(self.engine, tables=[SignalFactor.__table__])
        self.set_schema_version(4)
        logger.info("Migration v3 -> v4 complete")

    def _migrate_v4_to_v5(self) -> None:
        """Drop old signal_factors and claude_factors tables, recreate with new schema."""
        logger.info("Migrating schema v4 -> v5 (new factor schema) ...")
        with self.engine.connect() as conn:
            # Drop old tables — data is being discarded per user request
            conn.execute(text("DROP TABLE IF EXISTS signal_factors"))
            conn.execute(text("DROP TABLE IF EXISTS claude_factors"))
            conn.commit()
        # Recreate with new schema
        Base.metadata.create_all(self.engine, tables=[SignalFactor.__table__])
        self.set_schema_version(5)
        logger.info("Migration v4 -> v5 complete (old factor data discarded)")

    def _migrate_v5_to_v6(self) -> None:
        """Add is_signal column to signal_factors table."""
        logger.info("Migrating schema v5 -> v6 (add is_signal column) ...")
        with self.engine.connect() as conn:
            try:
                conn.execute(text(
                    "ALTER TABLE signal_factors ADD COLUMN is_signal BOOLEAN DEFAULT 1"
                ))
            except OperationalError:
                pass  # Column already exists
            conn.commit()
        self.set_schema_version(6)
        logger.info("Migration v5 -> v6 complete")

    def _migrate_v6_to_v7(self) -> None:
        """Create signal_outcomes table for downstream feedback."""
        logger.info("Migrating schema v6 -> v7 (create signal_outcomes table) ...")
        # Base.metadata.create_all already creates new tables on init_db, but
        # if the DB pre-existed, we still need to ensure the table exists.
        Base.metadata.create_all(self.engine, tables=[SignalOutcome.__table__])
        self.set_schema_version(7)
        logger.info("Migration v6 -> v7 complete")

    def _migrate_v7_to_v8(self) -> None:
        """Create digests table for AI market summaries."""
        logger.info("Migrating schema v7 -> v8 (create digests table) ...")
        Base.metadata.create_all(self.engine, tables=[Digest.__table__])
        self.set_schema_version(8)
        logger.info("Migration v7 -> v8 complete")

    def _migrate_v8_to_v9(self) -> None:
        """Add composite indexes for N+1 query hot paths.

        - `ix_messages_sender_id_is_deleted`: supports get_senders() GROUP BY
          and loadSenders filter (was scanning ix_messages_is_deleted and
          filtering sender_id in Python).
        - `ix_messages_chat_id_is_deleted`: supports get_chats() per-chat
          COUNT/MAX after the GROUP BY rewrite — composite covers
          (chat_id, is_deleted) lookups more efficiently than the existing
          single-column ix_messages_chat_id.
        - `ix_signal_factors_chat_id_created_at`: supports trend queries
          ordered by created_at within a chat.
        - `ix_signal_outcomes_message_id`: supports outcome lookup by
          message_id (currently only chat_id is indexed).
        """
        logger.info("Migrating schema v8 -> v9 (N+1 query indexes) ...")
        with self.engine.connect() as conn:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_messages_sender_id_is_deleted "
                "ON messages (sender_id, is_deleted)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_messages_chat_id_is_deleted "
                "ON messages (chat_id, is_deleted)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_signal_factors_chat_id_created_at "
                "ON signal_factors (chat_id, created_at)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_signal_outcomes_message_id "
                "ON signal_outcomes (message_id)"
            ))
            conn.commit()
        self.set_schema_version(9)
        logger.info("Migration v8 -> v9 complete")

    def _migrate_v9_to_v10(self) -> None:
        """Create bot_subscriptions table for Telegram Bot push subscriptions."""
        logger.info("Migrating schema v9 -> v10 (bot_subscriptions table) ...")
        with self.engine.connect() as conn:
            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS bot_subscriptions ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  chat_id BIGINT NOT NULL UNIQUE,"
                "  enabled BOOLEAN DEFAULT 1,"
                "  min_score REAL DEFAULT 0.0,"
                "  event_types TEXT,"
                "  created_at DATETIME DEFAULT (datetime('now')),"
                "  updated_at DATETIME DEFAULT (datetime('now'))"
                ")"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_bot_subscriptions_chat_id "
                "ON bot_subscriptions (chat_id)"
            ))
            conn.commit()
        self.set_schema_version(10)
        logger.info("Migration v9 -> v10 complete")

    def _migrate_v10_to_v11(self) -> None:
        """Create subscription_plans table + add plan_id/status/expires_at to bot_subscriptions."""
        logger.info("Migrating schema v10 -> v11 (subscription_plans + bot_subscriptions extensions) ...")
        with self.engine.connect() as conn:
            # Create subscription_plans table
            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS subscription_plans ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  name VARCHAR(64) NOT NULL UNIQUE,"
                "  price_cents INTEGER DEFAULT 0,"
                "  currency VARCHAR(8) DEFAULT 'CNY',"
                "  interval_days INTEGER DEFAULT 30,"
                "  max_signals_per_day INTEGER DEFAULT 0,"
                "  features_json TEXT,"
                "  enabled BOOLEAN DEFAULT 1,"
                "  created_at DATETIME DEFAULT (datetime('now')),"
                "  updated_at DATETIME DEFAULT (datetime('now'))"
                ")"
            ))
            # Seed 3 default plans (individual inserts to avoid SQLAlchemy :bindparam parsing issues)
            for name, price, max_day, features in [
                ("free", 0, 10, '{"codename_only":true}'),
                ("pro", 2900, 100, '{"codename_only":true,"digest":true}'),
                ("enterprise", 9900, 0, '{"codename_only":true,"digest":true,"webhook":true,"api_access":true}'),
            ]:
                conn.execute(text(
                    "INSERT OR IGNORE INTO subscription_plans "
                    "(name, price_cents, currency, interval_days, max_signals_per_day, features_json, enabled) "
                    "VALUES (:name, :price, 'CNY', 30, :max_day, :features, 1)"
                ), {"name": name, "price": price, "max_day": max_day, "features": features})
            # Add columns to bot_subscriptions
            for col_name, col_type, col_default in [
                ("plan_id", "INTEGER", None),
                ("status", "VARCHAR(20)", "'active'"),
                ("expires_at", "DATETIME", None),
            ]:
                try:
                    alter = f"ALTER TABLE bot_subscriptions ADD COLUMN {col_name} {col_type}"
                    if col_default:
                        alter += f" DEFAULT {col_default}"
                    conn.execute(text(alter))
                except OperationalError:
                    pass  # Column already exists
            conn.commit()
        self.set_schema_version(11)
        logger.info("Migration v10 -> v11 complete")
