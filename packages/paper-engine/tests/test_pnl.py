"""PnL math over USDT-notional positions, single and portfolio level."""

from __future__ import annotations

import pytest

from selene_space_paper.pnl import calculate_account_pnl, calculate_pnl
from selene_space_types.trading import OrderSide, Position

PX = 67_000.0


def _position(
    *,
    side: OrderSide = OrderSide.BUY,
    entry: float = PX,
    usdt: float = PX,
    banked: float = 0.0,
) -> Position:
    base = usdt / entry if entry > 0 else 0
    return Position(
        symbol="BTC-USDT-SWAP",
        side=side,
        quantity=usdt,
        quantity_base=base,
        avg_entry_price=entry,
        leverage=10,
        margin=usdt / 10,
        unrealized_pnl=0.0,
        realized_pnl=banked,
        opened_at=1_742_630_400_000,
        updated_at=1_742_630_400_000,
    )


class TestSinglePositionPnl:
    @pytest.mark.parametrize(
        ("side", "mark", "want"),
        [
            (OrderSide.BUY, 68_000.0, 1_000.0),
            (OrderSide.BUY, 66_000.0, -1_000.0),
            (OrderSide.SELL, 66_000.0, 1_000.0),
            (OrderSide.SELL, 68_000.0, -1_000.0),
        ],
        ids=["long-gain", "long-loss", "short-gain", "short-loss"],
    )
    def test_directional_unrealized(self, side: OrderSide, mark: float, want: float) -> None:
        rec = calculate_pnl(_position(side=side), mark)
        assert rec.unrealized_pnl == pytest.approx(want)

    def test_flat_position_has_no_open_leg(self) -> None:
        rec = calculate_pnl(_position(side=OrderSide.BUY), 68_000.0)
        assert (rec.realized_pnl, rec.total_pnl) == (0.0, pytest.approx(1_000.0))

    def test_banked_realized_adds_into_total(self) -> None:
        rec = calculate_pnl(
            _position(usdt=33_500.0, banked=500.0),
            68_000.0,
        )
        assert (rec.realized_pnl, rec.unrealized_pnl, rec.total_pnl) == (
            500.0,
            pytest.approx(500.0),
            pytest.approx(1_000.0),
        )

    def test_fees_subtract_from_total(self) -> None:
        rec = calculate_pnl(_position(), 68_000.0, total_fees=50.0)
        assert rec.fees == 50.0
        assert rec.total_pnl == pytest.approx(950.0)

    def test_zero_size_carries_only_banked_pnl(self) -> None:
        rec = calculate_pnl(_position(usdt=0.0, banked=2_000.0), 68_000.0)
        assert (rec.unrealized_pnl, rec.realized_pnl, rec.total_pnl) == (
            0.0,
            2_000.0,
            pytest.approx(2_000.0),
        )


class TestPortfolioPnl:
    def test_each_symbol_marked_against_its_own_price(self) -> None:
        book = [
            _position(entry=67_000.0, usdt=67_000.0),
            Position(
                symbol="ETH-USDT-SWAP",
                side=OrderSide.BUY,
                quantity=30_000.0,
                quantity_base=10.0,
                avg_entry_price=3_000.0,
                leverage=10,
                margin=3_000.0,
                unrealized_pnl=0.0,
                realized_pnl=0.0,
                opened_at=1_742_630_400_000,
                updated_at=1_742_630_400_000,
            ),
        ]
        marks = {"BTC-USDT-SWAP": 68_000.0, "ETH-USDT-SWAP": 3_100.0}
        records = calculate_account_pnl(book, marks)
        assert len(records) == 2
        assert [r.unrealized_pnl for r in records] == [
            pytest.approx(1_000.0),
            pytest.approx(1_000.0),
        ]

    def test_fee_map_keyed_by_symbol(self) -> None:
        book = [_position()]
        marks = {"BTC-USDT-SWAP": 68_000.0}
        fees = {"BTC-USDT-SWAP": 67.0}
        (record,) = calculate_account_pnl(book, marks, fees)
        assert record.fees == 67.0
