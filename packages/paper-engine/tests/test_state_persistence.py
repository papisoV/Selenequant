"""SQLite-backed PaperState: Decimal fidelity and legacy-row tolerance (H19)."""

from __future__ import annotations

import json
import sqlite3
import time
from decimal import Decimal
from pathlib import Path

import pytest

from selene_space_paper.accounts import AccountManager
from selene_space_paper.orders import PaperOrderManager
from selene_space_paper.positions import PositionManager
from selene_space_paper.state import PaperState
from selene_space_types.trading import Fill

T0 = 1_742_630_400_000


def _fill_seq(**overrides) -> Fill:
    body = dict(
        id=f"fill-{int(time.time() * 1000)}",
        order_id="ord-1",
        price=67_000.0,
        quantity=10_000.0,
        fee=10_000.0 * 0.0005,
        fee_currency="USDT",
        fee_rate=0.0005,
        realized_pnl=0.0,
        exec_type="taker",
        side="buy",
        pos_side="long",
        symbol="BTC-USDT-SWAP",
        leverage=10,
        reduce_only=False,
        timestamp=T0,
    )
    body.update(overrides)
    return Fill(**body)


def _fresh_trio():
    return AccountManager(), PaperOrderManager(), PositionManager()


class TestFullCycle:
    def test_accounts_fills_and_fee_rates_round_trip(self, tmp_path: Path) -> None:
        state = PaperState(state_dir=tmp_path)
        accts, orders, positions = _fresh_trio()

        account = accts.create_account(
            "Test", 50_000.0, maker_fee_rate=0.0001, taker_fee_rate=0.0003
        )
        account.total_realized_pnl = Decimal("123.45")
        account.total_fee = Decimal("10.0")
        ledger = [_fill_seq(), _fill_seq(price=68_000.0, side="sell", reduce_only=True)]
        state.save_state(accts, orders, positions, fills=ledger)
        assert (tmp_path / "paper.db").exists()

        accts2, orders2, positions2 = _fresh_trio()
        restored, _meta = state.load_state(accts2, orders2, positions2)

        (saved,) = accts2.list_accounts()
        assert float(saved.total_realized_pnl) == pytest.approx(123.45)
        assert float(saved.total_fee) == pytest.approx(10.0)
        assert float(saved.maker_fee_rate) == pytest.approx(0.0001)
        assert float(saved.taker_fee_rate) == pytest.approx(0.0003)

        assert len(restored) == 2
        assert float(restored[0].fee_rate) == pytest.approx(0.0005)
        assert restored[1].reduce_only is True

    def test_wipe_leaves_an_empty_but_usable_store(self, tmp_path: Path) -> None:
        state = PaperState(state_dir=tmp_path)
        accts, orders, positions = _fresh_trio()
        accts.create_account("Test", 10_000.0)
        state.save_state(accts, orders, positions, fills=[_fill_seq()])
        assert (tmp_path / "paper.db").exists()

        state.clear_state()
        accts2, orders2, positions2 = _fresh_trio()
        fills, _meta = state.load_state(accts2, orders2, positions2)
        assert fills == []
        assert accts2.list_accounts() == []

    def test_never_saved_store_loads_to_empty(self, tmp_path: Path) -> None:
        state = PaperState(state_dir=tmp_path)
        fills, meta = state.load_state(*_fresh_trio())
        assert (fills, meta) == ([], {})

    def test_enriched_fill_columns_persist(self, tmp_path: Path) -> None:
        state = PaperState(state_dir=tmp_path)
        rich = _fill_seq(realized_pnl=500.0, exec_type="maker", fee_rate=0.0002, leverage=20)
        state.save_state(*_fresh_trio(), fills=[rich])

        accts2, orders2, positions2 = _fresh_trio()
        (loaded,), _meta = state.load_state(accts2, orders2, positions2)
        assert float(loaded.realized_pnl) == pytest.approx(500.0)
        assert loaded.exec_type == "maker"
        assert float(loaded.fee_rate) == pytest.approx(0.0002)
        assert loaded.leverage == 20


class TestDecimalFidelity:
    def test_decimal_values_survive_save_and_load_exactly(self, tmp_path: Path) -> None:
        """H19 save→load: Decimal digits must not drift through the JSON boundary."""
        state = PaperState(state_dir=tmp_path)
        accts, orders, positions = _fresh_trio()
        accts.create_account("DecimalRT", 10_000.0)

        probe = Fill(
            id="fill-rt-1",
            order_id="ord-rt-1",
            client_order_id="ord-rt-1",
            price=Decimal("0.1"),
            quantity=Decimal("0.2"),
            fee=Decimal("0.001"),
            fee_currency="USDT",
            fee_rate=Decimal("0.0005"),
            realized_pnl=Decimal("0.3"),
            exec_type="taker",
            side="buy",
            pos_side="long",
            symbol="BTC-USDT-SWAP",
            leverage=10,
            reduce_only=False,
            timestamp=T0,
        )
        state.save_state(accts, orders, positions, fills=[probe])

        accts2, orders2, positions2 = _fresh_trio()
        (back,), _meta = state.load_state(accts2, orders2, positions2)

        for field in ("price", "quantity", "fee", "realized_pnl"):
            value = getattr(back, field)
            assert isinstance(value, Decimal), f"{field} came back as {type(value).__name__}"
        # The canonical float-drift triple: 0.1 + 0.2 == 0.3 only under Decimal.
        assert back.price == Decimal("0.1")
        assert back.quantity == Decimal("0.2")
        assert back.realized_pnl == Decimal("0.3")
        assert back.price + back.quantity == back.realized_pnl

    def test_pre_decimal_float_rows_are_coerced_on_load(self, tmp_path: Path) -> None:
        """H19 load compat: legacy rows holding raw floats still open as Decimals."""
        legacy_row = {
            "id": "fill-legacy-1",
            "order_id": "ord-legacy-1",
            "client_order_id": "ord-legacy-1",
            "price": 67_000.5,
            "quantity": 0.123,
            "fee": 0.05,
            "fee_currency": "USDT",
            "fee_rate": 0.0005,
            "realized_pnl": 12.34,
            "exec_type": "taker",
            "side": "buy",
            "pos_side": "long",
            "symbol": "BTC-USDT-SWAP",
            "leverage": 10,
            "reduce_only": False,
            "timestamp": 1_700_000_000_000,
        }

        state = PaperState(state_dir=tmp_path)
        conn = sqlite3.connect(str(state._db_path))
        try:
            conn.execute("INSERT INTO fills (data) VALUES (?)", (json.dumps(legacy_row),))
            conn.commit()
        finally:
            conn.close()

        accts, orders, positions = _fresh_trio()
        (row,), _meta = state.load_state(accts, orders, positions)

        for field in ("price", "quantity", "realized_pnl"):
            assert isinstance(getattr(row, field), Decimal)
        for field, original in (("price", 67_000.5), ("quantity", 0.123), ("realized_pnl", 12.34)):
            assert abs(float(getattr(row, field)) - original) < 1e-10
