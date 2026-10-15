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
        # removed explicit Index — covered by ix_messages_chat_id_date
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
    is_signal: Mapped[bool] = mapped_column(Boolean, default=True)  # True=signal, False=noise
    # Factor dimensions (float for quantitative weighting)
    direction: Mapped[float | None] = mapped_column(Float)          # [-1.0, 1.0] negative=bearish, positive=bullish
    magnitude: Mapped[float | None] = mapped_column(Float)          # [0.0, 1.0] impact strength
    urgency: Mapped[float | None] = mapped_column(Float)            # [0.0, 1.0] time-sensitivity
    confidence: Mapped[float | None] = mapped_column(Float)         # [0.0, 1.0] LLM judgment confidence
    halflife_min: Mapped[int | None] = mapped_column(Integer)       # >= 1, decay half-life in minutes
    symbols: Mapped[str | None] = mapped_column(Text)               # JSON array: '["BTC","ETH"]' or '["*"]'
    event_type: Mapped[str | None] = mapped_column(String(32))      # security|regulatory|macro|whale|market|listing|partnership|other
    reasoning: Mapped[str | None] = mapped_column(Text)             # LLM reasoning, <= 200 chars
    # Processing metadata
    llm_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/processing/completed/failed/skipped
    llm_error: Mapped[str | None] = mapped_column(String(256))
    llm_model: Mapped[str | None] = mapped_column(String(64))
    llm_raw: Mapped[str | None] = mapped_column(Text)                       # raw LLM response for debugging
    factor_version: Mapped[int] = mapped_column(Integer, default=2)         # v2 = new schema
    # Keyword filter metadata
    filter_result: Mapped[str | None] = mapped_column(String(16))           # passed/rejected
    matched_keywords: Mapped[str | None] = mapped_column(Text)              # JSON array
    keyword_preliminary: Mapped[str | None] = mapped_column(Text)           # JSON dict
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), server_default=sa_text("(datetime('now'))"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), server_default=sa_text("(datetime('now'))"))


# Composite index for chat+date queries
Index("ix_messages_chat_id_date", Message.chat_id, Message.date)

# SignalFactor indexes
Index("ix_signal_factors_message_id", SignalFactor.message_id)
Index("ix_signal_factors_chat_id", SignalFactor.chat_id)
Index("ix_signal_factors_llm_status", SignalFactor.llm_status)
Index("ix_signal_factors_event_type", SignalFactor.event_type)


class SignalOutcome(Base):
    """Outcome reported by a downstream consumer after consuming a signal.

    Soft-linked to signal_factors via (message_id, chat_id) — no FK, so
    outcomes survive even if the source signal row is deleted.
    """
    __tablename__ = "signal_outcomes"
    __table_args__ = (
        UniqueConstraint("message_id", "chat_id", "time_horizon_min", name="uq_outcome_msg_chat_horizon"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    reported_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), server_default=sa_text("(datetime('now'))"))
    actual_direction: Mapped[int | None] = mapped_column(Integer)          # -1 / 0 / +1 (downstream verdict)
    magnitude_pct: Mapped[float | None] = mapped_column(Float)             # actual price move %
    time_horizon_min: Mapped[int | None] = mapped_column(Integer)          # feedback window (15/60/240...)
    price_t0: Mapped[float | None] = mapped_column(Float)                  # price at signal time
    price_tn: Mapped[float | None] = mapped_column(Float)                  # price at T+N
    note: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(64))                 # which downstream system reported


class Digest(Base):
    """AI-generated market digest covering a time window [from_at, to_at].

    Each row is one digest generation. `to_at` of the latest row serves as
    the "last_digest_at" marker — next generation starts from there.
    """
    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    to_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    signal_count: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), server_default=sa_text("(datetime('now'))"))


class SubscriptionPlan(Base):
    """Subscription plan for bot signal push.

    Defines pricing tiers with signal quotas and feature flags.
    Seeded with 3 default plans on fresh DB (free/pro/enterprise).
    """
    __tablename__ = "subscription_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    price_cents: Mapped[int] = mapped_column(Integer, default=0)  # Price in cents (0 = free)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    interval_days: Mapped[int] = mapped_column(Integer, default=30)  # Billing cycle length
    max_signals_per_day: Mapped[int] = mapped_column(Integer, default=0)  # 0 = unlimited
    features_json: Mapped[str | None] = mapped_column(Text)  # JSON dict of feature flags
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), server_default=sa_text("(datetime('now'))"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), server_default=sa_text("(datetime('now'))"))


class BotSubscription(Base):
    """Telegram chat subscription for bot signal push.

    Each row represents one chat_id that wants to receive signal push
    notifications, with optional filters for minimum score and event types.
    """
    __tablename__ = "bot_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    min_score: Mapped[float] = mapped_column(Float, default=0.0)
    event_types: Mapped[str | None] = mapped_column(Text)  # JSON array: '["market","whale"]' or NULL=all
    plan_id: Mapped[int | None] = mapped_column(Integer)  # FK→subscription_plans.id (no SQLAlchemy FK constraint)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active/expired/cancelled/trial
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)  # NULL = never expires
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), server_default=sa_text("(datetime('now'))"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), server_default=sa_text("(datetime('now'))"))
