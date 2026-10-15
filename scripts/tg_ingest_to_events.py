"""Batch ingest tg_factors.db (offline LLM-output) into tg_events table.

Why this exists:
- tg_factors.db has 2096 rows (794 real signals) produced by an offline TG bot.
- event_guard.TGSource.ingest_llm_factor() expects rows in `tg_events` table
  (macro_events.db), but only 3 manual test rows exist.
- No live bot is running, so we replay the historical offline data through
  ingest_llm_factor (LLM event_type → EventGuard mapping) to populate tg_events.

This unblocks Step 2 (re-enable EventGuard for tg-spike-v1 with TG-only source)
without waiting for a live TG → DB ingestion service.

LLM factor pass-through (2026-07-19):
- Previously called classify_message() on reasoning text — wasted the LLM
  event_type/magnitude/urgency the bot already produced.
- Now reads event_type/magnitude/urgency/symbols from tg_factors and passes
  them to ingest_llm_factor(). Keyword reclassification only kicks in for
  legacy reasons via ingest_message(), not here.

Usage:
    python scripts/tg_ingest_to_events.py                 # replay all 794 real signals
    python scripts/tg_ingest_to_events.py --dry-run        # preview without writing
    python scripts/tg_ingest_to_events.py --limit 100     # only first 100 signals
    python scripts/tg_ingest_to_events.py --tg-db PATH --macro-db PATH
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_TG_DB = REPO / "实验" / "tg_factors.db"
DEFAULT_MACRO_DB = REPO / "data" / "macro_events.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("tg_ingest")


def ts_str_to_ms(ts_str: str) -> int:
    """Convert tg_factors.ts (string like '2026-07-17 20:20:59.000000') to ms epoch."""
    s = ts_str.split(".")[0]  # strip microseconds
    dt = datetime.fromisoformat(s)
    return int(dt.timestamp() * 1000)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tg-db", default=str(DEFAULT_TG_DB), help="Path to tg_factors.db")
    parser.add_argument("--macro-db", default=str(DEFAULT_MACRO_DB), help="Path to macro_events.db")
    parser.add_argument("--limit", type=int, default=None, help="Only ingest first N real signals")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--reset", action="store_true", help="Truncate tg_events before ingest (DANGER: deletes existing rows)")
    args = parser.parse_args()

    tg_db = Path(args.tg_db)
    macro_db = Path(args.macro_db)
    if not tg_db.exists():
        log.error("tg_factors.db not found at %s", tg_db)
        return 1
    if not macro_db.exists():
        log.error("macro_events.db not found at %s", macro_db)
        return 1

    # Import TGSource for ingest_llm_factor + schema migration helpers
    sys.path.insert(0, str(REPO / "packages" / "alpha-core"))
    from selene_space_alpha.event_guard.tg_source import (  # type: ignore
        TGSource, _ensure_schema, classify_llm_factor,
    )

    # Load 794 real signals with full LLM factor columns (not just reasoning text)
    conn_tg = sqlite3.connect(str(tg_db))
    rows = conn_tg.execute(
        """
        SELECT ts, reasoning, event_type, magnitude, urgency, symbols
        FROM tg_factors
        WHERE reasoning IS NOT NULL AND reasoning NOT LIKE 'noise:%'
        ORDER BY ts ASC
        """
    ).fetchall()
    conn_tg.close()
    if args.limit:
        rows = rows[: args.limit]

    log.info("Loaded %d real signals from %s", len(rows), tg_db)

    # Reset tg_events BEFORE ingest (otherwise we'd wipe the rows we just wrote)
    if args.reset and not args.dry_run:
        conn_reset = sqlite3.connect(str(macro_db))
        _ensure_schema(conn_reset)
        conn_reset.execute("DELETE FROM tg_events")
        conn_reset.commit()
        conn_reset.close()
        log.info("Reset tg_events table (per --reset)")

    src = TGSource(db_path=macro_db) if not args.dry_run else None
    classified = 0
    skipped_no_map = 0
    skipped_no_text = 0
    dist: dict[str, int] = {}
    for ts_str, message, llm_event_type, magnitude, urgency, symbols_json in rows:
        if not message:
            skipped_no_text += 1
            continue
        if args.dry_run:
            mapped = classify_llm_factor(
                llm_event_type or "",
                float(magnitude or 0.0),
                float(urgency or 0.0),
            )
            if mapped is None:
                skipped_no_map += 1
                continue
            eg_type, level, _ = mapped
            classified += 1
            dist[eg_type] = dist.get(eg_type, 0) + 1
        else:
            ts_ms = ts_str_to_ms(ts_str)
            # Parse symbols JSON to extract first symbol (or "*" if generic)
            try:
                sym_list = json.loads(symbols_json) if symbols_json else []
            except (ValueError, TypeError):
                sym_list = []
            symbols = sym_list[0] if sym_list else "*"
            alert = src.ingest_llm_factor(
                timestamp_ms=ts_ms,
                message=message,
                llm_event_type=llm_event_type or "",
                magnitude=float(magnitude or 0.0),
                urgency=float(urgency or 0.0),
                symbols=symbols,
            )
            if alert is not None:
                classified += 1
            else:
                skipped_no_map += 1
    if not args.dry_run:
        src._cache_ts = 0  # invalidate cache after batch writes

    log.info(
        "Classified: %d mapped to actionable event | Skipped: %d (no map) + %d (no text)",
        classified, skipped_no_map, skipped_no_text,
    )
    if args.dry_run:
        log.info("  [DRY-RUN] event_type distribution: %s", dist)

    # Verify what's in tg_events now
    conn_m = sqlite3.connect(str(macro_db))
    _ensure_schema(conn_m)
    total = conn_m.execute("SELECT COUNT(*) FROM tg_events").fetchone()[0]
    by_type = conn_m.execute(
        "SELECT event_type, COUNT(*) FROM tg_events GROUP BY event_type ORDER BY COUNT(*) DESC"
    ).fetchall()
    by_level = conn_m.execute(
        "SELECT alert_level, COUNT(*) FROM tg_events GROUP BY alert_level ORDER BY COUNT(*) DESC"
    ).fetchall()
    log.info("tg_events table now has %d rows", total)
    log.info("  by event_type: %s", dict(by_type))
    log.info("  by alert_level: %s", dict(by_level))
    conn_m.close()

    if args.dry_run:
        log.info("[DRY-RUN] No writes performed. Re-run without --dry-run to commit.")
    else:
        log.info("Done. EventGuard.TGSource can now read %d events from tg_events.", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
