"""Portfolio ledger: cash movements, position tracking, equity snapshots."""

import pytest

from selene_space_backtest.portfolio import Portfolio
from selene_space_types.trading import Fill, OrderSide


def _receipt(
    price: float = 100.0,
    quantity: float = 1.0,
    fee: float = 0.0,
    symbol: str = "BTC/USDT",
) -> Fill:
    return Fill(
        id="fill-001",
        order_id="ord-001",
        price=price,
        quantity=quantity,
        fee=fee,
        symbol=symbol,
        timestamp=1711000000000,
    )


class TestFreshLedger:
    def test_opens_with_cash_and_nothing_else(self) -> None:
        book = Portfolio(initial_cash=10_000.0)
        assert book.cash == 10_000.0
        assert book.positions == {}
        assert book.get_equity_curve() == []


class TestCashAndPositions:
    def test_buy_drains_cash_by_cost_plus_fee(self) -> None:
        book = Portfolio(initial_cash=10_000.0)
        book.apply_fill(_receipt(price=100.0, quantity=2.0, fee=1.0), OrderSide.BUY)

        assert book.cash == pytest.approx(10_000.0 - 200.0 - 1.0)
        assert book.get_position_quantity("BTC/USDT") == pytest.approx(2.0)

    def test_sell_refills_cash_net_of_fee(self) -> None:
        book = Portfolio(initial_cash=10_000.0)
        book.apply_fill(_receipt(price=100.0, quantity=2.0), OrderSide.BUY)
        book.apply_fill(
            _receipt(price=110.0, quantity=2.0, fee=0.5), OrderSide.SELL
        )

        assert book.cash == pytest.approx(10_000.0 - 200.0 + 220.0 - 0.5)
        assert book.get_position_quantity("BTC/USDT") == pytest.approx(0.0)


class TestEquitySnapshots:
    def test_curve_tracks_marked_equity_per_update(self) -> None:
        book = Portfolio(initial_cash=10_000.0)
        book.apply_fill(_receipt(price=100.0, quantity=1.0), OrderSide.BUY)

        for mark in (100.0, 110.0, 105.0):
            book.update_equity("BTC/USDT", mark)

        curve = book.get_equity_curve()
        assert len(curve) == 3
        assert curve[0] == pytest.approx(9900.0 + 100.0)
        assert curve[1] == pytest.approx(9900.0 + 110.0)
        assert curve[2] == pytest.approx(9900.0 + 105.0)


class TestLifecycle:
    def test_reset_restores_starting_state(self) -> None:
        book = Portfolio(initial_cash=5_000.0)
        book.apply_fill(_receipt(), OrderSide.BUY)
        book.update_equity("BTC/USDT", 100.0)

        book.reset()

        assert book.cash == 5_000.0
        assert book.positions == {}
        assert book.get_equity_curve() == []


class TestSymbolIsolation:
    def test_each_symbol_keeps_its_own_position(self) -> None:
        book = Portfolio(initial_cash=20_000.0)
        book.apply_fill(
            _receipt(price=100.0, quantity=2.0, symbol="BTC/USDT"), OrderSide.BUY
        )
        book.apply_fill(
            _receipt(price=50.0, quantity=3.0, symbol="ETH/USDT"), OrderSide.BUY
        )

        assert book.get_position_quantity("BTC/USDT") == pytest.approx(2.0)
        assert book.get_position_quantity("ETH/USDT") == pytest.approx(3.0)
        assert book.cash == pytest.approx(20_000.0 - 200.0 - 150.0)

    def test_position_key_comes_from_the_fill(self) -> None:
        book = Portfolio(initial_cash=10_000.0)
        book.apply_fill(
            _receipt(price=100.0, quantity=1.0, symbol="SOL/USDT"), OrderSide.BUY
        )

        assert book.get_position_quantity("SOL/USDT") == pytest.approx(1.0)
        assert book.get_position_quantity("BTC/USDT") == 0.0
