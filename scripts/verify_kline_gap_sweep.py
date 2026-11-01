#!/usr/bin/env python
"""One-time verification: run the L2 sweeper over a COPY of a production klines DB.

Why this exists
---------------
The BTC/USDT 5m partition accumulated a real multi-day hole (see the ralplan
`kline-gap-remediation-ralplan-v2`, fact F20). The legacy `repair_gaps` path
only looks back a fixed rolling window, so a hole whose right edge has already
drifted out of that window can never be closed by it. This script exercises the
NEW sweeper path instead and reports whether the partition converges to a
contiguous grid.

Safety
------
This script NEVER opens the source database for writing. It copies the file to
a temporary path and mutates only the copy, so running it cannot damage
production data. Point it at a copy of the live DB or at any DB you want to
inspect.

Usage
-----
    python scripts/verify_kline_gap_sweep.py --db data/klines.db
    python scripts/verify_kline_gap_sweep.py --db data/klines.db \
        --exchange binance --market-type futures --symbol BTC/USDT --interval 5m

Exit code is 0 when every scanned partition ends contiguous (or its remaining
holes are recorded `permanent`, meaning the exchange genuinely has no bars
there), and 1 when a repairable hole survives the sweep budget.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from selene_space_market.kline_db import KlineDB  # noqa: E402
from selene_space_market.kline_manager import KlineDataManager  # noqa: E402
from selene_space_market.service import MarketDataService  # noqa: E402

logger = logging.getLogger("verify_kline_gap_sweep")

# Crypto venues that always trade — the only ones whose gap structure can be
# judged without a trading calendar (mirrors INV-2's domain gate).
DEFAULT_ALLOWED = {
    ("binance", "spot"),
    ("binance", "futures"),
    ("okx", "spot"),
    ("okx", "swap"),
}


def _copy_db(source: str) -> str:
    """Copy ``source`` to a temp file so the original is never written."""
    src = Path(source)
    if not src.exists():
        raise FileNotFoundError(f"db not found: {src} (got {src.resolve()})")
    tmp = tempfile.NamedTemporaryFile(
        prefix="klines_verify_", suffix=".db", delete=False
    )
    tmp.close()
    shutil.copy2(src, tmp.name)
    logger.info("copied %s -> %s (temp; source untouched)", src, tmp.name)
    return tmp.name


def _contiguous(
    db: KlineDB, ex: str, mt: str, sym: str, ivl: str, step_ms: int
) -> bool:
    holes = db.find_gaps(ex, mt, sym, ivl, step_ms)
    return len(holes) == 0


async def _run(args: argparse.Namespace) -> int:
    work_db = _copy_db(args.db)
    db = KlineDB(work_db)
    db.open()
    try:
        return await _run_on_copy(args, db, work_db)
    finally:
        # Windows keeps the file locked while the sqlite handle is open, so the
        # handle must close before unlink. The copy can be multi-GB on a
        # production DB — never leave it behind.
        db.close()
        try:
            Path(work_db).unlink()
            logger.info("removed temp copy %s", work_db)
        except OSError:
            logger.warning("could not remove temp copy %s", work_db)


async def _run_on_copy(args: argparse.Namespace, db: KlineDB, work_db: str) -> int:
    manager = KlineDataManager(work_db)
    manager._db = db  # reuse the already-open handle
    manager.set_service(MarketDataService())

    allowed = DEFAULT_ALLOWED
    if args.exchange and args.market_type:
        allowed = {(args.exchange, args.market_type)}

    total_holes = 0
    for round_no in range(1, args.rounds + 1):
        report = await manager.sweep_once(
            allowed_sources=allowed,
            max_partitions=args.max_partitions,
            page_budget=args.page_budget,
        )
        print(
            f"round {round_no}: scanned={report.partitions_scanned} "
            f"found={report.holes_found} filled={report.holes_filled} "
            f"permanent={report.holes_permanent} advanced={report.partitions_advanced}"
        )
        total_holes += report.holes_found
        if report.holes_found == 0:
            break

    # Report per-partition contiguity over the domain we scanned.
    partitions = [p for p in db.list_partitions() if (p[0], p[1]) in allowed]
    if args.symbol:
        partitions = [p for p in partitions if p[2] == args.symbol]
    if args.interval:
        partitions = [p for p in partitions if p[3] == args.interval]

    failures: list[str] = []
    print("\n-- per-partition result --")
    for ex, mt, sym, ivl in partitions:
        step = manager.interval_ms(ivl)
        holes = db.find_gaps(ex, mt, sym, ivl, step)
        ledger = db.list_holes(ex, mt, sym, ivl, only_open=False)
        perm = [h for h in ledger if h.permanent]
        status = "CONTIGUOUS" if not holes else f"{len(holes)} HOLE(S)"
        print(f"  {ex}/{mt}/{sym}/{ivl}: {status} (permanent={len(perm)})")
        if holes:
            for left, right in holes:
                gap_hours = (right - left) / 3_600_000
                is_perm = any(
                    h.hole_left_ts == left and h.hole_right_ts == right for h in perm
                )
                tag = "permanent (exchange-side gap)" if is_perm else "REPAIRABLE"
                print(f"      hole {left} -> {right} (~{gap_hours:.1f}h) [{tag}]")
                if not is_perm:
                    failures.append(f"{sym}/{ivl} {left}->{right}")

    print()
    print(
        f"sweeper made {total_holes} total hole observation(s) across {args.rounds} round(s)"
    )
    if failures:
        print(f"VERDICT: FAIL — {len(failures)} repairable hole(s) survived the sweep")
        print("  (these need a larger --page-budget or more --rounds, or the exchange")
        print("   genuinely lacks the bars and they have not yet gone permanent)")
        return 1
    print("VERDICT: PASS — all scanned partitions contiguous or permanent-only")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--db",
        default="data/klines.db",
        help="source klines DB (read-only; copied to temp)",
    )
    p.add_argument("--exchange", default=None, help="restrict domain to this exchange")
    p.add_argument(
        "--market-type", default=None, help="restrict domain to this market type"
    )
    p.add_argument("--symbol", default=None, help="only report this symbol")
    p.add_argument("--interval", default=None, help="only report this interval")
    p.add_argument(
        "--rounds", type=int, default=4, help="sweep rounds to run (default 4)"
    )
    p.add_argument(
        "--max-partitions", type=int, default=200, help="per-round partition budget"
    )
    p.add_argument(
        "--page-budget", type=int, default=8, help="pages fetched per hole per round"
    )
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
