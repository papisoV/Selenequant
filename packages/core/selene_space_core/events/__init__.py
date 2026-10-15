"""Cross-package pub/sub: typed domain events plus the shared EventBus."""

from selene_space_core.events.bus import EventBus, get_event_bus
from selene_space_core.events.events import (
    OverfitDetectedEvent,
    RegimeChangedEvent,
    RiskThresholdBreachedEvent,
)

__all__ = [
    "EventBus",
    "get_event_bus",
    "OverfitDetectedEvent",
    "RegimeChangedEvent",
    "RiskThresholdBreachedEvent",
]
