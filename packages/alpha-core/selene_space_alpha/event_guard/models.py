"""Event guard models — alert levels, event sources, and alert dataclass."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AlertLevel(str, Enum):
    GREEN = "green"
    COOLDOWN = "cooldown"  # Post-event recovery phase (48-72h after event)
    YELLOW = "yellow"
    ORANGE = "orange"
    RED = "red"


class EventSource(str, Enum):
    MACRO_CALENDAR = "macro_calendar"
    TG_REALTIME = "tg_realtime"
    UNLOCK_CALENDAR = "unlock_calendar"
    TG_REGIME = "tg_regime"  # TG volatility-regime feed (48h persistence layer)
    CREDIT = "credit"  # HY OAS 5y rolling-percentile credit regime (opt-in)


# Default alert level → position multiplier mapping
# Validated by V10 macro guard backtest (3243 trades, OOS 50%):
#   RED(±2h) WR=62.5% → block justified
#   ORANGE(±24h) WR=66.4% vs normal 68.8% → mild risk, 0.7 not 0.3
#   YELLOW(±48h) WR=67.3% vs normal 68.8% → minimal risk, 0.85 not 0.6
#   COOLDOWN(+48h to +72h) WR=65.4% → still recovering, 0.9
#   Old schedule (0.0/0.3/0.6) cost -5447 PnL vs no-guard; new (0.0/0.7/0.85) costs ~-2590
ALERT_MULTIPLIERS: dict[AlertLevel, float] = {
    AlertLevel.GREEN: 1.0,
    AlertLevel.COOLDOWN: 0.9,
    AlertLevel.YELLOW: 0.85,
    AlertLevel.ORANGE: 0.7,
    AlertLevel.RED: 0.0,
}

# Per-event-type multiplier overrides (V10 data):
#   NFP: RED WR=50.0%, ORANGE WR=64.2% → aggressive reduction
#   CPI: RED WR=66.7%, ORANGE WR=68.8% → mild reduction
#   FOMC: RED WR=72.7%, ORANGE WR=66.1% → minimal reduction
# Post-event cooldown (48-72h after event):
#   NFP post-event slowest recovery (48-72h WR=65.4%)
#   CPI/FOMC post-event faster recovery
# Format: event_type → {AlertLevel → multiplier}
EVENT_TYPE_MULTIPLIERS: dict[str, dict[AlertLevel, float]] = {
    "NFP": {
        AlertLevel.RED: 0.0,
        AlertLevel.ORANGE: 0.5,
        AlertLevel.YELLOW: 0.75,
        AlertLevel.COOLDOWN: 0.85,
    },
    "CPI": {
        AlertLevel.RED: 0.5,
        AlertLevel.ORANGE: 0.8,
        AlertLevel.YELLOW: 0.9,
        AlertLevel.COOLDOWN: 0.95,
    },
    "FOMC": {
        AlertLevel.RED: 0.7,
        AlertLevel.ORANGE: 0.85,
        AlertLevel.YELLOW: 0.95,
        AlertLevel.COOLDOWN: 0.95,
    },
}


def get_event_multiplier(event_type: str, level: AlertLevel) -> float:
    """Get position multiplier for a specific event type and alert level.

    Falls back to ALERT_MULTIPLIERS if no per-type override exists.
    """
    type_overrides = EVENT_TYPE_MULTIPLIERS.get(event_type)
    if type_overrides and level in type_overrides:
        return type_overrides[level]
    return ALERT_MULTIPLIERS[level]


@dataclass(frozen=True)
class EventAlert:
    source: EventSource
    level: AlertLevel
    symbol: str  # "*" for global (affects all symbols), or specific like "XRP"
    reason: str  # Human-readable reason
    start_ts: int  # ms epoch — when the alert becomes active
    end_ts: int  # ms epoch — when the alert expires
    multiplier: float  # Position multiplier for this alert
    event_type: str = (
        ""  # Event type (e.g. "FOMC", "CPI", "NFP") for per-type overrides
    )

    def is_active(self, now_ts: int) -> bool:
        return self.start_ts <= now_ts < self.end_ts
