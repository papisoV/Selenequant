"""Factor backtest — event-study on tg_factors.db × klines.db.

Quantifies whether (event_type, magnitude>=threshold) factors predict
forward returns at T+1h/4h/24h. Compares against baseline (random
non-event timestamps, fixed seed for reproducibility).

Output: JSON file at data/factor_backtest_results/ + console summary.

Usage:
    python scripts/factor_backtest.py --symbol BTCUSDT --event-type SECURITY --min-magnitude 0.7
    python scripts/factor_backtest.py --horizons 1 4 24 --baseline-samples 200
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import bisect

logger = logging.getLogger(__name__)

_DEFAULT_FACTORS_DB = "internal-archive"
_DEFAULT_KLINES_DB = "data/klines.db"
_RESULTS_DIR = "data/factor_backtest_results"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Event-study backtest on tg_factors × klines")
    p.add_argument("--symbol", default="BTCUSDT", help="e.g. BTCUSDT (default)")
    p.add_argument("--exchange", default="binance")
    p.add_argument("--market-type", default="spot")
    p.add_argument("--interval", default="1h")
    p.add_argument("--event-type", default="SECURITY", help="Filter: SECURITY/REGULATORY/WHALE/macro/other")
    p.add_argument("--min-magnitude", type=float, default=0.7, help="Minimum magnitude threshold")
    p.add_argument("--days", type=int, default=60, help="Lookback days (default 60, max 180)")
    p.add_argument("--horizons", type=int, nargs="+", default=[1, 4, 24], help="Forward-return horizons in hours")
    p.add_argument("--baseline-samples", type=int, default=200, help="Random baseline sample count")
    p.add_argument("--seed", type=int, default=42, help="RNG seed for baseline sampling (fixed for reproducibility)")
    p.add_argument("--factors-db", default=_DEFAULT_FACTORS_DB)
    p.add_argument("--klines-db", default=_DEFAULT_KLINES_DB)
    p.add_argument("--output-dir", default=_RESULTS_DIR)
    return p.parse_args()


def load_factors(db_path: str, event_type: str, min_magnitude: float, days: int) -> list[dict]:
    """Load factors filtered by event_type + magnitude>=threshold, last N days."""
    if not Path(db_path).exists():
        logger.warning("factors DB not found: %s", db_path)
        return []
    now_ms = int(time.time() * 1000)
    since_ms = now_ms - days * 86_400_000
    since_iso = datetime.fromtimestamp(since_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db_path)
    sql = (
        "SELECT ts, event_type, magnitude, urgency, direction, symbols "
        "FROM tg_factors WHERE event_type = ? AND magnitude >= ? AND ts >= ? "
        "ORDER BY ts ASC"
    )
    rows = conn.execute(sql, [event_type, min_magnitude, since_iso]).fetchall()
    conn.close()
    fields = ["ts", "event_type", "magnitude", "urgency", "direction", "symbols"]
    return [dict(zip(fields, r)) for r in rows]


def load_klines(db_path: str, exchange: str, market_type: str, symbol: str, interval: str, days: int) -> list[tuple[int, float]] | None:
    """Load klines via KlineDB data-access layer."""
    try:
        from selene_space_market.kline_db import KlineDB
        import pandas as pd
        now_ms = int(time.time() * 1000)
        since_ms = now_ms - days * 86_400_000
        since_dt = pd.Timestamp(since_ms, unit="ms")
        end_dt = pd.Timestamp(now_ms, unit="ms")
        kdb = KlineDB(db_path)
        df = kdb.get_klines(exchange, market_type, symbol, interval, since_dt, end_dt)
        if df is None or df.empty:
            return None
        return [(int(ts), float(c)) for ts, c in zip(df["timestamp"], df["close"])]
    except Exception as e:
        logger.warning("load_klines failed: %s", e)
        return None


def ts_to_ms(ts_str: str) -> int:
    """Parse ISO8601 text ts from tg_factors into milliseconds."""
    try:
        dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def forward_return(klines: list[tuple[int, float]], event_ts_ms: int, horizon_h: int) -> float | None:
    """Compute forward return at T+horizon_h. Max-gap guard: reject if gap > 2× horizon."""
    timestamps = [k[0] for k in klines]
    idx = bisect.bisect_left(timestamps, event_ts_ms)
    if idx >= len(klines):
        return None
    entry = klines[idx][1]
    target_ms = event_ts_ms + horizon_h * 3_600_000
    end_idx = bisect.bisect_left(timestamps, target_ms)
    if end_idx >= len(klines):
        return None
    actual_gap = klines[end_idx][0] - target_ms
    if actual_gap > 2 * horizon_h * 3_600_000:
        return None
    exit_price = klines[end_idx][1]
    if entry == 0:
        return None
    return (exit_price - entry) / entry


def sample_baseline_timestamps(
    kline_ts: list[int], event_ts_list: list[int], n: int, seed: int, horizon_h_max: int
) -> list[int]:
    """Sample N random kline timestamps, excluding windows around any event (±24h)."""
    rng = random.Random(seed)
    if len(kline_ts) < 2:
        return []
    window_ms = 24 * 3_600_000
    candidates = list(range(len(kline_ts) - 1))  # index-based, leave room for horizon
    rng.shuffle(candidates)
    out: list[int] = []
    for c in candidates:
        ts = kline_ts[c]
        too_close = False
        for ev_ts in event_ts_list:
            if abs(ts - ev_ts) < window_ms:
                too_close = True
                break
        if not too_close:
            out.append(ts)
            if len(out) >= n:
                break
    return out


def stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": None, "median": None, "std": None, "min": None, "max": None}
    n = len(values)
    mean = sum(values) / n
    sorted_v = sorted(values)
    median = sorted_v[n // 2] if n % 2 == 1 else (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2
    variance = sum((v - mean) ** 2 for v in values) / n if n > 0 else 0.0
    std = variance ** 0.5
    return {
        "n": n,
        "mean": mean,
        "median": median,
        "std": std,
        "min": min(values),
        "max": max(values),
    }


def t_test(event_vals: list[float], baseline_vals: list[float]) -> dict:
    """Welch's t-test (unequal variance). Returns t, p_value, df."""
    try:
        from scipy import stats as sp_stats
        if not event_vals or not baseline_vals:
            return {"t": None, "p_value": None, "df": None}
        t, p = sp_stats.ttest_ind(event_vals, baseline_vals, equal_var=False)
        return {
            "t": float(t) if t == t else None,  # NaN check
            "p_value": float(p) if p == p else None,
            "df": None,
        }
    except ImportError:
        return {"t": None, "p_value": None, "df": None, "note": "scipy not installed"}


def run_backtest(args: argparse.Namespace) -> dict:
    # tg_factors stores event_type as lowercase (e.g. 'market', 'security')
    event_type_lower = args.event_type.lower()
    factors = load_factors(args.factors_db, event_type_lower, args.min_magnitude, args.days)
    klines = load_klines(args.klines_db, args.exchange, args.market_type, args.symbol, args.interval, args.days)

    if not factors:
        return {"error": f"No factors match event_type={event_type_lower} magnitude>={args.min_magnitude} in last {args.days}d"}
    if klines is None or len(klines) < 2:
        return {"error": f"No klines for {args.exchange}/{args.market_type}/{args.symbol} {args.interval} in last {args.days}d"}

    event_ts_ms_list = [ts_to_ms(f["ts"]) for f in factors]

    event_returns: dict[int, list[float]] = {h: [] for h in args.horizons}
    for ts_ms in event_ts_ms_list:
        for h in args.horizons:
            r = forward_return(klines, ts_ms, h)
            if r is not None:
                event_returns[h].append(r)

    kline_ts = [k[0] for k in klines]
    baseline_ts = sample_baseline_timestamps(
        kline_ts, event_ts_ms_list, args.baseline_samples, args.seed, max(args.horizons)
    )
    baseline_returns: dict[int, list[float]] = {h: [] for h in args.horizons}
    for ts_ms in baseline_ts:
        for h in args.horizons:
            r = forward_return(klines, ts_ms, h)
            if r is not None:
                baseline_returns[h].append(r)

    result: dict[str, Any] = {
        "config": {
            "symbol": args.symbol,
            "exchange": args.exchange,
            "market_type": args.market_type,
            "interval": args.interval,
            "event_type": event_type_lower,
            "min_magnitude": args.min_magnitude,
            "days": args.days,
            "horizons": args.horizons,
            "baseline_samples": args.baseline_samples,
            "seed": args.seed,
        },
        "event_count": len(factors),
        "baseline_count": len(baseline_ts),
        "horizons": {},
    }

    for h in args.horizons:
        ev_stats = stats(event_returns[h])
        bl_stats = stats(baseline_returns[h])
        tt = t_test(event_returns[h], baseline_returns[h])
        result["horizons"][h] = {
            "event": ev_stats,
            "baseline": bl_stats,
            "t_test": tt,
            "delta_mean": (ev_stats["mean"] - bl_stats["mean"]) if ev_stats["mean"] is not None and bl_stats["mean"] is not None else None,
        }

    return result


def write_summary(result: dict) -> str:
    """Return console-friendly summary string."""
    if "error" in result:
        return f"ERROR: {result['error']}"

    cfg = result["config"]
    lines = [
        f"Factor backtest: {cfg['event_type']} mag>={cfg['min_magnitude']} → {cfg['symbol']} {cfg['interval']}",
        f"events: n={result['event_count']} | baseline: n={result['baseline_count']} (seed={cfg['seed']})",
        "",
    ]
    for h in cfg["horizons"]:
        h_data = result["horizons"][h]
        ev = h_data["event"]
        bl = h_data["baseline"]
        delta = h_data["delta_mean"]
        tt = h_data["t_test"]
        ev_mean = f"{ev['mean']:+.4f}" if ev["mean"] is not None else "N/A"
        bl_mean = f"{bl['mean']:+.4f}" if bl["mean"] is not None else "N/A"
        delta_str = f"{delta:+.4f}" if delta is not None else "N/A"
        t_str = f"t={tt['t']:+.2f}" if tt.get("t") is not None else "t=N/A"
        p_str = f"p={tt['p_value']:.4f}" if tt.get("p_value") is not None else "p=N/A"
        lines.append(
            f"T+{h}h: event avg={ev_mean} (n={ev['n']}) | baseline avg={bl_mean} (n={bl['n']}) | "
            f"Δ={delta_str} | {t_str} {p_str}"
        )
    return "\n".join(lines)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = parse_args()
    args.days = min(args.days, 180)

    logger.info("Running backtest: %s %s mag>=%s last %dd",
                args.event_type, args.symbol, args.min_magnitude, args.days)
    result = run_backtest(args)

    summary = write_summary(result)
    print(summary)
    print()

    if "error" in result:
        return 1

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    # Sanitize symbol (BTC/USDT → BTCUSDT) for filesystem-safe filename
    safe_symbol = args.symbol.replace("/", "")
    out_path = out_dir / f"{ts}_{args.event_type.lower()}_mag{args.min_magnitude}_{safe_symbol}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)
    print(f"Results written to: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
