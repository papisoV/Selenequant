"""VPVR engine — Volume Profile Visible Range.

Computes total volume at each price level across a configurable lookback
window, producing the classic Volume Profile with Point of Control (POC)
and Value Area (VA) high/low.
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

from selene_space_types.derivatives import VPVRData, VPVRLevel
from selene_space_types.market import TradeEvent

logger = logging.getLogger(__name__)

WINDOW_DEFS: dict[str, int] = {
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "24h": 24 * 60 * 60_000,
}

_MAX_TRADES = 100_000

# Default bucket sizes per quote currency
_DEFAULT_BUCKET_USD: dict[str, float] = {
    "USDT": 50.0,
    "USDC": 50.0,
    "USD": 50.0,
    "BTC": 0.001,
    "ETH": 0.01,
}

# Value Area contains 68% of total volume (1 standard deviation)
_VA_PERCENT = 0.68


class VPVREngine:
    """Compute Volume Profile from trade events.

    Usage::

        engine = VPVREngine()
        engine.on_update(my_callback)
        engine.process(trade_event)
        profile = engine.get_profile("BTC/USDT", window="1h")
    """

    def __init__(
        self,
        *,
        bucket_size: float | None = None,
        max_trades: int = _MAX_TRADES,
    ) -> None:
        self._bucket_size = bucket_size
        self._max_trades = max_trades
        # key = (exchange, symbol) → deque of TradeEvent
        self._trades: dict[tuple[str, str], deque[TradeEvent]] = {}
        self._cache: dict[str, VPVRData] = {}
        self._callbacks: list[Callable[[VPVRData], Any]] = []
        # P4-1 throttle: per-symbol last-notify timestamp (monotonic)
        self._last_notify: dict[tuple[str, str], float] = {}
        self._notify_interval_s: float = 1.0

    def on_update(self, callback: Callable[[VPVRData], Any]) -> None:
        self._callbacks.append(callback)

    def process(self, trade: TradeEvent) -> None:
        """Ingest a trade. Throttled recompute+notify (max once per second per symbol).

        P4-1: Previously called get_profile() on every trade → O(N) recompute
        blocked the event loop. Now appends O(1) and only recomputes if at
        least _notify_interval_s has passed since the last notify for this symbol.
        Callers wanting fresh data should call get_profile() directly (lazy).
        """
        key = (trade.exchange, trade.symbol)
        if key not in self._trades:
            self._trades[key] = deque(maxlen=self._max_trades)
        self._trades[key].append(trade)

        # Throttle: skip recompute+notify if we notified this symbol recently
        now = time.monotonic()
        last = self._last_notify.get(key, 0.0)
        if now - last < self._notify_interval_s:
            return

        # Recompute for default window and notify
        profile = self.get_profile(trade.symbol, exchange=trade.exchange, window="1h")
        if profile:
            cache_key = f"{trade.exchange}:{trade.symbol}:1h"
            self._cache[cache_key] = profile
            self._last_notify[key] = now
            for cb in self._callbacks:
                try:
                    cb(profile)
                except Exception:
                    logger.exception("Error in VPVR callback")

    def get_profile(
        self,
        symbol: str,
        *,
        exchange: str = "binance",
        window: str = "1h",
        bucket_size: float | None = None,
    ) -> VPVRData | None:
        """Compute volume profile for the given symbol and window."""
        key = (exchange, symbol)
        trades = self._trades.get(key)
        if not trades:
            return None

        now_ms = int(time.time() * 1000)
        window_ms = WINDOW_DEFS.get(window, WINDOW_DEFS["1h"])
        cutoff = now_ms - window_ms
        filtered = [t for t in trades if t.timestamp >= cutoff]
        if not filtered:
            return None

        bucket = bucket_size or self._bucket_size or self._infer_bucket(symbol, filtered)
        levels = self._compute_levels(filtered, bucket)
        if not levels:
            return None

        poc_price = self._compute_poc(levels)
        va_high, va_low = self._compute_value_area(levels, poc_price)

        total_vol = sum(l.total_volume for l in levels)
        total_buy = sum(l.buy_volume for l in levels)
        total_sell = sum(l.sell_volume for l in levels)

        return VPVRData(
            symbol=symbol,
            exchange=exchange,
            levels=levels,
            poc_price=poc_price,
            va_high=va_high,
            va_low=va_low,
            total_volume=total_vol,
            total_buy_volume=total_buy,
            total_sell_volume=total_sell,
            bucket_size=bucket,
            window=window,
            timestamp=now_ms,
        )

    def _infer_bucket(self, symbol: str, trades: list[TradeEvent]) -> float:
        """Infer bucket size from symbol and trade price range."""
        if "/" in symbol:
            quote = symbol.split("/")[1]
            base = _DEFAULT_BUCKET_USD.get(quote, 50.0)
            # Adjust based on price range: wider range → larger buckets
            prices = [t.price for t in trades]
            if prices:
                price_range = max(prices) - min(prices)
                # Target ~50-80 buckets across the range
                ideal_bucket = price_range / 60.0
                # Use the larger of base or ideal, rounded to nice number
                bucket = max(base, ideal_bucket)
                magnitude = 10 ** math.floor(math.log10(max(bucket, 1e-10)))
                return round(bucket / magnitude) * magnitude
            return base
        return 50.0

    def _compute_levels(self, trades: list[TradeEvent], bucket: float) -> list[VPVRLevel]:
        """Bin trades into price buckets and compute volume per level."""
        bucket_map: dict[int, dict[str, float]] = defaultdict(
            lambda: {"total": 0.0, "buy": 0.0, "sell": 0.0},
        )

        for t in trades:
            bk = int(t.price // bucket)
            vol = t.quantity
            bucket_map[bk]["total"] += vol
            if t.side == "buy":
                bucket_map[bk]["buy"] += vol
            else:
                bucket_map[bk]["sell"] += vol

        levels: list[VPVRLevel] = []
        for bk in sorted(bucket_map.keys()):
            d = bucket_map[bk]
            levels.append(VPVRLevel(
                price=(bk + 0.5) * bucket,
                total_volume=d["total"],
                buy_volume=d["buy"],
                sell_volume=d["sell"],
            ))

        return levels

    @staticmethod
    def _compute_poc(levels: list[VPVRLevel]) -> float:
        """Find Point of Control — price level with highest volume."""
        if not levels:
            return 0.0
        return max(levels, key=lambda l: l.total_volume).price

    @staticmethod
    def _compute_value_area(levels: list[VPVRLevel], poc_price: float) -> tuple[float, float]:
        """Compute Value Area high/low (68% of total volume around POC).

        Algorithm:
        1. Start from POC level
        2. Expand up/down alternately, adding the next highest volume level
        3. Stop when cumulative volume >= 68% of total
        """
        if not levels:
            return 0.0, 0.0

        total_vol = sum(l.total_volume for l in levels)
        target_vol = total_vol * _VA_PERCENT
        if total_vol == 0:
            return poc_price, poc_price

        # Find POC index
        poc_idx = 0
        for i, l in enumerate(levels):
            if l.price == poc_price:
                poc_idx = i
                break

        # Expand from POC
        va_levels = {poc_idx}
        va_vol = levels[poc_idx].total_volume
        up_idx = poc_idx + 1
        down_idx = poc_idx - 1

        while va_vol < target_vol and (up_idx < len(levels) or down_idx >= 0):
            up_vol = levels[up_idx].total_volume if up_idx < len(levels) else 0.0
            down_vol = levels[down_idx].total_volume if down_idx >= 0 else 0.0

            if up_vol >= down_vol and up_idx < len(levels):
                va_levels.add(up_idx)
                va_vol += up_vol
                up_idx += 1
            elif down_idx >= 0:
                va_levels.add(down_idx)
                va_vol += down_vol
                down_idx -= 1
            else:
                break

        va_prices = [levels[i].price for i in va_levels]
        return max(va_prices), min(va_prices)
