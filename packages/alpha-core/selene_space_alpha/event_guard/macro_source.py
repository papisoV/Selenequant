"""Macro calendar event source — reads from macro_events.db to generate alerts."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from .models import AlertLevel, EventAlert, EventSource, get_event_multiplier

logger = logging.getLogger(__name__)

# Pre/post-event window durations (hours) → alert level
# Format: (hours_before, hours_after, level)
# Per-event-type multipliers are applied via get_event_multiplier()
# Note: COOLDOWN is post-event only (+48h to +72h). It's checked separately
# because the main windows use tightest-match (break) logic.
_MACRO_WINDOWS: list[tuple[float, float, AlertLevel]] = [
    (2, 2, AlertLevel.RED),       # ±2h: most dangerous window
    (24, 24, AlertLevel.ORANGE),  # ±24h: moderate risk
    (48, 48, AlertLevel.YELLOW),  # ±48h: mild risk
]

# Post-event cooldown window (hours_after_event)
_COOLDOWN_HOURS_AFTER = 72  # Cooldown extends to 72h after event


class MacroSource:
    """Generates EventAlerts from macro_events.db."""

    def __init__(self, db_path: str | Path = "data/macro_events.db") -> None:
        self._db_path = Path(db_path)
        self._cache_ts: int = 0
        self._events: list[tuple[str, str, int]] = []  # (event_type, date, timestamp_utc)
        self._cache_ttl_ms = 3600_000  # Refresh every hour

    def _ensure_cache(self, now_ts: int) -> None:
        """Refresh cache from DB if stale."""
        if now_ts - self._cache_ts < self._cache_ttl_ms and self._events:
            return
        if not self._db_path.exists():
            logger.warning("macro_events.db not found at %s", self._db_path)
            return
        try:
            conn = sqlite3.connect(str(self._db_path))
            cur = conn.execute(
                "SELECT event_type, date, timestamp_utc FROM macro_events ORDER BY timestamp_utc ASC"
            )
            self._events = [(r[0], r[1], r[2]) for r in cur.fetchall()]
            conn.close()
            self._cache_ts = now_ts
            logger.debug("MacroSource cache refreshed: %d events", len(self._events))
        except Exception as e:
            logger.error("MacroSource cache refresh failed: %s", e)

    def check(self, symbol: str, now_ts: int) -> list[EventAlert]:
        """Check macro events and return active alerts for the given symbol and time."""
        self._ensure_cache(now_ts)
        alerts: list[EventAlert] = []

        for event_type, date, event_ts in self._events:
            hours_to_event = (now_ts - event_ts) / 3_600_000.0

            matched = False
            for hours_before, hours_after, level in _MACRO_WINDOWS:
                if -hours_before <= hours_to_event <= hours_after:
                    start_ts = event_ts - int(hours_before * 3_600_000)
                    end_ts = event_ts + int(hours_after * 3_600_000)
                    mult = get_event_multiplier(event_type, level)

                    alerts.append(
                        EventAlert(
                            source=EventSource.MACRO_CALENDAR,
                            level=level,
                            symbol="*",  # Macro events affect all symbols
                            reason=f"{event_type} on {date} ({hours_to_event:+.1f}h)",
                            start_ts=start_ts,
                            end_ts=end_ts,
                            multiplier=mult,
                            event_type=event_type,
                        )
                    )
                    matched = True
                    break  # Only take the tightest matching window

            # Post-event cooldown: +48h to +72h after event
            # Only triggers if no tighter window matched and we're in the cooldown zone
            if not matched and 0 < hours_to_event < _COOLDOWN_HOURS_AFTER:
                # Check if we're past the YELLOW window (+48h) but within cooldown (+72h)
                yellow_after = _MACRO_WINDOWS[2][1]  # 48h
                if hours_to_event > yellow_after:
                    start_ts = event_ts + int(yellow_after * 3_600_000)
                    end_ts = event_ts + int(_COOLDOWN_HOURS_AFTER * 3_600_000)
                    mult = get_event_multiplier(event_type, AlertLevel.COOLDOWN)

                    alerts.append(
                        EventAlert(
                            source=EventSource.MACRO_CALENDAR,
                            level=AlertLevel.COOLDOWN,
                            symbol="*",
                            reason=f"{event_type} on {date} cooldown ({hours_to_event:+.1f}h)",
                            start_ts=start_ts,
                            end_ts=end_ts,
                            multiplier=mult,
                            event_type=event_type,
                        )
                    )

        return alerts
