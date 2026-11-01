"""Footprint engine — bins trades by price within kline intervals to produce
buy/sell volume distribution (footprint chart data).

Each bar tracks volume at every price level where a trade occurred, split by
taker side (buy/sell). This is the core data for footprint/cluster charts.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

from selene_space_types.derivatives import FootprintBarEvent, FootprintLevel
from selene_space_types.market import TradeEvent

logger = logging.getLogger(__name__)

_INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}

_MAX_TRADES = 50_000


class FootprintEngine:
    """Build footprint bars from trade events.

    Usage::

        engine = FootprintEngine()
        engine.on_bar(my_callback)
        engine.process(trade_event)
        bar = engine.get_bar("BTC/USDT", "5m")
    """

    def __init__(
        self,
        *,
        default_interval: str = "5m",
        max_trades: int = _MAX_TRADES,
    ) -> None:
        self._default_interval = default_interval
        self._max_trades = max_trades
        # key = (exchange, symbol, interval) → deque of TradeEvent
        self._trades: dict[tuple[str, str, str], deque[TradeEvent]] = {}
        # key = (exchange, symbol, interval) → latest completed FootprintBarEvent
        self._latest_bar: dict[tuple[str, str, str], FootprintBarEvent] = {}
        self._callbacks: list[Callable[[FootprintBarEvent], Any]] = []

    def on_bar(self, callback: Callable[[FootprintBarEvent], Any]) -> None:
        self._callbacks.append(callback)

    def process(self, trade: TradeEvent) -> None:
        """Ingest a trade and check if the current bar should close."""
        key = (trade.exchange, trade.symbol, self._default_interval)
        if key not in self._trades:
            self._trades[key] = deque(maxlen=self._max_trades)
        self._trades[key].append(trade)

        interval_ms = _INTERVAL_MS.get(self._default_interval, 300_000)
        bar_open = (trade.timestamp // interval_ms) * interval_ms
        bar_close = bar_open + interval_ms

        # If trade is in a new bar, emit the previous bar
        prev_bar = self._latest_bar.get(key)
        if prev_bar is not None and trade.timestamp >= prev_bar.bar_close_time:
            # Build the completed bar with the old bar's time range
            completed_bar = self._build_bar(key, prev_bar.bar_open_time, prev_bar.bar_close_time)
            if completed_bar is not None:
                for cb in self._callbacks:
                    try:
                        cb(completed_bar)
                    except Exception:
                        logger.exception("Error in Footprint callback")

        # Update current bar in-progress
        current_bar = self._build_bar(key, bar_open, bar_close)
        if current_bar is not None:
            self._latest_bar[key] = current_bar

    def get_bar(
        self,
        symbol: str,
        interval: str = "5m",
        exchange: str = "binance",
    ) -> FootprintBarEvent | None:
        """Return the latest (possibly in-progress) footprint bar."""
        return self._latest_bar.get((exchange, symbol, interval))

    def get_historical_bars(
        self,
        symbol: str,
        interval: str = "5m",
        exchange: str = "binance",
        limit: int = 50,
    ) -> list[FootprintBarEvent]:
        """Build footprint bars from buffered trades for recent completed intervals."""
        key = (exchange, symbol, interval)
        trades = self._trades.get(key)
        if not trades:
            return []

        interval_ms = _INTERVAL_MS.get(interval, 300_000)
        now_ms = int(time.time() * 1000)
        current_bar_open = (now_ms // interval_ms) * interval_ms

        # Group trades by bar
        bars_map: dict[int, list[TradeEvent]] = defaultdict(list)
        for t in trades:
            bar_open = (t.timestamp // interval_ms) * interval_ms
            if bar_open < current_bar_open:
                bars_map[bar_open].append(t)

        # Build bars, most recent first
        bars: list[FootprintBarEvent] = []
        for bar_open in sorted(bars_map.keys(), reverse=True)[:limit]:
            bar_close = bar_open + interval_ms
            bar = self._build_bar_from_trades(
                list(bars_map[bar_open]), exchange, symbol, interval, bar_open, bar_close,
            )
            if bar is not None:
                bars.append(bar)

        return sorted(bars, key=lambda b: b.bar_open_time)

    def _build_bar(
        self,
        key: tuple[str, str, str],
        bar_open: int,
        bar_close: int,
    ) -> FootprintBarEvent | None:
        """Build a footprint bar from buffered trades in the given time range."""
        trades = self._trades.get(key)
        if not trades:
            return None

        exchange, symbol, interval = key
        filtered = [t for t in trades if bar_open <= t.timestamp < bar_close]
        if not filtered:
            return None

        return self._build_bar_from_trades(filtered, exchange, symbol, interval, bar_open, bar_close)

    @staticmethod
    def _build_bar_from_trades(
        trades: list[TradeEvent],
        exchange: str,
        symbol: str,
        interval: str,
        bar_open: int,
        bar_close: int,
    ) -> FootprintBarEvent | None:
        """Build a FootprintBarEvent from a list of trades."""
        if not trades:
            return None

        # Bin by price level
        level_map: dict[float, dict[str, float]] = defaultdict(
            lambda: {"buy_vol": 0.0, "sell_vol": 0.0, "buy_usd": 0.0, "sell_usd": 0.0, "count": 0},
        )
        total_buy_vol = 0.0
        total_sell_vol = 0.0
        total_buy_usd = 0.0
        total_sell_usd = 0.0
        high_price = 0.0
        low_price = float("inf")

        for t in trades:
            price = t.price
            vol = t.quantity
            usd = vol * price
            side_key = "buy" if t.side == "buy" else "sell"

            level_map[price][f"{side_key}_vol"] += vol
            level_map[price][f"{side_key}_usd"] += usd
            level_map[price]["count"] += 1

            if side_key == "buy":
                total_buy_vol += vol
                total_buy_usd += usd
            else:
                total_sell_vol += vol
                total_sell_usd += usd

            if price > high_price:
                high_price = price
            if price < low_price:
                low_price = price

        if low_price == float("inf"):
            low_price = 0.0

        levels = [
            FootprintLevel(
                price=p,
                buy_volume=d["buy_vol"],
                sell_volume=d["sell_vol"],
                buy_notional_usd=d["buy_usd"],
                sell_notional_usd=d["sell_usd"],
                trade_count=int(d["count"]),
            )
            for p, d in sorted(level_map.items())
        ]

        return FootprintBarEvent(
            exchange=exchange,
            symbol=symbol,
            interval=interval,
            levels=levels,
            total_buy_volume=total_buy_vol,
            total_sell_volume=total_sell_vol,
            total_buy_notional_usd=total_buy_usd,
            total_sell_notional_usd=total_sell_usd,
            delta=total_buy_vol - total_sell_vol,
            high_price=high_price,
            low_price=low_price,
            bar_open_time=bar_open,
            bar_close_time=bar_close,
            timestamp=int(time.time() * 1000),
        )
