from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint, text as sa_text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SchemaVersion(Base):
    __tablename__ = "schema_version"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)


class Chat(Base):
    __tablename__ = "chats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_title: Mapped[str | None] = mapped_column(String(256))
    chat_username: Mapped[str | None] = mapped_column(String(128))
    chat_type: Mapped[str | None] = mapped_column(String(20))
    members: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), server_default=sa_text("(datetime('now'))"))


class Sender(Base):
    __tablename__ = "senders"

    sender_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sender_name: Mapped[str | None] = mapped_column(String(256))
    sender_username: Mapped[str | None] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), server_default=sa_text("(datetime('now'))"))


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("message_id", "chat_id", name="uq_message_chat"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_title: Mapped[str | None] = mapped_column(String(256))
    sender_id: Mapped[int | None] = mapped_column(BigInteger)
    sender_name: Mapped[str | None] = mapped_column(String(256))
    sender_username: Mapped[str | None] = mapped_column(String(128))
    text: Mapped[str | None] = mapped_column(Text)
    reply_to_msg_id: Mapped[int | None] = mapped_column(BigInteger)
    forward_from: Mapped[str | None] = mapped_column(String(256))
    date: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    has_media: Mapped[bool] = mapped_column(Boolean, default=False)
    is_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    media_type: Mapped[str | None] = mapped_column(String(32))
    media_id: Mapped[str | None] = mapped_column(String(256))
    crawled_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), server_default=sa_text("(datetime('now'))"))


class SignalFactor(Base):
    __tablename__ = "signal_factors"
    __table_args__ = (
        UniqueConstraint("message_id", "chat_id", name="uq_factor_message_chat"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_signal: Mapped[bool] = mapped_column(Boolean, default=True)
    direction: Mapped[float | None] = mapped_column(Float)
    magnitude: Mapped[float | None] = mapped_column(Float)
    urgency: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    halflife_min: Mapped[int | None] = mapped_column(Integer)
    symbols: Mapped[str | None] = mapped_column(Text)
    event_type: Mapped[str | None] = mapped_column(String(32))
    reasoning: Mapped[str | None] = mapped_column(Text)
    llm_status: Mapped[str] = mapped_column(String(20), default="pending")
    llm_error: Mapped[str | None] = mapped_column(String(256))
    llm_model: Mapped[str | None] = mapped_column(String(64))
    llm_raw: Mapped[str | None] = mapped_column(Text)
    factor_version: Mapped[int] = mapped_column(Integer, default=2)
    filter_result: Mapped[str | None] = mapped_column(String(16))
    matched_keywords: Mapped[str | None] = mapped_column(Text)
    keyword_preliminary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), server_default=sa_text("(datetime('now'))"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), server_default=sa_text("(datetime('now'))"))


Index("ix_messages_chat_id_date", Message.chat_id, Message.date)
Index("ix_signal_factors_message_id", SignalFactor.message_id)
Index("ix_signal_factors_chat_id", SignalFactor.chat_id)
Index("ix_signal_factors_llm_status", SignalFactor.llm_status)
Index("ix_signal_factors_event_type", SignalFactor.event_type)


class Digest(Base):
    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_at: Mapped[datetime] = mapped_column(DateTime)
    to_at: Mapped[datetime] = mapped_column(DateTime)
    signal_count: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), server_default=sa_text("(datetime('now'))"))
