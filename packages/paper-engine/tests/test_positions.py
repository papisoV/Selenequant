"""PositionManager lifecycle coverage: open, scale, trim, flip-to-flat, MTM."""

from __future__ import annotations

import itertools

import pytest

from selene_space_paper.positions import PositionManager
from selene_space_types.trading import Fill, MarginMode, OrderSide, PositionSide

BOOK = "acc-1"
SYMBOL = "BTC-USDT-SWAP"
ALT_SYMBOL = "ETH-USDT-SWAP"
LEV = 10
ENTRY = 67_000.0

_serial = itertools.count(1)


def _fill(px: float, usdt: float) -> Fill:
    """Fill whose ``quantity`` carries the USDT notional, not base size."""
    return Fill(
        id=f"fx-{next(_serial)}",
        order_id=f"ord-{next(_serial)}",
        price=px,
        quantity=usdt,
        fee=0.0,
        fee_currency="USDT",
        timestamp=1_742_630_400_000,
    )


def _trade(
    mgr: PositionManager,
    px: float,
    usdt: float,
    order_side: OrderSide,
    *,
    pos_side: PositionSide,
    mode: MarginMode = MarginMode.CROSS,
    wallet: float | None = None,
):
    return mgr.apply_fill_with_symbol(
        BOOK,
        SYMBOL,
        _fill(px, usdt),
        order_side,
        leverage=LEV,
        margin_mode=mode,
        pos_side=pos_side,
        available_balance=wallet,
    )


class TestLongLifecycle:
    def test_open_builds_full_position_record(self) -> None:
        pos, realized = _trade(PositionManager(), ENTRY, ENTRY, OrderSide.BUY, pos_side=PositionSide.LONG)
        got = (
            pos.side,
            pos.quantity,
            pos.quantity_base,
            pos.avg_entry_price,
            pos.leverage,
            pos.margin,
            realized,
        )
        want = (
            OrderSide.BUY,
            67_000.0,
            pytest.approx(1.0),
            67_000.0,
            LEV,
            pytest.approx(6_700.0),
            0.0,
        )
        assert got == want

    def test_scale_in_averages_entry(self) -> None:
        mgr = PositionManager()
        _trade(mgr, 66_000.0, 66_000.0, OrderSide.BUY, pos_side=PositionSide.LONG)
        pos, realized = _trade(mgr, 68_000.0, 68_000.0, OrderSide.BUY, pos_side=PositionSide.LONG)
        assert (pos.quantity, pos.quantity_base, pos.avg_entry_price, realized) == (
            pytest.approx(134_000.0),
            pytest.approx(2.0),
            pytest.approx(67_000.0),
            0.0,
        )

    def test_trim_returns_partial_realized(self) -> None:
        mgr = PositionManager()
        _trade(mgr, ENTRY, ENTRY, OrderSide.BUY, pos_side=PositionSide.LONG)
        pos, realized = _trade(mgr, 68_000.0, 33_500.0, OrderSide.SELL, pos_side=PositionSide.LONG)
        assert pos.quantity == pytest.approx(33_500.0)
        assert realized == pytest.approx(500.0)

    @pytest.mark.parametrize(
        ("open_side", "close_side", "open_px", "close_px", "notional", "want"),
        [
            (OrderSide.BUY, OrderSide.SELL, 66_000.0, 68_000.0, 66_000.0, 2_000.0),
            (OrderSide.BUY, OrderSide.SELL, 68_000.0, 66_000.0, 68_000.0, -2_000.0),
            (OrderSide.SELL, OrderSide.BUY, 68_000.0, 66_000.0, 68_000.0, 2_000.0),
            (OrderSide.SELL, OrderSide.BUY, 67_000.0, 68_000.0, 67_000.0, -1_000.0),
        ],
        ids=["long-win", "long-lose", "short-win", "short-lose"],
    )
    def test_round_trip_realized(
        self,
        open_side: OrderSide,
        close_side: OrderSide,
        open_px: float,
        close_px: float,
        notional: float,
        want: float,
    ) -> None:
        mgr = PositionManager()
        pos_side = PositionSide.LONG if open_side is OrderSide.BUY else PositionSide.SHORT
        _trade(mgr, open_px, notional, open_side, pos_side=pos_side)
        _, realized = _trade(mgr, close_px, notional, close_side, pos_side=pos_side)
        assert realized == pytest.approx(want)

    def test_full_close_flattens_book(self) -> None:
        mgr = PositionManager()
        _trade(mgr, 66_000.0, 66_000.0, OrderSide.BUY, pos_side=PositionSide.LONG)
        pos, realized = _trade(mgr, 68_000.0, 66_000.0, OrderSide.SELL, pos_side=PositionSide.LONG)
        assert pos.quantity == pytest.approx(0.0, abs=1)
        assert realized == pytest.approx(2_000.0)


class TestShortLifecycle:
    def test_open_registers_sell_side(self) -> None:
        mgr = PositionManager()
        _trade(mgr, ENTRY, ENTRY, OrderSide.SELL, pos_side=PositionSide.SHORT)
        held = mgr.get_position(BOOK, SYMBOL)
        assert held is not None
        assert held.side is OrderSide.SELL


class TestLiquidationEstimates:
    @pytest.mark.parametrize(
        ("mode", "wallet"),
        [
            (MarginMode.ISOLATED, None),
            (MarginMode.CROSS, None),
            (MarginMode.CROSS, 100_000.0),
        ],
        ids=["isolated", "cross-bare", "cross-with-wallet"],
    )
    def test_estimate_present_and_below_entry(self, mode: MarginMode, wallet: float | None) -> None:
        pos, _ = _trade(
            PositionManager(), ENTRY, ENTRY, OrderSide.BUY,
            pos_side=PositionSide.LONG, mode=mode, wallet=wallet,
        )
        assert pos.liquidation_price is not None
        assert pos.liquidation_price < ENTRY

    def test_adding_size_moves_the_estimate(self) -> None:
        mgr = PositionManager()
        first, _ = _trade(
            mgr, ENTRY, ENTRY, OrderSide.BUY,
            pos_side=PositionSide.LONG, mode=MarginMode.ISOLATED,
        )
        # Snapshot the estimate: adds mutate the live record in place.
        liq_before = first.liquidation_price
        second, _ = _trade(
            mgr, 68_000.0, 68_000.0, OrderSide.BUY,
            pos_side=PositionSide.LONG, mode=MarginMode.ISOLATED,
        )
        assert liq_before is not None
        assert second.liquidation_price is not None
        assert liq_before != second.liquidation_price


class TestMarkToMarket:
    @pytest.mark.parametrize(
        ("side", "mark", "want"),
        [
            (OrderSide.BUY, 68_000.0, 1_000.0),
            (OrderSide.SELL, 66_000.0, 1_000.0),
        ],
        ids=["long", "short"],
    )
    def test_unrealized_follows_mark(self, side: OrderSide, mark: float, want: float) -> None:
        mgr = PositionManager()
        pos_side = PositionSide.LONG if side is OrderSide.BUY else PositionSide.SHORT
        _trade(mgr, ENTRY, ENTRY, side, pos_side=pos_side)
        refreshed = mgr.update_unrealized_pnl(BOOK, SYMBOL, mark)
        assert len(refreshed) == 1
        assert refreshed[0].unrealized_pnl == pytest.approx(want)
        if side is OrderSide.BUY:
            assert refreshed[0].current_price == mark


class TestOpenBook:
    def test_flat_symbols_drop_out_of_listing(self) -> None:
        mgr = PositionManager()
        _trade(mgr, ENTRY, ENTRY, OrderSide.BUY, pos_side=PositionSide.LONG)
        eth_fill = _fill(3_000.0, 30_000.0)
        mgr.apply_fill_with_symbol(
            BOOK, ALT_SYMBOL, eth_fill,
            OrderSide.BUY, leverage=LEV, pos_side=PositionSide.LONG,
        )
        _trade(mgr, 68_000.0, ENTRY, OrderSide.SELL, pos_side=PositionSide.LONG)
        remaining = mgr.get_open_positions(BOOK)
        assert len(remaining) == 1
        assert remaining[0].symbol == ALT_SYMBOL


class TestSnapshotRoundtrip:
    def test_export_import_preserves_size_and_leverage(self) -> None:
        src = PositionManager()
        _trade(src, ENTRY, ENTRY, OrderSide.BUY, pos_side=PositionSide.LONG)

        dst = PositionManager()
        dst.load_data(src.get_all_data())
        held = dst.get_position(BOOK, SYMBOL)
        assert held is not None
        assert held.quantity == pytest.approx(67_000.0)
        assert held.leverage == LEV
