"""CVD aggregator — Cumulative Volume Delta over fixed time intervals.

Computes buy/sell volume imbalance per interval (5m, 1h, 4h) from
TradeEvent streams. Emits CVDBarEvent when a bar closes or on demand.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from selene_space_types.derivatives import CVDBarEvent

logger = logging.getLogger(__name__)

INTERVAL_DEFS: dict[str, int] = {
    "5m": 5 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
}

# Key: (exchange, symbol, interval)
_BarKey = tuple[str, str, str]


class _BarAccumulator:
    """Accumulates trades for a single (exchange, symbol, interval) bar."""

    __slots__ = (
        "exchange", "symbol", "interval", "interval_ms",
        "buy_volume", "sell_volume", "buy_notional_usd", "sell_notional_usd",
        "trade_count", "bar_open_time", "bar_close_time",
    )

    def __init__(self, exchange: str, symbol: str, interval: str, interval_ms: int) -> None:
        self.exchange = exchange
        self.symbol = symbol
        self.interval = interval
        self.interval_ms = interval_ms
        self.buy_volume = 0.0
        self.sell_volume = 0.0
        self.buy_notional_usd = 0.0
        self.sell_notional_usd = 0.0
        self.trade_count = 0
        self.bar_open_time = 0
        self.bar_close_time = 0

    def reset(self, open_time: int) -> None:
        self.buy_volume = 0.0
        self.sell_volume = 0.0
        self.buy_notional_usd = 0.0
        self.sell_notional_usd = 0.0
        self.trade_count = 0
        self.bar_open_time = open_time
        self.bar_close_time = open_time + self.interval_ms

    def add_trade(self, side: str, quantity: float, notional_usd: float, timestamp: int) -> None:
        if side == "buy":
            self.buy_volume += quantity
            self.buy_notional_usd += notional_usd
        else:
            self.sell_volume += quantity
            self.sell_notional_usd += notional_usd
        self.trade_count += 1

    def to_event(self) -> CVDBarEvent:
        now_ms = int(time.time() * 1000)
        return CVDBarEvent(
            exchange=self.exchange,
            symbol=self.symbol,
            interval=self.interval,
            buy_volume=self.buy_volume,
            sell_volume=self.sell_volume,
            buy_notional_usd=self.buy_notional_usd,
            sell_notional_usd=self.sell_notional_usd,
            cvd=self.buy_volume - self.sell_volume,
            cvd_usd=self.buy_notional_usd - self.sell_notional_usd,
            trade_count=self.trade_count,
            bar_open_time=self.bar_open_time,
            bar_close_time=self.bar_close_time,
            timestamp=now_ms,
        )


class CVDAggregator:
    """Aggregate trade events into CVD bars per interval.

    Usage::

        agg = CVDAggregator()
        agg.on_bar_close(my_callback)
        agg.process_trade(exchange, symbol, side, quantity, notional_usd, timestamp)
        bar = agg.get_current_bar("binance", "BTC/USDT", "5m")
    """

    def __init__(self, intervals: list[str] | None = None) -> None:
        self._intervals = intervals or list(INTERVAL_DEFS.keys())
        self._bars: dict[_BarKey, _BarAccumulator] = {}
        self._callbacks: list[Callable[[CVDBarEvent], Any]] = []

    def on_bar_close(self, callback: Callable[[CVDBarEvent], Any]) -> None:
        self._callbacks.append(callback)

    def process_trade(
        self,
        exchange: str,
        symbol: str,
        side: str,
        quantity: float,
        notional_usd: float,
        timestamp: int,
    ) -> list[CVDBarEvent]:
        """Ingest a trade and return any closed bar events."""
        closed_bars: list[CVDBarEvent] = []

        for interval in self._intervals:
            interval_ms = INTERVAL_DEFS[interval]
            key = (exchange, symbol, interval)

            bar = self._bars.get(key)
            bar_open = self._compute_bar_open(timestamp, interval_ms)

            if bar is None:
                bar = _BarAccumulator(exchange, symbol, interval, interval_ms)
                bar.reset(bar_open)
                self._bars[key] = bar
            elif bar_open != bar.bar_open_time:
                # Bar boundary crossed — close current bar, start new one
                closed_bars.append(bar.to_event())
                bar.reset(bar_open)

            bar.add_trade(side, quantity, notional_usd, timestamp)

        for closed in closed_bars:
            for cb in self._callbacks:
                try:
                    cb(closed)
                except Exception:
                    logger.exception("Error in CVD bar close callback")

        return closed_bars

    def get_current_bar(self, exchange: str, symbol: str, interval: str) -> CVDBarEvent | None:
        bar = self._bars.get((exchange, symbol, interval))
        return bar.to_event() if bar else None

    def get_all_current_bars(self, exchange: str, symbol: str) -> dict[str, CVDBarEvent]:
        result: dict[str, CVDBarEvent] = {}
        for interval in self._intervals:
            bar = self._bars.get((exchange, symbol, interval))
            if bar:
                result[interval] = bar.to_event()
        return result

    def flush_all(self) -> list[CVDBarEvent]:
        """Force-close all open bars (e.g. on shutdown)."""
        events = []
        for bar in self._bars.values():
            if bar.trade_count > 0:
                events.append(bar.to_event())
        self._bars.clear()
        return events

    @staticmethod
    def _compute_bar_open(timestamp: int, interval_ms: int) -> int:
        return (timestamp // interval_ms) * interval_ms
