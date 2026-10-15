#!/usr/bin/env python
"""生成 K 线测试夹具 SQLite（替代被排除的 klines.db 二进制）。

与 services/local-api/tests/data/klines.db 相同 schema；默认生成少量合成行
（固定种子确定性），供测试断言读写行为。
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
from pathlib import Path

DEFAULT_OUT = (
    Path(__file__).resolve().parents[1]
    / "services"
    / "local-api"
    / "tests"
    / "data"
    / "klines.db"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS klines (
    exchange     TEXT    NOT NULL,
    market_type TEXT    NOT NULL,
    symbol      TEXT    NOT NULL,
    interval    TEXT    NOT NULL,
    timestamp   INTEGER NOT NULL,
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      REAL    NOT NULL,
    closed      INTEGER DEFAULT 0,
    PRIMARY KEY (exchange, market_type, symbol, interval, timestamp)
);
"""


def gen_rows(n: int) -> list[tuple]:
    rng = random.Random(42)
    rows = []
    ts0 = 1_700_000_000_000
    price = 60000.0
    for i in range(n):
        drift = rng.gauss(0, 0.004)
        o = price
        c = max(1000.0, o * (1 + drift))
        h = max(o, c) * (1 + abs(rng.gauss(0, 0.001)))
        low = min(o, c) * (1 - abs(rng.gauss(0, 0.001)))
        v = abs(rng.gauss(1000, 300))
        rows.append(
            (
                "binance",
                "futures",
                "BTC/USDT",
                "4h",
                ts0 + i * 4 * 3600_000,
                round(o, 2),
                round(h, 2),
                round(low, 2),
                round(c, 2),
                round(v, 2),
                1,
            )
        )
        price = c
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 klines 测试夹具")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--bars", type=int, default=100)
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        args.out.unlink()
    conn = sqlite3.connect(args.out)
    try:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?,?,?)", gen_rows(args.bars)
        )
        conn.commit()
    finally:
        conn.close()
    print(f"generated {args.bars} bars -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
