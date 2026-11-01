"""Acceptance check for the whole-market A-share daily base (plan v2.1 §5).

Three criteria, all mechanically decidable against the live DB plus the
artifacts ``scripts/backfill_klines_astock.py`` produced:

1. **Coverage** — ``COUNT(DISTINCT symbol) >= --expect`` (default 5574, the
   market size measured from ``get_a_share_list()``).
2. **Depth on deep-history names** — every symbol in ``allowlist.json`` holds at
   least ``--min-coverage`` (default 90%) of the exchange calendar's sessions
   inside the window. Expressed as a ratio against the calendar rather than an
   absolute bar count, so an honest name that halts often is not mistaken for a
   truncated one. The allowlist already excludes suspended and recently-listed
   names, which is what made the naive "every symbol has >= 700 bars" version of
   this check unsatisfiable.
3. **No unexplained interior gap** — every symbol in ``gap_report.json`` with a
   hole is either on the suspension allowlist or is a failure. The allowlist is
   the *input* here (produced by B0); this script only asserts nothing outside
   it has a hole.

Exit code is non-zero if any criterion fails, so it can gate a release.

Usage (repo root):
  python -X utf8 scripts/verify_daily_all.py
  python -X utf8 scripts/verify_daily_all.py --data-dir data --expect 5574
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for _pkg in ("core", "shared-types", "market-data"):
    sys.path.insert(0, str(PROJECT_ROOT / "packages" / _pkg))

from selene_space_core.kline_paths import default_klines_db_path  # noqa: E402

EXCHANGE = "astock"
MARKET_TYPE = "daily_all"
INTERVAL = "1d"
CST = timezone(timedelta(hours=8))


def verify_no_gaps(gap_report: dict, suspended: set[str]) -> list[str]:
    """Symbols with an interior hole and no suspension excuse.

    Empty list = pass. A hole outside ``suspended`` means either a data gap the
    source could not fill or a backfill bug — both must be looked at, so this
    returns them rather than logging and moving on.
    """
    return [
        symbol
        for symbol, gaps in sorted(gap_report.items())
        if gaps and symbol not in suspended
    ]


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"missing artifact: {path} (run the backfill first)")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SystemExit(f"unreadable artifact {path}: {type(exc).__name__}: {exc}")
    # A JSON array or scalar parsed without error but carries no artifacts. The
    # callers below index into this, so say so here rather than let a
    # ``.get`` on a list fail with an AttributeError three lines later.
    if not isinstance(data, dict):
        raise SystemExit(
            f"artifact {path} is a JSON {type(data).__name__}, expected an object"
        )
    return data


def _require_list(doc: dict, key: str, path: Path) -> list:
    """``doc[key]`` as a list, or a named error.

    The outer ``isinstance(data, dict)`` only guarantees the *document* is an
    object. These artifacts are hand-edited in practice — ``allowlist.json`` is
    the frozen review surface the plan defines — so a key can still hold a
    scalar or an object. Indexing that gives a bare ``TypeError`` from inside
    analysis code, which reads as a bug here rather than as a malformed file.
    """
    value = doc.get(key, [])
    if not isinstance(value, list):
        raise SystemExit(f"{path}: '{key}' must be a list, got {type(value).__name__}")
    return value


def _require_gap_values(doc: dict, path: Path) -> dict:
    """Every symbol in ``gap_report`` must map to a list of holes.

    The document guard in ``_load_json`` only proves the outer object is a dict.
    Both readers of this artifact test the *value* for truthiness — the
    ``gapped`` count in criterion (3) and ``verify_no_gaps`` — so a value that is
    falsy but not an empty list is read as "this symbol has no hole". Measured:
    ``{"600519.SH": null}`` returns ``[]`` from ``verify_no_gaps`` and an empty
    ``gapped``, i.e. a hidden hole reported as a clean pass. That is a fail-open
    on the one artifact whose whole job is to make holes visible, so it is
    rejected rather than coerced.
    """
    for symbol, gaps in doc.items():
        if not isinstance(gaps, list):
            raise SystemExit(
                f"{path}: '{symbol}' must map to a list of holes, got "
                f"{type(gaps).__name__}"
            )
    return doc


def _require_str(doc: dict, key: str, path: Path) -> str | None:
    """``doc[key]`` as a string (or absent), or a named error.

    ``window_start`` is fed to ``datetime.strptime``; an int there fails with a
    bare ``TypeError`` from inside the calendar math rather than pointing at the
    file. Absent stays ``None`` so the caller's own "no window_start" failure
    still fires.
    """
    value = doc.get(key)
    if value is not None and not isinstance(value, str):
        raise SystemExit(
            f"{path}: '{key}' must be a string, got {type(value).__name__}"
        )
    return value


def load_suspensions(path: Path) -> set[str]:
    """Symbols whose interior hole is an explained trading halt.

    Deliberately a file rather than a constant: criterion (3) is only a real
    gate if adding an excuse is a visible, reviewable act. A hardcoded set
    would silently drift into "whatever the last run produced" and the check
    would stop being able to fail — the vacuous-gate antipattern this whole
    script exists to avoid.

    Shape: ``{"reason": "...", "symbols": ["000670.SZ", ...]}``.
    """
    if not path.exists():
        return set()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SystemExit(f"unreadable suspensions file {path}: {exc}") from exc
    symbols = doc.get("symbols", [])
    if not isinstance(symbols, list):
        raise SystemExit(f"{path}: 'symbols' must be a list, got {type(symbols)}")
    return {str(s) for s in symbols}


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    """Open the DB read-only.

    Deliberately not ``KlineDB.open()``: that runs ``CREATE TABLE IF NOT
    EXISTS``, so pointing this script at a path with no data would silently
    create an empty schema and then report "0 symbols" — an absence that reads
    like a finding instead of a wrong file.
    """
    if not db_path.exists():
        raise SystemExit(f"klines DB not found: {db_path}")
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _require_day(value: str, source: str) -> str:
    """``value`` as a ``YYYY-MM-DD`` day, or a named error.

    ``_require_str`` proves the *type* is a string; ``"2023-13-45"`` is still a
    string. ``window_start`` and ``window_end`` are both parsed by
    ``datetime.strptime`` inside ``_day_ms``, so a hand-edited ``allowlist.json``
    or a typo'd flag otherwise raises a bare ``ValueError`` from the calendar
    math — reading as a bug here rather than as a bad date on the input.
    """
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise SystemExit(f"{source}: {value!r} is not a YYYY-MM-DD date")
    return value


def _day_ms(day: str, *, end_of_day: bool = False) -> int:
    """CST-midnight epoch millis for ``YYYY-MM-DD``; optionally end of day.

    Same convention the backfill stamps bars with, so the window boundary is
    counted identically on both sides.
    """
    dt = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=CST)
    if end_of_day:
        dt = dt + timedelta(days=1) - timedelta(milliseconds=1)
    return int(dt.timestamp() * 1000)


def count_sessions(start: str, end: str) -> int | None:
    """XSHG trading sessions in ``[start, end]``, or ``None`` if unavailable.

    The exchange calendar, not a bar count, is what makes criterion (2) a real
    test: "did this symbol trade on most of the days the market was open" is
    answerable even for a name that halts often, whereas "has 700 bars" silently
    encodes an assumption that nothing ever halts.

    Imported lazily and defensively — a missing calendar must degrade to a
    loud SKIP, never to a pass.
    """
    try:
        import exchange_calendars as xcals

        cal = xcals.get_calendar("XSHG")
        return len(cal.sessions_in_range(start, end))
    except Exception:
        return None


def main() -> int:
    p = argparse.ArgumentParser(description="Verify the whole-market daily base")
    p.add_argument("--db", default=None, help="默认取 default_klines_db_path()")
    p.add_argument(
        "--data-dir",
        default=str(PROJECT_ROOT / "data"),
        help="gap_report.json / allowlist.json 所在目录",
    )
    p.add_argument(
        "--expect",
        type=int,
        default=5574,
        help="期望的最少 symbol 数(全市场规模),默认 5574",
    )
    p.add_argument(
        "--min-coverage",
        type=float,
        default=0.90,
        help="allowlist 票相对交易日历的最少覆盖率,默认 0.90",
    )
    p.add_argument(
        "--window-start",
        default=None,
        help="窗内根数统计的起算日 YYYY-MM-DD;默认取 allowlist.json 记录值",
    )
    p.add_argument(
        "--window-end",
        default=None,
        help="窗内统计的截止日 YYYY-MM-DD;默认今天(北京时间)",
    )
    p.add_argument(
        "--suspensions",
        default=None,
        help="停牌白名单 JSON(默认 <data-dir>/suspensions.json;不存在=空)",
    )
    args = p.parse_args()
    if args.window_end is None:
        args.window_end = datetime.now(CST).date().isoformat()

    db_path = Path(args.db) if args.db else default_klines_db_path()
    data_dir = Path(args.data_dir)
    susp_path = (
        Path(args.suspensions) if args.suspensions else data_dir / "suspensions.json"
    )
    gap_report_path = data_dir / "gap_report.json"
    gap_report = _require_gap_values(_load_json(gap_report_path), gap_report_path)
    allowlist_path = data_dir / "allowlist.json"
    allowlist_doc = _load_json(allowlist_path)
    allowlist = set(_require_list(allowlist_doc, "symbols", allowlist_path))
    suspended = load_suspensions(susp_path)
    window_start = args.window_start or _require_str(
        allowlist_doc, "window_start", allowlist_path
    )
    # Validated here rather than at each use: window_start arrives from either
    # --window-start or allowlist.json, and window_end from --window-end or the
    # clock, so the malformed one can be an artifact just as easily as a flag.
    if window_start:
        _require_day(window_start, "window_start")
    _require_day(args.window_end, "window_end")

    print(f"=== verify_daily_all: {db_path} ===")
    print(f"    artifacts: {data_dir}   window_start={window_start}")
    print(f"    suspensions: {susp_path} ({len(suspended)} excuses)")

    failures: list[str] = []
    conn = _connect_readonly(db_path)
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "klines" not in tables:
            raise SystemExit(f"{db_path} has no 'klines' table — wrong database file?")

        # (1) coverage -------------------------------------------------------
        row = conn.execute(
            "SELECT COUNT(DISTINCT symbol) FROM klines "
            "WHERE exchange=? AND market_type=? AND interval=?",
            (EXCHANGE, MARKET_TYPE, INTERVAL),
        ).fetchone()
        distinct = int(row[0])
        # The gate is the `failures.append` below, not this flag — the flag
        # only picks the word printed. Kept inline so the two can never drift
        # into disagreeing about what "ok" means.
        print(
            f"\n(1) coverage: {distinct} distinct symbols "
            f"(expect >= {args.expect}) → "
            f"{'PASS' if distinct >= args.expect else 'FAIL'}"
        )
        if distinct < args.expect:
            failures.append(
                f"coverage: {distinct} distinct symbols < expected {args.expect}"
            )

        # (2) depth on allowlist names --------------------------------------
        # Measured as a *ratio* against the exchange calendar, not an absolute
        # bar count. The original ">= 700 bars" assumed a halt-free stock: the
        # 3-year window holds 727 XSHG sessions, so 700 demanded ~96% coverage
        # of every name. 603389.SH trades on 694 of 727 (95.5%) because it
        # spends ~33 sessions in many short halts, none long enough to trip the
        # 15-day gap test — an honest name rejected by the absolute test, whose
        # "failure" was indistinguishable from a real truncation.
        #
        # The ratio keeps the truncation signal (a partial backfill or a wrong
        # symbol collapses it) while letting ordinary halts through. Measured
        # on the live base, allowlist coverage runs min 0.9546, p5 0.9931,
        # median 1.0000 — so 0.90 sits well clear of the honest tail.
        if not window_start:
            failures.append(
                "depth: no window_start available (allowlist.json lacks it and "
                "--window-start was not given)"
            )
            print("\n(2) depth: SKIPPED — no window_start")
        else:
            # Reuse the same CST-midnight convention the backfill stamps with,
            # so the boundary bar is counted identically on both sides.
            start_ms = _day_ms(window_start)
            sessions = count_sessions(window_start, args.window_end)
            if sessions is None:
                failures.append(
                    "depth: exchange calendar unavailable for "
                    f"{window_start}..{args.window_end}; cannot compute coverage"
                )
                print("\n(2) depth: SKIPPED — calendar unavailable")
            else:
                shallow: list[tuple[str, int, float]] = []
                ordered = sorted(allowlist)
                end_ms = _day_ms(args.window_end, end_of_day=True)
                # Chunked IN-list keeps the statement under SQLite's variable cap.
                for i in range(0, len(ordered), 500):
                    chunk = ordered[i : i + 500]
                    placeholders = ",".join("?" * len(chunk))
                    rows = conn.execute(
                        f"SELECT symbol, COUNT(*) AS n FROM klines "
                        f"WHERE exchange=? AND market_type=? AND interval=? "
                        f"AND timestamp >= ? AND timestamp <= ? "
                        f"AND symbol IN ({placeholders}) GROUP BY symbol",
                        (
                            EXCHANGE,
                            MARKET_TYPE,
                            INTERVAL,
                            start_ms,
                            end_ms,
                            *chunk,
                        ),
                    ).fetchall()
                    # Every allowlist symbol is compared against the calendar,
                    # so a symbol with rows but no result here (0 rows) is
                    # caught by the same expression as a thin one.
                    seen = {str(r[0]): int(r[1]) for r in rows}
                    for sym in chunk:
                        n = seen.get(sym, 0)
                        if n < sessions * args.min_coverage:
                            shallow.append((sym, n, n / sessions))
                print(
                    f"(2) depth: {len(allowlist)} allowlist symbols vs "
                    f"{sessions} calendar sessions, {len(shallow)} below "
                    f"{args.min_coverage:.0%} coverage → "
                    f"{'PASS' if not shallow else 'FAIL'}"
                )
                if shallow:
                    worst = sorted(shallow, key=lambda r: r[2])[:5]
                    print(
                        "    shallow (symbol, bars, coverage): "
                        f"{[(s, n, round(r, 3)) for s, n, r in worst]}"
                    )
                    failures.append(
                        f"depth: {len(shallow)} allowlist symbols below "
                        f"{args.min_coverage:.0%} of {sessions} sessions"
                    )

        # (3) no unexplained interior gap -----------------------------------
        # A real gate: a hole is a failure unless the symbol is on the
        # suspension allowlist. Printing "REVIEW" and returning 0 would make
        # this criterion decorative.
        gapped = sorted(s for s, g in gap_report.items() if g)
        violators = verify_no_gaps(gap_report, suspended=suspended)
        print(
            f"(3) gaps: {len(gapped)} symbols report an interior hole, "
            f"{len(suspended)} excused as halts → "
            f"{'PASS' if not violators else 'FAIL'}"
        )
        if violators:
            print(
                f"    unexplained holes ({len(violators)}), first 10: {violators[:10]}"
            )
            print(
                f"    if these are genuine trading halts, add them to "
                f"{susp_path} with a reason; otherwise they are data holes "
                f"the source could not fill."
            )
            failures.append(
                f"gaps: {len(violators)} symbols have an unexplained interior "
                f"hole (first: {violators[:3]})"
            )
        elif gapped:
            print(f"    all {len(gapped)} hole symbols are on the suspension allowlist")
    finally:
        conn.close()

    # Artifacts that came back empty mean the backfill never really ran.
    if not gap_report and not allowlist:
        failures.append(
            "artifacts are empty — did the backfill actually fetch anything?"
        )

    print(f"\n{'─' * 60}")
    if failures:
        print("RESULT: FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
