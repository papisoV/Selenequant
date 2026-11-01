#!/usr/bin/env python
"""One-time production backfill: close interior kline holes via the L2 sweeper.

Why a standalone script (not the app)
-------------------------------------
The sweeper lives in the app's lifespan, but the production kline DB belongs to
a tree whose branch does not carry this code yet. Rather than merge a large
divergence just to close two holes once, this script drives `sweep_once`
directly against the DB file. It is the deliberate, reversible, one-shot tool
for that job.

Safety model
------------
- **Default is a dry run**: the DB is copied to a temp path, the sweep runs on
  the copy, and the report says what *would* happen. The source file is never
  opened for writing. (Same posture as scripts/verify_kline_gap_sweep.py.)
- ``--apply`` is the only way to touch the real file, and it takes a backup
  first. Nothing is written if the backup fails.

Usage
-----
    # dry run: copy, sweep, report, discard (source untouched)
    python scripts/backfill_kline_holes.py --db <path>/klines.db

    # apply to the real DB (backs up first)
    python scripts/backfill_kline_holes.py --db <path>/klines.db --apply

    # narrow to one partition
    python scripts/backfill_kline_holes.py --db <path>/klines.db \
        --exchange binance --market-type futures --symbol BTC/USDT --interval 5m

Exit code: 0 when every targeted partition ends contiguous or permanent-only,
1 when a repairable hole survives the round budget.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
for _pkg in (
    "packages/market-data",
    "packages/shared-types",
    "packages/core",
    "packages/exchange-sdk",
    "packages/paper-engine",
    "packages/llm-adapter",
    "packages/risk-engine",
    "packages/storage",
):
    _p = PROJECT_ROOT / _pkg
    if _p.exists():
        sys.path.insert(0, str(_p))

from selene_space_market.kline_db import KlineDB  # noqa: E402
from selene_space_market.kline_manager import KlineDataManager  # noqa: E402
from selene_space_market.service import MarketDataService  # noqa: E402
from selene_space_market.sources.binance_source import BinanceSource  # noqa: E402
from selene_space_market.sources.okx_source import OKXSource  # noqa: E402

logger = logging.getLogger("backfill_kline_holes")

# Venues whose gaps are always repairable (24/7 markets — no trading calendar).
CRYPTO_SOURCES: set[tuple[str, str]] = {
    ("binance", "spot"),
    ("binance", "futures"),
    ("okx", "spot"),
    ("okx", "futures"),
}


def _build_service(intervals: list[str]) -> MarketDataService:
    """A service with the crypto sources the sweeper needs to fetch holes."""
    import os

    proxy_url = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
    if not proxy_url:
        try:
            from selene_space_exchange.exchanges.polymarket.client import (
                detect_local_proxy,
            )

            proxy_url = detect_local_proxy()
        except Exception:
            proxy_url = None

    svc = MarketDataService(large_trade_threshold_usd=10_000.0)
    for mt in ("spot", "futures"):
        svc.register_source(
            BinanceSource(
                market_type=mt, proxy_url=proxy_url, kline_intervals=intervals
            )
        )
    for mt in ("spot", "futures"):
        svc.register_source(
            OKXSource(market_type=mt, proxy_url=proxy_url, kline_intervals=intervals)
        )
    return svc


def _target_partitions(
    db: KlineDB, args: argparse.Namespace
) -> list[tuple[str, str, str, str]]:
    parts = [p for p in db.list_partitions() if (p[0], p[1]) in CRYPTO_SOURCES]
    if args.exchange:
        parts = [p for p in parts if p[0] == args.exchange]
    if args.market_type:
        parts = [p for p in parts if p[1] == args.market_type]
    if args.symbol:
        parts = [p for p in parts if p[2] == args.symbol]
    if args.interval:
        parts = [p for p in parts if p[3] == args.interval]
    return parts


def _measure(
    db: KlineDB, manager: KlineDataManager, parts: list[tuple[str, str, str, str]]
) -> list[dict]:
    """Per-partition hole census (also reports out-of-domain holes read-only)."""
    census = []
    for ex, mt, sym, ivl in parts:
        step = manager.interval_ms(ivl)
        holes = db.find_gaps(ex, mt, sym, ivl, step)
        missing = sum(max(0, (right - left) // step - 1) for left, right in holes)
        ledger = db.list_holes(ex, mt, sym, ivl, only_open=False)
        census.append(
            {
                "partition": (ex, mt, sym, ivl),
                "holes": len(holes),
                "missing_bars": missing,
                "permanent": sum(1 for h in ledger if h.permanent),
            }
        )
    return census


def _print_census(title: str, census: list[dict]) -> None:
    print(f"\n-- {title} --")
    if not census:
        print("  (no in-domain partitions matched)")
        return
    for c in census:
        ex, mt, sym, ivl = c["partition"]
        status = (
            "CONTIGUOUS"
            if c["holes"] == 0
            else f"{c['holes']} HOLE(S) / {c['missing_bars']} bars missing"
        )
        perm = f" permanent={c['permanent']}" if c["permanent"] else ""
        print(f"  {ex}/{mt}/{sym}/{ivl}: {status}{perm}")


async def _sweep(
    db: KlineDB,
    service: MarketDataService,
    args: argparse.Namespace,
    targets: list[tuple[str, str, str, str]],
) -> int:
    """Sweep exactly the target partitions (not the first N of the store).

    `sweep_once` takes the first ``max_partitions`` in-domain partitions, which
    is right for a periodic whole-store scan but wrong for a targeted backfill —
    it would sweep unrelated partitions and never reach the one you asked for.
    So drive ``_sweep_partition`` per target directly.
    """
    from selene_space_market.kline_manager import SweepReport

    manager = KlineDataManager(":memory:")
    manager._db = db
    manager.set_service(service)

    for round_no in range(1, args.rounds + 1):
        report = SweepReport()
        for ex, mt, sym, ivl in targets:
            await manager._sweep_partition(
                report,
                exchange=ex,
                market_type=mt,
                symbol=sym,
                interval=ivl,
                step_ms=manager.interval_ms(ivl),
                page_budget=args.page_budget,
                quality_checker=None,
            )
        print(
            f"round {round_no}: scanned={report.partitions_scanned} "
            f"found={report.holes_found} filled={report.holes_filled} "
            f"permanent={report.holes_permanent} advanced={report.partitions_advanced} "
            f"pages_fetched={report.pages_fetched}"
        )
        for w in report.warnings:
            print(f"  warning: {w}")
        if report.holes_found == 0:
            break
    return 0


def _consistent_copy(src: Path, dest: Path) -> None:
    """Copy a SQLite DB safely even while another process is writing it.

    A plain file copy can capture a torn page state when the DB is live (the
    app holds it open). ``sqlite3.Connection.backup`` takes a consistent
    snapshot under the DB's own locking, so the copy is always a valid DB.
    """
    import sqlite3

    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    target = sqlite3.connect(str(dest))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


async def _run(args: argparse.Namespace) -> int:
    src = Path(args.db)
    if not src.exists():
        print(f"ERROR: db not found: {src.resolve()}", file=sys.stderr)
        return 2

    if args.apply:
        # Backup first, using the consistent-snapshot path so a live write in
        # the app can't tear the copy. Nothing is written if this fails.
        backup = src.with_suffix(src.suffix + f".bak-{args.stamp}")
        _consistent_copy(src, backup)
        print(f"backup written: {backup}")
        work_db = str(src)
        _cleanup = None
    else:
        tmp = tempfile.NamedTemporaryFile(
            prefix="klines_backfill_", suffix=".db", delete=False
        )
        tmp.close()
        _consistent_copy(src, Path(tmp.name))
        print(
            f"DRY RUN — operating on a copy: {tmp.name}\n  (pass --apply to write the real DB)"
        )
        work_db = tmp.name
        _cleanup = tmp.name

    db = KlineDB(work_db)
    db.open()
    # The app keeps this DB open while we write to it (--apply); without a busy
    # timeout a concurrent write can raise "database is locked" instead of
    # waiting its turn.
    try:
        db._conn.execute("PRAGMA busy_timeout=30000")  # type: ignore[union-attr]
    except Exception:
        pass
    service = _build_service(args.intervals)
    manager = KlineDataManager(":memory:")
    manager._db = db
    manager.set_service(service)
    try:
        parts = _target_partitions(db, args)
        before = _measure(db, manager, parts)
        _print_census("BEFORE", before)
        await _sweep(db, service, args, parts)
        after = _measure(db, manager, parts)
        _print_census("AFTER", after)

        survivors = [c for c in after if c["holes"] > c["permanent"]]
        print()
        if survivors:
            print(
                f"VERDICT: FAIL — {len(survivors)} partition(s) still have a repairable hole"
            )
            return 1
        closed = sum(c["missing_bars"] for c in before) - sum(
            c["missing_bars"] for c in after
        )
        print(f"VERDICT: PASS — {closed} bars restored, no repairable hole left")
        return 0
    finally:
        db.close()
        if _cleanup:
            try:
                Path(_cleanup).unlink()
            except OSError:
                logger.warning("could not remove temp copy %s", _cleanup)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--db", required=True, help="klines DB path (read-only unless --apply)"
    )
    p.add_argument(
        "--apply", action="store_true", help="write to the real DB (backs up first)"
    )
    p.add_argument("--stamp", default="backfill", help="backup filename suffix tag")
    p.add_argument("--exchange", default=None)
    p.add_argument("--market-type", default=None)
    p.add_argument("--symbol", default=None)
    p.add_argument("--interval", default=None)
    p.add_argument("--rounds", type=int, default=4)
    p.add_argument("--page-budget", type=int, default=8)
    p.add_argument("--intervals", nargs="*", default=["5m", "15m", "30m", "1h"])
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
