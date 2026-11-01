"""Tests for the read-only PA gate-forensics diagnostic (item 2 + 3).

The diagnostic answers, from persisted records only: where do records die
(terminal-node histogram), what did each predict vs what happened
(counterfactual), and is the forward move signal or drift (baseline up-rate).
Only the pure/DB-layer helpers are exercised here — the network-free core; the
end-to-end run is a read-only script whose output this repo gitignores.

The load-bearing invariants:
- a terminal node is bucketed by gate family so a *broken* gate (data-integrity
  deaths) is distinguishable from an *over-conservative* one (entry-quality);
- the histogram counts every record, including ones with no trace at all;
- record rows read the REAL persisted shape (`stage1.diagnosis_or_decision`),
  and records predating the current tree degrade to a bucket, never crash.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import pa_gate_forensics as fx  # noqa: E402


# ---------------------------------------------------------------------------
# _classify — node id -> gate family
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "node,family",
    [
        ("1.1", "data_integrity"),
        ("1.2", "data_integrity"),
        ("2.2", "entry_quality"),
        ("3.1", "entry_quality"),
        ("3.2", "entry_quality"),
        ("4.1", "final_verdict"),
        ("9.9", "unknown"),
        ("", "unknown"),
    ],
)
def test_classify_buckets_by_gate_family(node: str, family: str) -> None:
    assert fx._classify(node) == family


def test_classify_separates_broken_from_conservative() -> None:
    """The whole point: §1.x (bad data) must NOT share a bucket with §3.x."""
    assert fx._classify("1.1") != fx._classify("3.2")


# ---------------------------------------------------------------------------
# _histogram — counts every record
# ---------------------------------------------------------------------------


def test_histogram_counts_all_and_buckets() -> None:
    rows: list[dict] = [
        {"terminal_node": "3.1", "family": "entry_quality", "gate_result": "wait"},
        {"terminal_node": "3.2", "family": "entry_quality", "gate_result": "wait"},
        {"terminal_node": "4.1", "family": "final_verdict", "gate_result": "proceed"},
        {"terminal_node": None, "family": "none", "gate_result": None},
    ]
    h = fx._histogram(rows)
    assert h["n"] == 4
    assert h["terminal_node"] == {"3.1": 1, "3.2": 1, "4.1": 1, "?": 1}
    assert h["family"] == {"entry_quality": 2, "final_verdict": 1, "none": 1}
    assert h["gate_result"] == {"wait": 2, "proceed": 1, "?": 1}


def test_histogram_empty_is_zeros_not_error() -> None:
    h = fx._histogram([])
    assert h["n"] == 0
    assert h["family"] == {}


# ---------------------------------------------------------------------------
# _record_rows — reads the real persisted shape from a sqlite fixture
# ---------------------------------------------------------------------------


def _write_records(db_path: Path, rows: list[tuple[str, str, str, str, dict]]) -> None:
    con = sqlite3.connect(str(db_path))
    con.execute(
        "CREATE TABLE analysis_records ("
        "id TEXT, created_at TEXT, symbol TEXT, interval TEXT, record_json TEXT)"
    )
    for rid, created, symbol, interval, rec in rows:
        con.execute(
            "INSERT INTO analysis_records VALUES (?, ?, ?, ?, ?)",
            (rid, created, symbol, interval, json.dumps(rec)),
        )
    con.commit()
    con.close()


def _rec(state: str, gate: str, nodes: list[str], direction: str | None) -> dict:
    """A DW record in the REAL persisted shape: trace under stage1, next_period
    inside stage1.diagnosis_or_decision (orchestrator persists diagnosis_summary)."""
    return {
        "stage1": {
            "trace": [{"node_id": n} for n in nodes],
            "diagnosis_or_decision": {
                "state": state,
                "gate_result": gate,
                "next_period": {"direction": direction} if direction else None,
            },
        }
    }


def test_record_rows_extract_terminal_and_family(tmp_path: Path) -> None:
    db = tmp_path / "a.db"
    _write_records(
        db,
        [
            (
                "ar-1",
                "2026-09-17 10:00:00",
                "SOL/USDT",
                "1h",
                _rec("trend_up", "wait", ["1.1", "2.1", "3.1"], "long"),
            ),
            (
                "ar-2",
                "2026-09-17 11:00:00",
                "BTC/USDT",
                "4h",
                _rec("range", "wait", ["1.1", "1.2", "3.2"], None),
            ),
        ],
    )
    rows = fx._record_rows(str(db), "2026-09-01")
    assert len(rows) == 2
    by_id = {r["record_id"]: r for r in rows}
    assert by_id["ar-1"]["terminal_node"] == "3.1"
    assert by_id["ar-1"]["family"] == "entry_quality"
    assert by_id["ar-1"]["direction"] == "long"
    assert by_id["ar-2"]["terminal_node"] == "3.2"
    assert by_id["ar-2"]["direction"] is None


def test_record_rows_since_filter_excludes_old(tmp_path: Path) -> None:
    db = tmp_path / "a.db"
    _write_records(
        db,
        [
            (
                "old",
                "2026-08-14 11:00:00",
                "BTC/USDT",
                "1h",
                _rec("range", "wait", ["1.1"], None),
            ),
            (
                "new",
                "2026-09-17 11:00:00",
                "BTC/USDT",
                "1h",
                _rec("range", "wait", ["1.1"], None),
            ),
        ],
    )
    rows = fx._record_rows(str(db), "2026-09-01")
    assert [r["record_id"] for r in rows] == ["new"]


def test_record_rows_missing_trace_is_none_not_crash(tmp_path: Path) -> None:
    """A record with no trace must yield terminal_node=None/family='none'."""
    db = tmp_path / "a.db"
    _write_records(
        db,
        [("ar-x", "2026-09-17 10:00:00", "ETH/USDT", "1h", {"stage1": {}})],
    )
    row = fx._record_rows(str(db), "2026-09-01")[0]
    assert row["terminal_node"] is None
    assert row["family"] == "none"
    assert row["trace"] == []


def test_record_rows_bad_json_is_skipped_not_fatal(tmp_path: Path) -> None:
    db = tmp_path / "a.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE analysis_records (id TEXT, created_at TEXT, symbol TEXT, "
        "interval TEXT, record_json TEXT)"
    )
    con.execute(
        "INSERT INTO analysis_records VALUES ('bad','2026-09-17',?,?,?)",
        ("X", "1h", "{not json"),
    )
    con.execute(
        "INSERT INTO analysis_records VALUES ('ok','2026-09-17',?,?,?)",
        ("X", "1h", json.dumps(_rec("range", "wait", ["3.2"], None))),
    )
    con.commit()
    con.close()
    rows = fx._record_rows(str(db), "2026-09-01")
    assert [r["record_id"] for r in rows] == ["ok"]


# ---------------------------------------------------------------------------
# _ms — ISO -> epoch ms (defensive on malformed input)
# ---------------------------------------------------------------------------


def test_ms_parses_iso_naive_as_utc() -> None:
    assert fx._ms("1970-01-01T00:00:01") == 1000


def test_ms_returns_none_on_bad_string() -> None:
    assert fx._ms("not-a-date") is None
