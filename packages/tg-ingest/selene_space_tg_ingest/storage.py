import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, func, text
from sqlalchemy import tuple_
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from selene_space_tg_ingest.models import Base, Chat, Digest, Message, Sender, SignalFactor
from selene_space_tg_ingest.schemas import EditUpdate, ParsedChat, ParsedMessage, ParsedSender
from selene_space_tg_ingest.tz_utils import utc_now, sql_tz_shift, tz_offset_hours

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 7

# Default DB path under selene data/ (matches selene convention).
# Caller can override via Storage(db_path=...) — CLI scripts read from config.
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "tgwatcher.db"


class Storage:
    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = DEFAULT_DB_PATH
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            echo=False,
            connect_args={"timeout": 30},
            pool_pre_ping=True,
        )
        self._session_factory = sessionmaker(bind=self.engine)

    def init_db(self) -> None:
        Base.metadata.create_all(self.engine)
        with self.engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.execute(text("PRAGMA busy_timeout=30000"))
            conn.commit()

        current_version = self._get_schema_version()
        if current_version < SCHEMA_VERSION:
            self._run_migrations(current_version)

        logger.info("Database initialized (WAL mode, 30s busy timeout, schema v%d)", SCHEMA_VERSION)

    def _get_schema_version(self) -> int:
        with self.engine.connect() as conn:
            try:
                result = conn.execute(text("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"))
                row = result.fetchone()
                return row[0] if row else 1
            except Exception:
                return 1

    def _set_schema_version(self, version: int) -> None:
        with self.engine.connect() as conn:
            conn.execute(text("DELETE FROM schema_version"))
            conn.execute(text("INSERT INTO schema_version (version) VALUES (:v)"), {"v": version})
            conn.commit()

    def _run_migrations(self, from_version: int) -> None:
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
                    pass

            conn.execute(text(
                "INSERT OR IGNORE INTO chats (chat_id, chat_title, updated_at) "
                "SELECT DISTINCT chat_id, chat_title, datetime('now') "
                "FROM messages WHERE chat_id IS NOT NULL"
            ))

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

            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_messages_is_deleted ON messages (is_deleted)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_messages_media_type ON messages (media_type)"))
            conn.commit()

        self._set_schema_version(2)
        logger.info("Migration v1 -> v2 complete")

    def _migrate_v2_to_v3(self) -> None:
        logger.info("Migrating schema v2 -> v3 (server defaults) ...")
        with self.engine.connect() as conn:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_messages_chat_id_date ON messages (chat_id, date)"))
            conn.commit()
        self._set_schema_version(3)
        logger.info("Migration v2 -> v3 complete")

    def _migrate_v3_to_v4(self) -> None:
        logger.info("Migrating schema v3 -> v4 (signal_factors table) ...")
        Base.metadata.create_all(self.engine, tables=[SignalFactor.__table__])
        self._set_schema_version(4)
        logger.info("Migration v3 -> v4 complete")

    def _migrate_v4_to_v5(self) -> None:
        """Drop old signal_factors and claude_factors tables, recreate with new schema."""
        logger.info("Migrating schema v4 -> v5 (new factor schema) ...")
        with self.engine.connect() as conn:
            conn.execute(text("DROP TABLE IF EXISTS signal_factors"))
            conn.execute(text("DROP TABLE IF EXISTS claude_factors"))
            conn.commit()
        Base.metadata.create_all(self.engine, tables=[SignalFactor.__table__])
        self._set_schema_version(5)
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
                pass
            conn.commit()
        self._set_schema_version(6)
        logger.info("Migration v5 -> v6 complete")

    def _migrate_v6_to_v7(self) -> None:
        """Add digests table (LLM market summary, tgwatcher absorption)."""
        logger.info("Migrating schema v6 -> v7 (digests table) ...")
        Base.metadata.create_all(self.engine, tables=[Digest.__table__])
        self._set_schema_version(7)
        logger.info("Migration v6 -> v7 complete")

    def get_session(self) -> Session:
        return self._session_factory()

    @staticmethod
    def _ensure_datetime(value):
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                return value.astimezone(timezone.utc).replace(tzinfo=None)
            return value
        if isinstance(value, str):
            try:
                dt = datetime.fromisoformat(value)
                if dt.tzinfo is not None:
                    return dt.astimezone(timezone.utc).replace(tzinfo=None)
                return dt
            except ValueError:
                return None
        return None

    def upsert_chat_from_parsed(self, chat: ParsedChat) -> None:
        with self.get_session() as session:
            row = session.query(Chat).filter(Chat.chat_id == chat.chat_id).first()
            if row:
                if chat.chat_title is not None:
                    row.chat_title = chat.chat_title
                if chat.chat_username is not None:
                    row.chat_username = chat.chat_username
                if chat.chat_type is not None:
                    row.chat_type = chat.chat_type
                if chat.members is not None:
                    row.members = chat.members
                row.updated_at = utc_now()
            else:
                row = Chat(
                    chat_id=chat.chat_id,
                    chat_title=chat.chat_title,
                    chat_username=chat.chat_username,
                    chat_type=chat.chat_type,
                    members=chat.members,
                )
                session.add(row)
            session.commit()

    def upsert_sender_from_parsed(self, sender: ParsedSender) -> None:
        with self.get_session() as session:
            row = session.query(Sender).filter(Sender.sender_id == sender.sender_id).first()
            if row:
                if sender.sender_name is not None:
                    row.sender_name = sender.sender_name
                if sender.sender_username is not None:
                    row.sender_username = sender.sender_username
                row.updated_at = utc_now()
            else:
                row = Sender(
                    sender_id=sender.sender_id,
                    sender_name=sender.sender_name,
                    sender_username=sender.sender_username,
                )
                session.add(row)
            session.commit()

    def mark_message_deleted(self, message_id: int, chat_id: int) -> None:
        with self.get_session() as session:
            record = session.query(Message).filter(
                Message.message_id == message_id,
                Message.chat_id == chat_id,
            ).first()
            if record:
                record.is_deleted = True
                session.commit()

    def update_edited_message(self, update: EditUpdate) -> None:
        with self.get_session() as session:
            record = session.query(Message).filter(
                Message.message_id == update.message_id,
                Message.chat_id == update.chat_id,
            ).first()
            if record:
                record.text = update.text
                record.is_edited = True
                record.edited_at = self._ensure_datetime(update.edit_date)
                session.commit()

    def insert_messages(self, messages: list[ParsedMessage]) -> int:
        """Batch insert new messages, skip existing. Returns count of new rows."""
        if not messages:
            return 0

        for attempt in range(3):
            try:
                with self.get_session() as session:
                    keys = [(m.message_id, m.chat_id) for m in messages]
                    existing = set(
                        session.query(Message.message_id, Message.chat_id)
                        .filter(
                            tuple_(Message.message_id, Message.chat_id).in_(keys)
                        )
                        .all()
                    )
                    saved = 0
                    for msg in messages:
                        key = (msg.message_id, msg.chat_id)
                        if key in existing:
                            if msg.text or msg.media_type:
                                record = session.query(Message).filter(
                                    Message.message_id == msg.message_id,
                                    Message.chat_id == msg.chat_id,
                                ).first()
                                if record and msg.text and record.text != msg.text:
                                    record.text = msg.text
                                    record.is_edited = True
                                    record.edited_at = self._ensure_datetime(msg.edit_date) or utc_now()
                                if record and msg.media_type and not record.media_type:
                                    record.media_type = msg.media_type
                                    record.media_id = msg.media_id
                            continue

                        record = Message(
                            message_id=msg.message_id,
                            chat_id=msg.chat_id,
                            chat_title=msg.chat_title,
                            sender_id=msg.sender_id,
                            sender_name=msg.sender_name,
                            sender_username=msg.sender_username,
                            text=msg.text,
                            reply_to_msg_id=msg.reply_to_msg_id,
                            forward_from=msg.forward_from,
                            date=self._ensure_datetime(msg.date),
                            has_media=msg.has_media,
                            is_edited=msg.is_edited,
                            edited_at=self._ensure_datetime(msg.edit_date),
                            is_deleted=False,
                            media_type=msg.media_type,
                            media_id=msg.media_id,
                            crawled_at=utc_now(),
                        )
                        session.add(record)
                        saved += 1
                    if saved:
                        session.commit()
                return saved
            except (IntegrityError, OperationalError) as e:
                logger.warning("insert_messages attempt %d failed: %s", attempt + 1, e)
                time.sleep(0.5 * (attempt + 1))
        logger.error("insert_messages failed after 3 attempts")
        return 0

    def save_messages(self, messages: list[ParsedMessage]) -> int:
        """Orchestrate: upsert chat/sender metadata, then insert messages."""
        if not messages:
            return 0

        seen_chats: set[int] = set()
        seen_senders: set[int] = set()
        for msg in messages:
            if msg.chat_id not in seen_chats:
                self.upsert_chat_from_parsed(msg.chat)
                seen_chats.add(msg.chat_id)
            sender = msg.sender
            if sender and sender.sender_id not in seen_senders:
                self.upsert_sender_from_parsed(sender)
                seen_senders.add(sender.sender_id)

        return self.insert_messages(messages)

    def get_last_message_id(self, chat_id: int) -> int | None:
        with self.get_session() as session:
            result = (
                session.query(func.max(Message.message_id))
                .filter(Message.chat_id == chat_id, Message.is_deleted == False)
                .scalar()
            )
        return result

    def get_last_message_date(self, chat_id: int) -> datetime | None:
        with self.get_session() as session:
            result = (
                session.query(func.max(Message.date))
                .filter(Message.chat_id == chat_id, Message.is_deleted == False)
                .scalar()
            )
        return result

    def get_stats(self) -> dict:
        with self.get_session() as session:
            total = session.query(func.count(Message.id)).filter(Message.is_deleted == False).scalar()
            chat_count = session.query(func.count(func.distinct(Message.chat_id))).filter(Message.is_deleted == False).scalar()
            earliest = session.query(func.min(Message.date)).filter(Message.is_deleted == False).scalar()
            latest = session.query(func.max(Message.date)).filter(Message.is_deleted == False).scalar()
        return {
            "total_messages": total,
            "monitored_chats": chat_count,
            "earliest_message": earliest,
            "latest_message": latest,
        }

    def query_messages(
        self,
        chat_id: int | None = None,
        keyword: str | None = None,
        sender_id: int | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        page: int = 1,
        page_size: int = 50,
        media_type: str | None = None,
        include_deleted: bool = False,
    ) -> dict:
        with self.get_session() as session:
            q = session.query(Message)
            if not include_deleted:
                q = q.filter(Message.is_deleted == False)
            if chat_id is not None:
                q = q.filter(Message.chat_id == chat_id)
            if keyword:
                escaped = keyword.replace("%", "\\%").replace("_", "\\_")
                q = q.filter(Message.text.ilike(f"%{escaped}%", escape="\\"))
            if sender_id is not None:
                q = q.filter(Message.sender_id == sender_id)
            if date_from:
                q = q.filter(Message.date >= date_from)
            if date_to:
                q = q.filter(Message.date <= date_to)
            if media_type:
                q = q.filter(Message.media_type == media_type)

            total = q.count()
            rows = (
                q.order_by(Message.date.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            messages = []
            for r in rows:
                messages.append({
                    "id": r.id,
                    "message_id": r.message_id,
                    "chat_id": r.chat_id,
                    "chat_title": r.chat_title,
                    "sender_id": r.sender_id,
                    "sender_name": r.sender_name,
                    "sender_username": r.sender_username,
                    "text": r.text,
                    "reply_to_msg_id": r.reply_to_msg_id,
                    "forward_from": r.forward_from,
                    "date": r.date.isoformat() if r.date else None,
                    "has_media": r.has_media,
                    "is_edited": r.is_edited,
                    "edited_at": r.edited_at.isoformat() if r.edited_at else None,
                    "is_deleted": r.is_deleted,
                    "media_type": r.media_type,
                    "media_id": r.media_id,
                })
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "messages": messages,
        }

    def get_chats(self) -> list[dict]:
        with self.get_session() as session:
            chat_rows = session.query(Chat).all()
            if chat_rows:
                result = []
                for chat in chat_rows:
                    msg_count = session.query(func.count(Message.id)).filter(
                        Message.chat_id == chat.chat_id, Message.is_deleted == False
                    ).scalar()
                    last_date = session.query(func.max(Message.date)).filter(
                        Message.chat_id == chat.chat_id
                    ).scalar()
                    result.append({
                        "chat_id": chat.chat_id,
                        "chat_title": chat.chat_title,
                        "chat_username": chat.chat_username,
                        "chat_type": chat.chat_type,
                        "members": chat.members,
                        "msg_count": msg_count,
                        "last_msg_date": last_date.isoformat() if last_date else None,
                    })
                return result

            rows = (
                session.query(
                    Message.chat_id,
                    Message.chat_title,
                    func.count(Message.id).label("msg_count"),
                    func.max(Message.date).label("last_msg_date"),
                )
                .filter(Message.is_deleted == False)
                .group_by(Message.chat_id, Message.chat_title)
                .all()
            )
        return [
            {
                "chat_id": r.chat_id,
                "chat_title": r.chat_title,
                "msg_count": r.msg_count,
                "last_msg_date": r.last_msg_date.isoformat() if r.last_msg_date else None,
            }
            for r in rows
        ]

    def get_message_trend(self, period: str = "day", days: int = 30, chat_id: int | None = None) -> dict:
        with self.get_session() as session:
            tz_shift = sql_tz_shift()
            q = session.query(
                Message.chat_id,
                Message.chat_title,
                func.date(Message.date, tz_shift).label("msg_date"),
                func.count(Message.id).label("count"),
            ).filter(Message.is_deleted == False)
            if chat_id is not None:
                q = q.filter(Message.chat_id == chat_id)
            extra_days = 1 if tz_offset_hours() > 0 else 0
            q = q.filter(Message.date >= func.datetime("now", f"-{days + extra_days} days"))
            q = q.group_by(Message.chat_id, Message.chat_title, func.date(Message.date, tz_shift))
            q = q.order_by(func.date(Message.date, tz_shift))
            rows = q.all()

        datasets: dict[tuple, dict] = {}
        all_dates: set = set()
        for r in rows:
            key = (r.chat_id, r.chat_title or "Unknown")
            if key not in datasets:
                datasets[key] = {"chat_id": r.chat_id, "chat_title": r.chat_title or "Unknown", "data": {}}
            date_str = str(r.msg_date) if r.msg_date else ""
            datasets[key]["data"][date_str] = r.count
            all_dates.add(date_str)

        sorted_dates = sorted(all_dates)
        result = {
            "labels": sorted_dates,
            "datasets": [
                {
                    "chat_id": ds["chat_id"],
                    "chat_title": ds["chat_title"],
                    "data": [ds["data"].get(d, 0) for d in sorted_dates],
                }
                for ds in datasets.values()
            ],
        }
        return result

    def get_activity_heatmap(self, chat_id: int | None = None) -> dict:
        with self.get_session() as session:
            tz_shift = sql_tz_shift()
            q = session.query(
                func.strftime("%H", Message.date, tz_shift).label("hour"),
                func.strftime("%w", Message.date, tz_shift).label("dow"),
                func.count(Message.id).label("count"),
            ).filter(Message.is_deleted == False)
            if chat_id is not None:
                q = q.filter(Message.chat_id == chat_id)
            q = q.group_by(func.strftime("%H", Message.date, tz_shift), func.strftime("%w", Message.date, tz_shift))
            rows = q.all()

        data = []
        for r in rows:
            hour = int(r.hour) if r.hour is not None else 0
            dow = int(r.dow) if r.dow is not None else 0
            data.append({"hour": hour, "dow": dow, "count": r.count})
        return {"data": data}

    def get_group_comparison(self) -> dict:
        from sqlalchemy import distinct
        with self.get_session() as session:
            rows = (
                session.query(
                    Message.chat_id,
                    Message.chat_title,
                    func.count(Message.id).label("msg_count"),
                    func.count(distinct(Message.sender_id)).label("active_senders"),
                    func.min(Message.date).label("earliest"),
                    func.max(Message.date).label("latest"),
                )
                .filter(Message.is_deleted == False)
                .group_by(Message.chat_id, Message.chat_title)
                .order_by(func.count(Message.id).desc())
                .limit(20)
                .all()
            )
        groups = []
        for r in rows:
            days_span = 1
            if r.earliest and r.latest:
                days_span = max(1, (r.latest - r.earliest).days)
            groups.append({
                "chat_id": r.chat_id,
                "chat_title": r.chat_title,
                "msg_count": r.msg_count,
                "active_senders": r.active_senders,
                "avg_per_day": round(r.msg_count / days_span, 1),
            })
        return {"groups": groups}

    def get_senders(self, chat_id: int | None = None) -> list[dict]:
        with self.get_session() as session:
            sender_rows = session.query(Sender).all() if not chat_id else []
            if sender_rows:
                result = []
                for sender in sender_rows:
                    q = session.query(func.count(Message.id)).filter(
                        Message.sender_id == sender.sender_id, Message.is_deleted == False
                    )
                    if chat_id is not None:
                        q = q.filter(Message.chat_id == chat_id)
                    msg_count = q.scalar()
                    if msg_count > 0:
                        result.append({
                            "sender_id": sender.sender_id,
                            "sender_name": sender.sender_name,
                            "sender_username": sender.sender_username,
                            "msg_count": msg_count,
                        })
                return result

            q = session.query(
                Message.sender_id,
                Message.sender_name,
                func.count(Message.id).label("msg_count"),
            ).filter(Message.sender_id.isnot(None), Message.is_deleted == False)
            if chat_id is not None:
                q = q.filter(Message.chat_id == chat_id)
            q = q.group_by(Message.sender_id, Message.sender_name)
            q = q.order_by(func.count(Message.id).desc())
            rows = q.all()
        return [{"sender_id": r.sender_id, "sender_name": r.sender_name, "msg_count": r.msg_count} for r in rows]

    def save_signal_factor(self, factor_dict: dict) -> None:
        with self.get_session() as session:
            existing = session.query(SignalFactor).filter(
                SignalFactor.message_id == factor_dict["message_id"],
                SignalFactor.chat_id == factor_dict["chat_id"],
            ).first()
            if existing:
                for key, value in factor_dict.items():
                    if hasattr(existing, key):
                        setattr(existing, key, value)
            else:
                session.add(SignalFactor(**factor_dict))
            session.commit()

    def get_signal_factor(self, message_id: int, chat_id: int) -> dict | None:
        with self.get_session() as session:
            sf = session.query(SignalFactor).filter(
                SignalFactor.message_id == message_id,
                SignalFactor.chat_id == chat_id,
            ).first()
            if not sf:
                return None
        return {c.name: getattr(sf, c.name) for c in sf.__table__.columns}

    def query_signal_factors(self, chat_id: int | None = None,
                             event_type: str | None = None,
                             direction: str | None = None,
                             date_from: datetime | None = None, date_to: datetime | None = None,
                             page: int = 1, page_size: int = 50) -> dict:
        with self.get_session() as session:
            q = session.query(SignalFactor, Message).join(
                Message, (SignalFactor.message_id == Message.message_id) & (SignalFactor.chat_id == Message.chat_id)
            ).filter(SignalFactor.llm_status == "completed")
            if chat_id:
                q = q.filter(SignalFactor.chat_id == chat_id)
            if event_type:
                q = q.filter(SignalFactor.event_type == event_type)
            if direction == "bullish":
                q = q.filter(SignalFactor.direction > 0)
            elif direction == "bearish":
                q = q.filter(SignalFactor.direction < 0)
            elif direction == "neutral":
                q = q.filter(SignalFactor.direction == 0)
            if date_from:
                q = q.filter(Message.date >= date_from)
            if date_to:
                q = q.filter(Message.date <= date_to)
            total = q.count()
            rows = q.order_by(Message.date.desc()).offset((page - 1) * page_size).limit(page_size).all()
        items = []
        for sf, msg in rows:
            item = {c.name: getattr(sf, c.name) for c in sf.__table__.columns}
            item["text"] = msg.text
            item["date"] = str(msg.date) if msg.date else None
            items.append(item)
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def get_signal_stats(self, chat_id: int | None = None,
                         date_from: datetime | None = None, date_to: datetime | None = None) -> dict:
        with self.get_session() as session:
            q = session.query(SignalFactor).filter(SignalFactor.llm_status == "completed")
            if chat_id:
                q = q.filter(SignalFactor.chat_id == chat_id)
            if date_from or date_to:
                q = q.join(Message, (SignalFactor.message_id == Message.message_id) & (SignalFactor.chat_id == Message.chat_id))
                if date_from:
                    q = q.filter(Message.date >= date_from)
                if date_to:
                    q = q.filter(Message.date <= date_to)
            total = q.count()
            bullish = q.filter(SignalFactor.direction > 0).count()
            bearish = q.filter(SignalFactor.direction < 0).count()
            neutral = q.filter(SignalFactor.direction == 0).count()
            event_types = [r[0] for r in session.query(SignalFactor.event_type).filter(
                SignalFactor.event_type.isnot(None)).distinct().all()]
            event_counts = {et: q.filter(SignalFactor.event_type == et).count() for et in event_types}
            avg_direction = session.query(func.avg(SignalFactor.direction)).filter(
                SignalFactor.direction.isnot(None)).scalar()
            avg_magnitude = session.query(func.avg(SignalFactor.magnitude)).filter(
                SignalFactor.magnitude.isnot(None)).scalar()
            avg_urgency = session.query(func.avg(SignalFactor.urgency)).filter(
                SignalFactor.urgency.isnot(None)).scalar()
            avg_confidence = session.query(func.avg(SignalFactor.confidence)).filter(
                SignalFactor.confidence.isnot(None)).scalar()
            avg_halflife = session.query(func.avg(SignalFactor.halflife_min)).filter(
                SignalFactor.halflife_min.isnot(None)).scalar()
        return {
            "total": total,
            "direction": {"bullish": bullish, "neutral": neutral, "bearish": bearish},
            "event_types": event_counts,
            "avg_direction": round(avg_direction, 3) if avg_direction else None,
            "avg_magnitude": round(avg_magnitude, 3) if avg_magnitude else None,
            "avg_urgency": round(avg_urgency, 3) if avg_urgency else None,
            "avg_confidence": round(avg_confidence, 3) if avg_confidence else None,
            "avg_halflife_min": round(avg_halflife, 1) if avg_halflife else None,
        }

    def get_unprocessed_messages(self, chat_id: int | None = None,
                                  date_from: datetime | None = None, date_to: datetime | None = None,
                                  overwrite: bool = False) -> list[dict]:
        with self.get_session() as session:
            if overwrite:
                q = session.query(Message).outerjoin(
                    SignalFactor, (Message.message_id == SignalFactor.message_id) & (Message.chat_id == SignalFactor.chat_id)
                ).filter(
                    Message.is_deleted == False,
                    Message.text.isnot(None),
                    (SignalFactor.id.is_(None)) | (SignalFactor.llm_status.in_(["pending", "failed", "processing"]))
                )
            else:
                q = session.query(Message).outerjoin(
                    SignalFactor, (Message.message_id == SignalFactor.message_id) & (Message.chat_id == SignalFactor.chat_id)
                ).filter(
                    Message.is_deleted == False,
                    Message.text.isnot(None),
                    SignalFactor.id.is_(None)
                )
            if chat_id:
                q = q.filter(Message.chat_id == chat_id)
            if date_from:
                q = q.filter(Message.date >= date_from)
            if date_to:
                q = q.filter(Message.date <= date_to)
            rows = q.order_by(Message.date.asc()).all()
        return [{c.name: getattr(r, c.name) for c in r.__table__.columns} for r in rows]

    def get_signal_trend(self, period: str = "day", days: int = 30,
                         chat_id: int | None = None) -> dict:
        with self.get_session() as session:
            tz_shift = sql_tz_shift()
            extra_days = 1 if tz_offset_hours() > 0 else 0
            q = session.query(
                func.date(Message.date, tz_shift).label("date"),
                func.avg(SignalFactor.direction).label("avg_direction"),
                func.avg(SignalFactor.magnitude).label("avg_magnitude"),
                func.count().label("count"),
            ).join(
                SignalFactor, (Message.message_id == SignalFactor.message_id) & (Message.chat_id == SignalFactor.chat_id)
            ).filter(
                SignalFactor.llm_status == "completed",
                Message.date >= func.datetime("now", f"-{days + extra_days} days"),
            )
            if chat_id:
                q = q.filter(Message.chat_id == chat_id)
            rows = q.group_by(func.date(Message.date, tz_shift)).order_by(func.date(Message.date, tz_shift)).all()
        trend = {}
        for r in rows:
            date_str = str(r.date)
            trend[date_str] = {
                "avg_direction": round(r.avg_direction, 3) if r.avg_direction else 0,
                "avg_magnitude": round(r.avg_magnitude, 3) if r.avg_magnitude else 0,
                "count": r.count,
            }
        return {"period": period, "days": days, "trend": trend}

    def delete_signal_factors_by_chat(self, chat_id: int) -> int:
        with self.get_session() as session:
            count = session.query(SignalFactor).filter(SignalFactor.chat_id == chat_id).delete()
            session.commit()
        return count

    def reset_stuck_processing(self, timeout_minutes: int = 10) -> int:
        cutoff = utc_now() - timedelta(minutes=timeout_minutes)
        with self.get_session() as session:
            count = session.query(SignalFactor).filter(
                SignalFactor.llm_status == "processing",
                SignalFactor.updated_at < cutoff,
            ).update({"llm_status": "pending"})
            session.commit()
        return count

    def delete_chat_data(self, chat_id: int) -> int:
        """Delete all messages and the chat row for a given chat_id. Returns deleted message count."""
        with self.get_session() as session:
            count = session.query(Message).filter(Message.chat_id == chat_id).delete()
            session.query(SignalFactor).filter(SignalFactor.chat_id == chat_id).delete()
            session.query(Chat).filter(Chat.chat_id == chat_id).delete()
            session.commit()
        return count

    def delete_all_data(self) -> int:
        """Delete all messages, chats, and senders. Returns deleted message count."""
        with self.get_session() as session:
            count = session.query(Message).delete()
            session.query(Chat).delete()
            session.query(Sender).delete()
            session.query(SignalFactor).delete()
            session.commit()
        return count

    def get_message_by_id(self, message_id: int) -> dict | None:
        with self.get_session() as session:
            msg = session.query(Message).filter(Message.message_id == message_id).first()
            if not msg:
                return None
        return {
            "message_id": msg.message_id,
            "chat_id": msg.chat_id,
            "chat_title": msg.chat_title,
            "sender_id": msg.sender_id,
            "sender_name": msg.sender_name,
            "sender_username": msg.sender_username,
            "text": msg.text,
            "reply_to_msg_id": msg.reply_to_msg_id,
            "forward_from": msg.forward_from,
            "date": msg.date.isoformat() if msg.date else None,
            "has_media": msg.has_media,
            "is_edited": msg.is_edited,
            "edited_at": msg.edited_at.isoformat() if msg.edited_at else None,
            "is_deleted": msg.is_deleted,
            "media_type": msg.media_type,
            "media_id": msg.media_id,
        }
