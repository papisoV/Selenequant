"""EventGuard — unified event-driven position multiplier for SeleneQuant."""

from .credit_source import CreditSource
from .guard import EventGuard
from .macro_source import MacroSource
from .models import (
    AlertLevel,
    EventAlert,
    EventSource,
    ALERT_MULTIPLIERS,
    EVENT_TYPE_MULTIPLIERS,
    get_event_multiplier,
)
from .tg_source import TGSource, classify_message
from .vix_source import VixSource

__all__ = [
    "ALERT_MULTIPLIERS",
    "AlertLevel",
    "CreditSource",
    "EVENT_TYPE_MULTIPLIERS",
    "EventAlert",
    "EventGuard",
    "EventSource",
    "MacroSource",
    "TGSource",
    "VixSource",
    "classify_message",
    "get_event_multiplier",
]
