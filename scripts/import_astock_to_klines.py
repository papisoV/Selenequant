"""Import external-research-project A-share feather files into selene's klines.db.

One-off bridge so selene's formula mining (run_alpha_mining_sharpe.py)
can train on A-share daily bars. Schema matches real_fitness._load_kline_events
expectations: klines(timestamp, exchange, symbol, interval, open, high, low,
close, volume) with ms-epoch timestamps.

Usage:
  python scripts/import_astock_to_klines.py \
    --external-research-project-root <local-path-redacted> \
    --symbols 600519.SH 000001.SZ
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "services" / "local-api" / "data" / "klines.db"


def import_one(conn: sqlite3.Connection, feather_path: Path, exchange: str) -> int:
    symbol = feather_path.name.split("-")[0]
    df = pd.read_feather(feather_path).reset_index()
    df.columns = ["date", "open", "high", "low", "close", "volume"]
    total = len(df)
    # qfq side effect: cumulative dividends can push early bars negative;
    # KlineEvent greater_than validation rejects them, so mining/backtest
    # would silently lose those bars. Drop the negative prefix here.
    neg = (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
    if neg.any():
        dropped = int(neg.sum())
        df = df[~neg].reset_index(drop=True)
        print(
            f"  [qfq] {symbol}: dropped {dropped}/{total} bars with non-positive prices",
            file=sys.stderr,
        )
    df["timestamp"] = df["date"].astype("datetime64[ms]").astype("int64")
    df["exchange"] = exchange
    df["symbol"] = symbol
    df["interval"] = "1d"
    df["market_type"] = "astock"
    df["closed"] = 1
    rows = df[
        [
            "exchange",
            "market_type",
            "symbol",
            "interval",
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "closed",
        ]
    ].values.tolist()

    conn.execute(
        "DELETE FROM klines WHERE exchange=? AND symbol=? AND interval='1d'",
        (exchange, symbol),
    )
    conn.executemany(
        "INSERT INTO klines (exchange, market_type, symbol, interval, timestamp,"
        "open, high, low, close, volume, closed) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--external-research-project-root", required=True, type=Path)
    parser.add_argument("--symbols", nargs="+")
    parser.add_argument(
        "--symbols-file", type=Path, help="text file with one symbol per line"
    )
    parser.add_argument("--exchange", default="astock")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()

    symbols = list(args.symbols or [])
    if args.symbols_file:
        symbols += [
            s.strip()
            for s in args.symbols_file.read_text(encoding="utf-8").splitlines()
            if s.strip()
        ]
    if not symbols:
        parser.error("either --symbols or --symbols-file is required")

    conn = sqlite3.connect(str(args.db))
    try:
        for sym in symbols:
            feather = (
                args.external_research_project_root
                / "data"
                / "astock"
                / "astock"
                / f"{sym}-1d-astock.feather"
            )
            if not feather.exists():
                print(f"[skip] {sym}: feather not found", file=sys.stderr)
                continue
            n = import_one(conn, feather, args.exchange)
            print(f"[ok] {sym}: {n} bars -> {args.db.name}")
        conn.commit()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
