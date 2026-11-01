"""Export the frozen 300-bar BTC/USDT 30m fixture for parity verification (US-2).

Reads from the selene klines DB and writes a JSON fixture consumed by BOTH:
  - the original QuantDinger GridEngine (driven offline, no HTTP), and
  - the selene port (selene_space_grid),
so the two engines can be compared bar-for-bar on identical data.

Usage:
    python scripts/export_grid_fixture.py            # default 300 bars
    python scripts/export_grid_fixture.py --bars 300 --force
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

DEFAULT_DB = os.path.join(PROJECT_ROOT, "services", "local-api", "data", "klines.db")
DEFAULT_OUT = os.path.join(
    PROJECT_ROOT,
    "packages",
    "grid-engine",
    "tests",
    "fixtures",
    "grid_fixture_btc_300.json",
)

EXCHANGE = "binance"
MARKET_TYPE = "futures"
SYMBOL = "BTC/USDT"
INTERVAL = "30m"


def export(bars: int, db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """SELECT timestamp, open, high, low, close, volume, closed
               FROM klines
               WHERE exchange=? AND market_type=? AND symbol=? AND interval=?
               ORDER BY timestamp DESC LIMIT ?""",
            (EXCHANGE, MARKET_TYPE, SYMBOL, INTERVAL, bars),
        ).fetchall()
    finally:
        conn.close()
    if len(rows) < bars:
        raise SystemExit(
            f"klines.db has only {len(rows)} rows for "
            f"{EXCHANGE}/{MARKET_TYPE}/{SYMBOL}/{INTERVAL}, need {bars}"
        )
    rows.reverse()  # chronological order
    records = [
        {
            "time": int(ts),
            "open": float(o),
            "high": float(h),
            "low": float(lo),
            "close": float(c),
            "volume": float(v),
            "closed": bool(cl),
        }
        for ts, o, h, lo, c, v, cl in rows
    ]
    return {
        "meta": {
            "exchange": EXCHANGE,
            "market_type": MARKET_TYPE,
            "symbol": SYMBOL,
            "interval": INTERVAL,
            "bars": len(records),
            "first_ts": records[0]["time"],
            "last_ts": records[-1]["time"],
            "source_db": "services/local-api/data/klines.db",
        },
        "bars": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export frozen grid parity fixture")
    parser.add_argument("--bars", type=int, default=300)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing fixture (frozen by default)",
    )
    args = parser.parse_args()

    if os.path.exists(args.out) and not args.force:
        print(f"fixture already frozen at {args.out}; pass --force to re-export")
        return 0
    payload = export(args.bars, args.db)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    meta = payload["meta"]
    print(
        f"froze {meta['bars']} bars: {SYMBOL} {INTERVAL} futures "
        f"[{meta['first_ts']} .. {meta['last_ts']}] -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
