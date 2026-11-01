"""Deterministic continuity rules — pre-LLM guard for trading decisions.

Method basis (publications only, no third-party corpus): Al Brooks,
"Reading Price Charts Bar by Bar" (Wiley, 2009) Ch. 2-4 & 10-12; "Trading
Price Action Trends" (Wiley, 2011) Ch. 3-5 & 21-23. The three event-window
conventions below (reversal cooldown, resting-order expiry, stop-touch
invalidation) are standard price-action practice described in those
publications; all wording here is original, cleanroom for SeleneQuant (AGPL-3.0-only).

These rules run BEFORE the LLM gate: they consume the previous Stage2
decision plus newly arrived bars and answer one deterministic question —
"may a fresh opposite-side decision be taken at all?" No LLM involved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ContinuityGuard:
    """Outcome of the deterministic continuity check."""

    allowed: bool
    rule: str | None
    reason: str


def _decision_bar_offset(order: dict[str, Any]) -> int:
    """Bars elapsed since the decision was taken (0 = decided on last bar)."""
    value = order.get("bars_since_decision")
    return value if isinstance(value, int) and value >= 0 else 0


def _direction(order: dict[str, Any]) -> str | None:
    d = order.get("direction") or order.get("side")
    if isinstance(d, str) and d in ("long", "short"):
        return d
    return None


def _stop_price(order: dict[str, Any]) -> float | None:
    p = order.get("stop_loss") or order.get("stop_price")
    return float(p) if isinstance(p, (int, float)) else None


def reversal_cooldown(
    last_decision: dict[str, Any],
    new_bars: list[dict[str, Any]],  # noqa: ARG001 (uniform rule signature)
    *,
    cooldown_bars: int = 3,
) -> ContinuityGuard:
    """A fresh signal against the previous side is suppressed for N bars.

    Flipping always-in direction every bar is the classic overtrade failure;
    price-action practice is to let a newly taken decision breathe for a few
    bars before acting on an opposite-side read.
    """
    direction = _direction(last_decision)
    if direction is None:
        return ContinuityGuard(True, None, "上次决策无方向字段,冷却不适用")
    if not last_decision.get("signal_reversal"):
        return ContinuityGuard(True, None, "本次信号与上次同向,冷却不适用")
    elapsed = _decision_bar_offset(last_decision)
    if elapsed < cooldown_bars:
        return ContinuityGuard(
            False,
            "reversal_cooldown",
            f"反向信号距上次决策仅{elapsed}根<{cooldown_bars}根冷却窗",
        )
    return ContinuityGuard(True, None, "冷却窗已过")


def limit_order_expiry(
    last_decision: dict[str, Any],
    new_bars: list[dict[str, Any]],  # noqa: ARG001 (uniform rule signature)
    *,
    expiry_bars: int = 3,
) -> ContinuityGuard:
    """A resting limit order older than M bars is dead — re-decide, don't reuse.

    A limit that has not filled within a few bars means the market moved away
    from the intended entry; treating it as still-live trades stale intent.
    """
    order = last_decision.get("order") or {}
    if not isinstance(order, dict) or order.get("type") != "limit":
        return ContinuityGuard(True, None, "非限价单或无挂单,过期不适用")
    resting = order.get("bars_resting")
    resting = resting if isinstance(resting, int) and resting >= 0 else 0
    if resting > expiry_bars:
        return ContinuityGuard(
            False,
            "limit_order_expiry",
            f"限价单已挂{resting}根>{expiry_bars}根未成交,视为失效须重新决策",
        )
    return ContinuityGuard(True, None, "挂单仍在有效窗内")


def stop_loss_invalidation(
    last_decision: dict[str, Any], new_bars: list[dict[str, Any]]
) -> ContinuityGuard:
    """Any new bar touching the declared stop price kills the prior decision.

    After a stop is hit the original premise is judged wrong by the market;
    downstream logic must not keep building on that decision.
    """
    order = last_decision.get("order") or {}
    stop = _stop_price(order if isinstance(order, dict) else {})
    if stop is None:
        return ContinuityGuard(True, None, "上次决策无止损价,触损不适用")
    for bar in new_bars:
        if bar.get("low", 0.0) <= stop <= bar.get("high", 0.0):
            return ContinuityGuard(
                False,
                "stop_loss_invalidation",
                f"新增K线触及止损价{stop},上次决策失效",
            )
    return ContinuityGuard(True, None, "止损价未触及")


def evaluate_continuity(
    last_decision: dict[str, Any], new_bars: list[dict[str, Any]]
) -> ContinuityGuard:
    """Run all three rules; the first blocking one wins, else allow."""
    for rule_fn in (stop_loss_invalidation, reversal_cooldown, limit_order_expiry):
        guard = rule_fn(last_decision, new_bars)
        if not guard.allowed:
            return guard
    return ContinuityGuard(True, None, "三规则均未否决")


__all__ = [
    "ContinuityGuard",
    "evaluate_continuity",
    "limit_order_expiry",
    "reversal_cooldown",
    "stop_loss_invalidation",
]
