"""tg_export_factors — bridge tgwatcher.db → tg_factors.db.

Source: data/tgwatcher.db (signal_factors + messages tables)
Output: standalone tg_factors.db with tg_factors table per TG_FACTOR_SCHEMA.md

TGSpikeSource in selene_space_alpha reads tg_factors.db (default path internal-archive).
This script keeps that bridge working after absorption.

Usage:
    python scripts/tg_export_factors.py <source_db> [--output <tg_factors.db>]
    python scripts/tg_export_factors.py data/tgwatcher.db --output internal-archive
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "tg-ingest"))

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import create_engine, text


def export(source_db: str, output_db: str) -> None:
    src = create_engine(f"sqlite:///{source_db}", connect_args={"timeout": 30})
    dst = create_engine(f"sqlite:///{output_db}")

    with dst.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS tg_factors (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                msg_id       INTEGER NOT NULL,
                ts           TEXT NOT NULL,
                symbols      TEXT NOT NULL,
                direction    REAL NOT NULL,
                magnitude    REAL NOT NULL,
                urgency      REAL NOT NULL,
                confidence   REAL NOT NULL,
                halflife_min INTEGER NOT NULL,
                event_type   TEXT NOT NULL,
                reasoning    TEXT NOT NULL,
                created_at   TEXT DEFAULT (datetime('now')),
                UNIQUE(msg_id)
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_tg_factors_ts ON tg_factors(ts)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_tg_factors_event_type ON tg_factors(event_type)"))
        conn.commit()

    with src.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                sf.message_id,
                m.date,
                sf.direction,
                sf.magnitude,
                sf.urgency,
                sf.confidence,
                sf.halflife_min,
                sf.symbols,
                sf.event_type,
                sf.reasoning,
                sf.is_signal,
                sf.llm_model
            FROM signal_factors sf
            JOIN messages m ON sf.message_id = m.message_id AND sf.chat_id = m.chat_id
            WHERE sf.llm_status = 'completed'
            ORDER BY m.date ASC
        """)).fetchall()

    written = 0
    skipped = 0
    with dst.connect() as conn:
        for row in rows:
            msg_id, date_val, direction, magnitude, urgency, confidence, halflife_min, symbols, event_type, reasoning, is_signal, llm_model = row

            ts = date_val.isoformat() if date_val and hasattr(date_val, 'isoformat') else str(date_val) if date_val else ""

            try:
                sym_list = json.loads(symbols) if symbols else []
            except (json.JSONDecodeError, TypeError):
                sym_list = []

            if not sym_list:
                sym_list = ["*"]

            symbols_json = json.dumps(sym_list, ensure_ascii=False)

            direction = direction if direction is not None else 0.0
            magnitude = magnitude if magnitude is not None else 0.1
            urgency = urgency if urgency is not None else 0.1
            confidence = confidence if confidence is not None else 0.9
            halflife_min = halflife_min if halflife_min is not None else 60
            event_type = event_type or "other"
            reasoning = reasoning or ""

            try:
                conn.execute(text("""
                    INSERT OR REPLACE INTO tg_factors
                    (msg_id, ts, symbols, direction, magnitude, urgency, confidence, halflife_min, event_type, reasoning)
                    VALUES (:msg_id, :ts, :symbols, :direction, :magnitude, :urgency, :confidence, :halflife_min, :event_type, :reasoning)
                """), {
                    "msg_id": msg_id,
                    "ts": ts,
                    "symbols": symbols_json,
                    "direction": direction,
                    "magnitude": magnitude,
                    "urgency": urgency,
                    "confidence": confidence,
                    "halflife_min": halflife_min,
                    "event_type": event_type,
                    "reasoning": reasoning,
                })
                written += 1
            except Exception:
                skipped += 1

        conn.commit()

    print(f"Exported {written} records to {output_db}" + (f" ({skipped} skipped)" if skipped else ""))

    with dst.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM tg_factors")).scalar()
        signals = conn.execute(text("SELECT COUNT(*) FROM tg_factors WHERE magnitude > 0.2")).scalar()
        noise = total - signals
        print(f"  Total: {total}, Signals (mag>0.2): {signals}, Noise: {noise}")

        event_dist = {}
        for r in conn.execute(text("SELECT event_type, COUNT(*) FROM tg_factors GROUP BY event_type")):
            event_dist[r[0]] = r[1]
        print(f"  Event types: {event_dist}")

        avg_dir = conn.execute(text("SELECT AVG(direction) FROM tg_factors")).scalar()
        avg_mag = conn.execute(text("SELECT AVG(magnitude) FROM tg_factors")).scalar()
        print(f"  Avg direction: {avg_dir:.3f}, Avg magnitude: {avg_mag:.3f}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python tg_export_factors.py <source_db> [--output <tg_factors.db>]")
        sys.exit(1)

    source_db = sys.argv[1]
    output_db = "tg_factors.db"

    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--output" and i + 1 < len(sys.argv):
            output_db = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    if not os.path.exists(source_db):
        print(f"Error: source DB not found: {source_db}")
        sys.exit(1)

    export(source_db, output_db)


if __name__ == "__main__":
    main()
