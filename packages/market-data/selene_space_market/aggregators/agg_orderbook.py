"""Aggregated Orderbook engine — cross-exchange L2 merge.

Merges L2 orderbook snapshots from multiple exchanges (Binance, OKX, etc.)
into a single consolidated book with per-exchange quantity breakdowns.
"""

from __future__ import annotations

import logging
import math
import time

from selene_space_types.derivatives import (
    AggregatedOrderbookLevel,
    AggregatedOrderbookSnapshot,
)
from selene_space_types.market import OrderBookL2Snapshot

logger = logging.getLogger(__name__)

# Price aggregation tolerance — levels within this % are merged
_PRICE_TOLERANCE = 0.0001  # 0.01%


def _finite(value: object, *, positive: bool = False) -> float | None:
    """Return *value* as a float, or None if it is not a usable number.

    A half-populated L2 snapshot carries NaN or None prices; ``int(round(nan))``
    inside ``_quantize_price`` then raises (ValueError / TypeError) and took the
    whole endpoint down with a 500. Callers skip the offending level instead.
    ``positive=True`` additionally rejects zero/negative (a price key of 0 is
    meaningless and would merge every such level into one bucket).
    """
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    if positive and f <= 0:
        return None
    return f


class AggregatedOrderbookEngine:
    """Merge cross-exchange L2 orderbooks into a single view.

    Usage::

        engine = AggregatedOrderbookEngine(market_service)
        snapshot = engine.compute("BTC/USDT")
    """

    def __init__(self, market_service: object | None = None) -> None:
        self._svc = market_service

    def set_service(self, market_service: object) -> None:
        self._svc = market_service

    def compute(
        self,
        symbol: str,
        exchanges: list[str] | None = None,
        market_types: list[str] | None = None,
        depth: int = 25,
    ) -> AggregatedOrderbookSnapshot | None:
        """Compute aggregated orderbook for a symbol across exchanges.

        Args:
            symbol: Trading pair (e.g. "BTC/USDT")
            exchanges: List of exchanges to merge (default: all available)
            market_types: Market types per exchange (default: ["futures", "spot"])
            depth: Number of levels per side
        """
        if self._svc is None:
            return None

        if exchanges is None:
            exchanges = ["binance", "okx"]
        if market_types is None:
            market_types = ["futures", "spot"]

        # Collect snapshots from all sources
        snapshots: list[tuple[str, OrderBookL2Snapshot]] = []
        for ex in exchanges:
            for mt in market_types:
                try:
                    # _svc is duck-typed (object|None, same as kf_index); the
                    # callable is validated by the endpoint's hasattr guard.
                    ob = self._svc.get_orderbook(  # type: ignore[attr-defined]
                        symbol, exchange=ex, market_type=mt
                    )
                    if ob is not None and ob.bids and ob.asks:
                        snapshots.append((ex, ob))
                except Exception:
                    logger.debug("AggOB: %s/%s unavailable for %s", ex, mt, symbol)

        if not snapshots:
            return None

        now_ms = int(time.time() * 1000)

        # Merge bid levels
        bid_map: dict[int, AggregatedOrderbookLevel] = {}
        for ex, ob in snapshots:
            ref_bid = _finite(ob.bids[0].price, positive=True)
            if ref_bid is None:
                continue  # unrepresentable top-of-book: skip this venue's bids
            for level in ob.bids[:depth]:
                price = _finite(level.price, positive=True)
                qty = _finite(level.quantity)
                if price is None or qty is None:
                    continue  # drop the malformed level, keep the rest
                price_key = self._quantize_price(price, ref_bid)
                if price_key in bid_map:
                    existing = bid_map[price_key]
                    bid_map[price_key] = AggregatedOrderbookLevel(
                        price=existing.price,
                        total_quantity=existing.total_quantity + qty,
                        exchange_breakdown={
                            **existing.exchange_breakdown,
                            ex: existing.exchange_breakdown.get(ex, 0) + qty,
                        },
                    )
                else:
                    bid_map[price_key] = AggregatedOrderbookLevel(
                        price=price,
                        total_quantity=qty,
                        exchange_breakdown={ex: qty},
                    )

        # Merge ask levels
        ask_map: dict[int, AggregatedOrderbookLevel] = {}
        for ex, ob in snapshots:
            ref_ask = _finite(ob.asks[0].price, positive=True)
            if ref_ask is None:
                continue
            for level in ob.asks[:depth]:
                price = _finite(level.price, positive=True)
                qty = _finite(level.quantity)
                if price is None or qty is None:
                    continue
                price_key = self._quantize_price(price, ref_ask)
                if price_key in ask_map:
                    existing = ask_map[price_key]
                    ask_map[price_key] = AggregatedOrderbookLevel(
                        price=existing.price,
                        total_quantity=existing.total_quantity + qty,
                        exchange_breakdown={
                            **existing.exchange_breakdown,
                            ex: existing.exchange_breakdown.get(ex, 0) + qty,
                        },
                    )
                else:
                    ask_map[price_key] = AggregatedOrderbookLevel(
                        price=price,
                        total_quantity=qty,
                        exchange_breakdown={ex: qty},
                    )

        # Sort: bids descending, asks ascending
        bids = sorted(bid_map.values(), key=lambda l: l.price, reverse=True)[:depth]
        asks = sorted(ask_map.values(), key=lambda l: l.price)[:depth]

        # A venue can pass the non-empty check yet carry only malformed levels,
        # leaving one side empty after the filters above. Building the snapshot
        # then fails pydantic's mid_price gt=0 — the same 500 by another door.
        # Report "no book" instead, matching the no-snapshot case.
        if not bids or not asks:
            logger.debug("AggOB: %s has no usable levels after filtering", symbol)
            return None

        # Compute spread and mid price
        spread = 0.0
        mid_price = 0.0
        if bids and asks:
            best_bid = bids[0].price
            best_ask = asks[0].price
            spread = best_ask - best_bid
            mid_price = (best_bid + best_ask) / 2

        # Compute total value
        total_bid_usd = sum(l.price * l.total_quantity for l in bids)
        total_ask_usd = sum(l.price * l.total_quantity for l in asks)

        return AggregatedOrderbookSnapshot(
            symbol=symbol,
            bids=bids,
            asks=asks,
            spread=round(spread, 2),
            mid_price=round(mid_price, 2),
            total_bid_value_usd=round(total_bid_usd, 2),
            total_ask_value_usd=round(total_ask_usd, 2),
            source_count=len(snapshots),
            timestamp=now_ms,
        )

    @staticmethod
    def _quantize_price(price: float, ref_price: float) -> int:
        """Quantize price into bucket key for merging nearby levels.

        Uses tick size relative to reference price to group levels
        that are effectively the same price across exchanges.
        """
        if ref_price <= 0:
            return int(price * 100)
        tick = ref_price * _PRICE_TOLERANCE
        return int(round(price / tick))
