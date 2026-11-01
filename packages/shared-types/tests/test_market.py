"""Wire-format locks for selene_space_types.market — every event model must
survive a JSON dump/validate cycle unchanged and carry the standard header."""

import pytest
from selene_space_types.market import (
    KlineEvent,
    OrderBookL2Delta,
    OrderBookL2Snapshot,
    PriceLevel,
    TickerEvent,
    TradeEvent,
)

HEADER_FIELDS = {"exchange", "symbol", "timestamp"}


def _recycle(model_cls, instance):
    """Serialize to JSON then rebuild — the model must come back identical."""
    return model_cls.model_validate_json(instance.model_dump_json())


@pytest.mark.parametrize(
    "model_cls",
    [TickerEvent, TradeEvent, KlineEvent, OrderBookL2Snapshot, OrderBookL2Delta],
)
def test_event_models_expose_standard_header(model_cls):
    assert HEADER_FIELDS <= set(model_cls.model_fields.keys())


def test_price_level_survives_json_cycle():
    lvl = PriceLevel(price=43120.75, quantity=0.037)
    assert _recycle(PriceLevel, lvl) == lvl


def test_ticker_event_survives_json_cycle():
    tick = TickerEvent(
        exchange="okx",
        symbol="ETH/USDT",
        timestamp=1720001111222,
        last_price=3122.5,
        bid=3122.0,
        ask=3123.0,
        volume_24h=98765.43,
        change_24h_pct=-1.72,
    )
    assert _recycle(TickerEvent, tick) == tick


def test_trade_event_survives_json_cycle():
    fill = TradeEvent(
        exchange="okx",
        symbol="SOL/USDT",
        timestamp=1720001111333,
        trade_id="555000111",
        price=148.35,
        quantity=12.5,
        side="sell",
    )
    assert _recycle(TradeEvent, fill) == fill


def test_kline_event_survives_json_cycle():
    bar = KlineEvent(
        exchange="binance",
        symbol="SOL/USDT",
        timestamp=1720001111444,
        interval="15m",
        open=147.9,
        high=149.6,
        low=147.5,
        close=148.35,
        volume=5432.1,
        closed=True,
    )
    assert _recycle(KlineEvent, bar) == bar


def test_l2_snapshot_keeps_side_depth_after_json_cycle():
    book = OrderBookL2Snapshot(
        exchange="binance",
        symbol="ETH/USDT",
        timestamp=1720001111555,
        sequence_id=770011,
        bids=[PriceLevel(price=3121.5, quantity=4.2)],
        asks=[PriceLevel(price=3123.5, quantity=2.8)],
    )
    rebuilt = _recycle(OrderBookL2Snapshot, book)
    assert rebuilt == book
    assert len(rebuilt.bids) == 1
    assert len(rebuilt.asks) == 1


def test_snapshot_defaults_to_empty_book():
    bare = OrderBookL2Snapshot(
        exchange="binance",
        symbol="ETH/USDT",
        timestamp=1720001111555,
        sequence_id=2,
    )
    assert bare.bids == [] and bare.asks == []


def test_l2_delta_survives_json_cycle():
    patch = OrderBookL2Delta(
        exchange="okx",
        symbol="ETH/USDT",
        timestamp=1720001111666,
        sequence_id=770012,
        bids=[PriceLevel(price=3121.0, quantity=1.1)],
        asks=[PriceLevel(price=3124.0, quantity=0.0)],
    )
    assert _recycle(OrderBookL2Delta, patch) == patch


def test_zero_quantity_ask_level_means_removal():
    """quantity=0 means remove the level."""
    patch = OrderBookL2Delta(
        exchange="okx",
        symbol="ETH/USDT",
        timestamp=1720001111666,
        sequence_id=770013,
        asks=[PriceLevel(price=3124.0, quantity=0.0)],
    )
    assert patch.asks[0].quantity == 0.0
