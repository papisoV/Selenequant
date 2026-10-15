"""TG realtime event source — keyword-based event classification for EventGuard.

Replaces the old LLM sentiment labeling approach with deterministic rules.
Key insight from prior analysis:
  - LLM sentiment labels have low directional prediction accuracy
  - TG messages are NOT trading signals — they are event causes/risk sources
  - Only certain event types (安全事件, 监管, 宏观冲突) create actionable risk
  - Price reports ("BTC breaks $X") and neutral news are noise

Classification rules (keyword-based):
  1. 安全事件 (security): hack, exploit, drain, rug, vulnerability, breached
  2. 监管 (regulatory): ban, SEC, lawsuit, regulation, sanction, compliance
  3. 宏观冲突 (geopolitical): war, conflict, tariff, sanctions, military
  4. 巨鲸/机构 (whale/institutional): foundation sell, whale move, gov selling

Each classified event generates an EventAlert with appropriate risk level:
  - 安全事件: ORANGE (reduced position for 2h)
  - 监管: ORANGE (reduced position for 2h)
  - 宏观冲突: YELLOW-ORANGE depending on severity
  - 巨鲸: YELLOW (mild reduction for 1h)

Messages that don't match any rule are ignored (not events, just noise).

Data storage: tg_events table in macro_events.db
Real-time feed: Telegram bot or channel monitoring (future integration)
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from .models import AlertLevel, EventAlert, EventSource

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword-based event classification rules
# ---------------------------------------------------------------------------

# Each rule: (event_type, keywords, alert_level, affected, half_life_hours)
_EVENT_RULES: list[tuple[str, list[str], AlertLevel, str, float]] = [
    (
        "SECURITY",
        ["hack", "exploit", "drain", "rug", "vulnerability", "breached",
         "stolen", "attack", "flash loan", "reentrancy", "compromised",
         "hacked", "被盗", "攻击", "漏洞"],
        AlertLevel.ORANGE,
        "*",  # affects all
        2.0,
    ),
    (
        "REGULATORY",
        ["ban", "SEC", "lawsuit", "regulation", "sanction", "compliance",
         "enforcement", "crackdown", "prohibit", "restrict", "fine",
         "禁止", "监管", "制裁", "诉讼"],
        AlertLevel.ORANGE,
        "*",
        2.0,
    ),
    (
        "GEOPOLITICAL",
        ["war", "conflict", "tariff", "military", "invasion", "missile",
         "nuclear", "sanctions", "embargo", "retaliation",
         "战争", "冲突", "关税", "军事"],
        AlertLevel.ORANGE,
        "*",
        4.0,  # longer impact window
    ),
    (
        "WHALE",
        ["foundation sell", "whale move", "gov selling", "OTC sale",
         "massive transfer", "cold wallet", "entity unstake",
         "巨鲸", "基金会抛售", "政府出售"],
        AlertLevel.YELLOW,
        "*",  # could be symbol-specific in future
        1.0,
    ),
]

# Per-event-type TG multipliers (less severe than macro calendar events)
_TG_EVENT_MULTIPLIERS: dict[str, dict[AlertLevel, float]] = {
    "SECURITY": {
        AlertLevel.RED: 0.0,      # catastrophic hack
        AlertLevel.ORANGE: 0.7,   # major exploit
        AlertLevel.YELLOW: 0.85,  # minor incident
    },
    "REGULATORY": {
        AlertLevel.RED: 0.0,
        AlertLevel.ORANGE: 0.7,
        AlertLevel.YELLOW: 0.85,
    },
    "GEOPOLITICAL": {
        AlertLevel.RED: 0.0,
        AlertLevel.ORANGE: 0.75,
        AlertLevel.YELLOW: 0.9,
    },
    "MARKET": {
        AlertLevel.ORANGE: 0.7,
        AlertLevel.YELLOW: 0.85,
    },
    "WHALE": {
        AlertLevel.ORANGE: 0.85,
        AlertLevel.YELLOW: 0.9,
    },
}

# Severity boosting keywords (upgrade alert level)
_SEVERITY_BOOST_KEYWORDS: list[str] = [
    "record", "unprecedented", "historic", "massive", "catastrophic",
    "$1B", "$500M", "$100M",  # large amounts
]


def classify_message(text: str) -> tuple[str, AlertLevel, str, float] | None:
    """Classify a TG message into an event type using keyword rules.

    Returns (event_type, alert_level, affected, half_life_hours) or None if no match.
    """
    text_lower = text.lower()

    for event_type, keywords, default_level, affected, half_life in _EVENT_RULES:
        if any(kw.lower() in text_lower for kw in keywords):
            # Check severity boost
            level = default_level
            if any(boost in text_lower for boost in _SEVERITY_BOOST_KEYWORDS):
                # Upgrade one level
                level_order = [AlertLevel.YELLOW, AlertLevel.ORANGE, AlertLevel.RED]
                idx = level_order.index(level) if level in level_order else 0
                level = level_order[min(idx + 1, len(level_order) - 1)]

            return (event_type, level, affected, half_life)

    return None


# ---------------------------------------------------------------------------
# TGSource
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tg_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       INTEGER NOT NULL,
    message         TEXT    NOT NULL,
    event_type      TEXT    NOT NULL,
    alert_level     TEXT    NOT NULL,
    affected        TEXT    NOT NULL DEFAULT '*',
    half_life_h     REAL    NOT NULL DEFAULT 2.0,
    processed       INTEGER NOT NULL DEFAULT 0,
    magnitude       REAL,
    urgency         REAL,
    llm_event_type  TEXT,
    UNIQUE(timestamp, message)
);

CREATE INDEX IF NOT EXISTS idx_tg_events_ts ON tg_events (timestamp);
"""

# Migration: add columns if upgrading from older schema without magnitude/urgency/llm_event_type.
_MIGRATION_ADD_FACTOR_COLUMNS = [
    "ALTER TABLE tg_events ADD COLUMN magnitude REAL",
    "ALTER TABLE tg_events ADD COLUMN urgency REAL",
    "ALTER TABLE tg_events ADD COLUMN llm_event_type TEXT",
]


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create schema if missing and migrate older schemas by adding factor columns.

    sqlite raises OperationalError if the column already exists; ignore those.
    """
    conn.executescript(_SCHEMA)
    for stmt in _MIGRATION_ADD_FACTOR_COLUMNS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass


# ---------------------------------------------------------------------------
# LLM factor → EventGuard event mapping
# ---------------------------------------------------------------------------

# Maps LLM-produced event_type (from tg-ingest signal_llm.py) to EventGuard
# (event_type, alert_level, half_life_h). Mapping reflects alpha research:
#   - security/regulatory have ±2h actionable alpha (see TG_FACTOR_REVALIDATION)
#   - market has the strongest forward-vol persistence on BTC (24h 1.19x
#     p=0.002, forward-vol research 2026-08-14) — joined 2026-08-14 after
#     previously being dropped as "no alpha in backtest"
#   - whale is mild, 1h half-life
#   - macro/listing/partnership are NOT mapped: macro is handled by
#     TGSpikeSource.macro_overlay (avoid double-cut); listing/partnership had
#     no forward-vol signal. Unmapped types are dropped (returns None).
#
# SINGLE SOURCE OF TRUTH (synced with factors/tg_event_overlay_source.py
# EVENT_OVERLAY_TYPES, 2026-08-13): this map = LIVE EventGuard reduction types
# (security/regulatory/whale/market); EVENT_OVERLAY_TYPES = backtest de-risk
# types (macro/listing/whale/security/market). The two consumers are MUTUALLY
# EXCLUSIVE at the factory layer (use_event_overlay_source) — a session never
# runs both, so the overlapping types (security/whale/market) can never
# double-cut. If this map or EVENT_OVERLAY_TYPES changes, update the other
# consumer's contract.
_LLM_EVENT_TYPE_MAP: dict[str, tuple[str, "AlertLevel", float]] = {
    "security": ("SECURITY", None, 2.0),  # level filled at runtime below
    "regulatory": ("REGULATORY", None, 2.0),
    "whale": ("WHALE", None, 1.0),
    "market": ("MARKET", None, 2.0),
}


def classify_llm_factor(
    llm_event_type: str,
    magnitude: float,
    urgency: float,
) -> tuple[str, "AlertLevel", float] | None:
    """Map an LLM-produced factor to EventGuard (event_type, level, half_life_h).

    Returns None if the event_type is not in the actionable mapping (e.g.
    macro/listing/partnership). Level is chosen from magnitude/urgency:
      - magnitude >= 0.7 OR urgency >= 0.7  → ORANGE
      - otherwise                            → YELLOW
    """
    key = (llm_event_type or "").strip().lower()
    if key not in _LLM_EVENT_TYPE_MAP:
        return None
    eg_event_type, _, half_life = _LLM_EVENT_TYPE_MAP[key]
    # Decide level dynamically from LLM magnitude/urgency (fallback to ORANGE
    # if both are zero — matches historic classify_message default for these types).
    if magnitude >= 0.7 or urgency >= 0.7:
        level = AlertLevel.ORANGE
    elif magnitude > 0.0 or urgency > 0.0:
        level = AlertLevel.YELLOW
    else:
        level = AlertLevel.ORANGE
    return (eg_event_type, level, half_life)


class TGSource:
    """Generates EventAlerts from TG messages via keyword classification."""

    def __init__(self, db_path: str | Path = "data/macro_events.db") -> None:
        self._db_path = Path(db_path)
        self._cache_ts: int = 0
        self._events: list[tuple[int, str, str, str, float]] = []  # (ts, event_type, level, affected, half_life)
        self._cache_ttl_ms = 600_000  # Refresh every 10 min

    def _ensure_cache(self, now_ts: int) -> None:
        """Refresh cache from DB if stale."""
        if now_ts - self._cache_ts < self._cache_ttl_ms and self._events:
            return
        if not self._db_path.exists():
            return
        try:
            conn = sqlite3.connect(str(self._db_path))
            _ensure_schema(conn)
            cur = conn.execute(
                "SELECT timestamp, event_type, alert_level, affected, half_life_h "
                "FROM tg_events WHERE processed = 0 ORDER BY timestamp ASC"
            )
            self._events = [(r[0], r[1], r[2], r[3], r[4]) for r in cur.fetchall()]
            conn.close()
            self._cache_ts = now_ts
            if self._events:
                logger.debug("TGSource cache refreshed: %d pending events", len(self._events))
        except Exception as e:
            logger.error("TGSource cache refresh failed: %s", e)

    def ingest_message(self, timestamp_ms: int, message: str) -> EventAlert | None:
        """Classify and store a TG message (keyword-based). Returns EventAlert if actionable.

        Legacy path: keyword rules classify raw text. Use ``ingest_llm_factor``
        instead when the upstream pipeline has already produced LLM-derived
        ``event_type``/``magnitude``/``urgency`` — that path skips keyword
        reclassification and uses the LLM output directly.
        """
        result = classify_message(message)
        if result is None:
            return None

        event_type, level, affected, half_life = result

        # Persist to DB
        try:
            conn = sqlite3.connect(str(self._db_path))
            _ensure_schema(conn)
            conn.execute(
                "INSERT OR IGNORE INTO tg_events (timestamp, message, event_type, alert_level, affected, half_life_h) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (timestamp_ms, message, event_type, level.value, affected, half_life),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error("TGSource ingest failed: %s", e)

        # Invalidate cache so next check picks up new event
        self._cache_ts = 0

        # Create alert
        mult = _TG_EVENT_MULTIPLIERS.get(event_type, {}).get(level, 0.85)
        end_ts = timestamp_ms + int(half_life * 3_600_000)

        return EventAlert(
            source=EventSource.TG_REALTIME,
            level=level,
            symbol=affected,
            reason=f"TG: {event_type} — {message[:60]}",
            start_ts=timestamp_ms,
            end_ts=end_ts,
            multiplier=mult,
            event_type=event_type,
        )

    def ingest_llm_factor(
        self,
        timestamp_ms: int,
        message: str,
        llm_event_type: str,
        magnitude: float = 0.0,
        urgency: float = 0.0,
        symbols: str = "*",
    ) -> EventAlert | None:
        """Ingest an LLM-produced TG factor directly (no keyword reclassification).

        Maps the LLM ``event_type`` to EventGuard's actionable event types and
        picks alert level from magnitude/urgency. Event types not in the
        actionable mapping (macro/listing/partnership) are dropped
        (return None) — they either have no alpha (research finding) or are
        handled elsewhere (macro overlay at TGSpikeSource).

        Persisted to ``tg_events`` with the LLM factor columns populated so
        downstream tools can inspect what the LLM actually said.
        """
        mapped = classify_llm_factor(llm_event_type, magnitude, urgency)
        if mapped is None:
            return None
        eg_event_type, level, half_life = mapped

        affected = symbols if symbols and symbols != "*" else "*"

        try:
            conn = sqlite3.connect(str(self._db_path))
            _ensure_schema(conn)
            conn.execute(
                "INSERT OR IGNORE INTO tg_events "
                "(timestamp, message, event_type, alert_level, affected, half_life_h, magnitude, urgency, llm_event_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (timestamp_ms, message, eg_event_type, level.value, affected,
                 half_life, magnitude, urgency, llm_event_type),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error("TGSource.ingest_llm_factor failed: %s", e)

        self._cache_ts = 0

        mult = _TG_EVENT_MULTIPLIERS.get(eg_event_type, {}).get(level, 0.85)
        end_ts = timestamp_ms + int(half_life * 3_600_000)

        return EventAlert(
            source=EventSource.TG_REALTIME,
            level=level,
            symbol=affected,
            reason=f"TG(LLM): {eg_event_type} — {message[:60]}",
            start_ts=timestamp_ms,
            end_ts=end_ts,
            multiplier=mult,
            event_type=eg_event_type,
        )

    def check(self, symbol: str, now_ts: int) -> list[EventAlert]:
        """Check pending TG events and return active alerts."""
        self._ensure_cache(now_ts)
        alerts: list[EventAlert] = []

        for ts, event_type, level_str, affected, half_life in self._events:
            level = AlertLevel(level_str)

            # Check if still active
            end_ts = ts + int(half_life * 3_600_000)
            if now_ts >= end_ts:
                continue

            # Check affected
            if affected != "*" and affected.upper() != symbol.upper().replace("/USDT", ""):
                continue

            mult = _TG_EVENT_MULTIPLIERS.get(event_type, {}).get(level, 0.85)
            alerts.append(
                EventAlert(
                    source=EventSource.TG_REALTIME,
                    level=level,
                    symbol=affected,
                    reason=f"TG: {event_type}",
                    start_ts=ts,
                    end_ts=end_ts,
                    multiplier=mult,
                    event_type=event_type,
                )
            )

        return alerts

    def mark_processed(self, timestamp_ms: int) -> None:
        """Mark events older than timestamp as processed."""
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute(
                "UPDATE tg_events SET processed = 1 WHERE timestamp < ?",
                (timestamp_ms,),
            )
            conn.commit()
            conn.close()
            self._cache_ts = 0  # invalidate cache
        except Exception as e:
            logger.error("TGSource mark_processed failed: %s", e)
