"""Signal repository — encapsulates signal_factors + signal_outcomes persistence.

Extracted from the monolithic `Storage` class during Phase 1A refactor.
Owns no engine; borrows the Storage facade's session factory.

Contract: signatures match the original Storage methods byte-for-byte.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, text
from sqlalchemy.orm import sessionmaker

from tgwatcher.models import Message, SignalFactor, SignalOutcome
from tgwatcher.tz_utils import utc_now, sql_tz_shift, tz_offset_hours

logger = logging.getLogger(__name__)


class SignalRepository:
    """Persistence operations for `signal_factors` and `signal_outcomes` tables."""

    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def save_signal_factor(self, factor_dict: dict) -> None:
        with self._session_factory() as session:
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
        with self._session_factory() as session:
            sf = session.query(SignalFactor).filter(
                SignalFactor.message_id == message_id,
                SignalFactor.chat_id == chat_id,
            ).first()
            if not sf:
                return None
        return {c.name: getattr(sf, c.name) for c in sf.__table__.columns}

    def save_signal_outcome(self, outcome: dict) -> dict:
        """Upsert a signal outcome. Keyed on (message_id, chat_id, time_horizon_min).

        Returns the saved row as a dict.
        """
        horizon = outcome.get("time_horizon_min")
        with self._session_factory() as session:
            q = session.query(SignalOutcome).filter(
                SignalOutcome.message_id == outcome["message_id"],
                SignalOutcome.chat_id == outcome["chat_id"],
            )
            if horizon is None:
                # NULL horizon is a singleton per (message_id, chat_id);
                # SQLite treats NULLs as distinct in UNIQUE constraints, so
                # filter explicitly to maintain upsert semantics.
                existing = q.filter(SignalOutcome.time_horizon_min.is_(None)).first()
            else:
                existing = q.filter(SignalOutcome.time_horizon_min == horizon).first()
            if existing:
                for key, value in outcome.items():
                    if hasattr(existing, key):
                        setattr(existing, key, value)
                # Always refresh reported_at on re-report
                existing.reported_at = datetime.now(timezone.utc).replace(tzinfo=None)
                row = existing
            else:
                row = SignalOutcome(**outcome)
                session.add(row)
            session.commit()
            session.refresh(row)
            return {c.name: getattr(row, c.name) for c in row.__table__.columns}

    def get_signal_outcomes(self, message_id: int, chat_id: int) -> list[dict]:
        """Return all outcomes reported for a (message_id, chat_id) signal."""
        with self._session_factory() as session:
            rows = session.query(SignalOutcome).filter(
                SignalOutcome.message_id == message_id,
                SignalOutcome.chat_id == chat_id,
            ).order_by(SignalOutcome.time_horizon_min.asc()).all()
            return [{c.name: getattr(r, c.name) for c in r.__table__.columns} for r in rows]

    def query_signal_factors(self, chat_id: int | None = None,
                             event_type: str | None = None,
                             direction: str | None = None,
                             date_from: datetime | None = None, date_to: datetime | None = None,
                             page: int = 1, page_size: int = 50) -> dict:
        with self._session_factory() as session:
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
        with self._session_factory() as session:
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
            # Direction distribution — q.filter() returns a new Query,
            # so each count is independent (no accumulation across calls).
            bullish = q.filter(SignalFactor.direction > 0).count()
            bearish = q.filter(SignalFactor.direction < 0).count()
            neutral = q.filter(SignalFactor.direction == 0).count()
            # Event type distribution
            event_types = [r[0] for r in session.query(SignalFactor.event_type).filter(
                SignalFactor.event_type.isnot(None)).distinct().all()]
            event_counts = {et: q.filter(SignalFactor.event_type == et).count() for et in event_types}
            # Averages
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
        with self._session_factory() as session:
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
        with self._session_factory() as session:
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
        with self._session_factory() as session:
            count = session.query(SignalFactor).filter(SignalFactor.chat_id == chat_id).delete()
            session.commit()
        return count

    def reset_stuck_processing(self, timeout_minutes: int = 10) -> int:
        cutoff = utc_now() - timedelta(minutes=timeout_minutes)
        with self._session_factory() as session:
            count = session.query(SignalFactor).filter(
                SignalFactor.llm_status == "processing",
                SignalFactor.updated_at < cutoff,
            ).update({"llm_status": "pending"})
            session.commit()
        return count

    def query_signals_export(
        self,
        chat_id: int | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        event_type: str | None = None,
        direction: str | None = None,
        llm_model: str | None = None,
        is_signal: str | None = None,
        count_only: bool = False,
    ) -> list[dict] | int:
        """Query signal_factors joined with messages for the export endpoint.

        Phase 1B: migrated verbatim from the inline raw-SQL block that lived
        at `api.py:764` (now removed). Filter semantics preserved exactly:
        - `is_signal` accepts "true" / "false" / None (string from query args)
        - `direction` accepts "bullish" / "bearish" / "neutral" / None
          ("neutral" was not in the original block but is added for parity
          with query_signal_factors; the route doesn't currently pass it)
        - `date_from` / `date_to` are naive UTC datetimes (caller converts
          from local time)
        - Returns list of dicts; `count_only=True` returns an int instead

        Rows are ordered by message date DESC.
        """
        where_clauses = ["m.is_deleted = 0", "m.text IS NOT NULL", "f.llm_status = 'completed'"]
        params: dict = {}
        if chat_id:
            where_clauses.append("m.chat_id = :chat_id")
            params["chat_id"] = chat_id
        if date_from:
            where_clauses.append("m.date >= :df")
            params["df"] = date_from.isoformat()
        if date_to:
            where_clauses.append("m.date <= :dt")
            params["dt"] = date_to.isoformat()
        if event_type:
            where_clauses.append("f.event_type = :event_type")
            params["event_type"] = event_type
        if direction == "bullish":
            where_clauses.append("f.direction > 0")
        elif direction == "bearish":
            where_clauses.append("f.direction < 0")
        if llm_model:
            where_clauses.append("f.llm_model = :llm_model")
            params["llm_model"] = llm_model
        if is_signal == "true":
            where_clauses.append("f.is_signal = 1")
        elif is_signal == "false":
            where_clauses.append("f.is_signal = 0")

        where = " AND ".join(where_clauses)

        with self._session_factory() as session:
            if count_only:
                count = session.execute(text(f"""
                    SELECT COUNT(*) FROM messages m
                    INNER JOIN signal_factors f ON m.message_id = f.message_id AND m.chat_id = f.chat_id
                    WHERE {where}
                """), params).scalar()
                return count or 0

            query = f"""
                SELECT m.message_id, m.chat_id, m.chat_title, m.sender_name,
                       m.text, m.date,
                       f.direction, f.magnitude, f.urgency, f.confidence,
                       f.halflife_min, f.symbols, f.event_type, f.reasoning
                FROM messages m
                INNER JOIN signal_factors f ON m.message_id = f.message_id AND m.chat_id = f.chat_id
                WHERE {where}
                ORDER BY m.date DESC
            """
            rows = []
            for row in session.execute(text(query), params):
                rows.append({
                    "message_id": row.message_id,
                    "chat_id": row.chat_id,
                    "chat_title": row.chat_title,
                    "sender_name": row.sender_name,
                    "text": row.text,
                    "date": row.date,
                    "direction": row.direction,
                    "magnitude": row.magnitude,
                    "urgency": row.urgency,
                    "confidence": row.confidence,
                    "halflife_min": row.halflife_min,
                    "symbols": json.loads(row.symbols) if row.symbols else [],
                    "event_type": row.event_type,
                    "reasoning": row.reasoning,
                })
        return rows
