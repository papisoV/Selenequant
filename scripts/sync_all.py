#!/usr/bin/env python
"""Unified data sync entry point — runs all syncs in sequence.

Usage:
    python scripts/sync_all.py
    python scripts/sync_all.py --proxy http://<host>:<port>
    python scripts/sync_all.py --full-sync
    python scripts/sync_all.py --days 365
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SYNC_STATUS_PATH = PROJECT_ROOT / "data" / "sync_status.json"


def load_status() -> dict:
    if SYNC_STATUS_PATH.exists():
        return json.loads(SYNC_STATUS_PATH.read_text())
    return {}


def save_status(status: dict) -> None:
    SYNC_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SYNC_STATUS_PATH.write_text(json.dumps(status, indent=2))


def run_script(script: str, extra_args: list[str]) -> bool:
    cmd = [sys.executable, str(PROJECT_ROOT / "scripts" / script)] + extra_args
    print(f"\n{'='*60}")
    print(f"Running: {script} {' '.join(extra_args)}")
    print(f"{'='*60}")
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        print(f"WARNING: {script} exited with code {result.returncode}")
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified data sync for SeleneQuant")
    parser.add_argument("--proxy", default=None, help="HTTP proxy URL")
    parser.add_argument(
        "--days", type=int, default=365, help="Historical days for first sync"
    )
    parser.add_argument(
        "--full-sync", action="store_true", help="Force full historical fetch"
    )
    parser.add_argument("--skip-klines", action="store_true", help="Skip klines sync")
    parser.add_argument(
        "--skip-derivatives", action="store_true", help="Skip derivatives sync"
    )
    parser.add_argument(
        "--skip-orderflow", action="store_true", help="Skip orderflow sync"
    )
    args = parser.parse_args()

    status = load_status()
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    results: dict[str, str] = {}

    # 1. Klines sync
    if not args.skip_klines:
        klines_args = []
        if args.proxy:
            klines_args.extend(["--proxy", args.proxy])
        klines_args.extend(["--days", str(args.days)])
        ok = run_script("sync_klines.py", klines_args)
        results["klines"] = "ok" if ok else "failed"
        status["klines_last_sync"] = now_iso
    else:
        results["klines"] = "skipped"

    # 2. Derivatives sync (funding rate + OI + taker ratio)
    if not args.skip_derivatives:
        deriv_args = []
        if args.proxy:
            deriv_args.extend(["--proxy", args.proxy])
        deriv_args.extend(["--days", str(args.days)])
        ok = run_script("sync_derivatives.py", deriv_args)
        results["derivatives"] = "ok" if ok else "failed"
        status["derivatives_last_sync"] = now_iso
    else:
        results["derivatives"] = "skipped"

    # 3. Orderflow sync (top trader ratios, global LS, basis)
    if not args.skip_orderflow:
        of_args = []
        if args.proxy:
            of_args.extend(["--proxy", args.proxy])
        of_args.extend(["--days", str(args.days)])
        ok = run_script("sync_orderflow.py", of_args)
        results["orderflow"] = "ok" if ok else "failed"
        status["orderflow_last_sync"] = now_iso
    else:
        results["orderflow"] = "skipped"

    # Save status
    status["last_sync"] = now_iso
    status["results"] = results
    save_status(status)

    # Summary
    print(f"\n{'='*60}")
    print("SYNC COMPLETE")
    print(f"{'='*60}")
    for name, result in results.items():
        print(f"  {name}: {result}")
    print(f"  Status saved to: {SYNC_STATUS_PATH}")


if __name__ == "__main__":
    main()
