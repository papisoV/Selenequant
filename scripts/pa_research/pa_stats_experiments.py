"""PA statistics experiments over selene's own klines.db.

Runs the pa_stats_lib functions over real OHLCV bars and commits the
resulting JSON (versioned, pinned in git) plus a findings markdown.

Cleanroom note: the statistical questions come from Al Brooks' published
methodology (2009/2011). Parameter grids are project-chosen constants
documented alongside each result; every number here is computed from
selene's own data. No external implementation text or code is reproduced
(see PROVENANCE.md in this directory).

Usage:
    python scripts/pa_research/pa_stats_experiments.py
    python scripts/pa_research/pa_stats_experiments.py --market-type spot
    python scripts/pa_research/pa_stats_experiments.py --interval 4h
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from pa_research.pa_stats_lib import (  # noqa: E402
    atr_series,
    breakout_events,
    direction_edge,
    mm_hit,
    spike_counter,
    spike_reversal_rate,
    survival_count,
    swing_detect,
    wilson_ci,
)

LEGACY_KLINES_DB = Path("<local-path-redacted>")

RESULTS_DIR = REPO_ROOT / "results"
STATS_VERSION = "2026-09-10v1"

# Parameter grid — project-chosen constants (not copied from any external
# implementation; chosen for 1h crypto bars per Brooks' event-window habits).
P_SWING_K = 3
P_BREAKOUT_ATR_MULT = 0.15
P_SURVIVAL_WINDOWS = (3, 8)
P_MM_WINDOW = 8
P_MM_ATR_TOLERANCE = 0.5
P_SPIKE_MIN_BARS = 3
P_SPIKE_RANGE_MULT = 1.5
P_SPIKE_REVERSAL_WINDOW = 8
P_SPIKE_ATR_MOVE = 1.0
P_ATR_PERIOD = 14

DEGRADED_MIN_N = 100


def resolve_klines_db() -> Path:
    """Same resolution order as scripts/run_pa_diagnosis.py:48-56."""
    env = os.environ.get("SELENE_KLINES_DB")
    if env:
        return Path(env)
    repo_candidate = REPO_ROOT / "data" / "klines.db"
    if repo_candidate.is_file():
        return repo_candidate
    return LEGACY_KLINES_DB


def load_bars(
    db_path: Path, symbol: str, market_type: str, interval: str, max_bars: int | None
) -> list[dict]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            """
            SELECT timestamp, open, high, low, close, volume
            FROM klines
            WHERE exchange = 'binance'
              AND market_type = ?
              AND symbol = ?
              AND interval = ?
              AND closed = 1
            ORDER BY timestamp ASC
            """,
            (market_type, symbol, interval),
        ).fetchall()
    finally:
        con.close()
    if max_bars is not None:
        rows = rows[-max_bars:]
    return [
        {
            "timestamp": r[0],
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5]),
        }
        for r in rows
    ]


def rate_block(successes: int, total: int) -> dict:
    lo, hi = wilson_ci(successes, total)
    return {
        "successes": successes,
        "total": total,
        "rate": round(successes / total, 4) if total else None,
        "ci95": [round(lo, 4), round(hi, 4)] if total else None,
        "degraded": total < DEGRADED_MIN_N,
    }


def run_experiments(bars: list[dict]) -> dict:
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    opens = [b["open"] for b in bars]
    atrs = atr_series(highs, lows, closes, period=P_ATR_PERIOD)

    swings = swing_detect(highs, lows, k=P_SWING_K)
    events = breakout_events(
        highs, lows, closes, atrs, swings, atr_mult=P_BREAKOUT_ATR_MULT
    )

    swing_breakout: dict = {
        "params": {"swing_k": P_SWING_K, "breakout_atr_mult": P_BREAKOUT_ATR_MULT},
        "swing_points": len(swings),
        "breakout_events": len(events),
        "survival": {},
    }
    for w in P_SURVIVAL_WINDOWS:
        surv, evald = survival_count(closes, events, window=w)
        swing_breakout["survival"][f"window_{w}"] = rate_block(surv, evald)

    mm = mm_hit(
        highs,
        lows,
        closes,
        events,
        window=P_MM_WINDOW,
        atrs=atrs,
        atr_tolerance=P_MM_ATR_TOLERANCE,
    )
    measured_move = {
        "params": {
            "mm_window": P_MM_WINDOW,
            "mm_atr_tolerance": P_MM_ATR_TOLERANCE,
        },
        "hits": rate_block(mm.hits, mm.evaluated),
    }

    spikes = spike_counter(
        opens,
        highs,
        lows,
        closes,
        atrs,
        min_bars=P_SPIKE_MIN_BARS,
        range_mult=P_SPIKE_RANGE_MULT,
    )
    rev, evald = spike_reversal_rate(
        closes, atrs, spikes, window=P_SPIKE_REVERSAL_WINDOW, atr_move=P_SPIKE_ATR_MOVE
    )
    spike_reversal = {
        "params": {
            "min_bars": P_SPIKE_MIN_BARS,
            "range_mult": P_SPIKE_RANGE_MULT,
            "window": P_SPIKE_REVERSAL_WINDOW,
            "atr_move": P_SPIKE_ATR_MOVE,
        },
        "spikes": len(spikes),
        "reversal": rate_block(rev, evald),
    }

    # Self-baseline: does a naive "continue in the last bar's direction"
    # beat coin flip after costs? Predicted dir[i] = sign of bar i's move.
    naive_dirs = [
        "up"
        if closes[i] > closes[i - 1]
        else "down"
        if closes[i] < closes[i - 1]
        else ""
        for i in range(len(closes))
    ]
    edge = direction_edge(closes, naive_dirs)
    direction_edge_block = {
        "params": {
            "fee_rate": 0.001,
            "slip_rate": 0.0005,
            "predictor": "last_bar_sign",
        },
        **edge,
        "degraded": edge["n"] < DEGRADED_MIN_N,
    }

    any_degraded = any(
        blk.get("degraded", False)
        for blk in [
            *swing_breakout["survival"].values(),
            measured_move["hits"],
            spike_reversal["reversal"],
            direction_edge_block,
        ]
    )

    return {
        "swing_breakout": swing_breakout,
        "measured_move": measured_move,
        "spike_reversal": spike_reversal,
        "direction_edge": direction_edge_block,
        "_degraded": any_degraded,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="PA statistics experiments")
    ap.add_argument("--symbol", default="BTC/USDT")
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--market-type", default="futures", choices=["futures", "spot"])
    ap.add_argument("--max-bars", type=int, default=None)
    args = ap.parse_args()

    db_path = resolve_klines_db()
    if not db_path.is_file():
        print(f"ERROR: klines.db not found at {db_path}")
        return 1

    bars = load_bars(
        db_path, args.symbol, args.market_type, args.interval, args.max_bars
    )
    if not bars:
        print(
            f"ERROR: no bars for {args.symbol} {args.market_type} {args.interval} in {db_path}"
        )
        return 1

    experiments = run_experiments(bars)
    ts_min = datetime.fromtimestamp(bars[0]["timestamp"] / 1000, tz=timezone.utc)
    ts_max = datetime.fromtimestamp(bars[-1]["timestamp"] / 1000, tz=timezone.utc)

    result = {
        "version": STATS_VERSION,
        "data": {
            "symbol": args.symbol,
            "market_type": args.market_type,
            "interval": args.interval,
            "bars": len(bars),
            "ts_min": ts_min.isoformat(),
            "ts_max": ts_max.isoformat(),
            "db_path": str(db_path),
        },
        "experiments": experiments,
        # Reserved for the next-period prediction calibration loop (P1-003
        # loader consumes this file; the LLM's 1-bar-ahead calls will be
        # scored against direction_edge on the following window).
        "next_period_reserve": {
            "purpose": "LLM direction-call calibration baseline slot",
            "baseline_hit_rate_1bar": experiments["direction_edge"]["hit_rate"],
            "filled": False,
        },
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"{args.symbol.replace('/', '')}_{args.market_type}_{args.interval}"
    json_path = RESULTS_DIR / f"pa_stats_{tag}.json"
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    exp = experiments
    surv3 = exp["swing_breakout"]["survival"]["window_3"]
    mmh = exp["measured_move"]["hits"]
    rev = exp["spike_reversal"]["reversal"]
    de = exp["direction_edge"]
    lines = [
        f"# PA stats findings — {args.symbol} {args.market_type} {args.interval}",
        "",
        f"- version: {STATS_VERSION}",
        f"- bars: {len(bars)} ({ts_min.date()} .. {ts_max.date()}, source {db_path.name})",
        f"- degraded: {str(exp['_degraded']).lower()} (any evaluated n < {DEGRADED_MIN_N})",
        "",
        "## Method",
        "",
        "Statistical questions follow Al Brooks' published methodology",
        "(*Reading Price Charts Bar by Bar*, 2009; *Trading Price Action Trends*,",
        "2011): swing-point structure, breakout survival, measured-move",
        "projection, spike behavior. All parameters are project-chosen",
        "constants; all numbers are computed from this repository's own",
        "klines.db. Baselines are unconditioned historical frequencies —",
        "they must be read together with the current window's structure,",
        "never as conditional probabilities for the bar at hand.",
        "",
        "## Results",
        "",
        f"- swing breakouts (k={P_SWING_K}, atr_mult={P_BREAKOUT_ATR_MULT}): "
        f"{exp['swing_breakout']['breakout_events']} events",
        f"- survival (no close back through level, window=3): "
        f"{surv3['successes']}/{surv3['total']} = {surv3['rate']} "
        f"(CI95 {surv3['ci95'][0]}..{surv3['ci95'][1]})"
        if surv3["total"]
        else "- survival window=3: no evaluable events",
        f"- measured-move hit within {P_MM_WINDOW} bars: "
        f"{mmh['successes']}/{mmh['total']} = {mmh['rate']} "
        f"(CI95 {mmh['ci95'][0]}..{mmh['ci95'][1]})"
        if mmh["total"]
        else "- measured move: no evaluable events",
        f"- spike reversal within {P_SPIKE_REVERSAL_WINDOW} bars "
        f"({exp['spike_reversal']['spikes']} spikes): "
        f"{rev['successes']}/{rev['total']} = {rev['rate']} "
        f"(CI95 {rev['ci95'][0]}..{rev['ci95'][1]})"
        if rev["total"]
        else "- spike reversal: no evaluable spikes",
        f"- naive 1-bar direction edge vs coin flip: {de['edge_vs_coin']:.4f} "
        f"(after costs {de['edge_after_cost']:.4f}, n={de['n']})",
        "",
        "## Reading rules",
        "",
        "1. n < 100 marks a block degraded — treat its CI as too wide to act on.",
        "2. These are unconditioned historical frequencies for this symbol/",
        "   interval/market-type, not conditional predictions.",
        "3. The JSON file is the machine-readable source of truth; this md is",
        "   the human summary.",
        "",
    ]
    md_path = RESULTS_DIR / f"pa_stats_{tag}_findings.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"OK bars={len(bars)} degraded={exp['_degraded']}")
    print(f"  json: {json_path}")
    print(f"  md:   {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
