"""Message repository — encapsulates message + sender persistence logic.

Extracted from the monolithic `Storage` class during Phase 1A refactor.
Owns no engine; borrows the Storage facade's session factory.

Note: `save_messages` orchestration (which also calls ChatRepository.upsert
and MessageRepository.upsert_sender) stays on the Storage facade — that's
the orchestration layer, not a repo concern.

Contract: signatures match the original Storage methods byte-for-byte.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import func, tuple_
from sqlalchemy import distinct
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from tgwatcher.models import Chat, Message, Sender, SignalFactor
from tgwatcher.schemas import EditUpdate, ParsedMessage, ParsedSender
from tgwatcher.tz_utils import utc_now, sql_tz_shift, tz_offset_hours

logger = logging.getLogger(__name__)


class MessageRepository:
    """Persistence operations for `messages` and `senders` tables."""

    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

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

    def upsert_sender_from_parsed(self, sender: ParsedSender) -> None:
        with self._session_factory() as session:
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
        with self._session_factory() as session:
            record = session.query(Message).filter(
                Message.message_id == message_id,
                Message.chat_id == chat_id,
            ).first()
            if record:
                record.is_deleted = True
                session.commit()

    def update_edited_message(self, update: EditUpdate) -> None:
        with self._session_factory() as session:
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
                with self._session_factory() as session:
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
                            # Only update if text changed (edited) or media missing
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

    def get_last_message_id(self, chat_id: int) -> int | None:
        with self._session_factory() as session:
            result = (
                session.query(func.max(Message.message_id))
                .filter(Message.chat_id == chat_id, Message.is_deleted == False)
                .scalar()
            )
        return result

    def get_last_message_date(self, chat_id: int) -> datetime | None:
        with self._session_factory() as session:
            result = (
                session.query(func.max(Message.date))
                .filter(Message.chat_id == chat_id, Message.is_deleted == False)
                .scalar()
            )
        return result

    def get_stats(self) -> dict:
        with self._session_factory() as session:
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
        with self._session_factory() as session:
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

    def get_message_trend(self, period: str = "day", days: int = 30, chat_id: int | None = None) -> dict:
        with self._session_factory() as session:
            tz_shift = sql_tz_shift()
            q = session.query(
                Message.chat_id,
                Message.chat_title,
                func.date(Message.date, tz_shift).label("msg_date"),
                func.count(Message.id).label("count"),
            ).filter(Message.is_deleted == False)
            if chat_id is not None:
                q = q.filter(Message.chat_id == chat_id)
            # Expand the UTC window to cover the local-day range
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
        with self._session_factory() as session:
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
        with self._session_factory() as session:
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
        with self._session_factory() as session:
            # Unified GROUP BY query — was N+1 (loaded all senders, then
            # per-sender COUNT). The previous "if sender_rows" branch is
            # dropped because it was strictly worse than this GROUP BY:
            # it returned senders with zero messages (filtered out) and
            # ran N queries instead of 1.
            q = session.query(
                Message.sender_id,
                Message.sender_name,
                Message.sender_username,
                func.count(Message.id).label("msg_count"),
            ).filter(Message.sender_id.isnot(None), Message.is_deleted == False)
            if chat_id is not None:
                q = q.filter(Message.chat_id == chat_id)
            q = q.group_by(Message.sender_id, Message.sender_name, Message.sender_username)
            q = q.order_by(func.count(Message.id).desc())
            rows = q.all()
        return [
            {
                "sender_id": r.sender_id,
                "sender_name": r.sender_name,
                "sender_username": r.sender_username,
                "msg_count": r.msg_count,
            }
            for r in rows
        ]

    def delete_chat_data(self, chat_id: int) -> int:
        """Delete all messages and the chat row for a given chat_id. Returns deleted message count."""
        with self._session_factory() as session:
            count = session.query(Message).filter(Message.chat_id == chat_id).delete()
            session.query(SignalFactor).filter(SignalFactor.chat_id == chat_id).delete()
            session.query(Chat).filter(Chat.chat_id == chat_id).delete()
            session.commit()
        return count

    def delete_all_data(self) -> int:
        """Delete all messages, chats, and senders. Returns deleted message count."""
        with self._session_factory() as session:
            count = session.query(Message).delete()
            session.query(Chat).delete()
            session.query(Sender).delete()
            session.query(SignalFactor).delete()
            session.commit()
        return count

    def get_message_by_id(self, message_id: int) -> dict | None:
        with self._session_factory() as session:
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
