"""Portfolio applied-fill accounting and equity marking (P5 coverage)."""

import pytest

from selene_space_backtest.portfolio import Portfolio
from selene_space_types.trading import Fill, OrderSide


def _fill(
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


def test_brand_new_book_is_cash_only():
    book = Portfolio(initial_cash=10_000.0)
    assert book.cash == 10_000.0
    assert book.positions == {}
    assert book.get_equity_curve() == []


def test_purchase_reduces_balance_by_notional_and_fee():
    book = Portfolio(initial_cash=10_000.0)

    book.apply_fill(_fill(price=100.0, quantity=2.0, fee=1.0), OrderSide.BUY)

    assert book.cash == pytest.approx(10_000.0 - 200.0 - 1.0)
    assert book.get_position_quantity("BTC/USDT") == pytest.approx(2.0)


def test_liquidation_credits_proceeds_minus_fee_and_flattens_position():
    book = Portfolio(initial_cash=10_000.0)
    book.apply_fill(_fill(price=100.0, quantity=2.0), OrderSide.BUY)
    book.apply_fill(_fill(price=110.0, quantity=2.0, fee=0.5), OrderSide.SELL)

    assert book.cash == pytest.approx(10_000.0 - 200.0 + 220.0 - 0.5)
    assert book.get_position_quantity("BTC/USDT") == pytest.approx(0.0)


def test_mark_to_market_appends_one_snapshot_per_call():
    book = Portfolio(initial_cash=10_000.0)
    book.apply_fill(_fill(), OrderSide.BUY)

    marks = [100.0, 110.0, 105.0]
    for px in marks:
        book.update_equity("BTC/USDT", px)

    curve = book.get_equity_curve()
    assert len(curve) == 3
    # cash 9900 plus the marked position each time
    assert curve[0] == pytest.approx(10000.0)
    assert curve[1] == pytest.approx(10010.0)
    assert curve[2] == pytest.approx(10005.0)


def test_reset_wipes_trading_history_back_to_seed_capital():
    book = Portfolio(initial_cash=5_000.0)
    book.apply_fill(_fill(), OrderSide.BUY)
    book.update_equity("BTC/USDT", 100.0)

    book.reset()

    assert book.cash == 5_000.0
    assert book.positions == {}
    assert book.get_equity_curve() == []


def test_positions_bucketed_per_symbol():
    """P5: separate symbols must not share a position bucket."""
    book = Portfolio(initial_cash=20_000.0)
    book.apply_fill(_fill(price=100.0, quantity=2.0, symbol="BTC/USDT"), OrderSide.BUY)
    book.apply_fill(_fill(price=50.0, quantity=3.0, symbol="ETH/USDT"), OrderSide.BUY)

    assert book.get_position_quantity("BTC/USDT") == pytest.approx(2.0)
    assert book.get_position_quantity("ETH/USDT") == pytest.approx(3.0)
    assert book.cash == pytest.approx(20_000.0 - 200.0 - 150.0)


def test_fill_symbol_is_the_bucket_key_never_a_default():
    """P5: the fill's own symbol decides where the position lands."""
    book = Portfolio(initial_cash=10_000.0)
    book.apply_fill(_fill(symbol="SOL/USDT"), OrderSide.BUY)

    assert book.get_position_quantity("SOL/USDT") == pytest.approx(1.0)
    assert book.get_position_quantity("BTC/USDT") == 0.0
