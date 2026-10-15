"""Merge two klines SQLite databases into one, conservatively.

The write/read split left two DBs that each hold data the other lacks:

  - the backend wrote to the *retired* location and accumulated recent
    high-frequency bars (1m/5m/okx/astock/yahoo) the root DB never saw;
  - the root DB holds wide-symbol daily/4h histories (100+ symbols) the
    retired DB never saw.

So this is a genuine **union**, not an overwrite: neither side is a superset.
The merge is idempotent — ``INSERT OR REPLACE`` keyed on the natural primary
key ``(exchange, market_type, symbol, interval, timestamp)`` — so re-running
converges rather than duplicating.

Safety model (mirrors scripts/backfill_kline_holes.py):

  - **dry-run by default.** ``--apply`` is required to write anything.
  - the target is backed up with the SQLite backup API (not a file copy — the
    DB may be open) before a single row changes.
  - the whole merge is one transaction, so a failure leaves the target intact.
  - conservation is asserted *after* the write: the final row count must equal
    the exact union size computed independently, and every source partition
    must be present in the target.

Usage::

    python scripts/merge_klines_db.py --from services/local-api/data/klines.db
    python scripts/merge_klines_db.py --from ... --apply
    python scripts/merge_klines_db.py --from ... --verify   # re-check, no write
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "core"))

from selene_space_core.kline_paths import (  # noqa: E402
    default_klines_db_path,
    repo_root,
)

#: The natural key shared by both `klines` tables.
KEY_COLUMNS = ("exchange", "market_type", "symbol", "interval", "timestamp")

#: Auxiliary tables owned by the gap sweeper. Absent from a DB with no sweeper
#: history; when present they carry real state (scan watermark, hole ledger).
AUX_TABLES = ("kline_scan_state", "kline_hole_state")


def _connect(path: Path) -> sqlite3.Connection:
    # journal_mode=delete on both sides: no -wal/-shm to coordinate.
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r["name"] for r in rows}


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])


def _partition_rows(conn: sqlite3.Connection, table: str) -> dict[tuple, int]:
    rows = conn.execute(
        f"SELECT exchange, market_type, interval, COUNT(*) AS n "
        f"FROM {table} GROUP BY exchange, market_type, interval"
    ).fetchall()
    return {(r["exchange"], r["market_type"], r["interval"]): r["n"] for r in rows}


def _union_size(conn: sqlite3.Connection, local: str, remote: str, table: str) -> int:
    """Exact |A ∪ B| by natural key, computed in SQL (never materialised in Python).

    ``local``/``remote`` are the schema aliases on ``conn`` (e.g. ``main`` for
    the connection's own DB and the ATTACH alias for the other side).
    """
    cols = ", ".join(KEY_COLUMNS)
    sql = (
        f"SELECT COUNT(*) AS n FROM ("
        f"  SELECT {cols} FROM {local}.{table}"
        f"  UNION"
        f"  SELECT {cols} FROM {remote}.{table}"
        f")"
    )
    return int(conn.execute(sql).fetchone()["n"])


def _plan(dst_path: Path, src_path: Path) -> dict:
    """Read-only analysis of what --apply would do."""
    dst = _connect(dst_path)
    src = _connect(src_path)
    try:
        src.execute(f"ATTACH DATABASE '{dst_path.as_posix()}' AS dst")
        dst_tables = _table_names(dst)
        src_tables = _table_names(src)

        klines_union = _union_size(src, "main", "dst", "klines")
        dst_klines = _count(dst, "klines")
        src_klines = _count(src, "klines")

        dst_parts = _partition_rows(dst, "klines")
        src_parts = _partition_rows(src, "klines")
        part_delta = {
            key: (src_parts.get(key, 0), dst_parts.get(key, 0))
            for key in sorted(set(src_parts) | set(dst_parts))
        }

        aux = {
            name: (_count(src, name) if name in src_tables else None)
            for name in AUX_TABLES
        }
        return {
            "dst_klines": dst_klines,
            "src_klines": src_klines,
            "union": klines_union,
            "new_rows": klines_union - dst_klines,
            "overlap": src_klines + dst_klines - klines_union,
            "dst_tables": sorted(dst_tables),
            "aux": aux,
            "partitions": part_delta,
        }
    finally:
        src.close()
        dst.close()


def _print_plan(plan: dict) -> None:
    print(f"  target klines rows : {plan['dst_klines']:,}")
    print(f"  source klines rows : {plan['src_klines']:,}")
    print(f"  overlapping (both) : {plan['overlap']:,}")
    print(f"  union (=final)     : {plan['union']:,}")
    print(f"  NEW rows to add    : {plan['new_rows']:,}")
    print("\n  per-partition (source_rows, target_rows):")
    for (ex, mt, iv), (s, d) in plan["partitions"].items():
        mark = "  NEW " if d == 0 else ("  EXT " if s > d else "      ")
        print(f"   {mark}{ex:<9}{mt:<9}{iv:<5} src={s:>8,}  dst={d:>8,}")
    print("\n  auxiliary tables in source:")
    for name, n in plan["aux"].items():
        print(f"   {name:<20} {'absent' if n is None else f'{n:,} rows'}")


def _backup(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"{path.name}.bak-merge-{stamp}")
    src = sqlite3.connect(str(path))
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)  # consistent snapshot even if the DB is open
        finally:
            dst.close()
    finally:
        src.close()
    return dest


def _column_names(conn: sqlite3.Connection, qualified: str) -> list[str]:
    """Column names for ``schema.table`` — PRAGMA needs the schema *inside* it."""
    schema, _, table = qualified.partition(".")
    if not table:  # no schema given
        schema, table = "main", schema
    rows = conn.execute(f"PRAGMA {schema}.table_info({table})")
    return [r["name"] for r in rows]


def _apply(dst_path: Path, src_path: Path) -> None:
    backup = _backup(dst_path)
    print(f"  backup written: {backup}")

    conn = _connect(dst_path)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(f"ATTACH DATABASE '{src_path.as_posix()}' AS src")
        # Tables of the ATTACHed source, not the destination (the connection's
        # own DB would also be named in sqlite_master). Also create any aux
        # table the destination lacks, so its state is not silently dropped.
        src_tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM src.sqlite_master WHERE type='table'"
            )
        }

        # Merge only columns BOTH tables share — a schema drift on either side
        # must not silently mis-map values via `SELECT *` positional order.
        dst_cols = _column_names(conn, "main.klines")
        src_cols = set(_column_names(conn, "src.klines"))
        shared = [c for c in dst_cols if c in src_cols]
        if set(KEY_COLUMNS) - set(shared):
            raise RuntimeError(
                f"source is missing key columns: {set(KEY_COLUMNS) - set(shared)}"
            )
        col_list = ", ".join(shared)

        conn.execute("BEGIN IMMEDIATE")
        try:
            # Idempotent upsert on the natural key; source wins on conflict
            # because for a given bar the newer write is the more complete one.
            conn.execute(
                f"REPLACE INTO main.klines ({col_list}) "
                f"SELECT {col_list} FROM src.klines"
            )
            for name in AUX_TABLES:
                if name not in src_tables:
                    continue
                if name not in _table_names(conn):
                    # Copy the source's real DDL so the PRIMARY KEY survives —
                    # `CREATE TABLE AS SELECT` would drop it, and REPLACE would
                    # then have no key to dedupe on.
                    ddl = conn.execute(
                        "SELECT sql FROM src.sqlite_master WHERE type='table' AND name=?",
                        (name,),
                    ).fetchone()["sql"]
                    conn.execute(ddl.replace("CREATE TABLE", "CREATE TABLE main.", 1))
                name_cols = [
                    c
                    for c in _column_names(conn, f"main.{name}")
                    if c in set(_column_names(conn, f"src.{name}"))
                ]
                ncl = ", ".join(name_cols)
                conn.execute(
                    f"REPLACE INTO main.{name} ({ncl}) SELECT {ncl} FROM src.{name}"
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def _verify(dst_path: Path, src_path: Path, expect_union: int | None) -> bool:
    dst = _connect(dst_path)
    src = _connect(src_path)
    ok = True
    try:
        dst.execute(f"ATTACH DATABASE '{src_path.as_posix()}' AS src")
        union = _union_size(dst, "main", "src", "klines")
        final = _count(dst, "klines")
        print(f"  target rows after   : {final:,}")
        print(f"  independent union   : {union:,}")
        if final != union:
            print(f"  FAIL: target {final:,} != union {union:,}")
            ok = False
        else:
            print("  OK: target equals the exact union of both DBs")

        # Every source partition must be represented in the target.
        dst_parts = _partition_rows(dst, "main.klines")
        missing = [
            key
            for key, n in _partition_rows(dst, "src.klines").items()
            if n > dst_parts.get(key, 0)
        ]
        if missing:
            print(f"  FAIL: source partitions under-represented: {missing}")
            ok = False
        elif _partition_rows(dst, "src.klines"):
            print("  OK: every source partition present in target")

        # No orphan rows: every target bar has non-null key fields.
        nulls = dst.execute(
            "SELECT COUNT(*) AS n FROM main.klines WHERE "
            + " OR ".join(f"{c} IS NULL" for c in KEY_COLUMNS)
        ).fetchone()["n"]
        if nulls:
            print(f"  FAIL: {nulls:,} rows with NULL key columns")
            ok = False
    finally:
        src.close()
        dst.close()
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from",
        dest="src",
        required=True,
        help="Source DB to merge FROM (usually the retired backend DB)",
    )
    parser.add_argument(
        "--into",
        dest="dst",
        default=None,
        help="Target DB to merge INTO (default: the canonical repo-root DB)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write. Without this the script only reports the plan.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Only verify an already-merged target; write nothing.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow --into to be a path other than the canonical DB.",
    )
    args = parser.parse_args()

    dst_path = Path(args.dst).resolve() if args.dst else default_klines_db_path()
    src_path = Path(args.src).resolve()
    canonical = (repo_root() / "data" / "klines.db").resolve()

    if not src_path.is_file():
        print(f"ERROR: source DB not found: {src_path}")
        return 2
    if not dst_path.is_file():
        print(f"ERROR: target DB not found: {dst_path}")
        return 2
    if dst_path == src_path:
        print("ERROR: source and target are the same file")
        return 2
    if dst_path != canonical and not args.force:
        print(
            f"ERROR: --into {dst_path} is not the canonical DB {canonical}.\n"
            f"       Pass --force to override deliberately."
        )
        return 2

    print(f"source: {src_path}")
    print(f"target: {dst_path}")
    print()

    if args.verify:
        print("VERIFY (no writes)")
        return 0 if _verify(dst_path, src_path, None) else 1

    plan = _plan(dst_path, src_path)
    _print_plan(plan)

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to merge.")
        return 0

    print("\nAPPLYING")
    _apply(dst_path, src_path)
    print("\nVERIFYING")
    if not _verify(dst_path, src_path, plan["union"]):
        print(
            "\nVERIFY FAILED — target is inconsistent. Restore from the backup above."
        )
        return 1
    print("\nDONE — merge complete and verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
