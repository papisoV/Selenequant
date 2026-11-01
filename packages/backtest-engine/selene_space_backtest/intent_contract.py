"""Canonical TradeIntent → execution tuple parser shared across engines.

P1-4 unifies the strategy→order contract: a strategy returns one explicit
:class:`TradeIntent`, and both the backtest engine's typed passthrough and the
live ``SignalDrivenExecutionAdapter`` (``services/local-api/app/core/``) parse
it through this single helper, so both sides agree on the same
``(side, order_type, price, stop_price)`` quad.

This keeps the Signal-default-market-price path untouched: the helper is only
consulted when the return object already carries an ``order_type`` (the typed
intent branch). Plain :class:`Signal` objects still default to market orders
inside the engine.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from selene_space_types.trading import OrderSide, OrderType

if TYPE_CHECKING:  # pragma: no cover - annotation-only import
    from selene_space_types.agent import TradeIntent


def resolve_intent_quad(
    intent: TradeIntent,
) -> tuple[OrderSide, OrderType, Decimal | None, Decimal | None]:
    """Parse one TradeIntent into the canonical ``(side, order_type, price, stop_price)``.

    ``stop_price`` is read from ``risk_params["stop_price"]`` when present,
    mirroring the backtest engine's historical duck-typed behavior — the dict
    key is the single source of truth for the stop trigger.
    """
    stop = intent.risk_params.get("stop_price")
    return (
        intent.side,
        intent.order_type,
        Decimal(str(intent.price)) if intent.price else None,
        Decimal(str(stop)) if stop else None,
    )
