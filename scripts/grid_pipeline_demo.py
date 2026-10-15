"""One-shot research pipeline demo (research pipeline step 4).

Chains the four research steps and writes a summary JSON:
  1. fixture export check (frozen fixture present)
  2. parity verification (original vs port on the frozen fixture)
  3. multi-symbol scan (PnL% ranking)
  4. gate calibration vs always-on baseline

Each step fails loud (no silent swallow) — the demo aborts on the first
step that raises or returns a failure verdict.

Usage:
    python scripts/grid_pipeline_demo.py
    python scripts/grid_pipeline_demo.py --out data/grid_scan/pipeline_summary.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from grid_replay import DEFAULT_BARS, DEFAULT_DB, DEFAULT_INTERVAL  # noqa: E402

FIXTURE = os.path.join(
    PROJECT_ROOT,
    "packages",
    "grid-engine",
    "tests",
    "fixtures",
    "grid_fixture_btc_300.json",
)
PARITY_REPORT = os.path.join(
    PROJECT_ROOT, "packages", "grid-engine", "tests", "fixtures", "parity_report.json"
)
DEFAULT_UNIVERSE = ("BTC/USDT", "ETH/USDT", "SOL/USDT")
SCAN_OUT = os.path.join(PROJECT_ROOT, "data", "grid_scan", "scan.json")
CALIBRATION_OUT = os.path.join(PROJECT_ROOT, "data", "grid_scan", "calibration.json")


def _run(script: str, *extra: str) -> None:
    proc = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, script), *extra],
        capture_output=True,
        cwd=PROJECT_ROOT,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"step {script} FAILED (exit {proc.returncode}):\n"
            f"{proc.stderr.decode('utf-8', errors='replace')}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Grid research pipeline demo")
    parser.add_argument("--symbols", nargs="*", default=list(DEFAULT_UNIVERSE))
    parser.add_argument("--interval", default=DEFAULT_INTERVAL)
    parser.add_argument("--bars", type=int, default=DEFAULT_BARS)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument(
        "--out",
        default=os.path.join(
            PROJECT_ROOT, "data", "grid_scan", "pipeline_summary.json"
        ),
    )
    args = parser.parse_args()

    summary: dict = {"steps": {}}

    # step 1: frozen fixture present
    if not os.path.exists(FIXTURE):
        raise SystemExit(
            f"step 1 fixture check FAILED: {FIXTURE} missing "
            f"(run scripts/export_grid_fixture.py)"
        )
    with open(FIXTURE, encoding="utf-8") as f:
        meta = json.load(f)["meta"]
    summary["steps"]["1_fixture"] = {"ok": True, "meta": meta}
    print(f"[1/4] fixture OK: {meta['bars']} bars {meta['symbol']} {meta['interval']}")

    # step 2: parity verification
    _run("verify_grid_parity.py")
    with open(PARITY_REPORT, encoding="utf-8") as f:
        parity = json.load(f)
    if not parity["comparison"]["verdict"]:
        raise SystemExit("step 2 parity FAILED — see parity_report.json")
    summary["steps"]["2_parity"] = {
        "ok": True,
        "fills": parity["comparison"]["fills_count"],
        "pnl": parity["comparison"]["pnl"],
    }
    print(
        f"[2/4] parity OK: fills {parity['comparison']['fills_count']['original']} "
        f"bit-equal, PnL exact"
    )

    # step 3: multi-symbol scan
    _run(
        "grid_scan.py",
        "--symbols",
        *args.symbols,
        "--interval",
        args.interval,
        "--bars",
        str(args.bars),
        "--db",
        args.db,
        "--out",
        SCAN_OUT,
    )
    with open(SCAN_OUT, encoding="utf-8") as f:
        scan = json.load(f)
    summary["steps"]["3_scan"] = {
        "ok": True,
        "out": SCAN_OUT,
        "ranked": [r["symbol"] for r in scan["results"]],
        "errors": scan["errors"],
    }
    print(f"[3/4] scan OK: ranking {[r['symbol'] for r in scan['results']]}")

    # step 4: gate calibration
    _run(
        "grid_calibrate.py",
        "--symbols",
        *args.symbols,
        "--interval",
        args.interval,
        "--bars",
        str(args.bars),
        "--db",
        args.db,
        "--out",
        CALIBRATION_OUT,
    )
    with open(CALIBRATION_OUT, encoding="utf-8") as f:
        calib = json.load(f)
    summary["steps"]["4_calibrate"] = {
        "ok": True,
        "out": CALIBRATION_OUT,
        "decisions": calib["decisions"],
    }
    print(
        "[4/4] calibrate OK: decisions "
        f"{ {g: d['verdict'] for g, d in calib['decisions'].items()} }"
    )

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(f"\npipeline summary -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
