"""Internal async event bus — re-exported from core for backward compatibility.

The canonical EventBus now lives in selene_space_core.events.bus.
This module re-exports it so existing imports continue to work.
"""

from selene_space_core.events.bus import EventBus  # noqa: F401 — re-export
