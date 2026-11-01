#!/usr/bin/env python3
"""Download Binance 1h klines (BTC/USDT, ETH/USDT) or write deterministic synthetic Parquet.

Public REST API — no API key. Falls back to synthetic data if the API is unreachable.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

BINANCE_KLINES = "https://api.binance.com/api/v3/klines"
INTERVAL = "1h"
MS_PER_HOUR = 3_600_000
N_BARS_90D = 90 * 24  # 2160

# One request may carry at most this many candles.
_PAGE_LIMIT = 1000
# Columns as they appear in Binance's kline array payload.
_KLINE_FIELDS = (
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "_ignore",
    "quote_volume",
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate demo kline Parquet files for SeleneQuant."
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=Path("demo/data"),
        help="Directory for Parquet output (default: demo/data)",
    )
    return p.parse_args()


def _synthetic_ohlcv(
    *,
    base_price: float,
    n_bars: int,
    seed: int,
    start_ts_ms: int | None = None,
) -> pd.DataFrame:
    """Deterministic sine + drift + noise (used when API fails)."""
    rng = np.random.default_rng(seed)
    if start_ts_ms is None:
        start_ts_ms = int(time.time() * 1000) - n_bars * MS_PER_HOUR

    steps = np.arange(n_bars, dtype=np.float64)
    drift = 0.00008 * steps
    weekly = 0.02 * np.sin(2 * math.pi * steps / 168.0)  # ~1 week cycle
    walk = rng.normal(0.0, 0.004, size=n_bars).cumsum() * 0.0005
    close = np.exp(np.log(base_price) + drift + weekly + walk)

    # Open of bar N is the previous close; the first bar opens a hair below.
    open_ = np.empty(n_bars)
    open_[1:] = close[:-1]
    open_[0] = close[0] * 0.999

    # Independent wick widths, then push high/low out from the body.
    wick = rng.uniform(0.001, 0.008, size=n_bars)
    high = np.maximum(open_, close) * (1.0 + wick)
    low = np.minimum(open_, close) * (1.0 - wick)
    volume = rng.uniform(20.0, 800.0, size=n_bars)

    return pd.DataFrame(
        {
            "timestamp": start_ts_ms + np.arange(n_bars, dtype=np.int64) * MS_PER_HOUR,
            "open": np.round(open_, 8),
            "high": np.round(high, 8),
            "low": np.round(low, 8),
            "close": np.round(close, 8),
            "volume": np.round(volume, 8),
            "quote_volume": np.round(volume * close, 8),
        }
    )


def _klines_to_frame(rows: list) -> pd.DataFrame:
    """Binance kline array rows → DataFrame with required columns."""
    # Field order is fixed by the exchange API; index 6 (ignore) is unused.
    wanted = [i for i, name in enumerate(_KLINE_FIELDS) if name != "_ignore"]
    names = [_KLINE_FIELDS[i] for i in wanted]
    return pd.DataFrame(
        [
            {
                name: (int(row[i]) if name == "timestamp" else float(row[i]))
                for name, i in zip(names, wanted)
            }
            for row in rows
        ]
    )


def _fetch_symbol(
    client: httpx.Client,
    symbol: str,
    display_pair: str,
    start_ms: int,
    end_ms: int,
) -> pd.DataFrame:
    """Paginate Binance klines (max 1000 per request)."""
    rows: list = []
    cursor = start_ms
    total_bars = max(1, (end_ms - start_ms) // MS_PER_HOUR)
    pages = max(1, int(math.ceil(total_bars / float(_PAGE_LIMIT))))
    seen = 0

    while cursor < end_ms:
        seen += 1
        print(f"Downloading {display_pair} 1h klines... page {seen}/{pages}")

        response = client.get(
            BINANCE_KLINES,
            params={
                "symbol": symbol,
                "interval": INTERVAL,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": _PAGE_LIMIT,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break

        rows.extend(batch)
        cursor = int(batch[-1][0]) + MS_PER_HOUR
        if len(batch) < _PAGE_LIMIT:
            break

    if not rows:
        raise RuntimeError("No kline rows returned")
    return _klines_to_frame(rows)


def _download_or_synthetic(
    client: httpx.Client,
    *,
    symbol: str,
    display_pair: str,
    parquet_name: str,
    out_dir: Path,
    synthetic_base: float,
    synthetic_seed: int,
) -> None:
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - N_BARS_90D * MS_PER_HOUR

    try:
        frame = _fetch_symbol(client, symbol, display_pair, start_ms, end_ms)
        if len(frame) < N_BARS_90D // 2:
            raise RuntimeError("Incomplete kline download")
    except Exception as exc:
        print(
            f"Binance fetch failed ({exc!r}); writing synthetic data for {display_pair}."
        )
        frame = _synthetic_ohlcv(
            base_price=synthetic_base, n_bars=N_BARS_90D, seed=synthetic_seed
        )

    path = out_dir / parquet_name
    frame.to_parquet(path, engine="pyarrow", index=False)
    print(f"Wrote {len(frame)} rows to {path}")


def main() -> int:
    args = _parse_args()
    out_dir: Path = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    jobs = (
        ("BTCUSDT", "BTC/USDT", "btc_usdt_1h_90d.parquet", 40_000.0, 101),
        ("ETHUSDT", "ETH/USDT", "eth_usdt_1h_90d.parquet", 2500.0, 202),
    )

    client = httpx.Client()
    try:
        for symbol, pair, name, base, seed in jobs:
            _download_or_synthetic(
                client,
                symbol=symbol,
                display_pair=pair,
                parquet_name=name,
                out_dir=out_dir,
                synthetic_base=base,
                synthetic_seed=seed,
            )
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
