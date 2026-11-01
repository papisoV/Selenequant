#!/usr/bin/env python
"""Drive the production gap sweep (detect + repair + tail advance) once.

Why a driver at all
-------------------
``scripts/backfill_kline_holes.py`` calls ``_sweep_partition`` per target, which
repairs *interior* holes only — it predates the tail advance and therefore
cannot move a partition whose last local bar is months old. This script drives
``sweep_once`` (the same entry point the app's background loop uses), so the
data layer is exercised exactly as it will be in production.

Usage
-----
    # dry run on a consistent copy (source never opened for writing)
    python scripts/advance_kline_tails.py --db <path>/klines.db

    # write to the real DB (backup first)
    python scripts/advance_kline_tails.py --db <path>/klines.db --apply

    # bound the work for a first pass
    python scripts/advance_kline_tails.py ... --rounds 2 --tail-pages 2

Exit code 0 if the sweep completed; 1 if a round raised.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import tempfile
import time
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

logger = logging.getLogger("advance_kline_tails")

# 24/7 venues only — the sweeper judges gaps by wall clock, and equity
# calendars would manufacture permanent false holes (see continuous_market).
CRYPTO_SOURCES: set[tuple[str, str]] = {
    ("binance", "spot"),
    ("binance", "futures"),
    ("okx", "spot"),
    ("okx", "futures"),
}


def _build_service():
    """The crypto REST sources the tail advance fetches through."""
    import os

    from selene_space_market.service import MarketDataService
    from selene_space_market.sources.binance_source import BinanceSource
    from selene_space_market.sources.okx_source import OKXSource

    proxy_url = (
        os.environ.get("SELENE_PROXY_URL")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("PROXY_URL")
    )
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
            BinanceSource(market_type=mt, proxy_url=proxy_url, kline_intervals=["1m"])
        )
    for mt in ("spot", "futures"):
        svc.register_source(
            OKXSource(market_type=mt, proxy_url=proxy_url, kline_intervals=["1m"])
        )
    return svc


def _consistent_copy(src: Path, dest: Path) -> None:
    """Snapshot a live SQLite DB without capturing a torn page state."""
    import sqlite3

    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    target = sqlite3.connect(str(dest))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def _freshness(db: KlineDB, manager: KlineDataManager) -> dict[str, int]:
    """Bucket every crypto partition by how many bars behind it is.

    ``stale_open`` counts rows still flagged ``closed=0`` whose bar window has
    already ended: freshness alone cannot see them, because the partition's
    tail can be current while an older bar inside it froze half-written.
    """
    now = int(time.time() * 1000)
    buckets = {
        "fresh": 0,
        "<=100": 0,
        "<=1k": 0,
        "<=10k": 0,
        ">10k": 0,
        "stale_open": 0,
    }
    for _ex, _mt, _sym, ivl, ts in db.list_open_bars(allowed_sources=CRYPTO_SOURCES):
        if now - ts >= manager.interval_ms(ivl):
            buckets["stale_open"] += 1
    for ex, mt, sym, ivl in db.list_partitions():
        if (ex, mt) not in CRYPTO_SOURCES:
            continue
        latest = db.get_latest_timestamp(ex, mt, sym, ivl)
        if latest is None:
            continue
        behind = max(0, (now - latest) // manager.interval_ms(ivl))
        if behind <= 2:
            buckets["fresh"] += 1
        elif behind <= 100:
            buckets["<=100"] += 1
        elif behind <= 1000:
            buckets["<=1k"] += 1
        elif behind <= 10000:
            buckets["<=10k"] += 1
        else:
            buckets[">10k"] += 1
    return buckets


async def _run(args: argparse.Namespace) -> int:
    src = Path(args.db)
    if not src.exists():
        print(f"ERROR: db not found: {src.resolve()}", file=sys.stderr)
        return 2

    cleanup: str | None = None
    if args.apply:
        backup = src.with_suffix(src.suffix + f".bak-{args.stamp}")
        _consistent_copy(src, backup)
        print(f"backup written: {backup}")
        work_db = str(src)
    else:
        tmp = tempfile.NamedTemporaryFile(
            prefix="klines_tail_", suffix=".db", delete=False
        )
        tmp.close()
        _consistent_copy(src, Path(tmp.name))
        print(
            f"DRY RUN — operating on a copy: {tmp.name}\n"
            "  (pass --apply to write the real DB)"
        )
        work_db = tmp.name
        cleanup = tmp.name

    db = KlineDB(work_db)
    db.open()
    try:
        db._conn.execute("PRAGMA busy_timeout=60000")  # type: ignore[union-attr]
        manager = KlineDataManager(":memory:")
        manager._db = db
        manager.set_service(_build_service())

        before = _freshness(db, manager)
        print(f"before: {before}")
        rc = 0
        for round_no in range(1, args.rounds + 1):
            t0 = time.monotonic()
            try:
                report = await manager.sweep_once(
                    allowed_sources=CRYPTO_SOURCES,
                    page_budget=args.page_budget,
                    tail_page_budget=args.tail_pages,
                )
            except Exception:
                logger.exception("round %d failed", round_no)
                rc = 1
                break
            elapsed = time.monotonic() - t0
            print(
                f"round {round_no}: scanned={report.partitions_scanned} "
                f"found={report.holes_found} filled={report.holes_filled} "
                f"permanent={report.holes_permanent} "
                f"advanced={report.partitions_advanced} "
                f"sealed={report.stale_open_sealed} "
                f"transport_errors={report.transport_errors} "
                f"pages={report.pages_fetched} in {elapsed:.0f}s"
            )
            for w in report.warnings[:10]:
                print(f"  warning: {w}")
            print(f"  after: {_freshness(db, manager)}")
            if report.hole_transport_errors:
                # An unreachable exchange is not convergence: holes were left
                # unexamined, and calling this "done" would report a frozen
                # store as healthy. Failed tail fetches do not abort — those
                # partitions simply stay stale, which the freshness print shows.
                print(
                    f"aborted: {report.hole_transport_errors} hole fetch(es) could "
                    "not reach the exchange — nothing was verified"
                )
                return 1
            if (
                report.partitions_advanced == 0
                and report.holes_found == 0
                and report.stale_open_sealed == 0
            ):
                print("converged")
                break
        return rc
    finally:
        db.close()
        if cleanup:
            try:
                Path(cleanup).unlink()
            except OSError:
                logger.warning("could not remove temp copy %s", cleanup)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--db", required=True)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--stamp", default="tails")
    p.add_argument("--rounds", type=int, default=1)
    p.add_argument("--page-budget", type=int, default=6)
    p.add_argument("--tail-pages", type=int, default=8)
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING
        if args.quiet
        else (logging.DEBUG if args.verbose else logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
