"""Round 6: live position-vs-strategy-target audit.

Exercises :mod:`selene_space_risk.position_audit` (the advisory audit) and its
wiring into :class:`selene_space_risk.monitor.ContinuousRiskMonitor`. The audit
compares *held* positions (mirroring the risk snapshot's ``abs(qty * entry)``
notional) against strategy targets with a mutually exclusive, descending
priority per symbol: banned-held -> DANGEROUS, explicit target 0 still held ->
WARNING, notional/target over 1.35 -> DANGEROUS. It is advisory only — it never
feeds ``critical_reasons`` and never triggers the kill switch (contrast
``SymbolBlacklistRule``, a pre-trade CRITICAL hard-block).
"""

from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal

import pytest

from selene_space_risk.monitor import ContinuousRiskMonitor
from selene_space_risk.position_audit import (
    AUDIT_OVERBAND_RATIO,
    PositionTargetProvider,
    StaticPositionTargetProvider,
    audit_positions_against_targets,
    position_notional,
)
from selene_space_types.risk import RiskAlert, RiskLevel
from selene_space_types.trading import MarginMode, OrderSide, Position, PositionSide


def _pos(symbol: str, qty: float, entry: float) -> Position:
    """A held position with ``notional = qty * entry`` (monitor's convention)."""
    now_ms = int(time.time() * 1000)
    return Position(
        symbol=symbol,
        side=OrderSide.BUY,
        pos_side=PositionSide.NET,
        quantity=Decimal(str(qty)),
        quantity_base=Decimal(str(qty)),
        avg_entry_price=Decimal(str(entry)),
        leverage=1,
        margin_mode=MarginMode.CROSS,
        margin=Decimal("0"),
        liquidation_price=None,
        unrealized_pnl=Decimal("0"),
        unrealized_pnl_pct=Decimal("0"),
        realized_pnl=Decimal("0"),
        current_price=Decimal(str(entry)),
        opened_at=now_ms,
        updated_at=now_ms,
    )


class _RecordingKillSwitch:
    def __init__(self) -> None:
        self.activated: list[str] = []

    def auto_activate(self, reason: str, **_kwargs: object) -> None:
        self.activated.append(reason)


class _FakePositionManager:
    def __init__(self, positions: list[Position]) -> None:
        self._positions = list(positions)

    def get_open_positions(self, _account_id: str) -> list[Position]:
        return list(self._positions)


class _FakeExecutionEngine:
    """Sync-compute_equity engine exposing open positions to the monitor."""

    def __init__(self, positions: list[Position], equity: float = 10000.0) -> None:
        self._position_mgr = _FakePositionManager(positions)
        self._balances: list[object] = []
        self._positions = list(positions)
        self._equity = equity

    def compute_equity(self, _account_id: str) -> float:
        return self._equity


# --------------------------------------------------------------------------
# Notional / basic alert semantics
# --------------------------------------------------------------------------
def test_under_target_no_alert() -> None:
    # Notional convention: abs(qty * entry).
    assert position_notional(_pos("BTC/USDT", 1.0, 100.0)) == 100.0
    # notional 100 <= target 150 -> within band, silent.
    assert audit_positions_against_targets([_pos("BTC/USDT", 1.0, 100.0)], {"BTC/USDT": 150.0}) == []


def test_over_exposure_dangerous() -> None:
    alerts = audit_positions_against_targets([_pos("ETH/USDT", 2.0, 100.0)], {"ETH/USDT": 100.0})
    assert len(alerts) == 1
    assert alerts[0].rule_id == "position_over_target"
    assert alerts[0].level == RiskLevel.DANGEROUS


def test_ratio_boundary_135_strict() -> None:
    # Exactly 1.35x -> no alert (strict >); epsilon above -> alert.
    assert audit_positions_against_targets([_pos("X", 1.35, 100.0)], {"X": 100.0}) == []
    assert audit_positions_against_targets([_pos("X", 1.3501, 100.0)], {"X": 100.0}) != []
    assert AUDIT_OVERBAND_RATIO == 1.35


def test_expected_zero_but_holding_warning() -> None:
    alerts = audit_positions_against_targets([_pos("DOGE/USDT", 5.0, 10.0)], {"DOGE/USDT": 0.0})
    assert len(alerts) == 1
    assert alerts[0].rule_id == "position_not_closed"
    assert alerts[0].level == RiskLevel.WARNING


def test_zero_notional_holding_ignored() -> None:
    # Flat holding (notional 0) produces no alert even with target 0.
    assert audit_positions_against_targets([_pos("X", 0.0, 100.0)], {"X": 0.0}) == []


def test_missing_target_ignored() -> None:
    # No target entry -> no alert (not "implied close").
    assert audit_positions_against_targets([_pos("Y", 5.0, 100.0)], {}) == []


def test_empty_targets_no_alerts() -> None:
    assert audit_positions_against_targets([_pos("A", 1.0, 100.0)], {}) == []
    assert audit_positions_against_targets([], {}) == []


# --------------------------------------------------------------------------
# banned dimension (mutually exclusive priority)
# --------------------------------------------------------------------------
def test_banned_symbol_holding_dangerous() -> None:
    alerts = audit_positions_against_targets([_pos("BAD", 3.0, 100.0)], {}, banned=frozenset({"BAD"}))
    assert len(alerts) == 1
    assert alerts[0].rule_id == "banned_position"
    assert alerts[0].level == RiskLevel.DANGEROUS


def test_banned_overrides_not_closed_and_over_target() -> None:
    # Banned wins: only the DANGEROUS banned alert, never the not-closed one.
    alerts = audit_positions_against_targets(
        [_pos("C", 3.0, 100.0)], {"C": 0.0}, banned=frozenset({"C"})
    )
    assert len(alerts) == 1
    assert alerts[0].rule_id == "banned_position"
    # Banned also outranks an over-target mismatch on the same symbol.
    alerts = audit_positions_against_targets(
        [_pos("D", 9.0, 100.0)], {"D": 100.0}, banned=frozenset({"D"})
    )
    assert len(alerts) == 1
    assert alerts[0].rule_id == "banned_position"


# --------------------------------------------------------------------------
# Static provider contract
# --------------------------------------------------------------------------
def test_static_provider_separates_banned() -> None:
    prov = StaticPositionTargetProvider({"A": 100.0, "B": 0.0}, banned=["X"])
    # get_targets() is explicit-only: banned is NOT folded in as a zero target.
    assert prov.get_targets() == {"A": 100.0, "B": 0.0}
    assert prov.get_banned() == frozenset({"X"})
    # Immutable getters: mutating the returned copy leaves the provider intact.
    t = prov.get_targets()
    t["A"] = 999.0
    assert prov.get_targets() == {"A": 100.0, "B": 0.0}
    # Protocol contract on the public surface.
    assert hasattr(PositionTargetProvider, "get_targets")
    assert hasattr(PositionTargetProvider, "get_banned")


def test_alert_pydantic_and_timestamp() -> None:
    alerts = audit_positions_against_targets([_pos("A", 2.0, 100.0)], {"A": 100.0})
    assert len(alerts) == 1
    alert = alerts[0]
    assert isinstance(alert, RiskAlert)
    assert alert.timestamp > 0
    assert alert.context["symbol"] == "A"
    assert alert.context["notional"] == pytest.approx(200.0)
    assert alert.context["target"] == pytest.approx(100.0)


# --------------------------------------------------------------------------
# Monitor wiring: advisory only, off by default
# --------------------------------------------------------------------------
def test_monitor_default_skips_audit(caplog: pytest.LogCaptureFixture) -> None:
    monitor = ContinuousRiskMonitor()
    monitor._last_positions = [_pos("A", 2.0, 100.0)]  # over its (unset) target
    with caplog.at_level(logging.WARNING, logger="selene_space_risk.monitor"):
        monitor._run_position_audit()
    assert "POSITION AUDIT" not in caplog.text


def test_monitor_with_provider_emits_audit(caplog: pytest.LogCaptureFixture) -> None:
    positions = [_pos("BTC/USDT", 2.0, 100.0)]  # notional 200, target 100 -> over
    kill_switch = _RecordingKillSwitch()
    provider = StaticPositionTargetProvider({"BTC/USDT": 100.0})
    monitor = ContinuousRiskMonitor(
        execution_engine=_FakeExecutionEngine(positions),
        kill_switch=kill_switch,
        target_provider=provider,
    )
    with caplog.at_level(logging.WARNING, logger="selene_space_risk.monitor"):
        asyncio.run(monitor._check_cycle())
    assert "POSITION AUDIT" in caplog.text
    assert "position_over_target" in caplog.text
    assert kill_switch.activated == [], "advisory audit must never trip the kill switch"
