"""Binance public-data archive → trade ticks (aggressor-labelled).

The repo can already *replay* ticks (:class:`selene_space_market.tick_store.
tick_replay.TickReplay`) and *store* them (:class:`~selene_space_market.
tick_store.tick_db.TickStore`), but nothing could get historical ticks off
the wire. This module is that link.

Why the archive rather than REST:

* ``/fapi/v1/aggTrades`` refuses any window older than 2 days
  (``-4166 Search window is restricted to recent 2 days only``).
* ``/fapi/v1/klines?interval=1s`` is a 400 (``-1120 Invalid interval``);
  the 1s series lives on ``continuousKlines`` and carries **no aggressor
  side and no trade count** — only OHLCV.

So aggressor-labelled history has exactly one source: the daily
``aggTrades`` ZIPs on ``data.binance.vision``. Verified present for
SOLUSDT futures UM from 2021-01-01, with a T+1 lag (the current day's file
is not published until the next day).

Aggressor convention (Binance's, not ours): the CSV column is
``is_buyer_maker``. When the **buyer was the maker**, the *seller*
aggressed — so ``is_buyer_maker == True`` means a **sell** aggressor. This
matches the ``side`` semantics stored by :class:`TickStore`.
"""

from __future__ import annotations

import csv
import io
import logging
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import date, timedelta
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)

ARCHIVE_BASE = "https://data.binance.vision/data"

# Binance market_type -> archive path segment. Spot "trades" ZIPs carry no
# aggressor flag, so only aggTrades is usable here.
_MARKET_PATH = {"futures": "futures/um", "spot": "spot"}

# aggTrades CSV column order (verified 2026-10-15 on the 2026-10-08 futures file)
_AGG_TRADES_COLUMNS = (
    "agg_trade_id",
    "price",
    "quantity",
    "first_trade_id",
    "last_trade_id",
    "transact_time",
    "is_buyer_maker",
)

_MARKET_DATA_HEADERS = {"User-Agent": "Mozilla/5.0 (selene-space market-data)"}


def archive_url(
    symbol: str,
    day: date,
    *,
    market_type: str = "futures",
    data_type: str = "aggTrades",
) -> str:
    """Daily archive URL for one symbol/day.

    ``symbol`` accepts either the platform form (``SOL/USDT``) or the wire
    form (``SOLUSDT``); both normalise to the same URL.
    """
    try:
        path = _MARKET_PATH[market_type]
    except KeyError:
        raise ValueError(f"unknown market_type {market_type!r}") from None
    wire = symbol.replace("/", "").upper()
    stamp = day.strftime("%Y-%m-%d")
    return (
        f"{ARCHIVE_BASE}/{path}/daily/{data_type}/{wire}/"
        f"{wire}-{data_type}-{stamp}.zip"
    )


def parse_agg_trades_csv(handle: io.TextIOBase) -> Iterator[dict[str, Any]]:
    """Yield trade dicts from an aggTrades CSV handle (header optional).

    A header row is detected by looking for the literal ``price`` column
    name; without one, positional order is assumed (§ `_AGG_TRADES_COLUMNS`).
    Rows that do not parse are skipped rather than aborting the day — a
    single malformed line should not cost the whole archive.
    """
    reader = csv.reader(handle)
    for row in reader:
        if not row or len(row) < 7:
            continue
        if row[1].strip().lower() == "price":  # header
            continue
        try:
            price = float(row[1])
            quantity = float(row[2])
            timestamp_ms = int(row[5])
            is_buyer_maker = row[6].strip().lower() == "true"
        except (ValueError, IndexError):
            continue
        yield {
            "price": price,
            "quantity": quantity,
            "timestamp_ms": timestamp_ms,
            # is_buyer_maker=True -> seller aggressed
            "side": "sell" if is_buyer_maker else "buy",
        }


def _default_opener(proxy_url: str | None) -> urllib.request.OpenerDirector:
    handlers: list[urllib.request.BaseHandler] = []
    if proxy_url:
        handlers.append(
            urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        )
    return urllib.request.build_opener(*handlers)


def download_day_zip(
    symbol: str,
    day: date,
    *,
    market_type: str = "futures",
    data_type: str = "aggTrades",
    proxy_url: str | None = None,
    timeout: float = 120.0,
) -> bytes:
    """Download one daily archive and return its raw ZIP bytes.

    Raises :class:`FileNotFoundError` when the archive is not published
    (future dates, or today's file before the T+1 publish).
    """
    url = archive_url(symbol, day, market_type=market_type, data_type=data_type)
    opener = _default_opener(proxy_url)
    request = urllib.request.Request(url, headers=_MARKET_DATA_HEADERS)
    try:
        with opener.open(request, timeout=timeout) as response:
            payload: bytes = response.read()
            return payload
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise FileNotFoundError(
                f"archive not published: {url} (daily files lag by one day)"
            ) from exc
        raise


def iter_day_trades(
    symbol: str,
    day: date,
    *,
    market_type: str = "futures",
    data_type: str = "aggTrades",
    proxy_url: str | None = None,
    timeout: float = 120.0,
) -> Iterator[dict[str, Any]]:
    """Stream one day's ticks without holding the whole day in memory.

    The whole ZIP is still buffered (the members are not seekable over
    HTTP), but parsing is streaming so a multi-hundred-MB day does not
    become a matching Python list.
    """
    payload = download_day_zip(
        symbol,
        day,
        market_type=market_type,
        data_type=data_type,
        proxy_url=proxy_url,
        timeout=timeout,
    )
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        member = next((n for n in archive.namelist() if n.endswith(".csv")), None)
        if member is None:
            raise ValueError(f"no CSV member inside {archive.namelist()!r}")
        with archive.open(member) as raw:
            yield from parse_agg_trades_csv(io.TextIOWrapper(raw, encoding="utf-8"))


def fetch_range_into_store(
    store: Any,  # noqa: ANN401 — TickStore (duck-typed to avoid an import cycle)
    symbol: str,
    start: date,
    end: date,
    *,
    market_type: str = "futures",
    proxy_url: str | None = None,
    on_day: Callable[[date, int], None] | None = None,
) -> int:
    """Download ``[start, end]`` inclusive and insert into a TickStore.

    ``on_day(day, inserted)`` is called after each day so a long backfill
    can report progress. Days whose archive is not yet published are
    skipped with a warning rather than aborting the range — a range whose
    tail is "today" is the common case.
    """
    total = 0
    day = start
    while day <= end:
        try:
            trades = list(
                iter_day_trades(
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
                "exchange": "binance",
                "symbol": symbol,
                "price": t["price"],
                "quantity": t["quantity"],
                "side": t["side"],
                "timestamp_ms": t["timestamp_ms"],
            }
            for t in trades
        ]
        inserted = store.insert_trades(batch) if batch else 0
        total += inserted
        if on_day is not None:
            on_day(day, inserted)
        day += timedelta(days=1)
    return total


__all__ = [
    "ARCHIVE_BASE",
    "archive_url",
    "download_day_zip",
    "fetch_range_into_store",
    "iter_day_trades",
    "parse_agg_trades_csv",
]
