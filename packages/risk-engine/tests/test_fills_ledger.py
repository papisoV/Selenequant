"""RED tests for FillsLedger (Phase 2a).

All tests should FAIL with NotImplementedError — Phase 2b will implement GREEN.
"""

from __future__ import annotations

from datetime import date, timezone, datetime
from pathlib import Path

import pytest

from selene_space_risk.fills_ledger import FillsLedger
from selene_space_types.trading import Fill


def _make_fill(
    *,
    fill_id: str,
    order_id: str,
    symbol: str = "BTCUSDT",
    qty: float = 0.01,
    price: float = 50000.0,
    fee: float = 0.0,
    realized_pnl: float = 0.0,
    side: str = "buy",
    timestamp: int,
    account_id: str = "pa-1",
) -> Fill:
    return Fill(
        id=fill_id,
        order_id=order_id,
        client_order_id=order_id,
        price=price,
        quantity=qty,
        fee=fee,
        realized_pnl=realized_pnl,
        side=side,
        symbol=symbol,
        timestamp=timestamp,
    )


@pytest.fixture
def ledger(tmp_path: Path) -> FillsLedger:
    return FillsLedger(db_path=tmp_path / "fills.db")


def test_record_fill_and_get_daily_realized_pnl(ledger: FillsLedger) -> None:
    """Same-day fills aggregate daily_realized_pnl correctly."""
    ts = int(datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    fill1 = _make_fill(
        fill_id="f-1", order_id="o-1", qty=0.01, price=50000.0,
        realized_pnl=50.0, timestamp=ts,
    )
    fill2 = _make_fill(
        fill_id="f-2", order_id="o-2", qty=0.02, price=51000.0,
        realized_pnl=80.0, timestamp=ts + 1000,
    )

    ledger.record_fill(fill1, account_id="pa-1")
    ledger.record_fill(fill2, account_id="pa-1")

    daily = ledger.get_daily_realized_pnl("pa-1", date(2026, 7, 26))
    assert daily == pytest.approx(130.0)  # 50 + 80


def test_fills_aggregation_across_days(ledger: FillsLedger) -> None:
    """Fills on different UTC days aggregate into separate daily_realized_pnl."""
    ts1 = int(datetime(2026, 7, 26, 23, 59, tzinfo=timezone.utc).timestamp() * 1000)
    ts2 = int(datetime(2026, 7, 27, 0, 1, tzinfo=timezone.utc).timestamp() * 1000)

    ledger.record_fill(
        _make_fill(fill_id="f-1", order_id="o-1", realized_pnl=100.0, timestamp=ts1),
        account_id="pa-1",
    )
    ledger.record_fill(
        _make_fill(fill_id="f-2", order_id="o-2", realized_pnl=200.0, timestamp=ts2),
        account_id="pa-1",
    )

    assert ledger.get_daily_realized_pnl("pa-1", date(2026, 7, 26)) == pytest.approx(100.0)
    assert ledger.get_daily_realized_pnl("pa-1", date(2026, 7, 27)) == pytest.approx(200.0)
    assert ledger.get_daily_realized_pnl("pa-1", date(2026, 7, 25)) == pytest.approx(0.0)


def test_get_fills_for_order(ledger: FillsLedger) -> None:
    """Partial fills: get_fills_for_order returns all fills for one order_id."""
    ts = int(datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    ledger.record_fill(
        _make_fill(fill_id="f-1", order_id="o-1", qty=0.005, timestamp=ts),
        account_id="pa-1",
    )
    ledger.record_fill(
        _make_fill(fill_id="f-2", order_id="o-1", qty=0.005, timestamp=ts + 1000),
        account_id="pa-1",
    )
    ledger.record_fill(
        _make_fill(fill_id="f-3", order_id="o-2", qty=0.01, timestamp=ts + 2000),
        account_id="pa-1",
    )

    fills = ledger.get_fills_for_order("o-1")
    assert len(fills) == 2
    assert {f.id for f in fills} == {"f-1", "f-2"}
    # Ordered by timestamp ASC
    assert fills[0].id == "f-1"
    assert fills[1].id == "f-2"


def test_partial_fill_realized_pnl(ledger: FillsLedger) -> None:
    """Partial close: realized_pnl computed from avg_entry * partial_qty."""
    # Open long 0.02 @ 50000
    # Close 0.01 @ 52000 → realized = (52000 - 50000) * 0.01 - fee
    # fee = 52000 * 0.01 * 0.001 = 0.52
    # expected realized = 20 - 0.52 = 19.48
    pnl = ledger.compute_realized_pnl_for_fill(
        fill=_make_fill(
            fill_id="f-1", order_id="o-1", qty=0.01, price=52000.0,
            fee=0.52, side="sell", timestamp=0,
        ),
        position_avg_entry=50000.0,
        position_qty_before=0.02,  # long 0.02
    )
    assert float(pnl) == pytest.approx(19.48)


def test_fee_included_in_realized_pnl(ledger: FillsLedger) -> None:
    """Fee is subtracted from realized_pnl."""
    # Long 0.01 @ 50000, close 0.01 @ 51000, fee=10
    # realized = (51000 - 50000) * 0.01 - 10 = 10 - 10 = 0
    pnl = ledger.compute_realized_pnl_for_fill(
        fill=_make_fill(
            fill_id="f-1", order_id="o-1", qty=0.01, price=51000.0,
            fee=10.0, side="sell", timestamp=0,
        ),
        position_avg_entry=50000.0,
        position_qty_before=0.01,
    )
    assert float(pnl) == pytest.approx(0.0)


def test_record_fill_is_idempotent(ledger: FillsLedger) -> None:
    """Duplicate fill_id is a no-op (restart / replay safe)."""
    ts = int(datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    fill = _make_fill(
        fill_id="f-1", order_id="o-1", realized_pnl=50.0, timestamp=ts,
    )
    ledger.record_fill(fill, account_id="pa-1")
    # Replay same fill
    ledger.record_fill(fill, account_id="pa-1")

    assert ledger.get_daily_realized_pnl("pa-1", date(2026, 7, 26)) == pytest.approx(50.0)


def test_account_isolation(ledger: FillsLedger) -> None:
    """Fills for different account_ids don't cross-contaminate."""
    ts = int(datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    ledger.record_fill(
        _make_fill(fill_id="f-1", order_id="o-1", realized_pnl=100.0, timestamp=ts),
        account_id="pa-1",
    )
    ledger.record_fill(
        _make_fill(fill_id="f-2", order_id="o-2", realized_pnl=200.0, timestamp=ts),
        account_id="pa-2",
    )

    assert ledger.get_daily_realized_pnl("pa-1", date(2026, 7, 26)) == pytest.approx(100.0)
    assert ledger.get_daily_realized_pnl("pa-2", date(2026, 7, 26)) == pytest.approx(200.0)


def test_risk_engine_decimal_pnl(ledger: FillsLedger) -> None:
    """H19: realized_pnl computed in Decimal so 0.1 + 0.2 == 0.3 exactly.

    With float arithmetic, (0.3 - 0.1) * 1.0 - 0.0 would drift to
    0.09999999999999998. Decimal keeps it exact.
    """
    from decimal import Decimal

    # Open long 1.0 @ 0.1, close 1.0 @ 0.3, fee=0
    # realized = (0.3 - 0.1) * 1.0 - 0 = 0.2 exactly (Decimal)
    pnl = ledger.compute_realized_pnl_for_fill(
        fill=_make_fill(
            fill_id="f-dec",
            order_id="o-dec",
            qty=1.0,
            price=0.3,
            fee=0.0,
            side="sell",
            timestamp=0,
        ),
        position_avg_entry=0.1,
        position_qty_before=1.0,  # long 1.0
    )
    # Decimal arithmetic: 0.3 - 0.1 = 0.2 exactly (no float drift)
    assert isinstance(pnl, Decimal), "realized_pnl must be Decimal (H19)"
    assert pnl == Decimal("0.2"), f"expected 0.2 exactly, got {pnl}"

    # And the canonical float-drift check holds under Decimal
    assert pnl == Decimal("0.1") + Decimal("0.1")
