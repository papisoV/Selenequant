#!/usr/bin/env python
"""Gate forensics: is the PA gate *right* or *over-conservative*? (item 2 + 3)

Read-only. Opens the analysis DB and klines.db with ``mode=ro`` — no migration
runs, no writes. Produces one JSON artifact answering three questions over the
persisted decision-workflow corpus:

1. **Where do records die?**  A histogram of terminal nodes from each record's
   Stage-1 ``trace`` — grouped into data-integrity gates (§1.x), entry-quality
   gates (§2.x/§3.x), and the final verdict (§4.1). A gate that is *broken*
   would show §1.x deaths (bad data) or mass §4.1 rejects; a gate that is
   *over-conservative* would show every record parked in §3.x while the market
   ran away.
2. **What did each record predict, and what happened?**  Counterfactual scoring
   (``eval_counterfactual``) of ``next_period.direction`` against realised
   forward bars, at several horizons.
3. **Is the forward move signal, or just the drift?**  A baseline window-up-rate
   for the same symbols/interval over the same calendar span. Without this,
   "7/7 went up" reads as "the gate was wrong" when the market was simply in an
   uptrend — the exact base-rate trap this script exists to prevent.

The verdict is deliberately *not* computed here: n is tiny, so the script emits
the numbers and the sample size, and a human writes the one-line judgment. Any
downstream citation must carry ``n`` and the baseline, never the raw hit count.

Exit status
-----------
``0``  report written.
``3``  diagnostic: records carry a next_period but no forward bar exists in
       klines.db — sync klines before reading the hit rates.

Usage:
    python scripts/pa_gate_forensics.py
    python scripts/pa_gate_forensics.py --klines-db data/klines.db --out data/tuning_results/
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
for pkg in ("packages/decision-workflow", "packages/storage"):
    path = str(_REPO_ROOT / pkg)
    if path not in sys.path:
        sys.path.insert(0, path)

from selene_space_decision_workflow.eval_counterfactual import (  # noqa: E402
    score_record,
    summarize,
)
from selene_space_decision_workflow.trading_pipeline import load_bars  # noqa: E402

_EXIT_STALE_KLINES = 3

# Gate families in the trading decision tree. Deaths in §1.x mean the data was
# untrustworthy (a *broken* gate); deaths in §2.x/§3.x mean entry quality was
# judged insufficient (the gate working as designed); §4.1 is the final verdict.
_DATA_INTEGRITY_PREFIXES = ("1.",)
_ENTRY_QUALITY_PREFIXES = ("2.", "3.")
_FINAL_VERDICT_PREFIX = "4."

_DEFAULT_HORIZONS = (12, 24, 48)
# Baseline windows are sampled over the calendar span the records fall in, so
# the up-rate is measured in the same regime, not a lifetime average.
_BASELINE_LOOKBACK_DAYS = 30


def _connect_ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _ms(iso: str) -> int | None:
    """Parse a naive-UTC ISO timestamp (as the DB stores it) to epoch ms."""
    try:
        return int(
            datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp() * 1000
        )
    except ValueError:
        return None


def _classify(node_id: str) -> str:
    """Bucket a terminal node id into data / entry-quality / final / unknown."""
    if node_id.startswith(_DATA_INTEGRITY_PREFIXES):
        return "data_integrity"
    if node_id.startswith(_ENTRY_QUALITY_PREFIXES):
        return "entry_quality"
    if node_id.startswith(_FINAL_VERDICT_PREFIX):
        return "final_verdict"
    return "unknown"


def _record_rows(db_path: str, since: str) -> list[dict]:
    """Per-record forensic row: gate state + terminal node + family bucket.

    Reads ``record_json`` for the Stage-1 diagnosis (state/gate/terminal) and
    the trace node ids. Falls back to ``unknown`` buckets rather than guessing
    when a record predates the current tree.
    """
    con = _connect_ro(db_path)
    try:
        rows = con.execute(
            "SELECT id, created_at, symbol, interval, record_json "
            "FROM analysis_records WHERE created_at >= ? ORDER BY created_at",
            (since,),
        ).fetchall()
    finally:
        con.close()

    out: list[dict] = []
    for rid, created, symbol, interval, raw in rows:
        try:
            rec = json.loads(raw)
        except (TypeError, ValueError):
            continue
        s1 = (rec.get("stage1") or {}) if isinstance(rec, dict) else {}
        dod = s1.get("diagnosis_or_decision") or {}
        trace = s1.get("trace") or []
        nodes = [t.get("node_id") for t in trace if isinstance(t, dict)]
        terminal = nodes[-1] if nodes else None
        np_ = dod.get("next_period")
        out.append(
            {
                "record_id": rid,
                "created_at": created,
                "symbol": symbol,
                "interval": interval,
                "state": dod.get("state"),
                "gate_result": dod.get("gate_result"),
                "terminal_node": terminal,
                "family": _classify(terminal) if terminal else "none",
                "trace": nodes,
                "direction": (np_ or {}).get("direction")
                if isinstance(np_, dict)
                else None,
            }
        )
    return out


def _histogram(rows: list[dict]) -> dict:
    by_node: dict[str, int] = {}
    by_family: dict[str, int] = {}
    by_gate: dict[str, int] = {}
    for r in rows:
        by_node[r["terminal_node"] or "?"] = (
            by_node.get(r["terminal_node"] or "?", 0) + 1
        )
        by_family[r["family"]] = by_family.get(r["family"], 0) + 1
        by_gate[r["gate_result"] or "?"] = by_gate.get(r["gate_result"] or "?", 0) + 1
    return {
        "terminal_node": by_node,
        "family": by_family,
        "gate_result": by_gate,
        "n": len(rows),
    }


def _baseline_up_rate(klines_db: Path, rows: list[dict], horizon: int) -> dict:
    """Fraction of *any* forward window that rose, over the records' calendar span.

    This is the denominator the raw hit-rate must be read against: if SOL 4h rose
    in 67% of all windows that month, then a gate parked there during the run is
    not evidence of bias on its own.
    """
    # Span the records: earliest anchor to last anchor + one lookback. Bounding
    # the top keeps the baseline reproducible (klines.db keeps growing, so an
    # open-ended window would drift each run) and confines it to the regime.
    anchors = [r["created_at"] for r in rows if r.get("created_at")]
    if not anchors:
        return {}
    lo = min(anchors)
    hi = max(anchors)
    pairs = sorted({(r["symbol"], r["interval"]) for r in rows if r["symbol"]})
    result: dict[str, dict] = {}

    lo_ms = _ms(lo)
    hi_anchor = _ms(hi)
    hi_ms = (
        hi_anchor + _BASELINE_LOOKBACK_DAYS * 86_400_000
        if hi_anchor is not None
        else None
    )
    for symbol, interval in pairs:
        con = _connect_ro(str(klines_db))
        try:
            # market_type is pinned to 'futures' for the same reason load_bars
            # defaults to it: klines.db stores BOTH spot and futures rows at the
            # same timestamp, so omitting the filter double-counts every window
            # and skews the up-rate (measured: 2× rows, wrong denominator).
            series = con.execute(
                "SELECT timestamp, close FROM klines "
                "WHERE symbol = ? AND interval = ? AND exchange = 'binance' "
                "AND market_type = 'futures' ORDER BY timestamp ASC",
                (symbol, interval),
            ).fetchall()
        finally:
            con.close()
        span = [
            (t, c)
            for (t, c) in series
            if (lo_ms is None or t >= lo_ms) and (hi_ms is None or t <= hi_ms)
        ]
        ups = 0
        windows = 0
        moves: list[float] = []
        for i in range(len(span) - horizon):
            c0, c1 = span[i][1], span[i + horizon][1]
            if not c0:
                continue
            windows += 1
            move = (c1 - c0) / c0
            moves.append(move)
            if move > 0:
                ups += 1
        if windows:
            result[f"{symbol}/{interval}"] = {
                "windows": windows,
                "up_pct": round(100.0 * ups / windows, 1),
                "mean_move_pct": round(100.0 * sum(moves) / len(moves), 3),
            }
    return result


def _counterfactual(analysis_db: str, klines_db: str, horizon: int) -> dict:
    """Population + settled verdicts for the corpus at one horizon."""
    con = _connect_ro(analysis_db)
    try:
        rows = con.execute(
            "SELECT id, record_json FROM analysis_records "
            "WHERE created_at >= '2026-09-01' ORDER BY created_at"
        ).fetchall()
    finally:
        con.close()

    verdicts: list[dict] = []
    population = 0
    any_forward = False
    for rid, raw in rows:
        rec = json.loads(raw)
        if score_record(rec, [], horizon=horizon) is None:
            continue
        population += 1
        idi = rec.get("input_data") or {}
        summary = idi.get("summary") or {}
        anchor = summary.get("window_end_iso")
        symbol = idi.get("symbol")
        interval = summary.get("interval") or idi.get("interval")
        forward: list[dict] = []
        if isinstance(anchor, (int, float)) and symbol:
            forward = load_bars(
                Path(klines_db),
                symbol,
                interval or "1h",
                horizon,
                after_ms=int(anchor) + 1,
                min_bars=0,
            )
        if forward:
            any_forward = True
        settled = score_record(rec, forward, horizon=horizon)
        if settled is not None:
            verdicts.append({"record_id": rid, **settled})
    return {
        "summary": summarize(verdicts),
        "verdicts": verdicts,
        "any_forward_bars": any_forward,
        "population": population,
    }


def _main(args: argparse.Namespace) -> int:
    rows = _record_rows(args.db, args.since)
    report: dict = {
        "generated_by": "pa_gate_forensics",
        "analysis_db": args.db,
        "klines_db": args.klines_db,
        "since": args.since,
        "histogram": _histogram(rows),
        "records": rows,
        "horizons": {},
        "baseline": {},
    }

    any_forward_any = False
    for h in args.horizons:
        cf = _counterfactual(args.db, args.klines_db, h)
        any_forward_any = any_forward_any or cf["any_forward_bars"]
        report["horizons"][str(h)] = {
            "summary": cf["summary"],
            "verdicts": cf["verdicts"],
        }
        report["baseline"][str(h)] = _baseline_up_rate(Path(args.klines_db), rows, h)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"pa-gate-forensics-{stamp}.json"
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    hist = report["histogram"]
    print("PA 门取证(只读)")
    print(f"  记录数                   : {hist['n']}")
    print(f"  终止节点族分布            : {hist['family']}")
    print(f"  gate_result 分布          : {hist['gate_result']}")
    for h, blk in report["horizons"].items():
        s = blk["summary"]
        print(
            f"  horizon={h:>3}: 靶群={s.get('population')} 已结算={s.get('settled')} "
            f"方向={s.get('directional_settled')} 中性={s.get('neutral_settled')} "
            f"命中率={s.get('hit_rate')}"
        )
    print(f"  报告已写入                : {out_path}")

    if any(r["direction"] for r in rows) and not any_forward_any:
        print(
            f"  [诊断] 记录带 next_period,但 klines.db ({args.klines_db}) 无后续 bar "
            f"—— 先 sync klines 再读命中率。",
            file=sys.stderr,
        )
        return _EXIT_STALE_KLINES
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="PA 门取证:直方图 + 反事实 + 基准率(只读)"
    )
    parser.add_argument(
        "--db", default=str(Path.home() / ".selenespace/data/selenespace.db")
    )
    parser.add_argument("--klines-db", default="data/klines.db")
    parser.add_argument("--since", default="2026-09-01", help="只看该日期之后的记录")
    parser.add_argument(
        "--horizons", type=int, nargs="+", default=list(_DEFAULT_HORIZONS)
    )
    parser.add_argument("--out", default="data/tuning_results/")
    sys.exit(_main(parser.parse_args()))


if __name__ == "__main__":
    main()
