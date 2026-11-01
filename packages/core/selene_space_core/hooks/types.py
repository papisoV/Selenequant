"""Hook priority levels and the fixed set of predefined hook names."""

from __future__ import annotations

import enum


class HookPriority(enum.IntEnum):
    LOW = 10
    NORMAL = 50
    HIGH = 90


PREDEFINED_HOOKS = (
    "on_market_tick",
    "on_signal",
    "on_order_placed",
    "on_risk_triggered",
    "on_backtest_complete",
)
