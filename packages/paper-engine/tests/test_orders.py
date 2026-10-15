"""Order state-machine gates and PaperOrderManager bookkeeping."""

from __future__ import annotations

import pytest

from selene_space_paper.orders import InvalidOrderTransition, PaperOrderManager, can_transition
from selene_space_types.trading import OrderSide, OrderStatus, OrderType

OWNER = "acc-1"
PX = 67_000.0


def _open(
    mgr: PaperOrderManager,
    *,
    symbol: str = "BTC/USDT",
    side: OrderSide = OrderSide.BUY,
    qty: float = 0.1,
    order_type: OrderType = OrderType.MARKET,
    price: float | None = None,
):
    return mgr.place_order(OWNER, symbol=symbol, side=side, order_type=order_type, quantity=qty, price=price)


class TestTransitionMatrix:
    def test_legal_progressions(self) -> None:
        allowed = [
            (OrderStatus.CREATED, OrderStatus.ACCEPTED),
            (OrderStatus.CREATED, OrderStatus.REJECTED),
            (OrderStatus.ACCEPTED, OrderStatus.PARTIAL),
            (OrderStatus.ACCEPTED, OrderStatus.FILLED),
            (OrderStatus.ACCEPTED, OrderStatus.CANCELLED),
            (OrderStatus.PARTIAL, OrderStatus.FILLED),
            (OrderStatus.PARTIAL, OrderStatus.CANCELLED),
        ]
        assert all(can_transition(a, b) is True for a, b in allowed)

    def test_illegal_progressions(self) -> None:
        forbidden = [
            # Terminal states are absorbing.
            (OrderStatus.FILLED, OrderStatus.CREATED),
            (OrderStatus.FILLED, OrderStatus.CANCELLED),
            (OrderStatus.CANCELLED, OrderStatus.ACCEPTED),
            (OrderStatus.REJECTED, OrderStatus.ACCEPTED),
            # Filling cannot be skipped.
            (OrderStatus.CREATED, OrderStatus.FILLED),
            (OrderStatus.CREATED, OrderStatus.PARTIAL),
        ]
        assert all(can_transition(a, b) is False for a, b in forbidden)


class TestLifecycle:
    def _mgr(self) -> PaperOrderManager:
        return PaperOrderManager()

    def test_market_order_lands_accepted(self) -> None:
        order = _open(self._mgr())
        assert (order.status, float(order.quantity)) == (
            OrderStatus.ACCEPTED,
            pytest.approx(0.1),
        )

    def test_cancel_moves_to_cancelled(self) -> None:
        mgr = self._mgr()
        cancelled = mgr.cancel_order(_open(mgr).id)
        assert cancelled.status == OrderStatus.CANCELLED

    def test_cancelling_a_filled_order_is_rejected(self) -> None:
        mgr = self._mgr()
        order = _open(mgr)
        mgr.update_fill(order.id, 0.1, PX)
        assert order.status == OrderStatus.FILLED
        with pytest.raises(InvalidOrderTransition):
            mgr.cancel_order(order.id)

    def test_unknown_order_id_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            self._mgr().cancel_order("nonexistent")


class TestFillAccounting:
    def test_partial_fill_marks_partial_status(self) -> None:
        mgr = PaperOrderManager()
        order = _open(mgr, qty=1.0)
        mgr.update_fill(order.id, 0.5, PX)
        assert order.status == OrderStatus.PARTIAL
        assert float(order.filled_quantity) == pytest.approx(0.5)

    def test_final_fill_completes_the_order(self) -> None:
        mgr = PaperOrderManager()
        order = _open(mgr, qty=0.5)
        mgr.update_fill(order.id, 0.5, PX)
        assert order.status == OrderStatus.FILLED

    def test_two_fills_average_weighted_price(self) -> None:
        mgr = PaperOrderManager()
        order = _open(mgr, qty=1.0)
        mgr.update_fill(order.id, 0.5, 66_000.0)
        mgr.update_fill(order.id, 0.5, 68_000.0)
        assert order.status == OrderStatus.FILLED
        assert float(order.avg_fill_price) == pytest.approx(67_000.0)


class TestQueriesAndPersistence:
    def test_status_filter_separates_live_from_done(self) -> None:
        mgr = PaperOrderManager()
        keep = _open(mgr)
        done = _open(mgr, symbol="ETH/USDT", side=OrderSide.SELL, qty=1.0)
        mgr.cancel_order(done.id)
        live = mgr.get_orders(OWNER, status=OrderStatus.ACCEPTED)
        assert [o.id for o in live] == [keep.id]

    def test_open_orders_lists_unsettled_only(self) -> None:
        mgr = PaperOrderManager()
        _open(mgr)
        assert len(mgr.get_open_orders(OWNER)) == 1

    def test_snapshot_roundtrip_restores_book(self) -> None:
        src = PaperOrderManager()
        _open(src, qty=0.5, order_type=OrderType.LIMIT, price=65_000.0)
        dst = PaperOrderManager()
        dst.load_data(src.get_all_data())
        assert len(dst.get_orders(OWNER)) == 1
