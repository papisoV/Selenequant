"""Tests for scripts/merge_klines_db.py — the write/read-split reconciliation.

The merge operates on the one hand-written SQLite schema in the repo, so the
things that can silently go wrong are structural, not algorithmic:

  - the exact union size is computed in SQL; if the UNION SQL is wrong the
    conservation assertion passes while rows are lost;
  - ``REPLACE`` is only idempotent if the target table keeps its PRIMARY KEY —
    a ``CREATE TABLE AS SELECT`` would drop it and duplicate on re-run;
  - a bare relative default would resolve against the caller's CWD.

These drive the real script end-to-end against throwaway DBs.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "merge_klines_db.py"

_KLINES_DDL = """
CREATE TABLE klines (
    exchange TEXT NOT NULL, market_type TEXT NOT NULL, symbol TEXT NOT NULL,
    interval TEXT NOT NULL, timestamp INTEGER NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL, closed INTEGER,
    PRIMARY KEY (exchange, market_type, symbol, interval, timestamp)
);
"""

_AUX_DDL = """
CREATE TABLE kline_scan_state (
    exchange TEXT NOT NULL, market_type TEXT NOT NULL, symbol TEXT NOT NULL,
    interval TEXT NOT NULL, verified_through_ts INTEGER, updated_at INTEGER NOT NULL,
    PRIMARY KEY (exchange, market_type, symbol, interval)
);
"""


def _make(path: Path, rows: list[tuple], *, aux: bool = False) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_KLINES_DDL)
        conn.executemany("INSERT INTO klines VALUES (?,?,?,?,?,1,1,1,1,1,1)", rows)
        if aux:
            conn.executescript(_AUX_DDL)
            conn.execute(
                "INSERT INTO kline_scan_state VALUES ('bin','spot','X','1h',1,1)"
            )
        conn.commit()
    finally:
        conn.close()


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


def _rows(path: Path, query: str) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute(query).fetchone()[0])
    finally:
        conn.close()


def _scenario(tmp_path: Path) -> tuple[Path, Path]:
    """source = {A,A' , B,B'} ; target = {A,A', C} → union has 5 keys."""
    src = tmp_path / "src.db"
    dst = tmp_path / "dst.db"
    _make(
        src,
        [
            ("bin", "spot", "X", "1h", 100),
            ("bin", "spot", "X", "1h", 200),
            ("okx", "fut", "Y", "5m", 10),
            ("okx", "fut", "Y", "5m", 20),
        ],
        aux=True,
    )
    _make(
        dst,
        [
            ("bin", "spot", "X", "1h", 100),
            ("bin", "spot", "X", "1h", 200),
            ("bin", "spot", "Z", "1d", 5),
        ],
    )
    return src, dst


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    src, dst = _scenario(tmp_path)
    before = _rows(dst, "SELECT COUNT(*) FROM klines")
    result = _run("--from", str(src), "--into", str(dst), "--force")
    assert result.returncode == 0
    assert "DRY RUN" in result.stdout
    assert _rows(dst, "SELECT COUNT(*) FROM klines") == before


def test_apply_produces_the_exact_union(tmp_path: Path) -> None:
    src, dst = _scenario(tmp_path)
    result = _run("--from", str(src), "--into", str(dst), "--force", "--apply")
    assert result.returncode == 0, result.stderr
    # 2 shared + 2 new-from-source + 1 target-only == 5
    assert _rows(dst, "SELECT COUNT(*) FROM klines") == 5


def test_apply_is_idempotent(tmp_path: Path) -> None:
    src, dst = _scenario(tmp_path)
    _run("--from", str(src), "--into", str(dst), "--force", "--apply")
    _run("--from", str(src), "--into", str(dst), "--force", "--apply")
    assert _rows(dst, "SELECT COUNT(*) FROM klines") == 5


def test_aux_table_is_carried_over_and_stays_idempotent(tmp_path: Path) -> None:
    """The gap-sweeper state must survive, and must not duplicate on re-run.

    A ``CREATE TABLE AS SELECT`` would drop the PRIMARY KEY and double the row.
    """
    src, dst = _scenario(tmp_path)
    _run("--from", str(src), "--into", str(dst), "--force", "--apply")
    _run("--from", str(src), "--into", str(dst), "--force", "--apply")
    assert _rows(dst, "SELECT COUNT(*) FROM kline_scan_state") == 1


def test_refuses_non_canonical_target_without_force(tmp_path: Path) -> None:
    src, dst = _scenario(tmp_path)
    result = _run("--from", str(src), "--into", str(dst))
    assert result.returncode == 2
    assert "--force" in result.stdout


def test_refuses_same_source_and_target(tmp_path: Path) -> None:
    src, _ = _scenario(tmp_path)
    result = _run("--from", str(src), "--into", str(src), "--force")
    assert result.returncode == 2


def test_missing_source_is_an_error(tmp_path: Path) -> None:
    _, dst = _scenario(tmp_path)
    result = _run("--from", str(tmp_path / "nope.db"), "--into", str(dst), "--force")
    assert result.returncode == 2
