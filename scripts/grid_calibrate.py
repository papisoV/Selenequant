"""Grid gate calibration against an always-on baseline (research pipeline step 3).

DESIGN RATIONALE (do not remove):

The original experiment (grid_pa_replay.py @ 43eee46) used an LLM
cycle diagnosis as a stand-in gate and ran a calibration of
"does efficiency_ratio agree with the LLM's real cycle diagnosis?" — the
answer was **"calibration FAILED (3/3)"** across all three pairs. ER alone
did not reproduce the LLM's cycle calls.

selene therefore does NOT trust a single gate signal as ground truth.
Instead this script builds the ground truth from data itself:
the **always-on grid PnL baseline**. A gate (er-gated / adx-gated) is kept
only if, relative to always-on, it improves PnL on >= 2/3 pairs AND does
not worsen max drawdown.

Decision rule (data-driven, no presets):
    keep gate G  <=>  #(pairs where net_pnl_pct(G) > net_pnl_pct(always-on)) >= 2
                     AND  #(pairs where mdd(G) <= mdd(always-on) + eps) == #pairs

Usage:
    python scripts/grid_calibrate.py
    python scripts/grid_calibrate.py --out data/grid_scan/calibration.json
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

from grid_replay import DEFAULT_BARS, DEFAULT_DB, DEFAULT_INTERVAL, replay

DEFAULT_UNIVERSE = ("BTC/USDT", "ETH/USDT", "SOL/USDT")
GATES = ("er-gated", "adx-gated")
MDD_EPS = 1e-9


def run_calibration(symbols: list[str], interval: str, bars: int, db: str) -> dict:
    baseline: dict[str, dict[str, float]] = {}
    for symbol in symbols:
        r = replay(symbol, interval, bars, db, "always-on")
        baseline[symbol] = {
            "net_pnl_pct": r["stats"]["pnl_pct_of_budget"],
            "max_drawdown": r["stats"]["max_drawdown"],
        }

    gated: dict[str, dict[str, dict[str, float] | None]] = {}
    for gate in GATES:
        gated[gate] = {}
        for symbol in symbols:
            entry: dict[str, float] | None
            try:
                r = replay(symbol, interval, bars, db, gate)
                entry = {
                    "net_pnl_pct": r["stats"]["pnl_pct_of_budget"],
                    "max_drawdown": r["stats"]["max_drawdown"],
                }
            except SystemExit:
                entry = None
            gated[gate][symbol] = entry

    decisions: dict[str, dict[str, object]] = {}
    for gate in GATES:
        rows = gated[gate]
        if any(rows[s] is None for s in symbols):
            decisions[gate] = {
                "verdict": "downgrade",
                "reason": "gate replay aborted (SystemExit, e.g. missing klines "
                "for a symbol); treated as downgrade",
            }
            continue
        # all entries present here (any-None branch above continued);
        # rebuild as a non-optional view so mypy sees dict[str, float]
        metrics: dict[str, dict[str, float]] = {
            s: m for s in symbols if (m := rows[s]) is not None
        }
        pnl_wins = len(
            [
                s
                for s in symbols
                if metrics[s]["net_pnl_pct"] > baseline[s]["net_pnl_pct"]
            ]
        )
        mdd_ok = all(
            metrics[s]["max_drawdown"] <= baseline[s]["max_drawdown"] + MDD_EPS
            for s in symbols
        )
        keep = pnl_wins >= 2 and mdd_ok
        decisions[gate] = {
            "verdict": "keep" if keep else "downgrade",
            "pnl_wins": f"{pnl_wins}/{len(symbols)}",
            "drawdown_never_worse": mdd_ok,
            "per_pair_delta": {
                s: {
                    "pnl_delta": metrics[s]["net_pnl_pct"] - baseline[s]["net_pnl_pct"],
                    "mdd_delta": metrics[s]["max_drawdown"]
                    - baseline[s]["max_drawdown"],
                }
                for s in symbols
            },
        }

    return {
        "baseline": baseline,
        "gated": gated,
        "decisions": decisions,
        "decision_rule": "keep iff pnl wins on >=2/3 pairs AND drawdown never worse",
        "note": "gate modes are an optional experiment: the always-on grid "
        "remains the production default regardless of verdict — "
        "a 'keep' verdict only marks the gate as worth a longer "
        "evaluation window",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gate calibration vs always-on baseline"
    )
    parser.add_argument("--symbols", nargs="*", default=list(DEFAULT_UNIVERSE))
    parser.add_argument("--interval", default=DEFAULT_INTERVAL)
    parser.add_argument("--bars", type=int, default=DEFAULT_BARS)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    report = run_calibration(args.symbols, args.interval, args.bars, args.db)
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"calibration -> {args.out}")
    else:
        print(text)

    print("\nDECISIONS (vs always-on baseline):")
    for gate, d in report["decisions"].items():
        extras = [f"{k}={v}" for k, v in d.items() if k != "verdict"]
        print(f"- {gate}: {d['verdict'].upper()} {' '.join(extras)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
