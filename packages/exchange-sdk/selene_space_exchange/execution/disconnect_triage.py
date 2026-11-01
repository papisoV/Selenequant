"""Fault-semantics triage for WebSocket disconnects.

Absorbed from LEAN's DefaultBrokerageMessageHandler (6-value triage) and
Disconnect dual-branch decision tree at [absorb:L1] — the *idea* that
disconnect severity = f(risk exposure), not source-level copy.

Crypto trades 7x24 with no market calendar, so LEAN's market-hours anchor
("pay to reconnect only when the market is open") is replaced by a
risk-exposure anchor: open positions / open orders make a disconnect
expensive (GRACE window then CLOSE_PROTECT flatten), while a flat book
gets SILENT_RETRY. Manual orders placed outside the system are isolated
with a one-time warning (LEAN _outsideLeanOrderWarningEmitted idea).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "DisconnectLevel",
    "TriageOutcome",
    "triage_disconnect",
    "classify_unknown_order",
    "should_warn_outside_order",
    "reset_outside_order_warning",
]


class DisconnectLevel(str, Enum):
    GRACE = "grace"  # 有敞口 → 宽限期内等重连
    SILENT_RETRY = "silent_retry"  # 无敞口 → 静默重试
    CLOSE_PROTECT = "close_protect"  # 宽限期耗尽仍未重连 → 平仓保护
    MANUAL_ORDER = "manual_order"  # 检测到外部手动单 → 隔离+一次性告警


@dataclass(frozen=True)
class TriageOutcome:
    level: DisconnectLevel
    grace_seconds: int = 900  # GRACE 宽限期
    reason: str = ""


def triage_disconnect(
    *,
    has_positions: bool,
    has_open_orders: bool,
    disconnected_at_ms: int,
    now_ms: int,
    grace_seconds: int = 900,
) -> TriageOutcome:
    """决策树纯函数：断线严重度 = f(风险敞口)。

    Open exposure stays GRACE while within the window and flips to
    CLOSE_PROTECT once ``elapsed >= grace_seconds * 1000`` — the boundary
    is inclusive so a grace timer firing exactly at the deadline closes
    the position. A flat book always gets SILENT_RETRY.
    """
    if has_positions or has_open_orders:
        elapsed = now_ms - disconnected_at_ms
        if elapsed >= grace_seconds * 1000:
            return TriageOutcome(
                DisconnectLevel.CLOSE_PROTECT,
                reason="宽限期耗尽仍未重连，触发平仓保护",
            )
        return TriageOutcome(
            DisconnectLevel.GRACE,
            grace_seconds=grace_seconds,
            reason="有风险敞口，进入重连宽限期",
        )
    return TriageOutcome(
        DisconnectLevel.SILENT_RETRY,
        reason="无持仓无挂单，静默重试",
    )


def classify_unknown_order(client_order_id: str, known_prefixes: list[str]) -> bool:
    """外部手动单判定：client_order_id 不匹配本系统已知前缀 → True。"""
    return not any(client_order_id.startswith(p) for p in known_prefixes)


# One-time warning latch — emulates LEAN _outsideLeanOrderWarningEmitted.
# Thread-safe: private WS order updates can be pushed from a worker thread.
_outside_order_warning_emitted: bool = False
_warning_lock = threading.Lock()


def should_warn_outside_order() -> bool:
    """Return True exactly once until reset; False afterwards."""
    global _outside_order_warning_emitted
    with _warning_lock:
        if _outside_order_warning_emitted:
            return False
        _outside_order_warning_emitted = True
        return True


def reset_outside_order_warning() -> None:
    """Re-arm the one-shot outside-order warning latch (test hook)."""
    global _outside_order_warning_emitted
    with _warning_lock:
        _outside_order_warning_emitted = False
