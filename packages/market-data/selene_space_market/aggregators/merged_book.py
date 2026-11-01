"""Cross-exchange orderbook merger — combines L2 snapshots from multiple exchanges.

Merges orderbook levels at the same price across exchanges, producing
a single aggregated view with per-exchange breakdown.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from selene_space_types.derivatives import MergedBookSnapshot, MergedPriceLevel
from selene_space_types.market import OrderBookL2Snapshot

logger = logging.getLogger(__name__)

# Default grouping tolerance for price merging (relative)
_DEFAULT_TOLERANCE = 1e-8
_MAX_LEVELS = 500


class MergedBookEngine:
    """Merge L2 orderbook snapshots from multiple exchanges.

    Usage::

        engine = MergedBookEngine()
        engine.update("binance", binance_snapshot)
        engine.update("okx", okx_snapshot)
        merged = engine.get_merged("BTC/USDT")
    """

    def __init__(
        self,
        *,
        tolerance: float = _DEFAULT_TOLERANCE,
        max_levels: int = _MAX_LEVELS,
        depth_levels_for_ratio: int = 20,
    ) -> None:
        self._tolerance = tolerance
        self._max_levels = max_levels
        self._depth_levels = depth_levels_for_ratio
        self._snapshots: dict[str, dict[str, OrderBookL2Snapshot]] = {}  # symbol -> {exchange -> snapshot}
        self._callbacks: list[Callable[[MergedBookSnapshot], Any]] = []
        self._cache: dict[str, MergedBookSnapshot] = {}

    def on_merged_update(self, callback: Callable[[MergedBookSnapshot], Any]) -> None:
        self._callbacks.append(callback)

    def update(self, exchange: str, snapshot: OrderBookL2Snapshot) -> None:
        """Ingest a new L2 snapshot from an exchange."""
        symbol = snapshot.symbol
        if symbol not in self._snapshots:
            self._snapshots[symbol] = {}
        self._snapshots[symbol][exchange] = snapshot

        merged = self._compute_merged(symbol)
        if merged:
            self._cache[symbol] = merged
            for cb in self._callbacks:
                try:
                    cb(merged)
                except Exception:
                    logger.exception("Error in merged book callback")

    def get_merged(self, symbol: str) -> MergedBookSnapshot | None:
        return self._cache.get(symbol)

    def get_exchanges(self, symbol: str) -> list[str]:
        return list(self._snapshots.get(symbol, {}).keys())

    def remove_exchange(self, symbol: str, exchange: str) -> None:
        if symbol in self._snapshots:
            self._snapshots[symbol].pop(exchange, None)
            merged = self._compute_merged(symbol)
            if merged:
                self._cache[symbol] = merged

    def _compute_merged(self, symbol: str) -> MergedBookSnapshot | None:
        sources = self._snapshots.get(symbol, {})
        if not sources:
            return None

        now_ms = int(time.time() * 1000)

        # Merge bids: collect all (price, amount, exchange) tuples
        bid_map: dict[int, MergedPriceLevel] = {}  # price_key -> level
        ask_map: dict[int, MergedPriceLevel] = {}

        for exchange, snap in sources.items():
            for level in snap.bids[:self._max_levels]:
                pk = self._price_key(level.price)
                if pk in bid_map:
                    existing = bid_map[pk]
                    bid_map[pk] = MergedPriceLevel(
                        price=existing.price,
                        total_amount=existing.total_amount + level.quantity,
                        exchange_breakdown={**existing.exchange_breakdown, exchange: level.quantity},
                    )
                else:
                    bid_map[pk] = MergedPriceLevel(
                        price=level.price,
                        total_amount=level.quantity,
                        exchange_breakdown={exchange: level.quantity},
                    )

            for level in snap.asks[:self._max_levels]:
                pk = self._price_key(level.price)
                if pk in ask_map:
                    existing = ask_map[pk]
                    ask_map[pk] = MergedPriceLevel(
                        price=existing.price,
                        total_amount=existing.total_amount + level.quantity,
                        exchange_breakdown={**existing.exchange_breakdown, exchange: level.quantity},
                    )
                else:
                    ask_map[pk] = MergedPriceLevel(
                        price=level.price,
                        total_amount=level.quantity,
                        exchange_breakdown={exchange: level.quantity},
                    )

        # Sort: bids descending, asks ascending
        bids = sorted(bid_map.values(), key=lambda x: -x.price)[:self._max_levels]
        asks = sorted(ask_map.values(), key=lambda x: x.price)[:self._max_levels]

        # Compute best bid/ask, spread, mid
        best_bid = bids[0].price if bids else 0.0
        best_ask = asks[0].price if asks else 0.0
        # Clamp spread to >= 0 — pydantic schema enforces ge=0, but crossed books
        # (best_bid >= best_ask) or negative/zero prices on one side can produce
        # negative spread. Treat those as zero rather than crashing the event bus.
        if best_bid > 0 and best_ask > 0:
            spread = max(0.0, best_ask - best_bid)
            mid_price = (best_bid + best_ask) / 2
        else:
            spread = 0.0
            mid_price = 0.0

        # Compute depth ratios (top N levels)
        bid_depth_usd = sum(
            lvl.total_amount * lvl.price for lvl in bids[:self._depth_levels]
        )
        ask_depth_usd = sum(
            lvl.total_amount * lvl.price for lvl in asks[:self._depth_levels]
        )
        bid_ask_ratio = bid_depth_usd / ask_depth_usd if ask_depth_usd > 0 else 1.0

        return MergedBookSnapshot(
            symbol=symbol,
            exchanges=list(sources.keys()),
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            mid_price=mid_price,
            bids=bids,
            asks=asks,
            bid_depth_usd=bid_depth_usd,
            ask_depth_usd=ask_depth_usd,
            bid_ask_ratio=bid_ask_ratio,
            timestamp=now_ms,
        )

    def _price_key(self, price: float) -> int:
        """Quantize price to avoid floating-point mismatch."""
        return int(round(price / self._tolerance))
