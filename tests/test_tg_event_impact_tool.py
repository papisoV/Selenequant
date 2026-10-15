"""Tests for TGEventImpactTool — TG events × positions × rule-based impact report."""
from __future__ import annotations

import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "packages" / "agent-runtime"))
sys.path.insert(0, str(REPO / "packages" / "shared-types"))
sys.path.insert(0, str(REPO / "packages" / "alpha-core"))


# ---------------------------------------------------------------------------
# Test helpers — minimal stubs matching the Position / PositionManager shape
# used by paper_tools.PaperPositionsTool
# ---------------------------------------------------------------------------


class _FakeSide:
    def __init__(self, value: str) -> None:
        self.value = value


@dataclass
class _FakePosition:
    symbol: str
    side: _FakeSide
    quantity: float
    avg_entry_price: float
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0


class _FakePositionManager:
    def __init__(self, positions: list[_FakePosition]) -> None:
        self._positions = positions

    def get_positions(self, account_id: str) -> list[_FakePosition]:
        return list(self._positions)


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


def _make_event(
    ts_ms: int,
    event_type: str = "SECURITY",
    level: str = "orange",
    affected: str = "*",
    magnitude: float = 0.8,
    urgency: float = 0.7,
    message: str = "test event",
    llm_event_type: str | None = None,
) -> dict:
    return {
        "timestamp": ts_ms,
        "message": message,
        "event_type": event_type,
        "alert_level": level,
        "affected": affected,
        "half_life_h": 2.0,
        "magnitude": magnitude,
        "urgency": urgency,
        "llm_event_type": llm_event_type or event_type.lower(),
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_events_and_no_positions(tmp_path):
    """Empty DB + empty positions → friendly 'nothing to assess' message."""
    from selene_space_agent.tools.tg_tools import TGEventImpactTool

    db = tmp_path / "macro_events.db"
    _build_db(db, [])

    pm = _FakePositionManager([])
    t = TGEventImpactTool(db_path=db, position_manager=pm, market_service=None)
    r = t.execute({"account_id": "acct-1", "hours": 6})
    assert r.error is None
    assert "Nothing to assess" in r.output or "无" in r.output


def test_no_events_with_positions(tmp_path):
    """Positions exist but no TG events → 'TG 信号静态, 持仓不受影响'."""
    from selene_space_agent.tools.tg_tools import TGEventImpactTool

    db = tmp_path / "macro_events.db"
    _build_db(db, [])

    pm = _FakePositionManager([
        _FakePosition("BTC/USDT", _FakeSide("buy"), 0.5, 65000.0, 120.0),
    ])
    t = TGEventImpactTool(db_path=db, position_manager=pm, market_service=None)
    r = t.execute({"account_id": "acct-1", "hours": 6})
    assert r.error is None
    assert "0 events" in r.output
    assert "BTC/USDT" in r.output
    assert "静态" in r.output or "不受 TG" in r.output


def test_events_with_no_positions(tmp_path):
    """Events exist but no open positions → 'no open positions' + events listed."""
    from selene_space_agent.tools.tg_tools import TGEventImpactTool

    db = tmp_path / "macro_events.db"
    now_ms = int(time.time() * 1000)
    _build_db(db, [_make_event(now_ms - 3_600_000, "SECURITY", "orange", "BTC", 0.85)])

    pm = _FakePositionManager([])
    t = TGEventImpactTool(db_path=db, position_manager=pm, market_service=None)
    r = t.execute({"account_id": "acct-1", "hours": 6})
    assert r.error is None
    assert "no open positions" in r.output.lower() or "无持仓" in r.output
    assert "SECURITY" in r.output


def test_security_high_magnitude_triggers_50pct_reduce(tmp_path):
    """SECURITY ORANGE mag=0.85 on BTC → '减仓 50%' for BTC/USDT position."""
    from selene_space_agent.tools.tg_tools import TGEventImpactTool

    db = tmp_path / "macro_events.db"
    now_ms = int(time.time() * 1000)
    _build_db(db, [_make_event(now_ms - 3_600_000, "SECURITY", "orange", "BTC", 0.85)])

    pm = _FakePositionManager([
        _FakePosition("BTC/USDT", _FakeSide("buy"), 0.5, 65000.0, 120.0),
    ])
    t = TGEventImpactTool(db_path=db, position_manager=pm, market_service=None)
    r = t.execute({"account_id": "acct-1", "hours": 6})
    assert r.error is None
    assert "减仓 50%" in r.output
    assert "BTC/USDT" in r.output
    assert "高优先级动作" in r.output


def test_symbol_matching_btc_affects_btcusdt(tmp_path):
    """affected=BTC matches position symbol BTC/USDT."""
    from selene_space_agent.tools.tg_tools import TGEventImpactTool

    db = tmp_path / "macro_events.db"
    now_ms = int(time.time() * 1000)
    _build_db(db, [_make_event(now_ms - 3_600_000, "SECURITY", "orange", "BTC", 0.85)])

    pm = _FakePositionManager([
        _FakePosition("BTC/USDT", _FakeSide("buy"), 0.5, 65000.0, 120.0),
        _FakePosition("ETH/USDT", _FakeSide("buy"), 2.0, 3200.0, -40.0),
    ])
    t = TGEventImpactTool(db_path=db, position_manager=pm, market_service=None)
    r = t.execute({"account_id": "acct-1", "hours": 6})
    assert r.error is None
    # BTC/USDT should be matched
    assert "BTC/USDT" in r.output
    # ETH/USDT should NOT appear under this event (only BTC was affected)
    # It may appear in the positions section, but not in the event impact block
    btc_block = r.output.split("== 事件影响 ==")[1].split("== 总结 ==")[0]
    assert "BTC/USDT" in btc_block
    assert "ETH/USDT" not in btc_block


def test_wildcard_affected_matches_all_positions(tmp_path):
    """affected=* matches every open position."""
    from selene_space_agent.tools.tg_tools import TGEventImpactTool

    db = tmp_path / "macro_events.db"
    now_ms = int(time.time() * 1000)
    _build_db(db, [_make_event(now_ms - 3_600_000, "REGULATORY", "orange", "*", 0.85)])

    pm = _FakePositionManager([
        _FakePosition("BTC/USDT", _FakeSide("buy"), 0.5, 65000.0, 120.0),
        _FakePosition("ETH/USDT", _FakeSide("buy"), 2.0, 3200.0, -40.0),
    ])
    t = TGEventImpactTool(db_path=db, position_manager=pm, market_service=None)
    r = t.execute({"account_id": "acct-1", "hours": 6})
    assert r.error is None
    # Both should be matched in the event impact block
    event_block = r.output.split("== 事件影响 ==")[1].split("== 总结 ==")[0]
    assert "BTC/USDT" in event_block
    assert "ETH/USDT" in event_block
    # REGULATORY ORANGE mag>=0.7 → 减仓 30%
    assert "减仓 30%" in r.output


def test_unrelated_symbol_no_action(tmp_path):
    """affected=DOGE does NOT affect BTC/USDT position → '不涉及当前持仓'."""
    from selene_space_agent.tools.tg_tools import TGEventImpactTool

    db = tmp_path / "macro_events.db"
    now_ms = int(time.time() * 1000)
    _build_db(db, [_make_event(now_ms - 3_600_000, "WHALE", "yellow", "DOGE", 0.5)])

    pm = _FakePositionManager([
        _FakePosition("BTC/USDT", _FakeSide("buy"), 0.5, 65000.0, 120.0),
    ])
    t = TGEventImpactTool(db_path=db, position_manager=pm, market_service=None)
    r = t.execute({"account_id": "acct-1", "hours": 6})
    assert r.error is None
    assert "不涉及当前持仓" in r.output
    # No active reduce action in the event impact block
    event_block = r.output.split("== 事件影响 ==")[1].split("== 总结 ==")[0]
    assert "减仓" not in event_block
    # Summary should report no high-priority actions
    summary = r.output.split("== 总结 ==")[1]
    assert "高优先级动作: 无" in summary


def test_multiple_events_summary_aggregates(tmp_path):
    """Multiple events with different impact levels → summary counts both."""
    from selene_space_agent.tools.tg_tools import TGEventImpactTool

    db = tmp_path / "macro_events.db"
    now_ms = int(time.time() * 1000)
    _build_db(db, [
        _make_event(now_ms - 1_800_000, "SECURITY", "orange", "BTC", 0.85,
                    message="Balancer hack"),
        _make_event(now_ms - 3_600_000, "REGULATORY", "yellow", "*", 0.4,
                    message="SEC comment"),
        _make_event(now_ms - 7_200_000, "WHALE", "yellow", "ETH", 0.3,
                    message="whale move"),
    ])

    pm = _FakePositionManager([
        _FakePosition("BTC/USDT", _FakeSide("buy"), 0.5, 65000.0, 120.0),
        _FakePosition("ETH/USDT", _FakeSide("buy"), 2.0, 3200.0, -40.0),
    ])
    t = TGEventImpactTool(db_path=db, position_manager=pm, market_service=None)
    r = t.execute({"account_id": "acct-1", "hours": 6})
    assert r.error is None
    assert "3 event(s) × 2 position(s)" in r.output
    # Security on BTC → 减仓 50%
    assert "减仓 50%" in r.output
    # Summary should mention BTC high-priority
    summary = r.output.split("== 总结 ==")[1]
    assert "BTC/USDT" in summary
    assert "减仓 50%" in summary


def test_missing_account_id_returns_error(tmp_path):
    """Missing account_id → ToolResult with error."""
    from selene_space_agent.tools.tg_tools import TGEventImpactTool

    t = TGEventImpactTool(
        db_path=tmp_path / "macro_events.db",
        position_manager=_FakePositionManager([]),
        market_service=None,
    )
    r = t.execute({"hours": 6})
    assert r.error is not None
    assert "account_id" in r.error


def test_db_missing_returns_friendly_message(tmp_path):
    """Non-existent DB → friendly message, no exception."""
    from selene_space_agent.tools.tg_tools import TGEventImpactTool

    t = TGEventImpactTool(
        db_path=tmp_path / "nonexistent.db",
        position_manager=_FakePositionManager([]),
        market_service=None,
    )
    r = t.execute({"account_id": "acct-1", "hours": 6})
    assert r.error is None
    assert "not found" in r.output.lower() or "DB not found" in r.output


def test_tool_metadata():
    from selene_space_agent.tools.tg_tools import TGEventImpactTool
    from selene_space_types.risk import RiskLevel

    t = TGEventImpactTool()
    assert t.name == "tg_event_impact"
    assert t.risk_level == RiskLevel.SAFE
    params = t.parameters
    assert "account_id" in params["properties"]
    assert "hours" in params["properties"]
    assert "limit" in params["properties"]
    assert "account_id" in params["required"]
