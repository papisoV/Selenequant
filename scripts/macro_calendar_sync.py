"""Macro event calendar sync script.

Fetches FOMC/CPI/NFP release dates from FRED API and VIX/DXY daily data
from yfinance (with FRED fallback), storing everything in macro_events.db.

Usage:
    python scripts/macro_calendar_sync.py --source jin10          # Jin10 only (no API key needed)
    python scripts/macro_calendar_sync.py                          # auto: Jin10 -> FRED/hardcoded fallback
    python scripts/macro_calendar_sync.py --source fred --api-key YOUR_KEY
    python scripts/macro_calendar_sync.py --proxy http://<host>:<port>
    python scripts/macro_calendar_sync.py --start-year 2024 --end-year 2026
    python scripts/macro_calendar_sync.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "alpha-core"))

from selene_space_alpha.macro.db import MacroEventDB
from selene_space_alpha.macro.fetcher import (
    MacroFetcher,
    _KNOWN_CPI_DATES,
    _KNOWN_FOMC_DATES,
    _KNOWN_NFP_DATES,
    _covered_by_jin10,
    _date_to_utc_ms,
)
from selene_space_alpha.macro.models import MacroEvent, MacroEventType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("macro_sync")

DEFAULT_DB_PATH = PROJECT_ROOT / "services" / "local-api" / "data" / "macro_events.db"
DEFAULT_START_YEAR = 2024
DEFAULT_END_YEAR = 2026

# Hardcoded release dates per event type (fetcher.py) — top-up source for the
# dates Jin10's free endpoint hasn't caught up to (it lags ~11 months).
_KNOWN_DATES: dict[MacroEventType, list[str]] = {
    MacroEventType.FOMC: _KNOWN_FOMC_DATES,
    MacroEventType.CPI: _KNOWN_CPI_DATES,
    MacroEventType.NFP: _KNOWN_NFP_DATES,
}
_RELEASE_TIMES: dict[str, str] = {
    MacroEventType.FOMC.value: "14:00",
    MacroEventType.CPI.value: "08:30",
    MacroEventType.NFP.value: "08:30",
}
_FRED_SERIES: dict[str, str] = {
    MacroEventType.FOMC.value: "FEDFUNDS",
    MacroEventType.CPI.value: "CPIAUCSL",
    MacroEventType.NFP.value: "PAYEMS",
}


class MacroCalendarSync:
    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB_PATH,
        fred_api_key: str = "",
        dry_run: bool = False,
        proxy_url: str | None = None,
        source: str = "auto",
    ) -> None:
        self._db = MacroEventDB(db_path)
        self._fetcher = MacroFetcher(fred_api_key=fred_api_key, proxy_url=proxy_url)
        self._dry_run = dry_run
        self._source = source

    async def sync_events(
        self, start_year: int, end_year: int
    ) -> dict[str, int]:
        """Sync FOMC/CPI/NFP events from the configured source."""
        results: dict[str, int] = {}
        start_date = f"{start_year}-01-01"
        end_date = f"{end_year}-12-31"

        for event_type in (MacroEventType.FOMC, MacroEventType.CPI, MacroEventType.NFP):
            if self._source == "fred":
                events = await self._fetcher.fetch_macro_events(
                    event_type, start_date, end_date
                )
            elif self._source == "jin10":
                from selene_space_alpha.macro.jin10_fetcher import Jin10Fetcher

                jin10 = Jin10Fetcher()
                try:
                    events = await jin10.fetch_macro_events(
                        event_type, start_date, end_date
                    )
                finally:
                    await jin10.close()
            else:  # auto: Jin10 first, FRED/hardcoded fallback
                events = await self._fetcher.fetch_macro_events_auto(
                    event_type, start_date, end_date
                )
                if events:
                    # Top up hardcoded dates Jin10 missed (same policy as
                    # quick_macro_sync). Jin10's free endpoint lags ~11 months, so
                    # without this the guard silently loses the 2026 release window.
                    # Fuzzy match: skip a hardcoded date within 1 day of a Jin10
                    # date (same release window) to avoid near-duplicate events.
                    jin10_dates = {e.date for e in events}
                    for d in _KNOWN_DATES[event_type]:
                        if _covered_by_jin10(d, jin10_dates):
                            continue
                        events.append(
                            MacroEvent(
                                event_type=event_type,
                                date=d,
                                release_time=_RELEASE_TIMES[event_type.value],
                                actual_value=None,
                                forecast_value=None,
                                previous_value=None,
                                surprise=None,
                                fred_series=_FRED_SERIES[event_type.value],
                                timestamp_utc=_date_to_utc_ms(
                                    d, _RELEASE_TIMES[event_type.value]
                                ),
                            )
                        )
            if not events:
                logger.warning("No %s events fetched", event_type.value)
                results[event_type.value] = 0
                continue

            if self._dry_run:
                logger.info(
                    "[DRY RUN] Would insert %d %s events",
                    len(events), event_type.value,
                )
                results[event_type.value] = len(events)
                continue

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
            n = self._db.replace_events(rows, (event_type.value,))
            results[event_type.value] = n
            logger.info("Synced %d %s events", n, event_type.value)

        return results

    async def sync_vix(self, start_year: int, end_year: int) -> int:
        """Sync VIX daily data."""
        start_date = f"{start_year}-01-01"
        end_date = f"{end_year}-12-31"

        rows_data = await self._fetcher.fetch_vix(start_date, end_date)
        if not rows_data:
            logger.warning("No VIX data fetched")
            return 0

        if self._dry_run:
            logger.info("[DRY RUN] Would insert %d VIX rows", len(rows_data))
            return len(rows_data)

        rows = [
            (v.date, v.open, v.high, v.low, v.close, v.timestamp)
            for v in rows_data
        ]
        n = self._db.put_vix(rows)
        logger.info("Synced %d VIX daily rows", n)
        return n

    async def sync_dxy(self, start_year: int, end_year: int) -> int:
        """Sync DXY daily data."""
        start_date = f"{start_year}-01-01"
        end_date = f"{end_year}-12-31"

        rows_data = await self._fetcher.fetch_dxy(start_date, end_date)
        if not rows_data:
            logger.warning("No DXY data fetched")
            return 0

        if self._dry_run:
            logger.info("[DRY RUN] Would insert %d DXY rows", len(rows_data))
            return len(rows_data)

        rows = [
            (d.date, d.close, d.timestamp)
            for d in rows_data
        ]
        n = self._db.put_dxy(rows)
        logger.info("Synced %d DXY daily rows", n)
        return n

    async def sync_all(self, start_year: int, end_year: int) -> dict[str, int]:
        """Run full sync: events + VIX + DXY."""
        self._db.open()
        try:
            event_results = await self.sync_events(start_year, end_year)
            vix_count = await self.sync_vix(start_year, end_year)
            dxy_count = await self.sync_dxy(start_year, end_year)

            results = {**event_results, "VIX": vix_count, "DXY": dxy_count}

            # Print summary
            print("\n" + "=" * 50)
            print("Macro Calendar Sync Summary")
            print("=" * 50)
            for key, count in results.items():
                print(f"  {key}: {count} rows")
            print(f"  DB: {self._db._db_path}")
            print("=" * 50)

            return results
        finally:
            self._db.close()
            await self._fetcher.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync macro event calendar from FRED/yfinance")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path")
    parser.add_argument("--api-key", default=None, help="FRED API key (or set FRED_API_KEY env)")
    parser.add_argument("--proxy", default=None, help="HTTP proxy URL (e.g. http://<host>:<port>)")
    parser.add_argument("--start-year", type=int, default=DEFAULT_START_YEAR)
    parser.add_argument("--end-year", type=int, default=DEFAULT_END_YEAR)
    parser.add_argument("--dry-run", action="store_true", help="Show what would be synced")
    parser.add_argument(
        "--source",
        choices=["auto", "jin10", "fred"],
        default="auto",
        help="Event source: auto=Jin10 with FRED/hardcoded fallback (default), jin10=Jin10 only, fred=hardcoded dates + FRED actuals (legacy)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    api_key = args.api_key or os.environ.get("FRED_API_KEY", "")
    if args.source == "fred" and not api_key:
        print("ERROR: --source fred requires a FRED API key. Use --api-key or set FRED_API_KEY env var.")
        print("Get a free key at: https://fred.stlouisfed.org/docs/api/api_key.html")
        sys.exit(1)

    proxy_url = args.proxy or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")

    sync = MacroCalendarSync(
        db_path=args.db,
        fred_api_key=api_key,
        dry_run=args.dry_run,
        proxy_url=proxy_url,
        source=args.source,
    )
    asyncio.run(sync.sync_all(args.start_year, args.end_year))


if __name__ == "__main__":
    main()
