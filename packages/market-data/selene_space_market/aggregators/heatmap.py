"""Liquidation heatmap aggregator — Z-score normalized density grid.

Consumes LiqMapResult events and builds a rolling 2D matrix
(price × time) of liquidation density, normalized to Z-scores.
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque

from selene_space_types.derivatives import HeatmapCell, HeatmapMatrix, LiqMapResult

logger = logging.getLogger(__name__)

# Keep last N LiqMap snapshots for the rolling window
_MAX_SNAPSHOTS = 48  # ~4 hours at 5min intervals
# Price grid resolution
_PRICE_BINS = 100
# Time window for heatmap (ms)
_TIME_WINDOW_MS = 4 * 60 * 60_000  # 4 hours


class HeatmapAggregator:
    """Build Z-score normalized liquidation heatmaps from LiqMap results.

    Usage::

        agg = HeatmapAggregator()
        agg.process(liqmap_result)
        matrix = agg.get_heatmap("binance", "BTC/USDT", "all_leverage")
    """

    def __init__(self, price_bins: int = _PRICE_BINS, time_window_ms: int = _TIME_WINDOW_MS) -> None:
        self._price_bins = price_bins
        self._time_window_ms = time_window_ms
        # Key: (exchange, symbol, leverage_type) -> deque of LiqMapResult
        self._snapshots: dict[tuple[str, str, str], deque[LiqMapResult]] = {}

    def process(self, result: LiqMapResult) -> None:
        key = (result.exchange, result.symbol, result.leverage_type)
        if key not in self._snapshots:
            self._snapshots[key] = deque(maxlen=_MAX_SNAPSHOTS)
        self._snapshots[key].append(result)

    def get_heatmap(
        self,
        exchange: str,
        symbol: str,
        leverage_type: str = "all_leverage",
    ) -> HeatmapMatrix | None:
        """Compute Z-score heatmap for a given key."""
        key = (exchange, symbol, leverage_type)
        snapshots = self._snapshots.get(key)
        if not snapshots or len(snapshots) < 2:
            return None

        now_ms = int(time.time() * 1000)
        cutoff = now_ms - self._time_window_ms

        # Filter to time window
        recent = [s for s in snapshots if s.timestamp >= cutoff]
        if len(recent) < 2:
            return None

        # Determine price range from all clusters
        all_prices = []
        for s in recent:
            all_prices.extend(c.price for c in s.clusters)
        if not all_prices:
            return None

        p_min = min(all_prices) * 0.998
        p_max = max(all_prices) * 1.002
        if p_max <= p_min:
            return None
        price_step = (p_max - p_min) / self._price_bins

        # Determine time buckets
        t_min = min(s.timestamp for s in recent)
        t_max = max(s.timestamp for s in recent)
        time_span = max(t_max - t_min, 60_000)  # at least 1 minute
        time_step = time_span // max(len(recent) - 1, 1)
        if time_step == 0:
            time_step = 60_000

        # Build 2D density grid
        # grid[price_bin][time_idx] = total density
        grid: dict[int, dict[int, float]] = {}

        for s_idx, snapshot in enumerate(recent):
            for cluster in snapshot.clusters:
                p_bin = int((cluster.price - p_min) / price_step)
                t_idx = s_idx
                p_bin = max(0, min(self._price_bins - 1, p_bin))
                if p_bin not in grid:
                    grid[p_bin] = {}
                grid[p_bin][t_idx] = grid[p_bin].get(t_idx, 0.0) + cluster.density

        # Flatten all values for Z-score computation
        all_values: list[float] = []
        for time_map in grid.values():
            all_values.extend(time_map.values())

        if not all_values:
            return None

        mean = sum(all_values) / len(all_values)
        variance = sum((v - mean) ** 2 for v in all_values) / len(all_values)
        std = math.sqrt(variance) if variance > 0 else 1.0

        # Build heatmap cells
        cells: list[HeatmapCell] = []
        for p_bin, time_map in grid.items():
            for t_idx, raw_density in time_map.items():
                z_score = (raw_density - mean) / std if std > 0 else 0.0
                price_level = p_min + p_bin * price_step
                cells.append(HeatmapCell(
                    price_level=price_level,
                    time_offset=t_idx,
                    z_score=round(z_score, 4),
                    raw_density=round(raw_density, 6),
                ))

        window_count = len(recent)
        return HeatmapMatrix(
            exchange=exchange,
            symbol=symbol,
            leverage_type=leverage_type,
            cells=cells,
            price_min=round(p_min, 2),
            price_max=round(p_max, 2),
            price_step=round(price_step, 2),
            time_step_ms=time_step,
            window_count=window_count,
            timestamp=now_ms,
        )
