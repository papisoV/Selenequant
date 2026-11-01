"""Timestamped stream-event factory for the agent runtimes.

Every runtime emits the same event shape, so construction lives here
rather than being duplicated per runtime.
"""

from __future__ import annotations

import time as _time
from typing import Any as _Any

from selene_space_types.agent import (
    AgentStreamEvent,
    AgentStreamEventType,
)

_MS_PER_SECOND = 1000


def _wall_clock_ms() -> int:
    return int(_time.time() * _MS_PER_SECOND)


def make_event(
    event_type: AgentStreamEventType,
    data: dict[str, _Any] | None = None,
) -> AgentStreamEvent:
    """Wrap a payload into a timestamped stream event.

    Missing payloads become an empty dict so consumers may index
    ``event.data`` without guarding.
    """
    payload: dict[str, _Any] = data if isinstance(data, dict) else {}
    return AgentStreamEvent(
        data=payload,
        timestamp=_wall_clock_ms(),
        type=event_type,
    )
