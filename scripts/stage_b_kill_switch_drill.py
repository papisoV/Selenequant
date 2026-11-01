"""Stage B kill-switch drill (independent of the shadow run).

Constructs a ShadowExecutionEngine + KillSwitch singleton, places orders
that REMAIN OPEN (a resting LIMIT far from market + a pending MARKET not
fed a mark_price), then activates the kill switch and verifies
cancel_all_open_orders() cancels them and journals
status='failed' reason='cancelled_by_kill_switch'.

Why independent: the bb_v3 strategy only uses MARKET orders with a
mark_price, which fill instantly in shadow mode — so a live run never has
open orders for a kill-switch trigger to cancel (check_6 stays EXPECTED_FAIL).
This drill manufactures open orders to prove the cancellation path works.

Uses the REAL journal DB (~/.selenespace/journal/order_journal.db) so
verdict check_6 (which scans it without a time filter) sees the evidence.

MUST end with ks.deactivate(): KillSwitch persists to
~/.selenespace/kill_switch.json; leaving it active would block every order
in the subsequent shadow run.

Usage (repo root, after activate.bat):
  python scripts/stage_b_kill_switch_drill.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from selene_space_exchange.execution.live_state import LiveStateRepository
from selene_space_exchange.execution.order_journal import OrderJournal
from selene_space_exchange.execution.shadow_engine import ShadowExecutionEngine
from selene_space_risk.kill_switch import KillSwitch
from selene_space_types.trading import MarginMode, OrderSide, OrderType, PositionSide

JOURNAL_DB = Path.home() / ".selenespace" / "journal" / "order_journal.db"


def count_cancel_evidence() -> int:
    import sqlite3

    if not JOURNAL_DB.exists():
        return 0
    con = sqlite3.connect(f"file:{JOURNAL_DB}?mode=ro", uri=True)
    try:
        cur = con.execute(
            "SELECT COUNT(*) FROM order_journal "
            "WHERE status='failed' AND reason='cancelled_by_kill_switch'"
        )
        return int(cur.fetchone()[0])
    finally:
        con.close()


async def main() -> int:
    engine = ShadowExecutionEngine(
        journal=OrderJournal(),
        state_repo=LiveStateRepository(),
    )
    ks = KillSwitch()  # singleton — same instance main.py wires
    if ks.is_active:
        print("[drill] kill switch already active — deactivating first")
        ks.deactivate()

    engine.set_kill_switch(ks)
    await engine.start()

    before = count_cancel_evidence()
    print(f"[drill] cancel evidence before: {before}")

    # 1. Resting LIMIT far from market — never fills, stays open.
    lim = await engine.place_order(
        account_id="pa-drill",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=0.01,
        price=1.0,  # far below BTC ~100k → rests in the book
        leverage=1,
        margin_mode=MarginMode.CROSS,
        pos_side=PositionSide.NET,
        reduce_only=False,
    )
    print(f"[drill] resting LIMIT id={lim.id} status={lim.status.value}")

    # 2. Pending MARKET — no mark_price fed, fill_on_next_tick → queues.
    mkt = await engine.place_order(
        account_id="pa-drill",
        symbol="BTC/USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=0.01,
        leverage=1,
        margin_mode=MarginMode.CROSS,
        pos_side=PositionSide.NET,
        reduce_only=False,
    )
    print(f"[drill] pending MARKET id={mkt.id} status={mkt.status.value}")

    open_after_place = (
        len(engine._pending_market_orders.get("BTC/USDT", []))
        + len(engine._order_manager.get_open_orders())
    )
    print(f"[drill] open orders after place: {open_after_place}")
    if open_after_place < 2:
        print("[drill] FAIL: expected ≥2 open orders to cancel")
        ks.deactivate()
        await engine.stop()
        return 2

    # 3. Activate the kill switch — synchronously fires cancel_all_open_orders.
    ks.activate(reason="stage_b kill-switch drill")
    after = count_cancel_evidence()
    print(f"[drill] cancel evidence after: {after}")

    open_after_kill = (
        len(engine._pending_market_orders.get("BTC/USDT", []))
        + len(engine._order_manager.get_open_orders())
    )
    print(f"[drill] open orders after kill switch: {open_after_kill}")

    ok = after > before and open_after_kill == 0
    print(f"[drill] {'PASS' if ok else 'FAIL'}: cancelled {after - before} order(s), "
          f"open orders remaining {open_after_kill}")

    # 4. Deactivate — restore persisted state so the shadow run can order.
    ks.deactivate()
    print(f"[drill] kill switch deactivated, is_active={ks.is_active}")

    await engine.stop()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
