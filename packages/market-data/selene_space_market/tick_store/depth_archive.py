"""Binance public-data archive → order-book depth snapshots.

The repo could already replay ticks and store trade ticks, and
:mod:`selene_space_market.tick_store.binance_archive` links the *trade*
archive to :class:`~selene_space_market.tick_store.tick_db.TickStore`.
Depth had no such link: every aggregator under
``selene_space_market/aggregators`` (``large_order``, ``tof``, ``cvd``, ...)
consumes a live ``@depth`` WebSocket feed, so nothing could backfill them.

That backfill exists on ``data.binance.vision`` as the daily ``bookDepth``
ZIPs. Verified present for SOLUSDT futures UM from 2023-01-01 with a T+1
publish lag.

What the archive contains — and what it does not:

* one row per (snapshot, band); 12 bands at ±0.20 %, ±1 %, ±2 %, ±3 %,
  ±4 %, ±5 %;
* ``depth`` is the **cumulative** resting quantity inside that band, so the
  ±5 % row is a superset of every tighter band on the same side;
* ``notional`` is the quote-currency value of that same resting size;
* snapshots arrive roughly every 30 s (2880 per day).

It is *not* order-by-order L2: no individual price level is recorded, so
"how thick is the wall at 102.50" cannot be read directly — only "how much
size rests within X % of the mid". Timestamps are UTC and carry no offset.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterator

from selene_space_market.tick_store.binance_archive import (
    download_day_zip as _download_day_zip,
)
from selene_space_market.tick_store.binance_archive import (
    archive_url as _base_archive_url,
)

logger = logging.getLogger(__name__)

DATA_TYPE = "bookDepth"

# CSV column order (verified 2026-10-15 on the 2026-10-12 futures file).
_DEPTH_COLUMNS = ("timestamp", "percentage", "depth", "notional")

# Archive timestamps are naive UTC ("2026-10-12 00:00:01").
_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def archive_url(
    symbol: str,
    day: date,
    *,
    market_type: str = "futures",
) -> str:
    """Daily ``bookDepth`` archive URL for one symbol/day.

    ``symbol`` accepts the platform form (``SOL/USDT``) or the wire form
    (``SOLUSDT``); both normalise to the same URL.
    """
    return _base_archive_url(symbol, day, market_type=market_type, data_type=DATA_TYPE)


def parse_book_depth_csv(handle: io.TextIOBase) -> Iterator[dict[str, Any]]:
    """Yield depth rows from a ``bookDepth`` CSV handle (header optional).

    Each row is one band of one snapshot, shaped as
    ``{"timestamp_ms", "percentage", "depth", "notional"}``. ``percentage``
    stays signed: negative is the bid side, positive the ask side.

    A header row is detected by the literal ``timestamp`` column name. Rows
    that do not parse are skipped rather than aborting the day — one
    malformed line should not cost the whole archive.
    """
    reader = csv.reader(handle)
    for row in reader:
        if not row or len(row) < 4:
            continue
        if row[0].strip().lower() == "timestamp":  # header
            continue
        try:
            stamp = datetime.strptime(row[0].strip(), _TS_FORMAT)
            percentage = float(row[1])
            depth = float(row[2])
            notional = float(row[3])
        except (ValueError, IndexError):
            continue
        yield {
            "timestamp_ms": int(stamp.replace(tzinfo=timezone.utc).timestamp() * 1000),
            "percentage": percentage,
            "depth": depth,
            "notional": notional,
        }


def iter_day_depth(
    symbol: str,
    day: date,
    *,
    market_type: str = "futures",
    proxy_url: str | None = None,
    timeout: float = 120.0,
) -> Iterator[dict[str, Any]]:
    """Stream one day's depth rows without holding the day as a Python list."""
    payload = _download_day_zip(
        symbol,
        day,
        market_type=market_type,
        data_type=DATA_TYPE,
        proxy_url=proxy_url,
        timeout=timeout,
    )
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        member = next((n for n in archive.namelist() if n.endswith(".csv")), None)
        if member is None:
            raise ValueError(f"no CSV member inside {archive.namelist()!r}")
        with archive.open(member) as raw:
            yield from parse_book_depth_csv(io.TextIOWrapper(raw, encoding="utf-8"))


def fetch_range_into_store(
    store: Any,  # noqa: ANN401 — DepthStore (duck-typed to avoid an import cycle)
    symbol: str,
    start: date,
    end: date,
    *,
    exchange: str = "binance",
    market_type: str = "futures",
    proxy_url: str | None = None,
    on_day: Callable[[date, int], None] | None = None,
) -> int:
    """Download ``[start, end]`` inclusive and insert into a DepthStore.

    ``on_day(day, inserted)`` runs after each day so a long backfill can
    report progress. Days whose archive is not yet published are skipped
    with a warning rather than aborting the range — a range whose tail is
    "today" is the common case.
    """
    total = 0
    day = start
    while day <= end:
        try:
            rows = list(
                iter_day_depth(
                    symbol, day, market_type=market_type, proxy_url=proxy_url
                )
            )
        except FileNotFoundError as exc:
            logger.warning("skip %s: %s", day, exc)
            if on_day is not None:
                on_day(day, 0)
            day += timedelta(days=1)
            continue

        batch = [
            {
                "exchange": exchange,
                "symbol": symbol,
                "timestamp_ms": r["timestamp_ms"],
                "percentage": r["percentage"],
                "depth": r["depth"],
                "notional": r["notional"],
            }
            for r in rows
        ]
        inserted = store.insert_depth(batch) if batch else 0
        total += inserted
        if on_day is not None:
            on_day(day, inserted)
        day += timedelta(days=1)
    return total


__all__ = [
    "DATA_TYPE",
    "archive_url",
    "fetch_range_into_store",
    "iter_day_depth",
    "parse_book_depth_csv",
]
