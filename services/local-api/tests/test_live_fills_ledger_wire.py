"""US-001 S0: live fills ledger write-side wire + fail-open .

Verifies main.py's S0-3 layer: every live fill (fired via LiveExecutionEngine's
on_fill/_fire_fill) lands in the shared fills ledger under LIVE_ACCOUNT_ID, and
a failing record_fill is swallowed (fail-open) without aborting later on_fill
callbacks.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.main import LIVE_ACCOUNT_ID, _record_live_fill
from selene_space_exchange.execution.live_engine import LiveExecutionEngine
from selene_space_risk.fills_ledger import FillsLedger
from selene_space_types.trading import Fill


def _make_fill(
    *,
    fill_id: str,
    order_id: str,
    ts: int,
    symbol: str = "BTC/USDT",
    qty: float = 0.01,
    price: float = 50000.0,
    fee: float = 0.5,
) -> Fill:
    return Fill(
        id=fill_id,
        order_id=order_id,
        client_order_id=order_id,
        price=price,
        quantity=qty,
        fee=fee,
        realized_pnl=0.0,
        side="buy",
        symbol=symbol,
        timestamp=ts,
    )


def _make_engine() -> LiveExecutionEngine:
    return LiveExecutionEngine(object(), reconciliation_interval_s=9999)


def _now_ms() -> int:
    return int(datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)


def test_live_fill_write_side_records_to_ledger(tmp_path) -> None:
    ledger = FillsLedger(db_path=tmp_path / "fills.db")
    engine = _make_engine()
    engine.set_fills_ledger(ledger)
    engine.on_fill(_record_live_fill(ledger, LIVE_ACCOUNT_ID))

    ts = _now_ms()
    asyncio.run(engine._fire_fill(_make_fill(fill_id="live-f1", order_id="o-1", ts=ts)))

    rows = ledger.get_all_fills(LIVE_ACCOUNT_ID, 0, ts + 86_400_000)
    assert len(rows) == 1
    assert rows[0].id == "live-f1"
    # read-side: engine and ledger are the same instance (live_engine :607 gate)
    assert engine._fills_ledger is ledger


def test_record_fill_failure_is_fail_open(tmp_path) -> None:
    FillsLedger(db_path=tmp_path / "fills.db")

    class _RaisingLedger:
        def record_fill(self, fill, account_id: str = "") -> None:
            raise RuntimeError("db write failed")

    engine = _make_engine()
    fired: list[str] = []
    engine.on_fill(_record_live_fill(_RaisingLedger(), LIVE_ACCOUNT_ID))
    engine.on_fill(lambda f: fired.append(f.id))

    ts = _now_ms()
    # must NOT raise: the fail-open wrapper swallows record_fill's exception
    asyncio.run(engine._fire_fill(_make_fill(fill_id="live-f2", order_id="o-2", ts=ts)))
    assert fired == ["live-f2"]  # downstream callback still executed


def test_record_live_fill_writes_under_constant(tmp_path) -> None:
    """The production wrapper records under LIVE_ACCOUNT_ID, not a hardcoded string."""
    ledger = FillsLedger(db_path=tmp_path / "fills.db")
    cb = _record_live_fill(ledger, LIVE_ACCOUNT_ID)

    ts = _now_ms()
    cb(_make_fill(fill_id="live-f3", order_id="o-3", ts=ts))

    rows = ledger.get_all_fills(LIVE_ACCOUNT_ID, 0, ts + 86_400_000)
    assert len(rows) == 1 and rows[0].id == "live-f3"
