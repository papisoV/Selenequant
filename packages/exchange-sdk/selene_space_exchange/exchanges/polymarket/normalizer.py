"""Polymarket user-channel event mapping.

Turns raw user-socket dicts into :class:`PolymarketOrderUpdate` /
:class:`PolymarketTradeUpdate`. Every field lookup is defensive: missing
values fall back to benign defaults (BUY side, PLACEMENT type, MATCHED
status) because the venue occasionally trims payloads mid-stream.
"""

from __future__ import annotations

from typing import Any

from selene_space_types.trading import (
    OrderSide,
    PolymarketOrderEventType,
    PolymarketOrderUpdate,
    PolymarketTradeUpdate,
)

EXCHANGE = "polymarket"

_SIDES = {"SELL": OrderSide.SELL}

_EVENT_KINDS = {
    entry.value: entry for entry in PolymarketOrderEventType
}


def _stamp(data: dict[str, Any]) -> int:
    for key in ("timestamp", "ts"):
        value = data.get(key)
        if value:
            return int(value)
    return 0


def _decimal(value: Any, default: str = "0") -> Any:
    return value if value not in (None, "") else default


class PolymarketNormalizer:
    def normalize_order_event(self, data: dict[str, Any]) -> PolymarketOrderUpdate:
        kind = _EVENT_KINDS.get(data.get("type", ""), PolymarketOrderEventType.PLACEMENT)
        return PolymarketOrderUpdate(
            exchange=EXCHANGE,
            order_id=data.get("id", ""),
            asset_id=data.get("asset_id", ""),
            market=data.get("market", ""),
            owner=data.get("owner", ""),
            side=_SIDES.get(data.get("side", ""), OrderSide.BUY),
            price=_decimal(data.get("price")),
            original_size=_decimal(data.get("original_size")),
            size_matched=_decimal(data.get("size_matched")),
            event_type=kind,
            timestamp=_stamp(data),
            raw=data,
        )

    def normalize_trade_event(self, data: dict[str, Any]) -> PolymarketTradeUpdate:
        return PolymarketTradeUpdate(
            exchange=EXCHANGE,
            trade_id=data.get("id", ""),
            asset_id=data.get("asset_id", ""),
            market=data.get("market", ""),
            owner=data.get("owner", ""),
            side=_SIDES.get(data.get("side", ""), OrderSide.BUY),
            price=_decimal(data.get("price")),
            size=_decimal(data.get("size")),
            status=data.get("status") or "MATCHED",
            timestamp=_stamp(data),
            raw=data,
        )
