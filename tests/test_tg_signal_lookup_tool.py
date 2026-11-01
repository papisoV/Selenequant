"""Tests for TGSignalLookupTool — query tg_events from EventGuard DB."""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "packages" / "agent-runtime"))
sys.path.insert(0, str(REPO / "packages" / "shared-types"))
sys.path.insert(0, str(REPO / "packages" / "alpha-core"))


def _build_db(db_path: Path, events: list[dict]) -> None:
    """Build a synthetic macro_events.db with the given tg_events rows."""
    from selene_space_alpha.event_guard.tg_source import _ensure_schema

    conn = sqlite3.connect(str(db_path))
    _ensure_schema(conn)
    if events:
        cols = list(events[0].keys())
        placeholders = ",".join("?" * len(cols))
        col_list = ",".join(cols)
        for ev in events:
            conn.execute(
                f"INSERT INTO tg_events ({col_list}) VALUES ({placeholders})",
                [ev[c] for c in cols],
            )
    conn.commit()
    conn.close()


def test_returns_no_events_when_db_missing(tmp_path):
    from selene_space_agent.tools.tg_tools import TGSignalLookupTool

    t = TGSignalLookupTool(db_path=tmp_path / "nonexistent.db")
    r = t.execute({"hours": 6})
    assert r.error is None
    assert "not found" in r.output.lower()


def test_returns_events_in_lookback_window(tmp_path):
    from selene_space_agent.tools.tg_tools import TGSignalLookupTool

    db = tmp_path / "macro_events.db"
    now_ms = int(time.time() * 1000)
    _build_db(db, [
        {
            "timestamp": now_ms - 3_600_000,  # 1h ago — in window
            "message": "SEC announces enforcement action",
            "event_type": "REGULATORY",
            "alert_level": "orange",
            "affected": "*",
            "half_life_h": 2.0,
            "magnitude": 0.8,
            "urgency": 0.7,
            "llm_event_type": "regulatory",
        },
        {
            "timestamp": now_ms - 200 * 3_600_000,  # 200h ago — outside 6h window
            "message": "old whale move",
            "event_type": "WHALE",
            "alert_level": "yellow",
            "affected": "*",
            "half_life_h": 1.0,
            "magnitude": 0.3,
            "urgency": 0.3,
            "llm_event_type": "whale",
        },
    ])

    t = TGSignalLookupTool(db_path=db)
    r = t.execute({"hours": 6})
    assert r.error is None
    assert "1 events" in r.output
    assert "REGULATORY" in r.output
    assert "WHALE" not in r.output  # outside window


def test_event_type_filter(tmp_path):
    from selene_space_agent.tools.tg_tools import TGSignalLookupTool

    db = tmp_path / "macro_events.db"
    now_ms = int(time.time() * 1000)
    _build_db(db, [
        {
            "timestamp": now_ms - 3_600_000,
            "message": "SEC action",
            "event_type": "REGULATORY",
            "alert_level": "orange",
            "affected": "*",
            "half_life_h": 2.0,
            "magnitude": 0.8,
            "urgency": 0.7,
            "llm_event_type": "regulatory",
        },
        {
            "timestamp": now_ms - 2 * 3_600_000,
            "message": "Balancer exploit drained $635M",
            "event_type": "SECURITY",
            "alert_level": "orange",
            "affected": "*",
            "half_life_h": 2.0,
            "magnitude": 0.9,
            "urgency": 0.8,
            "llm_event_type": "security",
        },
    ])

    t = TGSignalLookupTool(db_path=db)
    r = t.execute({"hours": 6, "event_type": "SECURITY"})
    assert r.error is None
    assert "1 events" in r.output
    assert "SECURITY" in r.output
    assert "REGULATORY" not in r.output


def test_hours_capped_at_720(tmp_path):
    from selene_space_agent.tools.tg_tools import TGSignalLookupTool

    db = tmp_path / "macro_events.db"
    now_ms = int(time.time() * 1000)
    # Event 800h ago — outside 720h cap
    _build_db(db, [
        {
            "timestamp": now_ms - 800 * 3_600_000,
            "message": "very old event",
            "event_type": "WHALE",
            "alert_level": "yellow",
            "affected": "*",
            "half_life_h": 1.0,
            "magnitude": 0.3,
            "urgency": 0.3,
            "llm_event_type": "whale",
        },
    ])

    t = TGSignalLookupTool(db_path=db)
    # User asks for 9999h but cap is 720 — event 800h ago should NOT appear
    r = t.execute({"hours": 9999})
    assert r.error is None
    assert "No TG events" in r.output


def test_limit_caps_result_count(tmp_path):
    from selene_space_agent.tools.tg_tools import TGSignalLookupTool

    db = tmp_path / "macro_events.db"
    now_ms = int(time.time() * 1000)
    events = [
        {
            "timestamp": now_ms - i * 60_000,  # i minutes ago
            "message": f"event {i}",
            "event_type": "WHALE",
            "alert_level": "yellow",
            "affected": "*",
            "half_life_h": 1.0,
            "magnitude": 0.3,
            "urgency": 0.3,
            "llm_event_type": "whale",
        }
        for i in range(1, 11)  # 10 events
    ]
    _build_db(db, events)

    t = TGSignalLookupTool(db_path=db)
    r = t.execute({"hours": 6, "limit": 3})
    assert r.error is None
    assert "3 events" in r.output


def test_missing_db_path_returns_friendly_message(tmp_path):
    from selene_space_agent.tools.tg_tools import TGSignalLookupTool

    t = TGSignalLookupTool(db_path=tmp_path / "does_not_exist.db")
    r = t.execute({"hours": 6})
    assert r.error is None
    assert "not found" in r.output.lower()


def test_tool_metadata():
    from selene_space_agent.tools.tg_tools import TGSignalLookupTool
    from selene_space_types.risk import RiskLevel

    t = TGSignalLookupTool()
    assert t.name == "tg_signal_lookup"
    assert t.risk_level == RiskLevel.SAFE
    params = t.parameters
    assert "hours" in params["properties"]
    assert "event_type" in params["properties"]
    assert "limit" in params["properties"]
