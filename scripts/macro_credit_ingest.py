"""Credit/yield macro series ingest — FRED → macro_events.db.

Pulls BAMLH0A0HYM2 / DGS2 / DGS10 / T10Y2Y (~5y) via FredProvider and upserts
into credit_daily / yield_daily tables for EventGuard CreditSource (US-006).

Usage:
    python scripts/macro_credit_ingest.py [--db PATH] [--years 5]

DB path resolution (lockstep with services/local-api/app/main.py:1384, so
ingest writes the DB EventGuard actually reads):
SELENE_MACRO_DB env var wins; default is services/local-api/data/macro_events.db.
Do NOT copy the legacy default `data/macro_events.db` from guard.py:30 /
vix_source.py:57 — US-007 unified the canonical DB location.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "macrodata"))

FRED_KEY_URL = "https://fred.stlouisfed.org/docs/api/api_key.html"

CREDIT_SERIES = "BAMLH0A0HYM2"
YIELD_PAIRS: dict[str, str] = {
    "DGS2": "dgs2",
    "DGS10": "dgs10",
    "T10Y2Y": "t10y2y",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("macro_credit_ingest")


def resolve_db_path(explicit: str | None = None) -> Path:
    """--db > SELENE_MACRO_DB > canonical services path (main.py:1384 chain)."""
    if explicit:
        return Path(explicit)
    env = os.environ.get("SELENE_MACRO_DB")
    if env:
        return Path(env)
    return PROJECT_ROOT / "services" / "local-api" / "data" / "macro_events.db"


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS credit_daily (ts TEXT PRIMARY KEY, value REAL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS yield_daily"
        " (ts TEXT PRIMARY KEY, dgs2 REAL, dgs10 REAL, t10y2y REAL)"
    )
    conn.commit()


def upsert_credit(conn: sqlite3.Connection, rows: list[tuple[str, float]]) -> int:
    conn.executemany(
        "INSERT OR REPLACE INTO credit_daily (ts, value) VALUES (?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def upsert_yield(
    conn: sqlite3.Connection,
    by_ts: dict[str, dict[str, float | None]],
) -> int:
    conn.executemany(
        "INSERT OR REPLACE INTO yield_daily (ts, dgs2, dgs10, t10y2y) VALUES (?, ?, ?, ?)",
        [
            (ts, cols.get("dgs2"), cols.get("dgs10"), cols.get("t10y2y"))
            for ts, cols in sorted(by_ts.items())
        ],
    )
    conn.commit()
    return len(by_ts)


async def _fetch_obs(
    provider: object, series_id: str, start: datetime
) -> dict[str, float | None]:
    series = await provider.fetch_series(series_id, start=start)  # type: ignore[attr-defined]
    return {
        row.date.strftime("%Y-%m-%d"): row.value
        for row in series.observations.itertuples(index=False)
        if row.value is not None
    }


async def _run_ingest(db_path: Path, years: float) -> dict[str, object]:
    # Imported lazily so the no-key exit path works without the package on sys.path.
    from selene_space_macrodata.providers.fred import FredProvider
    from selene_space_macrodata.source import MacroDataSourceConfig

    provider = FredProvider(MacroDataSourceConfig(provider="fred"))
    await provider.start()
    start = datetime.now(UTC) - timedelta(days=365 * years)

    try:
        conn = sqlite3.connect(str(db_path))
        try:
            ensure_tables(conn)

            failures: list[str] = []
            per_series: dict[str, dict[str, float | None]] = {}
            for series_id in (CREDIT_SERIES, *YIELD_PAIRS):
                try:
                    per_series[series_id] = await _fetch_obs(provider, series_id, start)
                except Exception as exc:
                    logger.warning("Series %s failed: %s", series_id, exc)
                    failures.append(series_id)
                    per_series[series_id] = {}

            credit_rows = sorted(
                (ts, v) for ts, v in per_series[CREDIT_SERIES].items() if v is not None
            )
            yield_by_ts = {
                ts: {col: per_series[sid].get(ts) for sid, col in YIELD_PAIRS.items()}
                for ts in sorted({ts for sid in YIELD_PAIRS for ts in per_series[sid]})
            }

            n_credit = upsert_credit(conn, credit_rows)
            n_yield = upsert_yield(conn, yield_by_ts)
        finally:
            conn.close()
    finally:
        await provider.stop()

    return {"credit_daily": n_credit, "yield_daily": n_yield, "failures": failures}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="FRED credit/yield ingest into macro_events.db"
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Override DB path (default: SELENE_MACRO_DB or canonical path)",
    )
    parser.add_argument(
        "--years", type=float, default=5.0, help="History depth in years"
    )
    args = parser.parse_args(argv)

    if not os.environ.get("FRED_API_KEY"):
        print("FRED_API_KEY not set.", file=sys.stderr)
        print(
            f"Apply for a free key (instant approval): {FRED_KEY_URL}",
            file=sys.stderr,
        )
        print(
            "Then: set FRED_API_KEY=<key> (Windows) / export FRED_API_KEY=<key>",
            file=sys.stderr,
        )
        return 2

    db_path = resolve_db_path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    results = asyncio.run(_run_ingest(db_path, args.years))

    failures: list[str] = results["failures"]  # type: ignore[assignment]
    print("macro_credit_ingest summary:")
    print(f"  credit_daily rows: {results['credit_daily']}")
    print(f"  yield_daily rows: {results['yield_daily']}")
    print(f"  failed series: {failures or 'none'}")
    print(f"  DB: {db_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
