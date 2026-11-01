"""Backfill whole-market A-share daily bars into the canonical klines.db.

Why this exists: the screener (structured-condition builder) has no
whole-market daily history to filter on. The three victims are list-1.2 daily
history, the anomaly detector's N-day baseline, and the screener itself. The
existing ``astock/astock`` partition holds 300 symbols of qfq data frozen at
2026-09-21, and ``astock/spot`` is fed by the favorites subscription loop,
which is capped at ``MAX_FAVORITES = 30`` — neither can reach the market.

Source: Sina ``quotes.sina.cn`` kline API, measured available for 5574/5574
A-share symbols (5225 沪深 + 349 北交所) with zero failures. Eastmoney is
deliberately not used — it is punitively throttled from this network
(``http=-1`` / ``RemoteDisconnected``), and production already abandoned it
(see ``astock_source.py``'s module docstring).

Four disciplines, mirroring ``backfill_klines_binance.py``:

* **只增不删** — ``INSERT OR REPLACE`` upsert keyed on the full partition
  tuple, so a rerun is idempotent and never touches other partitions.
* **串行 + 间隔** — one symbol per request, sleep between them, no concurrency.
  Measured on this network: 0.73s/symbol without connection reuse, 0.15s/symbol
  with it (a fresh TLS handshake per request dominates), so the single Session
  below is what keeps a full run near 15 minutes rather than over an hour.
* **诚实报错** — a single symbol's failure never aborts the run; failures are
  listed and the exit code is non-zero.
* **不许静默丢票** — codes that fail prefix validation and rows dropped for
  non-positive OHLC are counted and printed, never swallowed.

Two artifacts land next to the state file, both consumed by
``scripts/verify_daily_all.py``:

* ``gap_report.json`` — per-symbol date jumps > 15 calendar days. These are
  suspensions or data holes; a ratio heuristic (bars ÷ calendar span) cannot
  tell them apart from a recently listed stock, so this walks real dates.
* ``allowlist.json`` — symbols with a first bar at or before the window start
  *and* no detected gap. Deep-history names safe for moving-average
  conditions.

Usage (repo root):
  python -X utf8 scripts/backfill_klines_astock.py --dry-run
  python -X utf8 scripts/backfill_klines_astock.py --years 3 --fresh
  python -X utf8 scripts/backfill_klines_astock.py --resume
  python -X utf8 scripts/backfill_klines_astock.py --symbols 600519.SH,920002.BJ
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for _pkg in ("core", "shared-types", "market-data", "ashare"):
    sys.path.insert(0, str(PROJECT_ROOT / "packages" / _pkg))

from selene_space_ashare.data_fetcher import get_a_share_list  # noqa: E402
from selene_space_ashare.sina_daily import (  # noqa: E402
    GAP_THRESHOLD_DAYS,
    MAX_DATALEN,
    detect_gaps,
    detect_gaps_in_days,
    fetch_daily_rows,
    parse_rows,
)
from selene_space_core.kline_paths import default_klines_db_path  # noqa: E402
from selene_space_core.sina_symbols import code_to_symbol  # noqa: E402
from selene_space_market.kline_db import KlineDB  # noqa: E402
from selene_space_types.market import KlineEvent  # noqa: E402

EXCHANGE = "astock"
MARKET_TYPE = "daily_all"
INTERVAL = "1d"
BATCH_SIZE = 1000
SYMBOL_SLEEP_S = 0.05
CST = timezone(timedelta(hours=8))


def _window_start(years: float) -> str:
    """Inclusive first day of the backfill window, as ``YYYY-MM-DD`` (CST)."""
    today = datetime.now(CST).date()
    return (today - timedelta(days=int(years * 365.25))).isoformat()


def _next_weekday(day: str) -> str:
    """First Monday-Friday on or after ``day``, as ``YYYY-MM-DD``.

    ``window_start`` is a plain calendar date, so it lands on a weekend roughly
    two days in seven — and a window that opens on a Sunday rejects every
    symbol until Monday's bar exists. Aligning the *comparison* date forward is
    what keeps the allowlist from emptying out on weekends without loosening
    the criterion: only names that already traded by the first session of the
    window pass, exactly as on a weekday start.

    Exchange holidays still cost one extra day of tolerance; using the real
    XSHG calendar here would make the artifact's definition depend on an
    optional dependency (``exchange_calendars``), and the acceptance script is
    the place that already reasons in sessions.
    """
    cur = datetime.strptime(day, "%Y-%m-%d")
    while cur.weekday() >= 5:
        cur += timedelta(days=1)
    return cur.strftime("%Y-%m-%d")


def _empty_state() -> dict:
    """The one shape of a fresh state. Callers must not inline this literal:
    a second copy drifting out of sync is how ``gap_report`` went missing.

    Note what is *not* here: holes or first-bar dates. Both artifacts are
    re-derived from the DB at the end of every run (see :func:`_scan_partition`)
    so they cannot lag a resumed run. Carrying them in run state as well would
    be a second source of truth free to disagree with the one acceptance reads.
    """
    return {
        "done": [],
        "failed": [],
        "skipped_invalid": [],
    }


def _load_state(path: Path) -> dict:
    if not path.exists():
        return _empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SystemExit(
            f"state file {path} is unreadable ({type(exc).__name__}: {exc}); "
            f"pass --fresh to start over or delete it"
        ) from exc
    if not isinstance(data, dict):
        # The upgrade path below calls .pop/.setdefault; on a JSON array that
        # is an AttributeError from inside the resume logic, which reads as a
        # bug in this script rather than as a corrupt state file.
        raise SystemExit(
            f"state file {path} is a JSON {type(data).__name__}, expected an "
            f"object; pass --fresh to start over"
        )
    fresh = _empty_state()
    # ``gap_report`` and ``first_day`` were state keys before artifacts became
    # DB-derived; drop them on sight so an upgraded state file does not keep
    # rewriting a stale, contradictory copy of the evidence back to disk.
    data.pop("gap_report", None)
    data.pop("first_day", None)
    for key, default in fresh.items():
        # Missing is the normal upgrade path; present-but-wrong-typed is a
        # corrupt file. ``setdefault`` alone would keep a hand-edited
        # ``"done": null``, and the resume path does ``set(state["done"])``
        # then ``.append`` — a bare TypeError from inside the loop that reads
        # as a bug here rather than as a bad file on disk.
        if key not in data:
            data[key] = default
        elif not isinstance(data[key], list):
            raise SystemExit(
                f"state file {path}: '{key}' must be a list, got "
                f"{type(data[key]).__name__}; pass --fresh to start over"
            )
    return data


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _resolve_universe(explicit: str | None) -> list[str]:
    """``code,name`` pairs as suffixed symbols, or an explicit override."""
    if explicit:
        return [s.strip().upper() for s in explicit.split(",") if s.strip()]

    frame = get_a_share_list()
    out: list[str] = []
    for code in frame["code"]:
        try:
            out.append(code_to_symbol(str(code).strip()))
        except ValueError:
            # Counted as skipped_invalid by the caller once we return pairs;
            # here we surface the bad code rather than dropping it silently.
            out.append(f"INVALID:{code}")
    return out


def _scan_partition(
    db_path: Path, window_start: str
) -> tuple[dict[str, list], dict[str, str]]:
    """Read the ``("astock","daily_all","1d")`` partition back into artifacts.

    Returns ``(gap_report, first_day)``, both keyed by symbol: the interior
    holes (``[[prev_day, next_day, calendar_days], ...]``, only symbols with a
    hole appear) and the first bar date inside the window.

    Why re-derive instead of reusing the loop's tallies: the loop only sees the
    symbols *it* fetched. After ``--resume`` that is the tail of the universe,
    so a run-state artifact silently loses every hole an earlier run found —
    which is exactly how a real run produced a 2-hole report for a market that
    had 16. Reading the DB makes the artifact and the acceptance verdict agree
    by construction, because ``verify_daily_all.py`` reads the same rows.

    Read-only via ``mode=ro``, deliberately not ``KlineDB.open()``: that runs
    ``CREATE TABLE IF NOT EXISTS``, so a typo'd path would produce an empty
    schema and a confident "0 symbols" report instead of an error.
    """
    if not db_path.exists():
        raise SystemExit(f"klines DB not found: {db_path} (nothing to scan)")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    gap_report: dict[str, list] = {}
    first_day: dict[str, str] = {}
    try:
        # Timestamps are CST-midnight epoch millis, so converting back through
        # the same fixed offset recovers the session date exactly.
        cursor = conn.execute(
            "SELECT symbol, timestamp FROM klines "
            "WHERE exchange=? AND market_type=? AND interval=? "
            "AND timestamp >= ? ORDER BY symbol ASC, timestamp ASC",
            (
                EXCHANGE,
                MARKET_TYPE,
                INTERVAL,
                int(
                    datetime.strptime(window_start, "%Y-%m-%d")
                    .replace(tzinfo=CST)
                    .timestamp()
                    * 1000
                ),
            ),
        )
        current: str | None = None
        days: list[str] = []

        def close_symbol() -> None:
            if current is None or not days:
                return
            first_day[current] = days[0]
            gaps = detect_gaps_in_days(days, threshold_days=GAP_THRESHOLD_DAYS)
            if gaps:
                gap_report[current] = [list(g) for g in gaps]

        for symbol, ts_ms in cursor:
            if symbol != current:
                close_symbol()
                current, days = symbol, []
            days.append(datetime.fromtimestamp(ts_ms / 1000, tz=CST).date().isoformat())
        close_symbol()
    finally:
        conn.close()
    return gap_report, first_day


def main() -> int:  # noqa: C901 - a linear pipeline reads better than 6 helpers
    p = argparse.ArgumentParser(
        description="Backfill whole-market A-share daily bars (Sina) into SQLite"
    )
    p.add_argument(
        "--symbols",
        default=None,
        help="逗号分隔的带后缀符号(如 600519.SH,920002.BJ);默认取全市场 universe",
    )
    p.add_argument("--years", type=float, default=3.0, help="回补纵深(年),默认 3")
    p.add_argument(
        "--datalen",
        type=int,
        default=MAX_DATALEN,
        help=f"Sina 请求根数,默认 {MAX_DATALEN}(实测硬上限;1024 可、2000 返空)",
    )
    p.add_argument(
        "--sleep",
        type=float,
        default=SYMBOL_SLEEP_S,
        help="每票间隔秒数,默认 %.2f(串行,不并发)" % SYMBOL_SLEEP_S,
    )
    p.add_argument("--db", default=None, help="默认取 default_klines_db_path()")
    p.add_argument("--state", default=None, help="断点续传状态文件路径")
    p.add_argument(
        "--out-dir", default=None, help="gap_report.json / allowlist.json 落盘目录"
    )
    p.add_argument("--fresh", action="store_true", help="忽略既有状态,从零开始")
    p.add_argument("--resume", action="store_true", help="跳过状态文件中已完成的票")
    p.add_argument("--dry-run", action="store_true", help="只拉取解析,不写库")
    args = p.parse_args()

    if args.fresh and args.resume:
        p.error("--fresh 与 --resume 互斥,请二选一")

    db_path = Path(args.db) if args.db else default_klines_db_path()
    out_dir = Path(args.out_dir) if args.out_dir else PROJECT_ROOT / "data"
    state_path = (
        Path(args.state) if args.state else out_dir / "backfill_astock_state.json"
    )

    state = _empty_state()
    if not args.fresh:
        state = _load_state(state_path)
    skip = set(state["done"]) if (args.resume or not args.fresh) else set()

    universe = _resolve_universe(args.symbols)
    window_start = _window_start(args.years)

    print(
        f"=== {'DRY-RUN' if args.dry_run else 'BACKFILL'}: "
        f"{len(universe)} symbols × {args.years}y {INTERVAL} → {db_path} ==="
    )
    print(f"    window starts: {window_start} (CST)   datalen={args.datalen}")
    print(f"    state: {state_path}   out: {out_dir}")
    if skip:
        print(f"    resuming: skipping {len(skip)} already-completed symbols")

    db = None if args.dry_run else KlineDB(db_path=str(db_path))
    if db is not None:
        db.open()

    # One Session for the whole run: without it every request pays a fresh TLS
    # handshake, measured at 0.73s vs 0.15s per symbol — an 11-hour run becomes
    # a 15-minute one. ``trust_env=False`` keeps the machine's HTTP proxy out
    # of the path; the same convention ``data_fetcher`` uses for Eastmoney.
    session = requests.Session()
    session.trust_env = False

    # Loop-level holes. These are what ``--dry-run`` reports, since a dry run
    # has no DB to read back. A real run overwrites this wholesale from the DB
    # below — and the DB version is stricter: the loop sees every day the
    # source returned, while the DB lacks the rows dropped for non-positive
    # OHLC, so a halted day is a hole to the consumer and gets reported as one.
    gap_report: dict[str, list] = {}
    observed_first_day: dict[str, str] = {}
    written_total = 0
    buf: list[KlineEvent] = []
    pending: list[str] = []
    skipped_invalid: list[str] = []
    failed: list[str] = []
    dropped_nonpositive = 0
    dropped_unparsable = 0
    empty_symbols = 0
    started = time.monotonic()

    def flush() -> None:
        nonlocal written_total
        if db is not None and buf:
            written_total += db.put_klines(buf)
        buf.clear()
        for sym in pending:
            if sym not in state["done"]:
                state["done"].append(sym)
        pending.clear()
        if not args.dry_run:
            _save_state(state_path, state)

    try:
        for i, sym in enumerate(universe, 1):
            if sym.startswith("INVALID:"):
                bad = sym.split(":", 1)[1]
                skipped_invalid.append(bad)
                print(f"  [{i}/{len(universe)}] SKIP invalid code {bad!r}")
                continue
            if sym in skip:
                continue
            try:
                rows = fetch_daily_rows(sym, datalen=args.datalen, session=session)
            except Exception as exc:  # 单票失败不中断全局
                failed.append(sym)
                print(
                    f"  [{i}/{len(universe)}] {sym:12s} FAILED: "
                    f"{type(exc).__name__}: {exc}"
                )
                continue

            # Window filter on the date string — exact, no timezone arithmetic.
            rows = [r for r in rows if r.get("day", "") >= window_start]
            if not rows:
                empty_symbols += 1
                print(f"  [{i}/{len(universe)}] {sym:12s} empty inside window")
                continue

            gaps = detect_gaps(rows, threshold_days=GAP_THRESHOLD_DAYS)
            if gaps:
                gap_report[sym] = [list(g) for g in gaps]

            result = parse_rows(
                rows, sym, market_type=MARKET_TYPE, exchange=EXCHANGE, interval=INTERVAL
            )
            dropped_nonpositive += result.dropped_nonpositive
            dropped_unparsable += result.dropped_unparsable
            buf.extend(result.events)
            pending.append(sym)
            # First *stored* bar, not first fetched row: the DB-derived
            # first_day on a real run counts only rows that survived parsing,
            # and the two must mean the same thing or a dry run's allowlist
            # would be sized against a different population.
            if result.events:
                observed_first_day[sym] = min(
                    datetime.fromtimestamp(e.timestamp / 1000, tz=CST)
                    .date()
                    .isoformat()
                    for e in result.events
                )
            if len(buf) >= BATCH_SIZE:
                flush()
            if i % 250 == 0 or i == len(universe):
                elapsed = time.monotonic() - started
                rate = i / elapsed if elapsed else 0.0
                eta = (len(universe) - i) / rate if rate else 0.0
                print(
                    f"  [{i}/{len(universe)}] {sym:12s} "
                    f"{elapsed:5.0f}s elapsed, ~{eta:4.0f}s left, "
                    f"{written_total:,} rows written"
                )
            time.sleep(args.sleep)
        flush()
    finally:
        session.close()
        if db is not None:
            db.close()

    # Compare against the window's first session, not its raw calendar date:
    # a Sunday/Monday-adjacent start would otherwise reject every symbol.
    first_session = _next_weekday(window_start)

    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        # Re-derived from the DB, not from this loop's tallies. The loop only
        # sees the symbols *it* fetched; after a `--resume` that is a subset,
        # so run-state artifacts silently lose every hole an earlier run found
        # — measured: a resumed run reported 2 holes for a market with 16. The
        # DB is the same source of truth the acceptance script reads, which is
        # what makes the artifact and the verdict agree by construction.
        gap_report, first_day = _scan_partition(db_path, window_start)
        (out_dir / "gap_report.json").write_text(
            json.dumps(gap_report, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        # A symbol is "deep history" only if it was already trading when the
        # window opened AND shows no interior hole. Testing first-bar against
        # window_start is what separates a long-listed name from a recent IPO,
        # which a bar count alone cannot do.
        # The comparison targets the window's first session (see
        # ``_next_weekday``): window_start is a plain calendar date and usually
        # lands on a weekend, so an exact ``first_day <= window_start`` rejects
        # every symbol whenever that happens — a run started on 2026-10-28
        # opened a 2023-10-29 (Sunday) window and produced an empty allowlist
        # for a full market.
        allowlist = sorted(
            s
            for s in first_day
            if s not in gap_report and first_day[s] <= first_session
        )
        (out_dir / "allowlist.json").write_text(
            json.dumps(
                {
                    "window_start": window_start,
                    "criteria": (
                        "first bar <= first session on/after window_start AND "
                        f"no detected gap > {GAP_THRESHOLD_DAYS} calendar days"
                    ),
                    "symbols": allowlist,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
    else:
        # A dry run writes no DB, so the only evidence available is what this
        # pass observed. Same criteria, narrower population — stated rather
        # than silently substituted for the real artifact. Uses the same
        # first-session cutoff, so a dry run and the real run cannot disagree.
        allowlist = sorted(
            s
            for s, day in observed_first_day.items()
            if s not in gap_report and day <= first_session
        )
    if failed:
        state["failed"] = failed
    state["skipped_invalid"] = skipped_invalid
    if not args.dry_run:
        _save_state(state_path, state)

    elapsed = time.monotonic() - started
    print(f"  {'─' * 60}")
    print(f"  TOTAL {written_total:,} rows written in {elapsed:.0f}s")
    print(
        f"  symbols completed: {len(state['done'])}   empty-in-window: {empty_symbols}"
    )
    print(
        f"  dropped rows: nonpositive OHLC={dropped_nonpositive}   "
        f"unparsable={dropped_unparsable}"
    )
    print(
        f"  gaps detected: {len(gap_report)} symbols   "
        f"allowlist: {len(allowlist)} symbols"
    )
    if skipped_invalid:
        print(
            f"  SKIPPED invalid codes: {len(skipped_invalid)} "
            f"(e.g. {skipped_invalid[:5]})"
        )
    if failed:
        print(f"  FAILED symbols: {len(failed)} (e.g. {failed[:5]})")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
