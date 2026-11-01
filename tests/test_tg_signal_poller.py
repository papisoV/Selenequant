"""Tests for TG signal poller — bridges tg_events DB → intel:events WS channel."""
from __future__ import annotations

import asyncio
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "packages" / "alpha-core"))
sys.path.insert(0, str(REPO / "packages" / "agent-runtime"))
sys.path.insert(0, str(REPO / "packages" / "shared-types"))
sys.path.insert(0, str(REPO / "services" / "local-api"))


def _build_db(db_path: Path) -> None:
    """Build a fresh macro_events.db with tg_events schema."""
    from selene_space_alpha.event_guard.tg_source import _ensure_schema
    conn = sqlite3.connect(str(db_path))
    _ensure_schema(conn)
    conn.commit()
    conn.close()


def _insert_event(db_path: Path, ts_ms: int, event_type: str = "SECURITY",
                  level: str = "orange", message: str = "test event") -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR IGNORE INTO tg_events (timestamp, message, event_type, alert_level, "
        "affected, half_life_h, magnitude, urgency, llm_event_type) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ts_ms, message, event_type, level, "*", 2.0, 0.8, 0.7, event_type.lower()),
    )
    conn.commit()
    conn.close()


def test_poller_broadcasts_new_events(tmp_path, monkeypatch):
    """Poller should broadcast events with timestamp > last_seen_ts."""
    captured: list[dict] = []

    class FakeManager:
        async def broadcast(self, channel, payload):
            if channel == "intel:events":
                captured.append(payload)

    import app.api.v1.ws as ws_mod
    fake_manager = FakeManager()
    monkeypatch.setattr(ws_mod, "_intelligence_manager", fake_manager)

    db = tmp_path / "macro_events.db"
    _build_db(db)

    async def _runner():
        # Seed an event before poller starts — poller should set cursor to this
        now_ms = int(time.time() * 1000)
        _insert_event(db, now_ms - 60_000, "REGULATORY", "orange", "pre-startup event")

        # Start poller inside a running loop
        ws_mod._tg_signal_last_seen_ts = 0
        ws_mod.start_tg_signal_poller(db_path=str(db), interval_s=0.05)

        # Pre-startup event should NOT be broadcast (cursor advanced to it)
        await asyncio.sleep(0.15)
        assert len(captured) == 0, f"expected 0 broadcasts, got {len(captured)}"

        # Insert a new event — poller should pick it up
        new_ts = now_ms + 1_000
        _insert_event(db, new_ts, "SECURITY", "orange", "new hack event")
        await asyncio.sleep(0.2)

        assert len(captured) == 1, f"expected 1 broadcast, got {len(captured)}"
        payload = captured[0]
        assert payload["type"] == "tg_signal"
        assert payload["data"]["event_type"] == "SECURITY"
        assert payload["data"]["alert_level"] == "orange"
        assert payload["data"]["llm_event_type"] == "security"
        assert "new hack event" in payload["data"]["message"]

        await ws_mod.stop_tg_signal_poller()

    asyncio.run(_runner())


def test_poller_handles_missing_db(tmp_path, monkeypatch):
    """Poller should exit gracefully if DB does not exist."""
    import app.api.v1.ws as ws_mod

    captured: list[dict] = []
    class FakeManager:
        async def broadcast(self, channel, payload):
            captured.append(payload)
    monkeypatch.setattr(ws_mod, "_intelligence_manager", FakeManager())

    async def _runner():
        ws_mod.start_tg_signal_poller(db_path=str(tmp_path / "nonexistent.db"), interval_s=0.05)
        await asyncio.sleep(0.1)
        assert len(captured) == 0
        await ws_mod.stop_tg_signal_poller()

    asyncio.run(_runner())


def test_broadcast_tg_signal_routes_to_intel_events(monkeypatch):
    """broadcast_tg_signal should call _intelligence_manager.broadcast on intel:events."""
    import app.api.v1.ws as ws_mod

    captured: list[tuple[str, dict]] = []
    class FakeManager:
        async def broadcast(self, channel, payload):
            captured.append((channel, payload))
    monkeypatch.setattr(ws_mod, "_intelligence_manager", FakeManager())

    asyncio.run(ws_mod.broadcast_tg_signal({
        "timestamp": 12345,
        "event_type": "WHALE",
        "alert_level": "yellow",
        "message": "whale move",
    }))

    assert len(captured) == 1
    channel, payload = captured[0]
    assert channel == "intel:events"
    assert payload["type"] == "tg_signal"
    assert payload["data"]["event_type"] == "WHALE"
    assert payload["data"]["alert_level"] == "yellow"
