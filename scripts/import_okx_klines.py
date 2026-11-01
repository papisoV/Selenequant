"""Import OKX parquet K-line data into the local SQLite klines database.

Reads parquet files from ``data/OKX_K线数据.zip`` (or an extracted directory),
maps columns to the SeleneQuant schema, and bulk-inserts via ``KlineDB``.

Usage::

    call activate.bat
    python scripts/import_okx_klines.py
    python scripts/import_okx_klines.py --interval D1       # daily only
    python scripts/import_okx_klines.py --dry-run            # preview only
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import zipfile
from pathlib import Path

import pandas as pd

# Project root (scripts/ is one level down)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selene_space_market.kline_db import KlineDB
from selene_space_types.market import KlineEvent

BATCH_SIZE = 10_000
ZIP_PATH = Path("data/OKX_K线数据.zip")

_SYMBOL_SLASH = {
    "ADAUSDT": "ADA/USDT",
    "ATOMUSDT": "ATOM/USDT",
    "AVAXUSDT": "AVAX/USDT",
    "BCHUSDT": "BCH/USDT",
    "BNBUSDT": "BNB/USDT",
    "BTCUSDT": "BTC/USDT",
    "DOGEUSDT": "DOGE/USDT",
    "DOTUSDT": "DOT/USDT",
    "ETHUSDT": "ETH/USDT",
    "HBARUSDT": "HBAR/USDT",
    "LINKUSDT": "LINK/USDT",
    "LTCUSDT": "LTC/USDT",
    "NEARUSDT": "NEAR/USDT",
    "POLUSDT": "POL/USDT",
    "SHIBUSDT": "SHIB/USDT",
    "SOLUSDT": "SOL/USDT",
    "UNIUSDT": "UNI/USDT",
    "XLMUSDT": "XLM/USDT",
    "XRPUSDT": "XRP/USDT",
}

_FILE_RE = re.compile(r"OKX_K线数据/(\w+)_(D1|H1|M15|M5)\.parquet")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Import OKX parquet K-lines into SQLite")
    p.add_argument("--interval", choices=["D1", "H1", "M15", "M5"], help="Import only one interval")
    p.add_argument("--dry-run", action="store_true", help="Preview without writing")
    p.add_argument("--db", default="data/klines.db", help="Target SQLite database path")
    return p.parse_args()


def import_parquet_to_klines(
    df: pd.DataFrame,
    symbol: str,
    interval: str,
    db: KlineDB,
    *,
    dry_run: bool = False,
) -> int:
    """Convert a parquet DataFrame to KlineEvent list and insert into DB."""
    klines: list[KlineEvent] = []
    total = 0

    for row in df.itertuples(index=False):
        ts = int(row.time)
        if ts < 1e12:  # seconds → milliseconds
            ts *= 1000

        klines.append(
            KlineEvent(
                exchange="okx",
                market_type="futures",
                symbol=symbol,
                timestamp=ts,
                interval=interval,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(row.tick_volume),
                closed=True,
            )
        )

        if len(klines) >= BATCH_SIZE:
            if not dry_run:
                db.put_klines(klines)
            total += len(klines)
            klines.clear()

    # Flush remaining
    if klines:
        if not dry_run:
            db.put_klines(klines)
        total += len(klines)

    return total


def main() -> None:
    args = parse_args()
    zip_path = Path(args.db).parent.parent / ZIP_PATH if not ZIP_PATH.exists() else ZIP_PATH

    if not zip_path.exists():
        sys.exit(f"ZIP not found: {zip_path}")

    db = KlineDB(db_path=args.db)
    if not args.dry_run:
        db.open()

    mode = "DRY-RUN" if args.dry_run else "IMPORT"
    print(f"=== {mode}: {zip_path} → {args.db} ===")

    with zipfile.ZipFile(zip_path) as zf:
        files = [
            f for f in zf.namelist()
            if not f.endswith("/") and (m := _FILE_RE.match(f))
        ]
        # Filter by interval if requested
        files = [
            f for f in files
            if not args.interval or _FILE_RE.match(f).group(2) == args.interval
        ]

        grand_total = 0
        for fname in sorted(files):
            m = _FILE_RE.match(fname)
            symbol_raw = m.group(1)
            interval = m.group(2)
            symbol = _SYMBOL_SLASH.get(symbol_raw, symbol_raw)

            buf = io.BytesIO(zf.read(fname))
            df = pd.read_parquet(buf)
            n = import_parquet_to_klines(df, symbol, interval, db, dry_run=args.dry_run)
            grand_total += n
            print(f"  {symbol:12s} {interval:4s}  {len(df):>8,} rows → {n:>8,} inserted")

        print(f"  {'─' * 42}")
        print(f"  {'TOTAL':12s} {'':4s}  {'':>8}   {grand_total:>8,} rows")

    if not args.dry_run:
        db.close()
        print("Done. Verify with:")
        print(f"  python -c \"import sqlite3; c=sqlite3.connect('{args.db}'); "
              "print(c.execute('SELECT exchange, symbol, interval, COUNT(*) FROM klines GROUP BY 1,2,3 ORDER BY 2,3').fetchall())\"")


if __name__ == "__main__":
    main()
