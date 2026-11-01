"""Backfill a historical limit-up ladder panel for the ashare sentiment model.

Rebuilds, day by day, the limit-up ladder (连板梯队) structure that
:mod:`selene_space_ashare.market_phase` classifies into sentiment phases:
per-date board breadth, height, promotion rate, seal rate, and ladder
completeness. Output lands in
``packages/ashare/data_cache/ladder_panel.parquet`` (NOT committed).

Why kline recompute, not a snapshot endpoint
--------------------------------------------
Every A-share limit-up *snapshot* source available here only serves the last
~3 trading days — probed 2026-10: ``ak.stock_zt_pool_em(date=...)`` returns 0
rows for every 2024/2025 date, and ``fetch_limit_up_stats(date)`` returns
``{}`` for the same, both fine only for 10-08/10-09/10-12. baostock login
fails outright in this environment (error 10002007). So history must be
re-derived from unadjusted daily klines, which are deep (2015+).

Source: Tencent ``ak.stock_zh_a_hist_tx`` (``adjust=""`` = unadjusted),
byte-identical closes to the eastmoney endpoint that intermittently refuses
connections here. Every fetch goes through ``_fetch_with_retry`` so a hung
request is killed by its subprocess hard-timeout rather than stalling the run.

Cost
----
~5-8 s per stock serially (the fixed per-call network + subprocess cost
dominates; window size barely matters). Fetching is I/O-bound and measured to
scale cleanly — 24 names went 114 s serial -> 38 s at 16 workers with zero
failures — so the default 8 workers puts a full 5572-name universe at roughly
an hour rather than most of a day. Use ``--limit`` for smoke runs and
``--resume`` to continue an interrupted pass; every stock is cached
individually so re-runs skip completed work.

Known deviations (declared, not silent)
---------------------------------------
1. **ST names at PIT are unavailable.** The limit ratio is looked up with the
   *current* name from the universe list; a stock that was ST in the past may
   have its 5% limit mis-classified as 10%, missing genuine limit-ups. The
   panel's own thresholds absorb part of this systematically, but the bias is
   real and not corrected here.
2. **New-listing first-day no-limit behaviour is unhandled.** A newly listed
   stock's first day has no price limit; TSP carries an explicit
   ``_no_price_limit`` flag, ashare has no equivalent. Such days may be scored
   as 0 boards (harmless for ladder height, but not correct).
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from selene_space_ashare.data_fetcher import _fetch_with_retry
from selene_space_ashare.ladder import (
    attach_limit_flags,
    build_seal_rate_daily,
    ladder_daily_panel,
    run_length,
)

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "packages" / "ashare" / "data_cache"
PANEL_PATH = CACHE / "ladder_panel.parquet"
CACHE_DIR = CACHE / "ladder_cache"
UNIVERSE_PATH = CACHE / "universe" / "ashare_list.parquet"

# Tencent symbol prefix: Shanghai for 6xx/9xx, Shenzhen otherwise.
_SH_PREFIXES = ("6", "9")


def tx_symbol(code: str) -> str:
    """Map a 6-digit code to a Tencent symbol (``sh``/``sz`` + code)."""
    return ("sh" if code.startswith(_SH_PREFIXES) else "sz") + code


def _normalize_kline(raw: pd.DataFrame, code: str) -> pd.DataFrame:
    """Normalize a Tencent kline frame into the ladder input contract."""
    df = raw.copy()
    df["date"] = df["date"].astype(str).str.replace("-", "", regex=False)
    df = df.sort_values("date").reset_index(drop=True)
    df["symbol"] = code
    return df


def fetch_one(code: str, name: str, start: str, end: str) -> pd.DataFrame | None:
    """Fetch one stock's klines and derive its ladder columns.

    Returns ``None`` when the fetch fails or yields nothing.
    """
    import akshare as ak

    raw = _fetch_with_retry(
        ak.stock_zh_a_hist_tx,
        symbol=tx_symbol(code),
        start_date=start,
        end_date=end,
        adjust="",
    )
    if raw is None or len(raw) == 0:
        return None

    k = _normalize_kline(raw, code)
    flags = attach_limit_flags(k, code=code, name=name)
    # consecutive_boards from the sealed-close flag (run length)
    flags["consecutive_boards"] = run_length(flags["is_limit_up"])
    return flags


def build_panel(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Compose the final daily panel from per-stock annotated frames.

    Reuses :func:`ladder_daily_panel` for breadth/promotion/completeness and
    joins the per-date seal rate derived from the same flags.
    """
    cols = [
        "date",
        "first_board",
        "ge2_count",
        "ge3_count",
        "ge5_count",
        "rungs_filled",
        "max_consecutive",
        "promo_pool",
        "promo_ok",
        "promo_rate",
        "limit_up_count",
        "touched_count",
        "seal_rate",
        "ladder_completeness",
    ]
    if not frames:
        return pd.DataFrame(columns=cols)

    cont = pd.concat(
        [f[["symbol", "date", "consecutive_boards"]] for f in frames],
        ignore_index=True,
    )
    flags = pd.concat(
        [f[["date", "is_limit_up", "touched_limit"]] for f in frames],
        ignore_index=True,
    )

    panel = ladder_daily_panel(cont).merge(
        build_seal_rate_daily(flags), on="date", how="left"
    )
    return panel[cols].sort_values("date").reset_index(drop=True)


def _load_universe(limit: int | None) -> pd.DataFrame:
    if not UNIVERSE_PATH.exists():
        raise SystemExit(
            f"universe list missing: {UNIVERSE_PATH}\n"
            'run: python -c "from selene_space_ashare.data_fetcher '
            'import get_a_share_list; get_a_share_list(force=True)"'
        )
    u = pd.read_parquet(UNIVERSE_PATH)
    u["code"] = u["code"].astype(str).str.zfill(6)
    if limit is not None:
        u = u.head(limit)
    return u.reset_index(drop=True)


def _cache_file(code: str, start: str, end: str) -> Path:
    """Per-stock cache path, keyed by window so a changed window cannot
    silently reuse another window's rows under ``--resume``."""
    return CACHE_DIR / f"{code}_{start}_{end}.parquet"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--start", default="20250101", help="start date YYYYMMDD")
    ap.add_argument("--end", default=pd.Timestamp.today().strftime("%Y%m%d"))
    ap.add_argument(
        "--limit", type=int, default=None, help="only first N universe names (smoke)"
    )
    ap.add_argument(
        "--resume",
        action="store_true",
        help="reuse per-stock cache; skip names already fetched",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=8,
        help="concurrent fetches (1 = serial). 16 measured safe here.",
    )
    args = ap.parse_args(argv)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    universe = _load_universe(args.limit)
    print(f"universe={len(universe)} window={args.start}..{args.end}")

    frames: list[pd.DataFrame] = []
    skipped = 0
    failed = 0

    # Split resume-cached names (instant) from work needing a fetch.
    todo: list[tuple[str, str]] = []
    for _, row in universe.iterrows():
        code = row["code"]
        cache_file = _cache_file(code, args.start, args.end)
        if args.resume and cache_file.exists():
            frames.append(pd.read_parquet(cache_file))
            skipped += 1
        else:
            todo.append((code, str(row.get("name", ""))))

    t0 = time.time()
    done = 0
    if args.workers <= 1:
        for code, name in todo:
            df = fetch_one(code, name, args.start, args.end)
            done += 1
            if df is None:
                failed += 1
                print(f"  [{done}/{len(todo)}] {code} FAILED")
                continue
            df.to_parquet(_cache_file(code, args.start, args.end), index=False)
            frames.append(df)
            if done % 10 == 0 or done == len(todo):
                print(
                    f"  [{done}/{len(todo)}] ok={len(frames)} "
                    f"skip={skipped} fail={failed} {time.time() - t0:.0f}s"
                )
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {
                ex.submit(fetch_one, code, name, args.start, args.end): code
                for code, name in todo
            }
            for fut in as_completed(futures):
                code = futures[fut]
                done += 1
                df = fut.result()
                if df is None:
                    failed += 1
                    print(f"  [{done}/{len(todo)}] {code} FAILED")
                    continue
                df.to_parquet(_cache_file(code, args.start, args.end), index=False)
                frames.append(df)
                if done % 25 == 0 or done == len(todo):
                    print(
                        f"  [{done}/{len(todo)}] ok={len(frames)} "
                        f"skip={skipped} fail={failed} {time.time() - t0:.0f}s"
                    )

    panel = build_panel(frames)
    panel.to_parquet(PANEL_PATH, index=False)
    print(
        f"wrote {PANEL_PATH} rows={len(panel)} cols={list(panel.columns)} "
        f"(skipped={skipped} failed={failed})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
