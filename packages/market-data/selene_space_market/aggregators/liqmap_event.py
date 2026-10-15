"""LiqMap engine — clusters liquidation events by price and estimates
cascading liquidation zones.

Produces heatmap-ready data for the frontend Canvas renderer.
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from selene_space_types.derivatives import (
    LiqMapCluster,
    LiqMapZone,
    LiquidationEvent,
    LiquidationHeatmapData,
)

logger = logging.getLogger(__name__)

WINDOW_DEFS: dict[str, int] = {
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "24h": 24 * 60 * 60_000,
}

_MAX_EVENTS = 10_000

# Default price bucket sizes per symbol quote currency
_DEFAULT_BUCKET_USD: dict[str, float] = {
    "USDT": 100.0,
    "USDC": 100.0,
    "USD": 100.0,
    "BTC": 0.001,
    "ETH": 0.01,
}


class LiqMapEventEngine:
    """Cluster liquidation events by price and estimate cascading zones.

    Usage::

        engine = LiqMapEventEngine()
        engine.on_heatmap_update(my_callback)
        engine.process(liquidation_event)
        heatmap = engine.get_heatmap("BTC/USDT", current_price=68000.0)
    """

    def __init__(
        self,
        *,
        bucket_size: float | None = None,
        max_events: int = _MAX_EVENTS,
    ) -> None:
        self._bucket_size = bucket_size
        self._max_events = max_events
        self._events: dict[str, deque[LiquidationEvent]] = {}  # symbol → events
        self._callbacks: list[Callable[[LiquidationHeatmapData], Any]] = []
        self._cache: dict[str, LiquidationHeatmapData] = {}

    def on_heatmap_update(self, callback: Callable[[LiquidationHeatmapData], Any]) -> None:
        self._callbacks.append(callback)

    def process(self, event: LiquidationEvent) -> None:
        """Ingest a liquidation event and recompute heatmap."""
        symbol = event.symbol
        if symbol not in self._events:
            self._events[symbol] = deque(maxlen=self._max_events)
        self._events[symbol].append(event)
        self._purge_old(symbol)

        # Recompute for all windows and notify
        # We use 1h as default for callbacks
        heatmap = self.get_heatmap(symbol, window="1h")
        if heatmap:
            self._cache[symbol] = heatmap
            for cb in self._callbacks:
                try:
                    cb(heatmap)
                except Exception:
                    logger.exception("Error in LiqMap callback")

    def get_heatmap(
        self,
        symbol: str,
        current_price: float = 0.0,
        window: str = "1h",
    ) -> LiquidationHeatmapData | None:
        events = self._events.get(symbol)
        if not events:
            return None

        now_ms = int(time.time() * 1000)
        window_ms = WINDOW_DEFS.get(window, WINDOW_DEFS["1h"])
        cutoff = now_ms - window_ms
        filtered = [e for e in events if e.timestamp >= cutoff]

        if not filtered:
            return None

        # Determine bucket size
        bucket = self._bucket_size or self._infer_bucket(symbol, filtered)

        # Cluster by price bucket
        clusters = self._cluster_events(filtered, bucket)

        # Normalize intensity
        max_liq = max((c.total_liquidated_usd for c in clusters), default=0.0)
        if max_liq > 0:
            for c in clusters:
                c.intensity = c.total_liquidated_usd / max_liq

        # Compute cascading risk per cluster
        for c in clusters:
            c.cascading_risk = self._estimate_cascading_risk(c, len(filtered))
            c.dominant_side = (
                "long" if c.long_liquidated_usd > c.short_liquidated_usd
                else "short" if c.short_liquidated_usd > c.long_liquidated_usd
                else "neutral"
            )

        # Identify support/resistance zones
        zones = self._identify_zones(clusters, current_price)

        total_long = sum(e.notional_usd for e in filtered if e.side == "long")
        total_short = sum(e.notional_usd for e in filtered if e.side == "short")

        return LiquidationHeatmapData(
            symbol=symbol,
            current_price=current_price,
            clusters=clusters,
            zones=zones,
            total_long_liquidated_usd=total_long,
            total_short_liquidated_usd=total_short,
            window=window,
            timestamp=now_ms,
        )

    def _purge_old(self, symbol: str) -> None:
        cutoff = int(time.time() * 1000) - WINDOW_DEFS["24h"]
        events = self._events.get(symbol)
        if events:
            while events and events[0].timestamp < cutoff:
                events.popleft()

    def _infer_bucket(self, symbol: str, events: list[LiquidationEvent]) -> float:
        """Infer bucket size from symbol and event prices."""
        if "/" in symbol:
            quote = symbol.split("/")[1]
            return _DEFAULT_BUCKET_USD.get(quote, 100.0)
        return 100.0

    def _cluster_events(self, events: list[LiquidationEvent], bucket: float) -> list[LiqMapCluster]:
        """Group events into price-level clusters."""
        bucket_map: dict[int, list[LiquidationEvent]] = {}
        for e in events:
            bk = int(e.price // bucket)
            if bk not in bucket_map:
                bucket_map[bk] = []
            bucket_map[bk].append(e)

        clusters: list[LiqMapCluster] = []
        for bk, evts in sorted(bucket_map.items()):
            long_evts = [e for e in evts if e.side == "long"]
            short_evts = [e for e in evts if e.side == "short"]
            long_usd = sum(e.notional_usd for e in long_evts)
            short_usd = sum(e.notional_usd for e in short_evts)
            prices = [e.price for e in evts]

            clusters.append(LiqMapCluster(
                price=sum(prices) / len(prices),
                price_min=min(prices),
                price_max=max(prices),
                long_liquidated_usd=long_usd,
                short_liquidated_usd=short_usd,
                total_liquidated_usd=long_usd + short_usd,
                long_count=len(long_evts),
                short_count=len(short_evts),
                intensity=0.0,  # normalized later
                cascading_risk=0.0,  # computed later
                dominant_side="neutral",
            ))

        return clusters

    def _estimate_cascading_risk(self, cluster: LiqMapCluster, total_events: int) -> float:
        """Estimate cascading liquidation risk based on concentration and side dominance.

        Key signals:
        - High concentration (many events at same price)
        - Side dominance (one side much more than other = more cascade potential)
        - Absolute size (large liquidation = more market impact)
        """
        if total_events == 0:
            return 0.0

        # Concentration: what fraction of all events are in this cluster
        cluster_events = cluster.long_count + cluster.short_count
        concentration = cluster_events / total_events

        # Side dominance: how one-sided is this cluster
        total_usd = cluster.total_liquidated_usd
        if total_usd > 0:
            dominance = abs(cluster.long_liquidated_usd - cluster.short_liquidated_usd) / total_usd
        else:
            dominance = 0.0

        # Size factor: log-scale of USD amount
        size_factor = min(1.0, math.log10(max(total_usd, 1.0)) / 7.0)  # $10M = 1.0

        # Combined risk: weighted average
        risk = 0.3 * concentration + 0.4 * dominance + 0.3 * size_factor
        return min(1.0, risk)

    def _identify_zones(self, clusters: list[LiqMapCluster], current_price: float) -> list[LiqMapZone]:
        """Identify support and resistance liquidation zones."""
        if not clusters or current_price <= 0:
            return []

        # Support: clusters below current price where longs were liquidated
        support_clusters = [c for c in clusters if c.price_level < current_price]
        # Resistance: clusters above current price where shorts were liquidated
        resistance_clusters = [c for c in clusters if c.price_level >= current_price]

        zones: list[LiqMapZone] = []

        # Group nearby support clusters into zones
        zones.extend(self._merge_clusters_into_zones(support_clusters, "support", current_price))
        zones.extend(self._merge_clusters_into_zones(resistance_clusters, "resistance", current_price))

        return zones

    def _merge_clusters_into_zones(
        self, clusters: list[LiqMapCluster], zone_type: str, current_price: float
    ) -> list[LiqMapZone]:
        """Merge nearby clusters into a single zone."""
        if not clusters:
            return []

        # Sort by price
        sorted_clusters = sorted(clusters, key=lambda c: c.price_level)

        zones: list[LiqMapZone] = []
        current_group: list[LiqMapCluster] = [sorted_clusters[0]]

        # Merge clusters that are within 2x bucket spacing
        # Use a simple heuristic: if gap between adjacent clusters < average cluster width, merge
        for i in range(1, len(sorted_clusters)):
            prev = current_group[-1]
            curr = sorted_clusters[i]
            gap = curr.price_min - prev.price_max

            # If gap is small relative to price, merge
            avg_width = (prev.price_max - prev.price_min + curr.price_max - curr.price_min) / 2
            if gap < max(avg_width * 2, current_price * 0.001):  # within 0.1% or 2x width
                current_group.append(curr)
            else:
                zones.append(self._build_zone(current_group, zone_type))
                current_group = [curr]

        zones.append(self._build_zone(current_group, zone_type))
        return zones

    def _build_zone(self, clusters: list[LiqMapCluster], zone_type: str) -> LiqMapZone:
        """Build a LiqMapZone from a group of clusters."""
        total_usd = sum(c.total_liquidated_usd for c in clusters)
        max_risk = max(c.cascading_risk for c in clusters)

        return LiqMapZone(
            zone_type=zone_type,
            price_low=min(c.price_min for c in clusters),
            price_high=max(c.price_max for c in clusters),
            total_liquidated_usd=total_usd,
            cluster_count=len(clusters),
            cascading_risk=max_risk,
            estimated_magnitude_usd=total_usd * max_risk,
        )
