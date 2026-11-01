"""Stage B 24h Shadow Run verdict script.

Reads `data/stage_b/observations.jsonl` + `~/.selenespace/journal/order_journal.db`
and judges the 7 acceptance criteria (24h 验收标准).

Criteria:
  1. Shadow engine 无崩溃 (restarts == 0 OR observation recovers within 30s)
  2. Journal WAL 完整 (no status='intent' lagging > 5min)
  3. Signal count > 0 (client_order_id LIKE 'sso-%')
  4. Fill count > 0 (status='confirmed')
  5. Shadow vs paper equity 偏差 < 5% (equity_deviation_pct from observations)
  6. KillSwitch 触发 — wiring marker + journal cancel evidence
     (status='failed' reason='cancelled_by_kill_switch'); if never activated
     during the run → EXPECTED_FAIL (manual activation is a human step)
  7. 重启后 state 恢复 (positions row count consistent across restart)

Exit code: 0 if ≥6 PASS (EXPECTED FAIL does not count as fail), else 1.

Usage:
  python scripts/stage_b_verdict.py
  python scripts/stage_b_verdict.py --obs-path=data/stage_b/observations.jsonl \
      --journal-path=~/.selenespace/journal/order_journal.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DEFAULT_OBS_PATH = "data/stage_b/observations.jsonl"
_DEFAULT_JOURNAL_PATH = str(Path.home() / ".selenespace" / "journal" / "order_journal.db")
_DEFAULT_RESTARTS_PATH = "data/stage_b/restarts.jsonl"
_DEFAULT_REPORT_PATH = "data/stage_b/stage_b_report.md"

_INTENT_LAG_MS = 5 * 60 * 1000  # 5 minutes
_RECOVERY_WINDOW_MS = 30 * 1000  # 30 seconds
_EQUITY_DEV_THRESHOLD_PCT = 5.0  # Stage B criterion 5: shadow vs paper < 5%


@dataclass
class Verdict:
    """Single criterion verdict."""

    index: int
    name: str
    status: str  # PASS, FAIL, EXPECTED_FAIL
    data: str
    root_cause: str = ""


@dataclass
class Report:
    """Stage B verdict report."""

    verdicts: list[Verdict] = field(default_factory=list)

    def pass_count(self) -> int:
        return sum(1 for v in self.verdicts if v.status == "PASS")

    def fail_count(self) -> int:
        # EXPECTED_FAIL does not count as fail
        return sum(1 for v in self.verdicts if v.status == "FAIL")

    def expected_fail_count(self) -> int:
        return sum(1 for v in self.verdicts if v.status == "EXPECTED_FAIL")

    def passed(self) -> bool:
        # ≥6 PASS out of 7 (EXPECTED FAIL not counted as fail)
        return self.pass_count() >= 6 and self.fail_count() == 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage B 24h Shadow Run verdict script",
    )
    parser.add_argument(
        "--obs-path",
        default=_DEFAULT_OBS_PATH,
        help=f"Path to observations.jsonl (default: {_DEFAULT_OBS_PATH})",
    )
    parser.add_argument(
        "--journal-path",
        default=_DEFAULT_JOURNAL_PATH,
        help=f"Path to order_journal.db (default: {_DEFAULT_JOURNAL_PATH})",
    )
    parser.add_argument(
        "--restarts-path",
        default=_DEFAULT_RESTARTS_PATH,
        help=f"Path to restarts.jsonl (default: {_DEFAULT_RESTARTS_PATH})",
    )
    parser.add_argument(
        "--report-path",
        default=_DEFAULT_REPORT_PATH,
        help=f"Path to write report.md (default: {_DEFAULT_REPORT_PATH})",
    )
    return parser.parse_args()


def load_observations(obs_path: str) -> list[dict[str, Any]]:
    """Read JSON lines from observations file. Returns list of dicts."""
    path = Path(obs_path)
    if not path.exists():
        return []
    observations: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                observations.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return observations


def load_restarts(restarts_path: str) -> list[dict[str, Any]]:
    """Read JSON lines from restarts file. Returns list of dicts."""
    path = Path(restarts_path)
    if not path.exists():
        return []
    restarts: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                restarts.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return restarts


def read_journal_db(journal_path: str) -> sqlite3.Connection:
    """Open SQLite connection to order_journal.db (read-only)."""
    path = Path(journal_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Journal DB not found: {path}")
    # Open read-only via URI (do not set PRAGMA journal_mode — that's a write op
    # and would fail under read-only mode; the WAL mode is already set by the
    # writer process via _init_db).
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    return con


def check_1_no_crash(
    observations: list[dict[str, Any]],
    restarts: list[dict[str, Any]],
) -> Verdict:
    """Criterion 1: Shadow engine no crash.

    PASS if restarts == 0 OR every restart followed by observation within 30s.
    """
    if not restarts:
        return Verdict(
            index=1,
            name="Shadow engine 无崩溃",
            status="PASS",
            data="restarts=0",
        )

    # Verify each restart had observation recovery within 30s
    obs_timestamps = sorted(
        obs.get("ts", 0) for obs in observations if obs.get("ts")
    )
    unrecovered: list[str] = []
    for restart in restarts:
        restart_ts = restart.get("ts", 0)
        recovery_ts = next(
            (ts for ts in obs_timestamps if ts > restart_ts and ts - restart_ts <= _RECOVERY_WINDOW_MS),
            None,
        )
        if recovery_ts is None:
            unrecovered.append(restart.get("event", f"restart@{restart_ts}"))

    if unrecovered:
        return Verdict(
            index=1,
            name="Shadow engine 无崩溃",
            status="FAIL",
            data=f"restarts={len(restarts)}, unrecovered={len(unrecovered)}",
            root_cause=f"Restart(s) without 30s observation recovery: {', '.join(unrecovered)}",
        )
    return Verdict(
        index=1,
        name="Shadow engine 无崩溃",
        status="PASS",
        data=f"restarts={len(restarts)}, all recovered within 30s",
    )


def check_2_wal_complete(con: sqlite3.Connection) -> Verdict:
    """Criterion 2: Journal WAL complete.

    PASS if no status='intent' sso- entries lagging > 5min.
    """
    cutoff_ms = int(time.time() * 1000) - _INTENT_LAG_MS
    cur = con.execute(
        """
        SELECT COUNT(*) FROM order_journal
        WHERE status='intent'
          AND client_order_id LIKE 'sso-%'
          AND updated_at < ?
        """,
        (cutoff_ms,),
    )
    stale_count = int(cur.fetchone()[0])
    if stale_count > 0:
        # Fetch one example for root cause
        cur = con.execute(
            """
            SELECT client_order_id, status, updated_at FROM order_journal
            WHERE status='intent' AND client_order_id LIKE 'sso-%'
              AND updated_at < ?
            ORDER BY updated_at ASC LIMIT 1
            """,
            (cutoff_ms,),
        )
        row = cur.fetchone()
        example = f"client_order_id={row[0]}, updated_at={row[2]}" if row else ""
        return Verdict(
            index=2,
            name="Journal WAL 完整",
            status="FAIL",
            data=f"stale_intent={stale_count}",
            root_cause=f"Intent entries lagging > 5min. Example: {example}",
        )
    return Verdict(
        index=2,
        name="Journal WAL 完整",
        status="PASS",
        data="stale_intent=0",
    )


def check_3_signal_count(con: sqlite3.Connection, run_start_ts: int | None = None) -> Verdict:
    """Criterion 3: Signal count > 0 (sso- prefix).

    If run_start_ts is provided, only counts signals created during the run
    (created_at >= run_start_ts) to avoid contamination from prior runs.
    """
    if run_start_ts is not None:
        cur = con.execute(
            "SELECT COUNT(*) FROM order_journal WHERE client_order_id LIKE 'sso-%' AND created_at >= ?",
            (run_start_ts,),
        )
    else:
        cur = con.execute(
            "SELECT COUNT(*) FROM order_journal WHERE client_order_id LIKE 'sso-%'"
        )
    count = int(cur.fetchone()[0])
    if count > 0:
        return Verdict(
            index=3,
            name="Signal count > 0",
            status="PASS",
            data=f"signal_count={count}" + (f" (since run_start={run_start_ts})" if run_start_ts else ""),
        )
    return Verdict(
        index=3,
        name="Signal count > 0",
        status="FAIL",
        data="signal_count=0",
        root_cause="No sso- orders in journal during this run. Check BB_v3 warmup (need ≥ bb_len=5 bars) and interval=1m.",
    )


def check_4_fill_count(con: sqlite3.Connection, run_start_ts: int | None = None) -> Verdict:
    """Criterion 4: Fill count > 0 (status='confirmed').

    If run_start_ts is provided, only counts fills created during the run.
    """
    if run_start_ts is not None:
        cur = con.execute(
            """
            SELECT COUNT(*) FROM order_journal
            WHERE status='confirmed' AND client_order_id LIKE 'sso-%' AND updated_at >= ?
            """,
            (run_start_ts,),
        )
    else:
        cur = con.execute(
            """
            SELECT COUNT(*) FROM order_journal
            WHERE status='confirmed' AND client_order_id LIKE 'sso-%'
            """
        )
    count = int(cur.fetchone()[0])
    if count > 0:
        return Verdict(
            index=4,
            name="Fill count > 0",
            status="PASS",
            data=f"fill_count={count}" + (f" (since run_start={run_start_ts})" if run_start_ts else ""),
        )
    return Verdict(
        index=4,
        name="Fill count > 0",
        status="FAIL",
        data="fill_count=0",
        root_cause="No confirmed sso- fills during this run. Check _bridge_price_to_shadow wiring and on_price_tick logs.",
    )


def check_5_equity_deviation(observations: list[dict[str, Any]]) -> Verdict:
    """Criterion 5: Shadow vs paper equity deviation < 5%.

    Reads equity_deviation_pct from each observation that carries equity data
    (stage_b_shadow_run injects the latest snapshot per fill). Passes iff EVERY
    sampled deviation is < 5% — the deviation must hold continuously across
    the run, not just at the final snapshot.
    """
    sampled = [
        (o.get("ts", 0), float(o["equity_deviation_pct"]))
        for o in observations
        if o.get("equity_deviation_pct") is not None
    ]
    if not sampled:
        return Verdict(
            index=5,
            name="Shadow vs paper equity 偏差 < 5%",
            status="FAIL",
            data="no_equity_snapshots",
            root_cause="No equity deviation sampled. shadow_engine did not persist equity_snapshots — check fills > 0 and the live_state.db write path",
        )
    violations = [(ts, dev) for ts, dev in sampled if dev >= _EQUITY_DEV_THRESHOLD_PCT]
    max_dev = max(dev for _, dev in sampled)
    if violations:
        ts, dev = violations[-1]
        return Verdict(
            index=5,
            name="Shadow vs paper equity 偏差 < 5%",
            status="FAIL",
            data=f"samples={len(sampled)} max_dev={max_dev:.4f}%",
            root_cause=f"Equity deviation ≥5% at ts={ts} (dev={dev:.4f}%). Investigate RealisticSlippage vs paper slippage divergence",
        )
    return Verdict(
        index=5,
        name="Shadow vs paper equity 偏差 < 5%",
        status="PASS",
        data=f"samples={len(sampled)} max_dev={max_dev:.4f}%",
    )


def check_6_killswitch(con: sqlite3.Connection | None) -> Verdict:
    """Criterion 6: KillSwitch wired + actually cancels orders on activation.

    Wiring evidence: marker file data/stage_b/killswitch_wired.json written by
    main.py after set_kill_switch(). Cancellation evidence: journal rows with
    status='failed' reason='cancelled_by_kill_switch' — written by the engine's
    cancel_all_open_orders() when the kill switch activates. Manual activation
    is a human Step-B step, so a run with no activation yields EXPECTED_FAIL
    (not a failure): wiring verified, cancellation pending manual trigger.
    """
    marker_path = Path("data/stage_b/killswitch_wired.json")
    if not marker_path.exists():
        return Verdict(
            index=6,
            name="KillSwitch 触发",
            status="FAIL",
            data=f"marker file not found: {marker_path}",
            root_cause="shadow_engine.set_kill_switch() did not run; main.py KillSwitch wiring missing or backend not started",
        )
    try:
        marker = json.loads(marker_path.read_text())
    except Exception as exc:
        return Verdict(
            index=6,
            name="KillSwitch 触发",
            status="FAIL",
            data=f"marker file invalid JSON: {exc}",
            root_cause=f"killswitch_wired.json parse error at {marker_path}",
        )
    if not marker.get("wired"):
        return Verdict(
            index=6,
            name="KillSwitch 触发",
            status="FAIL",
            data=f"marker wired=False: {marker}",
            root_cause="shadow_engine.set_kill_switch() ran but marker.wired != True",
        )

    cancel_count = 0
    if con is not None:
        try:
            cur = con.execute(
                """
                SELECT COUNT(*) FROM order_journal
                WHERE status='failed' AND reason='cancelled_by_kill_switch'
                """
            )
            cancel_count = int(cur.fetchone()[0])
        except sqlite3.Error as exc:
            return Verdict(
                index=6,
                name="KillSwitch 触发",
                status="FAIL",
                data=f"journal_cancel_query_error: {exc}",
                root_cause="Could not query cancelled_by_kill_switch rows from journal",
            )

    if cancel_count > 0:
        return Verdict(
            index=6,
            name="KillSwitch 触发",
            status="PASS",
            data=f"wired={marker.get('engine_type')} cancelled_orders={cancel_count}",
            root_cause="KillSwitch activated and cancel_all_open_orders() cancelled open orders (journal failed/cancelled_by_kill_switch)",
        )
    return Verdict(
        index=6,
        name="KillSwitch 触发",
        status="EXPECTED_FAIL",
        data="wired=True, manual activation not performed during run",
        root_cause="KillSwitch wired (marker OK) but never activated — manual kill-switch trigger test is a human Step-B action. Cancel journal rows absent.",
    )


def check_7_state_recovery(
    observations: list[dict[str, Any]],
    restarts: list[dict[str, Any]],
) -> Verdict:
    """Criterion 7: Restart state recovery.

    PASS if positions row count consistent across restart boundaries.
    Compares positions count before/after each restart from observations.
    """
    if not restarts:
        return Verdict(
            index=7,
            name="重启后 state 恢复",
            status="PASS",
            data="no restarts during run (trivially consistent)",
        )

    # For each restart, find the observations immediately before/after
    # and compare positions count
    mismatches: list[str] = []
    for restart in restarts:
        restart_ts = restart.get("ts", 0)
        before = next(
            (o for o in reversed(observations) if o.get("ts", 0) < restart_ts),
            None,
        )
        after = next(
            (o for o in observations if o.get("ts", 0) > restart_ts),
            None,
        )
        if not before or not after:
            mismatches.append(f"restart@{restart_ts}: missing before/after observation")
            continue
        before_positions = before.get("positions_count")
        after_positions = after.get("positions_count")
        if before_positions is None or after_positions is None:
            mismatches.append(
                f"restart@{restart_ts}: positions_count missing in observations"
            )
            continue
        if before_positions != after_positions:
            mismatches.append(
                f"restart@{restart_ts}: positions {before_positions}→{after_positions}"
            )

    if mismatches:
        return Verdict(
            index=7,
            name="重启后 state 恢复",
            status="FAIL",
            data=f"mismatches={len(mismatches)}",
            root_cause=f"Position count mismatch across restart(s): {'; '.join(mismatches)}",
        )
    return Verdict(
        index=7,
        name="重启后 state 恢复",
        status="PASS",
        data=f"restarts={len(restarts)}, all positions_count consistent",
    )


def generate_report(report: Report, report_path: str) -> None:
    """Write verdict report to data/stage_b/stage_b_report.md."""
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("# Stage B 24h Shadow Run — Verdict Report")
    lines.append("")
    lines.append(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("| # | Criterion | Status | Data |")
    lines.append("|---|-----------|--------|------|")
    for v in report.verdicts:
        lines.append(f"| {v.index} | {v.name} | {v.status} | {v.data} |")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- PASS: {report.pass_count()}")
    lines.append(f"- FAIL: {report.fail_count()}")
    lines.append(f"- EXPECTED_FAIL: {report.expected_fail_count()}")
    lines.append(f"- Overall: {'PASS' if report.passed() else 'FAIL'}")
    lines.append("")

    # Root causes for FAIL / EXPECTED_FAIL
    fail_or_expected = [
        v for v in report.verdicts if v.status in ("FAIL", "EXPECTED_FAIL")
    ]
    if fail_or_expected:
        lines.append("## Root Causes")
        lines.append("")
        for v in fail_or_expected:
            lines.append(f"### #{v.index} {v.name} — {v.status}")
            lines.append("")
            lines.append(f"**Data**: {v.data}")
            lines.append("")
            lines.append(f"**Root Cause**: {v.root_cause}")
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()

    observations = load_observations(args.obs_path)
    restarts = load_restarts(args.restarts_path)

    report = Report()

    # Criteria 1, 7 use observations + restarts
    report.verdicts.append(check_1_no_crash(observations, restarts))

    # Criteria 2, 3, 4 use journal DB
    # Derive run_start_ts from earliest observation to filter out
    # sso- orders from prior runs that would contaminate counts.
    run_start_ts = (
        min(o.get("ts", 0) for o in observations if o.get("ts"))
        if observations else None
    )
    con = None
    try:
        con = read_journal_db(args.journal_path)
        report.verdicts.append(check_2_wal_complete(con))
        report.verdicts.append(check_3_signal_count(con, run_start_ts))
        report.verdicts.append(check_4_fill_count(con, run_start_ts))
    except FileNotFoundError as e:
        report.verdicts.append(Verdict(
            index=2, name="Journal WAL 完整", status="FAIL",
            data="journal_db_missing",
            root_cause=str(e),
        ))
        report.verdicts.append(Verdict(
            index=3, name="Signal count > 0", status="FAIL",
            data="journal_db_missing",
            root_cause=str(e),
        ))
        report.verdicts.append(Verdict(
            index=4, name="Fill count > 0", status="FAIL",
            data="journal_db_missing",
            root_cause=str(e),
        ))

    # Criterion 5 — equity deviation from observations
    report.verdicts.append(check_5_equity_deviation(observations))

    # Criterion 6 — KillSwitch wiring + cancel evidence (needs journal DB open)
    report.verdicts.append(check_6_killswitch(con))
    if con is not None:
        con.close()

    # Criterion 7 — state recovery
    report.verdicts.append(check_7_state_recovery(observations, restarts))

    # Print verdict to stdout
    print("=" * 70)
    print("Stage B 24h Shadow Run — Verdict")
    print("=" * 70)
    for v in report.verdicts:
        print(f"  [{v.index}] {v.name:<30} {v.status:<15} {v.data}")
    print("=" * 70)
    print(
        f"  PASS={report.pass_count()}  "
        f"FAIL={report.fail_count()}  "
        f"EXPECTED_FAIL={report.expected_fail_count()}  "
        f"Overall={'PASS' if report.passed() else 'FAIL'}"
    )
    print("=" * 70)

    # Write markdown report
    generate_report(report, args.report_path)
    print(f"  Report written to: {args.report_path}")

    return 0 if report.passed() else 1


if __name__ == "__main__":
    sys.exit(main())
