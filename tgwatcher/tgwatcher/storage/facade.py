"""Storage facade for TGWatcher.

Composes bounded repositories (migration, chat, message, signal) and
orchestrates cross-table workflows. Public API:

    from tgwatcher.storage import Storage

The facade owns the SQLAlchemy engine + session factory and delegates
table-specific CRUD to the repository classes. Cross-repo workflows
(e.g. `save_messages` which writes to chats, senders, and messages in
one logical operation) stay here on the facade.

Phase 1A refactor (plan: ticklish-cooking-glade.md).
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tgwatcher.models import Base
from tgwatcher.schemas import ParsedChat, ParsedMessage
from tgwatcher.storage.repositories.migration import SCHEMA_VERSION, MigrationRunner
from tgwatcher.storage.repositories.chat_repo import ChatRepository
from tgwatcher.storage.repositories.message_repo import MessageRepository
from tgwatcher.storage.repositories.signal_repo import SignalRepository

logger = logging.getLogger(__name__)


class Storage:
    """Top-level persistence facade. Composes repositories; owns the engine."""

    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            echo=False,
            connect_args={"timeout": 30},
            pool_pre_ping=True,
        )
        self._session_factory = sessionmaker(bind=self.engine)
        self._migrations = MigrationRunner(self.engine)
        self._chats = ChatRepository(self._session_factory)
        self._messages = MessageRepository(self._session_factory)
        self._signals = SignalRepository(self._session_factory)

    def init_db(self) -> None:
        Base.metadata.create_all(self.engine)
        with self.engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.execute(text("PRAGMA busy_timeout=30000"))
            conn.commit()

        current_version = self._migrations.get_schema_version()
        if current_version < SCHEMA_VERSION:
            self._migrations.run_migrations(current_version)

        logger.info("Database initialized (WAL mode, 30s busy timeout, schema v%d)", SCHEMA_VERSION)

    def get_session(self) -> Session:
        return self._session_factory()

    # --- Chat delegation ---

    def upsert_chat_from_parsed(self, chat: ParsedChat) -> None:
        self._chats.upsert_chat_from_parsed(chat)

    def get_chats(self) -> list[dict]:
        return self._chats.get_chats()

    # --- Message delegation ---

    def upsert_sender_from_parsed(self, sender):
        self._messages.upsert_sender_from_parsed(sender)

    def mark_message_deleted(self, message_id: int, chat_id: int) -> None:
        self._messages.mark_message_deleted(message_id, chat_id)

    def update_edited_message(self, update) -> None:
        self._messages.update_edited_message(update)

    def insert_messages(self, messages: list[ParsedMessage]) -> int:
        return self._messages.insert_messages(messages)

    def save_messages(self, messages: list[ParsedMessage]) -> int:
        """Orchestrate: upsert chat/sender metadata, then insert messages."""
        if not messages:
            return 0

        seen_chats: set[int] = set()
        seen_senders: set[int] = set()
        for msg in messages:
            if msg.chat_id not in seen_chats:
                self._chats.upsert_chat_from_parsed(msg.chat)
                seen_chats.add(msg.chat_id)
            sender = msg.sender
            if sender and sender.sender_id not in seen_senders:
                self._messages.upsert_sender_from_parsed(sender)
                seen_senders.add(sender.sender_id)

        return self._messages.insert_messages(messages)

    def get_last_message_id(self, chat_id: int) -> int | None:
        return self._messages.get_last_message_id(chat_id)

    def get_last_message_date(self, chat_id: int):
        return self._messages.get_last_message_date(chat_id)

    def get_stats(self) -> dict:
        return self._messages.get_stats()

    def query_messages(self, *args, **kwargs) -> dict:
        return self._messages.query_messages(*args, **kwargs)

    def get_message_trend(self, *args, **kwargs) -> dict:
        return self._messages.get_message_trend(*args, **kwargs)

    def get_activity_heatmap(self, *args, **kwargs) -> dict:
        return self._messages.get_activity_heatmap(*args, **kwargs)

    def get_group_comparison(self) -> dict:
        return self._messages.get_group_comparison()

    def get_senders(self, chat_id: int | None = None) -> list[dict]:
        return self._messages.get_senders(chat_id)

    def delete_chat_data(self, chat_id: int) -> int:
        return self._messages.delete_chat_data(chat_id)

    def delete_all_data(self) -> int:
        return self._messages.delete_all_data()

    def get_message_by_id(self, message_id: int) -> dict | None:
        return self._messages.get_message_by_id(message_id)

    # --- Signal delegation ---

    def save_signal_factor(self, factor_dict: dict) -> None:
        self._signals.save_signal_factor(factor_dict)

    def get_signal_factor(self, message_id: int, chat_id: int) -> dict | None:
        return self._signals.get_signal_factor(message_id, chat_id)

    def save_signal_outcome(self, outcome: dict) -> dict:
        return self._signals.save_signal_outcome(outcome)

    def get_signal_outcomes(self, message_id: int, chat_id: int) -> list[dict]:
        return self._signals.get_signal_outcomes(message_id, chat_id)

    def query_signal_factors(self, *args, **kwargs) -> dict:
        return self._signals.query_signal_factors(*args, **kwargs)

    def get_signal_stats(self, *args, **kwargs) -> dict:
        return self._signals.get_signal_stats(*args, **kwargs)

    def get_unprocessed_messages(self, *args, **kwargs) -> list[dict]:
        return self._signals.get_unprocessed_messages(*args, **kwargs)

    def get_signal_trend(self, *args, **kwargs) -> dict:
        return self._signals.get_signal_trend(*args, **kwargs)

    def delete_signal_factors_by_chat(self, chat_id: int) -> int:
        return self._signals.delete_signal_factors_by_chat(chat_id)

    def reset_stuck_processing(self, timeout_minutes: int = 10) -> int:
        return self._signals.reset_stuck_processing(timeout_minutes)

    def query_signals_export(
        self,
        chat_id: int | None = None,
        date_from=None,
        date_to=None,
        event_type: str | None = None,
        direction: str | None = None,
        llm_model: str | None = None,
        is_signal: str | None = None,
        count_only: bool = False,
    ):
        """Phase 1B: migrated raw SQL from api.py:764. See SignalRepository."""
        return self._signals.query_signals_export(
            chat_id=chat_id, date_from=date_from, date_to=date_to,
            event_type=event_type, direction=direction, llm_model=llm_model,
            is_signal=is_signal, count_only=count_only,
        )
