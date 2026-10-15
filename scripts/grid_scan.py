"""Multi-symbol grid scan over selene klines.db (research pipeline step 2).

Orchestration only — engine logic lives in selene_space_grid; the per-symbol
replay reuses scripts/grid_replay.replay to avoid any logic duplication.

Scans the deep-data universe (BTC/ETH/SOL 30m futures, ~18,394 rows each)
and emits a PnL%-ranked JSON.

Usage:
    python scripts/grid_scan.py                          # default universe
    python scripts/grid_scan.py --symbols BTC/USDT ETH/USDT
    python scripts/grid_scan.py --out data/grid_scan/latest.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (PROJECT_ROOT, SCRIPTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from grid_replay import DEFAULT_BARS, DEFAULT_DB, DEFAULT_INTERVAL, GATE_MODES, replay

DEFAULT_UNIVERSE = ("BTC/USDT", "ETH/USDT", "SOL/USDT")


def main() -> int:
    parser = argparse.ArgumentParser(description="Multi-symbol grid scan (research)")
    parser.add_argument("--symbols", nargs="*", default=list(DEFAULT_UNIVERSE))
    parser.add_argument("--interval", default=DEFAULT_INTERVAL)
    parser.add_argument("--bars", type=int, default=DEFAULT_BARS)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--gate-mode", default="always-on", choices=GATE_MODES)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    results = []
    errors = {}
    for symbol in args.symbols:
        try:
            results.append(
                replay(symbol, args.interval, args.bars, args.db, args.gate_mode)
            )
        except SystemExit as exc:
            errors[symbol] = str(exc)

    ranked = sorted(
        results, key=lambda r: r["stats"]["pnl_pct_of_budget"], reverse=True
    )
    payload = {
        "universe": list(args.symbols),
        "interval": args.interval,
        "bars": args.bars,
        "gate_mode": args.gate_mode,
        "ranked_by": "pnl_pct_of_budget (net of fees)",
        "results": ranked,
        "errors": errors,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"scan result -> {args.out}")
    else:
        print(text)

    print("\nPnL% ranking (net of fees):")
    for i, r in enumerate(ranked, 1):
        s = r["stats"]
        print(
            f"{i}. {r['symbol']:<10} net {s['pnl_pct_of_budget']:+.2%} "
            f"(gross {s['cumulative_pnl_gross']:+.2f}, fees {s['cumulative_fees']:.2f}) "
            f"fills {s['fills']} cycles {s['completed_cycles']} "
            f"MDD {s['max_drawdown']:.1%}"
        )
    for symbol, err in errors.items():
        print(f"!! {symbol}: {err}")
    if not ranked:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
