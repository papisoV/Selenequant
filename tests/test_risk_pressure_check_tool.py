"""Tests for RiskPressureCheckTool — aggregates macro/VIX/TG alerts into a single report.

Uses a fake duck-typed EventGuard — no real DB required. The tool only needs
``get_active_alerts(symbol, now_ts)`` and ``get_vix_regime(now_ts)`` to work.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "packages" / "agent-runtime"))
sys.path.insert(0, str(REPO / "packages" / "shared-types"))
sys.path.insert(0, str(REPO / "packages" / "alpha-core"))


# ---------------------------------------------------------------------------
# Fakes matching EventGuard / EventAlert / AlertLevel / EventSource shapes
# ---------------------------------------------------------------------------


class FakeAlertLevel(str, Enum):
    GREEN = "green"
    COOLDOWN = "cooldown"
    YELLOW = "yellow"
    ORANGE = "orange"
    RED = "red"


class FakeEventSource(str, Enum):
    MACRO_CALENDAR = "macro_calendar"
    TG_REALTIME = "tg_realtime"
    UNLOCK_CALENDAR = "unlock_calendar"


@dataclass(frozen=True)
class FakeEventAlert:
    source: FakeEventSource
    level: FakeAlertLevel
    symbol: str
    reason: str
    start_ts: int
    end_ts: int
    multiplier: float
    event_type: str = ""

    def is_active(self, now_ts: int) -> bool:
        return self.start_ts <= now_ts < self.end_ts


class FakeEventGuard:
    """Duck-typed EventGuard — returns pre-configured alerts + regime."""

    def __init__(
        self,
        alerts: list[FakeEventAlert] | None = None,
        vix_regime: str = "normal",
    ) -> None:
        self._alerts = alerts or []
        self._vix_regime = vix_regime

    def get_active_alerts(
        self, symbol: str | None = None, now_ts: int | None = None
    ) -> list[FakeEventAlert]:
        # Optional symbol filter — matches EventGuard's behavior of returning
        # both global (*) and symbol-specific alerts.
        if symbol is None or symbol == "*":
            return list(self._alerts)
        return [
            a for a in self._alerts
            if a.symbol == "*" or a.symbol.upper() == symbol.upper().replace("/USDT", "")
        ]

    def get_vix_regime(self, now_ts: int | None = None) -> str:
        return self._vix_regime


def _alert(
    source: FakeEventSource,
    level: FakeAlertLevel,
    multiplier: float,
    *,
    symbol: str = "*",
    reason: str = "test",
    event_type: str = "",
    duration_h: float = 1.0,
) -> FakeEventAlert:
    now_ms = int(time.time() * 1000)
    return FakeEventAlert(
        source=source,
        level=level,
        symbol=symbol,
        reason=reason,
        start_ts=now_ms - 60_000,
        end_ts=now_ms + int(duration_h * 3_600_000),
        multiplier=multiplier,
        event_type=event_type,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_active_alerts_low_vix():
    """No alerts + normal VIX → 综合压力 '无'."""
    from selene_space_agent.tools.risk_tools import RiskPressureCheckTool

    guard = FakeEventGuard(alerts=[], vix_regime="normal")
    t = RiskPressureCheckTool(guard)
    r = t.execute({})
    assert r.error is None
    assert "无活跃告警" in r.output or "0 active alert" in r.output
    assert "等级: 无" in r.output
    assert "正常交易" in r.output


def test_macro_orange_only():
    """Only CPI ORANGE → 综合压力 '中', suggested multiplier ≤0.8."""
    from selene_space_agent.tools.risk_tools import RiskPressureCheckTool

    alert = _alert(
        FakeEventSource.MACRO_CALENDAR,
        FakeAlertLevel.ORANGE,
        0.8,
        symbol="*",
        reason="CPI in 18h",
        event_type="CPI",
    )
    guard = FakeEventGuard(alerts=[alert], vix_regime="normal")
    t = RiskPressureCheckTool(guard)
    r = t.execute({})
    assert r.error is None
    assert "Macro Calendar" in r.output
    assert "ORANGE" in r.output
    assert "CPI" in r.output
    assert "等级: 中" in r.output
    assert "0.80" in r.output  # multiplier appears somewhere


def test_tg_red_only():
    """SECURITY RED → 综合压力 '高', 建议不开新仓."""
    from selene_space_agent.tools.risk_tools import RiskPressureCheckTool

    alert = _alert(
        FakeEventSource.TG_REALTIME,
        FakeAlertLevel.RED,
        0.0,
        symbol="*",
        reason="major hack",
        event_type="SECURITY",
    )
    guard = FakeEventGuard(alerts=[alert], vix_regime="normal")
    t = RiskPressureCheckTool(guard)
    r = t.execute({})
    assert r.error is None
    assert "TG Realtime" in r.output
    assert "RED" in r.output
    assert "等级: 高" in r.output
    assert "不开新仓" in r.output


def test_vix_high_regime_only():
    """VIX high regime + no events → 综合压力 '低', VIX multiplier applied."""
    from selene_space_agent.tools.risk_tools import RiskPressureCheckTool

    guard = FakeEventGuard(alerts=[], vix_regime="high")
    t = RiskPressureCheckTool(guard)
    r = t.execute({})
    assert r.error is None
    assert "high" in r.output.lower()
    assert "VIX" in r.output
    # VIX high regime multiplier is 0.8 — should appear in suggestion
    assert "0.80" in r.output or "0.8" in r.output


def test_vix_crisis_regime_only():
    """VIX crisis regime + no events → 综合压力 '高'."""
    from selene_space_agent.tools.risk_tools import RiskPressureCheckTool

    guard = FakeEventGuard(alerts=[], vix_regime="crisis")
    t = RiskPressureCheckTool(guard)
    r = t.execute({})
    assert r.error is None
    assert "crisis" in r.output.lower()
    assert "等级: 高" in r.output


def test_multi_source_combined():
    """macro ORANGE + TG ORANGE + VIX high → all three layers listed, combined pressure 中 or 高."""
    from selene_space_agent.tools.risk_tools import RiskPressureCheckTool

    macro_alert = _alert(
        FakeEventSource.MACRO_CALENDAR,
        FakeAlertLevel.ORANGE,
        0.8,
        symbol="*",
        reason="CPI in 12h",
        event_type="CPI",
    )
    tg_alert = _alert(
        FakeEventSource.TG_REALTIME,
        FakeAlertLevel.ORANGE,
        0.7,
        symbol="*",
        reason="Balancer hack",
        event_type="SECURITY",
    )
    guard = FakeEventGuard(alerts=[macro_alert, tg_alert], vix_regime="high")
    t = RiskPressureCheckTool(guard)
    r = t.execute({})
    assert r.error is None
    # All three layers should be represented
    assert "Macro Calendar" in r.output
    assert "TG Realtime" in r.output
    assert "high" in r.output.lower()
    # Both event types in triggered alerts list
    assert "CPI" in r.output
    assert "SECURITY" in r.output


def test_symbol_filter_btc():
    """symbol='BTC/USDT' filters to BTC-specific + global alerts."""
    from selene_space_agent.tools.risk_tools import RiskPressureCheckTool

    btc_alert = _alert(
        FakeEventSource.TG_REALTIME,
        FakeAlertLevel.ORANGE,
        0.7,
        symbol="BTC",
        reason="BTC-specific hack",
        event_type="SECURITY",
    )
    eth_alert = _alert(
        FakeEventSource.TG_REALTIME,
        FakeAlertLevel.ORANGE,
        0.7,
        symbol="ETH",
        reason="ETH-specific hack",
        event_type="SECURITY",
    )
    global_alert = _alert(
        FakeEventSource.MACRO_CALENDAR,
        FakeAlertLevel.YELLOW,
        0.9,
        symbol="*",
        reason="FOMC in 48h",
        event_type="FOMC",
    )
    guard = FakeEventGuard(alerts=[btc_alert, eth_alert, global_alert], vix_regime="normal")
    t = RiskPressureCheckTool(guard)
    r = t.execute({"symbol": "BTC/USDT"})
    assert r.error is None
    # BTC-specific and global should appear
    assert "BTC" in r.output
    assert "FOMC" in r.output
    # ETH-specific should NOT appear
    assert "ETH-specific" not in r.output


def test_symbol_in_header():
    """When symbol provided, header shows it; when omitted, shows '*'."""
    from selene_space_agent.tools.risk_tools import RiskPressureCheckTool

    guard = FakeEventGuard(alerts=[], vix_regime="normal")
    t = RiskPressureCheckTool(guard)

    r1 = t.execute({"symbol": "BTC/USDT"})
    assert "symbol=BTC/USDT" in r1.output

    r2 = t.execute({})
    assert "symbol=*" in r2.output


def test_tool_metadata():
    from selene_space_types.risk import RiskLevel
    from selene_space_agent.tools.risk_tools import RiskPressureCheckTool

    t = RiskPressureCheckTool(FakeEventGuard())
    assert t.name == "risk_pressure_check"
    assert t.risk_level == RiskLevel.SAFE
    params = t.parameters
    assert "symbol" in params["properties"]
    assert "now_ts" in params["properties"]


def test_now_ts_override():
    """Passing now_ts uses that timestamp instead of current time."""
    from selene_space_agent.tools.risk_tools import RiskPressureCheckTool

    guard = FakeEventGuard(alerts=[], vix_regime="normal")
    t = RiskPressureCheckTool(guard)
    r = t.execute({"now_ts": 1700000000000})
    assert r.error is None
    # Tool should execute successfully with the overridden timestamp
    assert "Risk pressure report" in r.output


def test_event_guard_exception_handled():
    """If EventGuard raises, tool returns error (no exception escapes)."""
    from selene_space_agent.tools.risk_tools import RiskPressureCheckTool

    class BrokenGuard:
        def get_active_alerts(self, *a, **kw):
            raise RuntimeError("DB connection failed")

        def get_vix_regime(self, *a, **kw):
            raise RuntimeError("DB connection failed")

    t = RiskPressureCheckTool(BrokenGuard())
    r = t.execute({})
    assert r.error is not None
    assert "DB connection failed" in r.error or "Risk pressure query failed" in r.output
