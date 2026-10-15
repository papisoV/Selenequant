"""H15: ContinuousRiskMonitor total_equity must include unrealized PnL.

Bug: monitor's ``_build_snapshot`` only summed ``balance.free + balance.locked``,
ignoring unrealized PnL on open positions. With open profit this understated
``total_equity`` → false-positive drawdown → KillSwitch false trip. With open
loss it overstated equity → missed risk events.

Fix: when the execution engine exposes ``compute_equity(account_id)`` (paper
engine does; live engine does not yet), the monitor prefers it over the raw
balance sum so ``total_equity`` reflects the true account value.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from selene_space_paper.paper_execution import DEFAULT_ACCOUNT_ID, PaperExecutionEngine
from selene_space_risk.monitor import ContinuousRiskMonitor
from selene_space_types.trading import (
    MarginMode,
    OrderSide,
    Position,
    PositionSide,
)


def _make_open_position(
    *,
    symbol: str = "BTC/USDT",
    side: OrderSide = OrderSide.BUY,
    entry_price: float = 67000.0,
    quantity_base: float = 0.1,
    unrealized_pnl: float = 0.0,
    account_id: str = DEFAULT_ACCOUNT_ID,
) -> Position:
    """Construct an open Position with the given unrealized PnL."""
    now_ms = int(time.time() * 1000)
    return Position(
        symbol=symbol,
        side=side,
        pos_side=PositionSide.NET,
        quantity=quantity_base * entry_price,
        quantity_base=quantity_base,
        avg_entry_price=entry_price,
        leverage=1,
        margin_mode=MarginMode.CROSS,
        margin=quantity_base * entry_price,
        liquidation_price=None,
        unrealized_pnl=unrealized_pnl,
        unrealized_pnl_pct=0.0,
        realized_pnl=0.0,
        current_price=entry_price,
        opened_at=now_ms,
        updated_at=now_ms,
    )


@pytest.fixture
def paper_engine() -> PaperExecutionEngine:
    """A started PaperExecutionEngine with the default account seeded."""
    engine = PaperExecutionEngine(initial_balance=10000.0)
    asyncio.run(engine.start())
    return engine


def test_monitor_total_equity_includes_unrealized_pnl(
    paper_engine: PaperExecutionEngine,
) -> None:
    """H15: snapshot.total_equity = wallet_balance + unrealized_pnl.

    With a 3000 USDT open profit on top of a 10000 USDT wallet balance,
    total_equity must be 13000 — not 10000 (the old balance-only sum).
    """
    # Seed an open long position with +3000 unrealized profit.
    pos = _make_open_position(unrealized_pnl=3000.0)
    paper_engine._position_mgr._book[DEFAULT_ACCOUNT_ID] = {"BTC/USDT": pos}

    monitor = ContinuousRiskMonitor(execution_engine=paper_engine)

    snapshot = monitor._compute_from_sync_state()
    assert snapshot is not None, "expected a snapshot from sync state"

    account = paper_engine._account_mgr.get_account(DEFAULT_ACCOUNT_ID)
    wallet_balance = float(account.initial_balance)  # 10000.0, no realized PnL yet

    assert snapshot.total_equity == pytest.approx(
        wallet_balance + 3000.0, rel=1e-6
    ), (
        f"total_equity should include unrealized PnL "
        f"(expected ~{wallet_balance + 3000.0}, got {snapshot.total_equity})"
    )


def test_monitor_no_false_drawdown_with_open_profit(
    paper_engine: PaperExecutionEngine,
) -> None:
    """H15: open profit must not trigger a false drawdown.

    Scenario: peak_equity was 10000 (wallet-only baseline, captured before
    the position was opened). Now the account has -2000 realized PnL
    (so wallet_balance = 8000) but +3000 open profit, so true total_equity
    = 11000 — above peak. drawdown_pct must be 0 (or negative), not the
    20% the old balance-only math would produce from the 8000 wallet.
    """
    from decimal import Decimal

    # Simulate -2000 realized loss so wallet_balance drops to 8000.
    account = paper_engine._account_mgr.get_account(DEFAULT_ACCOUNT_ID)
    account.total_realized_pnl = Decimal("-2000")
    account.current_balance = account.initial_balance + Decimal("-2000")

    pos = _make_open_position(unrealized_pnl=3000.0)
    paper_engine._position_mgr._book[DEFAULT_ACCOUNT_ID] = {"BTC/USDT": pos}

    monitor = ContinuousRiskMonitor(execution_engine=paper_engine)
    # peak_equity was captured when only the 10000 wallet was visible.
    monitor._peak_equity = 10000.0

    snapshot = monitor._compute_from_sync_state()
    assert snapshot is not None

    # Manually replay the drawdown calc from _check_cycle (avoids needing
    # the async loop). The guard requires total_equity > 0.
    assert snapshot.total_equity > 0
    snapshot.drawdown_pct = (1.0 - snapshot.total_equity / monitor._peak_equity) * 100.0

    # wallet_balance (8000) + unrealized (3000) = 11000
    assert snapshot.total_equity == pytest.approx(11000.0, rel=1e-6)
    assert snapshot.drawdown_pct <= 0.0, (
        f"open profit should not produce a drawdown "
        f"(got {snapshot.drawdown_pct:.2f}%)"
    )
