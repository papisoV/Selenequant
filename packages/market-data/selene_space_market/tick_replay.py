"""Tick data replay engine for historical market data simulation.

Supports loading ticks from files or DataFrames, chronological replay
with optional speed control, and aggregation into OHLCV klines.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Iterator

import pandas as pd


class TickType(Enum):
    """Classification of a market tick."""

    TRADE = "trade"
    BID_UPDATE = "bid_update"
    ASK_UPDATE = "ask_update"
    DEPTH_SNAPSHOT = "depth_snapshot"


@dataclass(frozen=True)
class Tick:
    """A single market event."""

    tick_type: TickType
    symbol: str
    price: float
    quantity: float
    timestamp_ms: int
    side: str | None = None  # "buy" / "sell" for trades
    extra: dict | None = None


class TickReplay:
    """Replay engine for historical tick data.

    Ticks are stored in memory and yielded in chronological order during
    replay.  An optional *speed* multiplier controls pacing: ``1.0``
    replays at real-time intervals, ``0`` yields instantly.
    """

    def __init__(self, ticks: list[Tick] | None = None) -> None:
        self._ticks: list[Tick] = sorted(
            ticks or [], key=lambda t: t.timestamp_ms
        )

    # -- loaders --------------------------------------------------------------

    def load_from_file(self, path: Path) -> None:
        """Load ticks from a JSON file containing a list of dicts."""
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        new_ticks = [_dict_to_tick(d) for d in raw]
        self._ticks = sorted(self._ticks + new_ticks, key=lambda t: t.timestamp_ms)

    def load_from_dataframe(self, df: pd.DataFrame) -> None:
        """Load ticks from a DataFrame.

        Expected columns: ``timestamp``, ``price``, ``quantity``, ``type``,
        ``side``.
        """
        new_ticks: list[Tick] = []
        for _, row in df.iterrows():
            tick_type = TickType(row["type"]) if isinstance(row["type"], str) else row["type"]
            new_ticks.append(
                Tick(
                    tick_type=tick_type,
                    symbol=str(row.get("symbol", "")),
                    price=float(row["price"]),
                    quantity=float(row["quantity"]),
                    timestamp_ms=int(row["timestamp"]),
                    side=row.get("side"),
                    extra=None,
                )
            )
        self._ticks = sorted(self._ticks + new_ticks, key=lambda t: t.timestamp_ms)

    # -- replay ---------------------------------------------------------------

    def replay(
        self,
        speed: float = 1.0,
        callback: Callable[[Tick], None] | None = None,
    ) -> Iterator[Tick]:
        """Yield ticks in chronological order.

        Args:
            speed: Multiplier for replay pacing.
                ``1.0`` = real-time, ``0`` = instant (no delay).
            callback: Optional function called with each tick.
        """
        import time

        prev_ts: int | None = None
        for tick in self._ticks:
            if speed > 0 and prev_ts is not None:
                delay = (tick.timestamp_ms - prev_ts) / 1000.0 / speed
                if delay > 0:
                    time.sleep(delay)
            prev_ts = tick.timestamp_ms
            if callback is not None:
                callback(tick)
            yield tick

    # -- aggregation ----------------------------------------------------------

    def aggregate_to_klines(self, interval_ms: int = 3600000) -> pd.DataFrame:
        """Aggregate TRADE ticks into OHLCV kline bars.

        Args:
            interval_ms: Bar interval in milliseconds (default 1 hour).

        Returns:
            DataFrame with columns: timestamp, open, high, low, close,
            volume.  Empty DataFrame if there are no TRADE ticks.
        """
        trade_ticks = [t for t in self._ticks if t.tick_type == TickType.TRADE]
        if not trade_ticks:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        records: list[dict] = []
        # Group ticks by interval bucket
        first_ts = trade_ticks[0].timestamp_ms
        bucket_start = first_ts - (first_ts % interval_ms)
        current_bucket = bucket_start
        bucket_ticks: list[Tick] = []

        for tick in trade_ticks:
            tick_bucket = tick.timestamp_ms - (tick.timestamp_ms % interval_ms)
            if tick_bucket != current_bucket:
                if bucket_ticks:
                    records.append(_ticks_to_bar(current_bucket, bucket_ticks))
                current_bucket = tick_bucket
                bucket_ticks = []
            bucket_ticks.append(tick)

        if bucket_ticks:
            records.append(_ticks_to_bar(current_bucket, bucket_ticks))

        return pd.DataFrame(records)

    # -- query ----------------------------------------------------------------

    def get_tick_count(self) -> int:
        """Return the number of loaded ticks."""
        return len(self._ticks)

    def get_time_range(self) -> tuple[int, int]:
        """Return ``(first_ts, last_ts)`` of loaded ticks.

        Returns ``(0, 0)`` if there are no ticks.
        """
        if not self._ticks:
            return (0, 0)
        return (self._ticks[0].timestamp_ms, self._ticks[-1].timestamp_ms)

    def filter_by_time(self, start_ms: int, end_ms: int) -> TickReplay:
        """Return a new :class:`TickReplay` with ticks in ``[start_ms, end_ms]``."""
        filtered = [
            t for t in self._ticks if start_ms <= t.timestamp_ms <= end_ms
        ]
        return TickReplay(filtered)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TICK_TYPE_MAP = {e.value: e for e in TickType}


def _dict_to_tick(d: dict) -> Tick:
    """Convert a raw dict to a :class:`Tick`."""
    raw_type = d.get("tick_type") or d.get("type", "trade")
    tick_type = _TICK_TYPE_MAP.get(raw_type, TickType.TRADE) if isinstance(raw_type, str) else raw_type
    return Tick(
        tick_type=tick_type,
        symbol=d.get("symbol", ""),
        price=float(d.get("price", 0)),
        quantity=float(d.get("quantity", 0)),
        timestamp_ms=int(d.get("timestamp_ms") or d.get("timestamp", 0)),
        side=d.get("side"),
        extra=d.get("extra"),
    )


def _ticks_to_bar(bucket_ts: int, ticks: list[Tick]) -> dict:
    """Aggregate a list of TRADE ticks into a single OHLCV bar dict."""
    prices = [t.price for t in ticks]
    volume = sum(t.quantity for t in ticks)
    return {
        "timestamp": bucket_ts,
        "open": prices[0],
        "high": max(prices),
        "low": min(prices),
        "close": prices[-1],
        "volume": volume,
    }
