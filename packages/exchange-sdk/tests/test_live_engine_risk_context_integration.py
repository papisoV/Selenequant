"""Tests for live_engine.py risk context integration with FillsLedger + EquityTracker.

Phase 2b US-005: verifies the engine stores the FillsLedger + EquityTracker
references (via constructor or setters) so place_order can wire them into the
risk context. Full place_order flow requires a live exchange — these tests
verify the wiring contract only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from selene_space_exchange.execution.live_engine import LiveExecutionEngine
from selene_space_risk.fills_ledger import FillsLedger
from selene_space_risk.equity_tracker import EquityTracker
from selene_space_types.trading import Fill


def _ts(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp() * 1000)


class _StubTradingClient:
    """Minimal TradingClient stub — LiveExecutionEngine only needs it for construction."""

    exchange = "binance"


def _make_engine(
    *,
    fills_ledger: Any | None = None,
    equity_tracker: Any | None = None,
) -> LiveExecutionEngine:
    """Build a LiveExecutionEngine with a None risk_engine (wiring-only test)."""
    return LiveExecutionEngine(
        trading_client=_StubTradingClient(),
        risk_engine=None,
        fills_ledger=fills_ledger,
        equity_tracker=equity_tracker,
    )


def test_fills_ledger_wired_via_constructor(tmp_path: Path) -> None:
    """Constructor stashes fills_ledger; risk context will use it for daily_realized_pnl."""
    ledger = FillsLedger(db_path=tmp_path / "fills.db")
    today = datetime.now(timezone.utc)
    ts = _ts(today.year, today.month, today.day, 12, 0)
    fill = Fill(
        id="f-1", order_id="o-1", client_order_id="o-1",
        price=50000.0, quantity=0.01, fee=0.0,
        realized_pnl=-15.0, side="sell", symbol="BTCUSDT", timestamp=ts,
    )
    ledger.record_fill(fill, account_id="pa-1")

    engine = _make_engine(fills_ledger=ledger)
    assert engine._fills_ledger is ledger
    today_utc = datetime.now(timezone.utc).date()
    assert ledger.get_daily_realized_pnl("pa-1", today_utc) == pytest.approx(-15.0)


def test_equity_tracker_wired_via_constructor() -> None:
    """Constructor stashes equity_tracker; risk context will use tracker.peak for peak_equity."""
    tracker = EquityTracker()
    tracker.update(100.0, _ts(2026, 7, 26, 9, 0))
    tracker.update(70.0, _ts(2026, 7, 26, 14, 0))

    engine = _make_engine(equity_tracker=tracker)
    assert engine._equity_tracker is tracker
    assert tracker.peak == pytest.approx(100.0)
    assert tracker.get_drawdown(70.0) == pytest.approx(0.30)


def test_set_fills_ledger_and_set_equity_tracker_setters() -> None:
    """set_fills_ledger / set_equity_tracker inject dependencies post-construction."""
    engine = _make_engine()
    assert engine._fills_ledger is None
    assert engine._equity_tracker is None

    tracker = EquityTracker()
    tracker.update(100.0, _ts(2026, 7, 26, 9, 0))
    engine.set_equity_tracker(tracker)
    assert engine._equity_tracker is tracker
    assert tracker.peak == pytest.approx(100.0)

    # Setter accepts None (backward compat for shadow mode without tracker)
    engine.set_equity_tracker(None)
    assert engine._equity_tracker is None

