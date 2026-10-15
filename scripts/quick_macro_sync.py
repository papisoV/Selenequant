"""Quick macro data sync — no FRED API key required.

Uses Jin10 datacenter API (key-less) for CPI/NFP release dates + forecast +
actual (real surprise), falling back to hardcoded FOMC/CPI/NFP release dates
(from fetcher.py) when Jin10 is unavailable. VIX/DXY come from yfinance.
Actual values may be NULL for pending releases; EventGuard only needs dates
and release times to compute alert windows.

Usage:
    python scripts/quick_macro_sync.py
    python scripts/quick_macro_sync.py --proxy http://<host>:<port>
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "alpha-core"))

from selene_space_alpha.macro.db import MacroEventDB
from selene_space_alpha.macro.fetcher import (
    MacroFetcher,
    _KNOWN_FOMC_DATES,
    _KNOWN_CPI_DATES,
    _KNOWN_NFP_DATES,
    _covered_by_jin10,
    _date_to_utc_ms,
)
from selene_space_alpha.macro.models import MacroEventType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("quick_macro_sync")

DEFAULT_DB_PATH = PROJECT_ROOT / "services" / "local-api" / "data" / "macro_events.db"

# Release times per event type
_RELEASE_TIMES = {
    "FOMC": "14:00",
    "CPI": "08:30",
    "NFP": "08:30",
}

_FRED_SERIES = {
    "FOMC": "FEDFUNDS",
    "CPI": "CPIAUCSL",
    "NFP": "PAYEMS",
}


async def _sync_events(db: MacroEventDB, fetcher: MacroFetcher) -> dict[str, int]:
    """Sync FOMC/CPI/NFP events via Jin10-first fetch (hardcoded fallback).

    Jin10 is authoritative where it has data (real dates + real forecast). Any
    hardcoded date NOT covered by Jin10 is still inserted as a fallback row so
    the event guard never silently loses a window (e.g. Jin10 free endpoint
    lags months; its data may not cover the most recent releases). FOMC now has
    a Jin10 attr_id (24); Jin10's +1-day Beijing dates are corrected to US-Eastern
    inside Jin10Fetcher, and hardcoded dates top up whatever Jin10 misses.
    """
    counts: dict[str, int] = {}
    known_dates_by_type = {
        MacroEventType.FOMC: _KNOWN_FOMC_DATES,
        MacroEventType.CPI: _KNOWN_CPI_DATES,
        MacroEventType.NFP: _KNOWN_NFP_DATES,
    }
    for event_type, known_dates in known_dates_by_type.items():
        events = await fetcher.fetch_macro_events_auto(
            event_type, "2024-01-01", "2026-12-31"
        )

        release_time = _RELEASE_TIMES[event_type.value]
        fred_series = _FRED_SERIES[event_type.value]

        def _hardcoded_row(d: str) -> tuple:
            return (
                event_type.value,
                d,
                release_time,
                None,  # actual_value
                None,  # forecast_value
                None,  # previous_value
                None,  # surprise
                fred_series,
                _date_to_utc_ms(d, release_time),
            )

        if not events:
            # No Jin10 at all: insert every hardcoded date (forecast None).
            rows = [_hardcoded_row(d) for d in known_dates]
            n = db.replace_events(rows, (event_type.value,))
            counts[event_type.value] = n
            logger.info("Inserted %d %s event dates (hardcoded fallback)", n, event_type.value)
            continue

        # Jin10-first: write its rows, then top up any hardcoded date it missed.
        rows = [
            (
                e.event_type.value,
                e.date,
                e.release_time,
                e.actual_value,
                e.forecast_value,
                e.previous_value,
                e.surprise,
                e.fred_series,
                e.timestamp_utc,
            )
            for e in events
        ]
        jin10_dates = {e.date for e in events}
        missing = [d for d in known_dates if not _covered_by_jin10(d, jin10_dates)]
        rows += [_hardcoded_row(d) for d in missing]
        n = db.replace_events(rows, (event_type.value,))
        counts[event_type.value] = n
        logger.info(
            "Inserted %d %s events (%d Jin10 + %d hardcoded top-up)",
            n, event_type.value, len(events), len(missing),
        )
    return counts


async def _sync_vix_dxy(db: MacroEventDB, proxy_url: str | None) -> dict[str, int]:
    """Sync VIX and DXY via yfinance."""
    fetcher = MacroFetcher(fred_api_key="", proxy_url=proxy_url)
    results: dict[str, int] = {}

    # VIX
    vix_rows = await fetcher.fetch_vix("2024-01-01", "2026-12-31")
    if vix_rows:
        tuples = [(v.date, v.open, v.high, v.low, v.close, v.timestamp) for v in vix_rows]
        n = db.put_vix(tuples)
        results["VIX"] = n
        logger.info("Synced %d VIX daily rows", n)
    else:
        results["VIX"] = 0
        logger.warning("No VIX data fetched")

    # DXY
    dxy_rows = await fetcher.fetch_dxy("2024-01-01", "2026-12-31")
    if dxy_rows:
        tuples = [(d.date, d.close, d.timestamp) for d in dxy_rows]
        n = db.put_dxy(tuples)
        results["DXY"] = n
        logger.info("Synced %d DXY daily rows", n)
    else:
        results["DXY"] = 0
        logger.warning("No DXY data fetched (known intermittent issue)")

    await fetcher.close()
    return results


async def main() -> None:
    parser = argparse.ArgumentParser(description="Quick macro sync (no FRED key needed)")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path")
    parser.add_argument("--proxy", default=None, help="HTTP proxy URL")
    args = parser.parse_args()

    db = MacroEventDB(args.db)
    db.open()
    fetcher = MacroFetcher(fred_api_key="", proxy_url=args.proxy)

    try:
        event_counts = await _sync_events(db, fetcher)
        market_counts = await _sync_vix_dxy(db, args.proxy)

        print("\n" + "=" * 50)
        print("Quick Macro Sync Summary")
        print("=" * 50)
        for key, count in {**event_counts, **market_counts}.items():
            print(f"  {key}: {count} rows")
        print(f"  DB: {db._db_path}")
        print("=" * 50)
    finally:
        db.close()
        await fetcher.close()


if __name__ == "__main__":
    asyncio.run(main())
