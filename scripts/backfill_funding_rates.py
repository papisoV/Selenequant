"""Backfill binance funding-rate history into data/derivatives.db.

One-shot backfill (rerunnable): /fapi/v1/fundingRate has NO startTime
walk-forward (a low startTime returns only the newest page), so pagination
walks BACKWARD from now via endTime until the API returns an empty page
(symbol listing date). Upserts through the existing DerivativesDB.
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
import sys

for _p in (
    "packages/core",
    "packages/shared-types",
    "packages/backtest-engine",
    "packages/market-data",
    "packages/exchange-sdk",
    "packages/paper-engine",
    "packages/strategy-engine",
    "packages/llm-adapter",
    "packages/agent-runtime",
):
    sys.path.insert(0, str(PROJECT_ROOT / _p))

from selene_space_market.derivatives_db import DerivativesDB

DB_PATH = PROJECT_ROOT / "data" / "derivatives.db"
SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT")
PAGE_LIMIT = 1000  # API caps at 500
PAGE_SLEEP_S = 0.25


def backfill_symbol(db: DerivativesDB, client: httpx.Client, symbol: str) -> int:
    exchange = "binance"
    wire = symbol.replace("/", "")
    end = int(time.time() * 1000)
    written = 0
    while True:
        resp = client.get(
            "https://fapi.binance.com/fapi/v1/fundingRate",
            params={"symbol": wire, "endTime": end, "limit": PAGE_LIMIT},
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            break
        db.put_funding_rates(
            [
                (
                    exchange,
                    symbol,
                    int(r["fundingTime"]),
                    float(r["fundingRate"]),
                    float(r.get("markPrice") or 0.0),
                    0.0,
                    0,
                )
                for r in rows
            ]
        )
        written += len(rows)
        oldest = min(int(r["fundingTime"]) for r in rows)
        if len(rows) < 500:
            break  # reached the listing date
        end = oldest
        time.sleep(PAGE_SLEEP_S)
    return written


def main() -> None:
    db = DerivativesDB(str(DB_PATH))
    db.open()
    with httpx.Client(timeout=30.0) as client:
        for symbol in SYMBOLS:
            n = backfill_symbol(db, client, symbol)
            total = db.count("funding_rates", "binance", symbol)
            print(f"{symbol}: +{n} rows (total {total})")
    db.close()


if __name__ == "__main__":
    main()
