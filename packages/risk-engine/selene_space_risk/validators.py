"""Sanity gates for machine-generated TradeIntents.

Think of each validator as a question a human trader would ask before signing
off on an order. The answer comes back as plain-Chinese complaint strings —
never exceptions, even for malformed input. An empty list means that gate
passed quietly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from selene_space_types.agent import TradeIntent
from selene_space_types.trading import OrderSide

__all__ = [
    "ValidationResult",
    "validate",
    "validate_direction",
    "validate_price",
    "validate_stop_loss",
]

#: Slack permitted between the quoted price and the live market price.
DEFAULT_MAX_PRICE_DEVIATION = 0.05

# Marks "key absent from risk_params", distinct from an explicit None value.
_ABSENT = object()


@dataclass(frozen=True)
class ValidationResult:
    """Combined verdict across every gate; clean only when nobody complained."""

    valid: bool
    errors: list[str] = field(default_factory=list)


def _plain_number(value: object) -> bool:
    """int/float only — bool technically subclasses int but is rejected here."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _pct(fraction: float) -> str:
    return f"{fraction:.2f}%"


def _quibbles_about_price(
    intent: TradeIntent,
    live: float,
    allowance: float,
) -> Iterator[str]:
    quote = intent.price
    if quote is None or live <= 0:
        return  # nothing quotable to compare against

    stretch = abs(quote - live) / live
    if stretch <= allowance:
        return

    yield (
        f"{intent.symbol} 意图报价 {quote:g} 相对现价 {live:g} deviates "
        f"{_pct(stretch)}，越过了容许偏离带宽 {_pct(allowance)}"
    )


def validate_price(
    intent: TradeIntent,
    current_price: float,
    *,
    max_deviation: float = DEFAULT_MAX_PRICE_DEVIATION,
) -> list[str]:
    """Flag quotes that stray further than ``max_deviation`` away from spot."""
    return list(_quibbles_about_price(intent, current_price, max_deviation))


def validate_stop_loss(intent: TradeIntent) -> list[str]:
    """The stop_loss slot must exist and carry a strictly positive number."""
    slot = (intent.risk_params or {}).get("stop_loss", _ABSENT)

    if slot is _ABSENT:
        return ["Missing stop_loss：risk_params 里压根没有 stop_loss 这一键"]
    if not _plain_number(slot) or slot <= 0:
        return [f"Invalid stop_loss：读到 {slot!r}，只认正的数值"]

    return []


def _quibbles_about_direction(intent: TradeIntent) -> Iterator[str]:
    cfg = intent.risk_params or {}
    fill = intent.price
    tp = cfg.get("take_profit")

    if fill is None or tp is None or not _plain_number(tp):
        return  # without both anchors there is no directional claim to test

    long_trade = intent.side == OrderSide.BUY

    if long_trade and tp <= fill:
        yield (
            f"BUY 方向 take_profit {tp:g} 却没有站上入场价 {fill:g}，"
            "多单止盈理应挂在入场价之上"
        )
    if not long_trade and tp >= fill:
        yield (
            f"SELL 方向 take_profit {tp:g} 却没有跌破入场价 {fill:g}，"
            "空单止盈理应压在入场价之下"
        )

    sl = cfg.get("stop_loss")
    if not _plain_number(sl):
        return
    if long_trade and sl >= fill:
        yield (
            f"BUY 方向 stop_loss {sl:g} 居然不低于入场价 {fill:g}，"
            "多单止损必须收在入场价下方"
        )
    if not long_trade and sl <= fill:
        yield (
            f"SELL 方向 stop_loss {sl:g} 居然不高于入场价 {fill:g}，"
            "空单止损必须抬在入场价上方"
        )


def validate_direction(intent: TradeIntent) -> list[str]:
    """Both exit brackets must sit on the profitable side of the entry price."""
    return list(_quibbles_about_direction(intent))


def validate(intent: TradeIntent, current_price: float) -> ValidationResult:
    """Run the whole gauntlet; the intent survives only if nothing complains."""
    gathered = [
        *validate_price(intent, current_price),
        *validate_stop_loss(intent),
        *validate_direction(intent),
    ]
    return ValidationResult(valid=not gathered, errors=gathered)
