"""LiqMap engine — estimate and cluster liquidation prices.

Reverse-engineers The Kingfisher's LiqMap algorithm using public data:
1. Estimate liquidation prices from mark_price + leverage tiers
2. Weight by OI distribution (from top_position_ratio)
3. Smooth into clusters via KDE
4. Output per-leverage-tier {price, density} pairs
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from typing import Any

from selene_space_types.derivatives import LiqMapCluster, LiqMapResult, LiqRatioItem

logger = logging.getLogger(__name__)

# Leverage tier definitions (matching Kingfisher's configurations)
LEVERAGE_TIERS: dict[str, list[int]] = {
    "all_leverage": list(range(2, 126)),       # 2x-125x: scalp & swing
    "high_leverage": list(range(90, 126)),      # 90x-125x: aggressive scalp
    "low_leverage": list(range(2, 11)),         # 2x-10x: conservative swing
    "optical_opti": list(range(25, 96)),        # 25x-95x: short-term swing
}

# Binance maintenance margin ratios (approximate, per leverage bracket)
# These are simplified; real values vary by bracket and symbol
_MMR_DEFAULT = 0.005  # 0.5% for <= 20x
_MMR_HIGH = 0.015     # 1.5% for 20-50x
_MMR_VERY_HIGH = 0.025  # 2.5% for 50-125x

# KDE bandwidth as fraction of mark price
_KDE_BANDWIDTH_FRAC = 0.001  # 0.1% of price = ~$60 for BTC at $60k
# Price grid resolution
_PRICE_GRID_BINS = 200
# Min density to include in output
_MIN_DENSITY_THRESHOLD = 0.01


def _get_mmr(leverage: int) -> float:
    if leverage <= 20:
        return _MMR_DEFAULT
    elif leverage <= 50:
        return _MMR_HIGH
    return _MMR_VERY_HIGH


def _estimate_liquidation_price(
    mark_price: float,
    leverage: int,
    side: str,  # "long" or "short"
) -> float:
    """Estimate liquidation price for a given mark price, leverage, and side.

    For longs: liq_price = mark_price * (1 - 1/leverage + MMR)
    For shorts: liq_price = mark_price * (1 + 1/leverage - MMR)
    """
    mmr = _get_mmr(leverage)
    if side == "long":
        return mark_price * (1.0 - 1.0 / leverage + mmr)
    else:
        return mark_price * (1.0 + 1.0 / leverage - mmr)


def _gaussian_kde(
    prices: list[float],
    weights: list[float],
    bandwidth: float,
    eval_points: list[float],
) -> list[float]:
    """Evaluate Gaussian KDE at given points.

    density(x) = sum(w_i * N(x | p_i, h)) / sum(w_i)
    """
    if not prices or not eval_points:
        return []
    total_weight = sum(weights)
    if total_weight == 0:
        return [0.0] * len(eval_points)

    result = []
    inv_2h2 = 1.0 / (2.0 * bandwidth * bandwidth)
    norm = 1.0 / (bandwidth * math.sqrt(2.0 * math.pi))

    for x in eval_points:
        density = 0.0
        for p, w in zip(prices, weights):
            diff = x - p
            density += w * norm * math.exp(-diff * diff * inv_2h2)
        result.append(density / total_weight)
    return result


class LiqMapKDEEngine:
    """Estimate and cluster liquidation prices from market data.

    Usage::

        engine = LiqMapKDEEngine()
        engine.on_result(my_callback)

        # Feed liquidation events and OI data
        engine.update(mark_price=62700.0, exchange="binance", symbol="BTC/USDT")
        result = engine.get_map("binance", "BTC/USDT", "all_leverage")
    """

    def __init__(
        self,
        leverage_tiers: dict[str, list[int]] | None = None,
        price_grid_bins: int = _PRICE_GRID_BINS,
    ) -> None:
        self._tiers = leverage_tiers or LEVERAGE_TIERS
        self._grid_bins = price_grid_bins
        self._callbacks: list[Callable[[LiqMapResult], Any]] = []

        # State per (exchange, symbol)
        # Stores: mark_price, long_oi_ratio, short_oi_ratio
        self._state: dict[tuple[str, str], dict[str, Any]] = {}

    def on_result(self, callback: Callable[[LiqMapResult], Any]) -> None:
        self._callbacks.append(callback)

    def update_mark_price(self, exchange: str, symbol: str, mark_price: float) -> None:
        key = (exchange, symbol)
        if key not in self._state:
            self._state[key] = {}
        self._state[key]["mark_price"] = mark_price

    def update_oi_ratio(
        self, exchange: str, symbol: str, long_ratio: float, short_ratio: float
    ) -> None:
        key = (exchange, symbol)
        if key not in self._state:
            self._state[key] = {}
        self._state[key]["long_ratio"] = long_ratio
        self._state[key]["short_ratio"] = short_ratio

    def compute_map(
        self,
        exchange: str,
        symbol: str,
        leverage_type: str = "all_leverage",
    ) -> LiqMapResult | None:
        """Compute LiqMap for a given leverage tier.

        Uses current mark_price and OI ratios to estimate where
        liquidation prices cluster across the leverage spectrum.
        """
        key = (exchange, symbol)
        state = self._state.get(key)
        if not state or "mark_price" not in state:
            return None

        mark_price = state["mark_price"]
        long_ratio = state.get("long_ratio", 0.5)
        short_ratio = state.get("short_ratio", 0.5)
        leverages = self._tiers.get(leverage_type)
        if not leverages:
            return None

        # Step 1: Estimate liquidation prices for each leverage
        long_liq_prices: list[float] = []
        long_weights: list[float] = []
        short_liq_prices: list[float] = []
        short_weights: list[float] = []

        for lev in leverages:
            # Weight inversely proportional to leverage (more OI at lower leverage)
            weight = 1.0 / lev

            # Long liquidation prices (below mark price)
            long_price = _estimate_liquidation_price(mark_price, lev, "long")
            if long_price > 0:
                long_liq_prices.append(long_price)
                long_weights.append(weight * long_ratio)

            # Short liquidation prices (above mark price)
            short_price = _estimate_liquidation_price(mark_price, lev, "short")
            if short_price > 0:
                short_liq_prices.append(short_price)
                short_weights.append(weight * short_ratio)

        if not long_liq_prices and not short_liq_prices:
            return None

        # Step 2: Build evaluation grid
        all_prices = long_liq_prices + short_liq_prices
        p_min = min(all_prices) * 0.995
        p_max = max(all_prices) * 1.005
        step = (p_max - p_min) / self._grid_bins
        eval_points = [p_min + i * step for i in range(self._grid_bins + 1)]

        # Step 3: KDE smoothing
        bandwidth = mark_price * _KDE_BANDWIDTH_FRAC
        long_density = _gaussian_kde(long_liq_prices, long_weights, bandwidth, eval_points)
        short_density = _gaussian_kde(short_liq_prices, short_weights, bandwidth, eval_points)

        # Step 4: Extract clusters (local maxima above threshold)
        long_clusters = self._extract_clusters(eval_points, long_density, "long")
        short_clusters = self._extract_clusters(eval_points, short_density, "short")

        # Step 5: Normalize densities
        max_density = max(
            (c.density for c in long_clusters + short_clusters),
            default=1.0,
        )
        if max_density > 0:
            for c in long_clusters + short_clusters:
                c.density = c.density / max_density

        # Filter by threshold
        all_clusters = [
            c for c in long_clusters + short_clusters
            if c.density >= _MIN_DENSITY_THRESHOLD
        ]
        # Sort by density descending
        all_clusters.sort(key=lambda c: c.density, reverse=True)

        now_ms = int(time.time() * 1000)
        result = LiqMapResult(
            exchange=exchange,
            symbol=symbol,
            leverage_type=leverage_type,
            mark_price=mark_price,
            clusters=all_clusters,
            long_total_density=sum(c.density for c in long_clusters if c.density >= _MIN_DENSITY_THRESHOLD),
            short_total_density=sum(c.density for c in short_clusters if c.density >= _MIN_DENSITY_THRESHOLD),
            timestamp=now_ms,
        )

        for cb in self._callbacks:
            try:
                cb(result)
            except Exception:
                logger.exception("Error in LiqMap result callback")

        return result

    def get_map(self, exchange: str, symbol: str, leverage_type: str = "all_leverage") -> LiqMapResult | None:
        return self.compute_map(exchange, symbol, leverage_type)

    def get_all_tiers(self, exchange: str, symbol: str) -> dict[str, LiqMapResult]:
        results: dict[str, LiqMapResult] = {}
        for tier in self._tiers:
            r = self.compute_map(exchange, symbol, tier)
            if r:
                results[tier] = r
        return results

    @staticmethod
    def _extract_clusters(
        prices: list[float],
        densities: list[float],
        side: str,
    ) -> list[LiqMapCluster]:
        """Find local maxima in the density curve."""
        clusters: list[LiqMapCluster] = []
        if len(densities) < 3:
            return clusters

        for i in range(1, len(densities) - 1):
            if densities[i] > densities[i - 1] and densities[i] > densities[i + 1]:
                clusters.append(LiqMapCluster(
                    price=prices[i],
                    density=densities[i],
                    side="neutral",
                    dominant_side=side,
                ))
        return clusters

    def compute_ratios(
        self,
        exchange: str,
        leverage_type: str = "all_leverage",
    ) -> list[LiqRatioItem]:
        """Compute LiqRatio across all tracked symbols.

        Returns a list of per-symbol long/short liquidation weight ratios,
        sorted by net_direction (most bearish first).
        """
        now_ms = int(time.time() * 1000)
        items: list[LiqRatioItem] = []

        for (ex, symbol), state in self._state.items():
            if ex != exchange or "mark_price" not in state:
                continue

            result = self.compute_map(ex, symbol, leverage_type)
            if result is None:
                continue

            items.append(LiqRatioItem(
                exchange=ex,
                symbol=symbol,
                leverage_type=leverage_type,
                long_liq_weight=result.long_total_density,
                short_liq_weight=result.short_total_density,
                net_direction=result.short_total_density - result.long_total_density,
                mark_price=result.mark_price,
                timestamp=now_ms,
            ))

        items.sort(key=lambda x: x.net_direction)
        return items
