"""Binance wire messages → unified events across seven payload families."""

from __future__ import annotations

import pytest

from selene_space_exchange.exchanges.binance.normalizer import (
    BinanceDepthDelta,
    BinanceNormalizer,
)
from selene_space_exchange.normalizers.symbol import SymbolNormalizer
from selene_space_types.derivatives import FundingRateEvent, LiquidationEvent
from selene_space_types.market import KlineEvent, TickerEvent, TradeEvent

EVENT_TIME = 1_711_000_000_000


def _norm() -> BinanceNormalizer:
    return BinanceNormalizer(SymbolNormalizer())


def _event(kind: str, **extra) -> dict:
    return {"e": kind, "E": EVENT_TIME, "s": "BTCUSDT", **extra}


def _ticker() -> dict:
    return _event(
        "24hrTicker",
        c="67000.00", b="66999.50", a="67000.50",
        v="12345.67", q="827160490.00",
        h="68000.00", l="66000.00", P="2.35",
    )


class TestTickerNormalization:
    def test_basic_fields(self) -> None:
        got = _norm().normalize(_ticker())
        assert isinstance(got, TickerEvent)
        assert (
            got.exchange,
            got.symbol,
            got.timestamp,
            got.last_price,
            got.bid,
            got.ask,
            got.volume_24h,
            got.change_24h_pct,
        ) == ("binance", "BTC/USDT", EVENT_TIME, 67000.0, 66999.5, 67000.5, 12345.67, 2.35)


def _trade(**over) -> dict:
    body = _event("trade", t=123456789, p="67000.00", q="0.50", m=False)
    body.update(over)
    return body


class TestTradeNormalization:
    def test_basic_fields(self) -> None:
        got = _norm().normalize(_trade())
        assert isinstance(got, TradeEvent)
        assert (
            got.exchange,
            got.symbol,
            got.timestamp,
            got.trade_id,
            got.price,
            got.quantity,
            got.side,
        ) == ("binance", "BTC/USDT", EVENT_TIME, "123456789", 67000.0, 0.5, "buy")

    def test_m_true_flags_a_sell(self) -> None:
        """m=True says the buyer was the maker, so the aggressor sold."""
        got = _norm().normalize(_trade(t=999, q="1.0", m=True))
        assert isinstance(got, TradeEvent)
        assert got.side == "sell"


def _kline_message(**body_over) -> dict:
    body = {
        "t": 1710997200000,
        "T": 1711000799999,
        "s": "BTCUSDT",
        "i": "1h",
        "o": "66800.00",
        "h": "67200.00",
        "l": "66700.00",
        "c": "67000.00",
        "v": "1234.56",
        "x": True,
    }
    body.update(body_over)
    return _event("kline", k=body)


class TestKlineNormalization:
    def test_basic_fields(self) -> None:
        got = _norm().normalize(_kline_message())
        assert isinstance(got, KlineEvent)
        checks = {
            "exchange": got.exchange == "binance",
            "symbol": got.symbol == "BTC/USDT",
            "timestamp": got.timestamp == 1710997200000,
            "interval": got.interval == "1h",
            "ohlc": (got.open, got.high, got.low, got.close) == (66800.0, 67200.0, 66700.0, 67000.0),
            "volume": got.volume == 1234.56,
            "closed": got.closed is True,
        }
        assert all(checks.values()), f"failing: {[name for name, ok in checks.items() if not ok]}"

    def test_timestamp_is_candle_open_time_never_event_time(self) -> None:
        """The buffer merge updates bars in place by timestamp equality; using E
        would fork a fresh bar on every WS tick."""
        live = _kline_message(
            o="67000.00", h="67100.00", l="66900.00", c="67050.00", v="500.00", x=False
        )
        live["E"] = 1711000099999
        got = _norm().normalize(live)
        assert isinstance(got, KlineEvent)
        assert got.timestamp == 1710997200000
        assert got.timestamp != 1711000099999


def _depth(**over) -> dict:
    body = _event(
        "depthUpdate",
        U=100001,
        u=100002,
        b=[["66999.00", "2.50"], ["66998.00", "1.00"]],
        a=[["67001.00", "1.50"], ["67002.00", "3.00"]],
    )
    body.update(over)
    return body


class TestDepthNormalization:
    def test_spot_frame_has_no_previous_id(self) -> None:
        got = _norm().normalize(_depth())
        assert isinstance(got, BinanceDepthDelta)
        assert (got.first_update_id, got.last_update_id) == (100001, 100002)
        assert got.previous_update_id is None  # spot frames carry no pu
        assert (got.delta.exchange, got.delta.symbol, got.delta.sequence_id) == (
            "binance",
            "BTC/USDT",
            100002,
        )
        assert (len(got.delta.bids), len(got.delta.asks)) == (2, 2)
        assert (got.delta.bids[0].price, got.delta.bids[0].quantity) == (66999.0, 2.5)

    def test_futures_frame_links_via_pu(self) -> None:
        got = _norm().normalize(
            _depth(
                T=EVENT_TIME,
                U=200001,
                u=200005,
                pu=200000,
                b=[["66999.00", "2.50"]],
                a=[["67001.00", "1.50"]],
            )
        )
        assert isinstance(got, BinanceDepthDelta)
        assert (got.first_update_id, got.last_update_id, got.previous_update_id) == (200001, 200005, 200000)

    def test_empty_sides_produce_empty_books(self) -> None:
        got = _norm().normalize(_depth(U=1, u=2, b=[], a=[]))
        assert isinstance(got, BinanceDepthDelta)
        assert (len(got.delta.bids), len(got.delta.asks)) == (0, 0)


def _agg_trade(**over) -> dict:
    body = _event(
        "aggTrade",
        a=164053398,
        p="68000.00",
        q="2.500",
        f=362090727,
        l=362090730,
        T=EVENT_TIME,
        m=False,
    )
    body.update(over)
    return body


class TestAggTradeNormalization:
    def test_basic_fields(self) -> None:
        got = _norm().normalize(_agg_trade())
        assert isinstance(got, TradeEvent)
        assert (
            got.exchange,
            got.symbol,
            got.trade_id,
            got.price,
            got.quantity,
            got.side,
        ) == ("binance", "BTC/USDT", "164053398", 68000.0, 2.5, "buy")

    def test_seller_side(self) -> None:
        eth_print = _event(
            "aggTrade", s="ETHUSDT", a=999, p="3500.00", q="10.0", f=1, l=1, T=EVENT_TIME, m=True
        )
        got = _norm().normalize(eth_print)
        assert isinstance(got, TradeEvent)
        assert got.side == "sell"


def _force_order(order: dict) -> dict:
    return {"e": "forceOrder", "E": EVENT_TIME, "o": order}


LIQUIDATED_LONG = {
    "s": "BTCUSDT",
    "S": "SELL",
    "o": "LIMIT",
    "f": "IOC",
    "q": "0.500",
    "p": "67000.00",
    "ap": "66950.00",
    "X": "FILLED",
    "l": "0.500",
    "z": "0.500",
    "T": EVENT_TIME,
}


class TestLiquidationNormalization:
    def test_sell_print_means_long_position_blown_up(self) -> None:
        got = _norm().normalize(_force_order(LIQUIDATED_LONG))
        assert isinstance(got, LiquidationEvent)
        checks = {
            "exchange": got.exchange == "binance",
            "symbol": got.symbol == "BTC/USDT",
            "side": got.side == "long",
            "quantity": got.quantity == 0.5,
            "price": got.price == 67000.0,
            "avg": got.avg_price == 66950.0,
            "notional": got.notional_usd == pytest.approx(0.5 * 66950.0),
            "status": got.status == "FILLED",
        }
        assert all(checks.values()), f"failing: {[name for name, ok in checks.items() if not ok]}"

    def test_buy_print_means_short_position_blown_up(self) -> None:
        bought_back = {
            **LIQUIDATED_LONG,
            "s": "ETHUSDT",
            "S": "BUY",
            "q": "10.0",
            "p": "3500.00",
            "ap": "3510.00",
            "l": "10.0",
            "z": "10.0",
        }
        got = _norm().normalize(_force_order(bought_back))
        assert isinstance(got, LiquidationEvent)
        assert got.side == "short"
        assert got.notional_usd == pytest.approx(10.0 * 3510.0)


def _mark_price() -> dict:
    return _event(
        "markPriceUpdate",
        p="67500.12345678",
        i="67490.00000000",
        P="67495.00000000",
        r="0.00010000",
        T=1711036800000,
    )


class TestFundingRateNormalization:
    def test_basic_fields(self) -> None:
        got = _norm().normalize(_mark_price())
        assert isinstance(got, FundingRateEvent)
        assert (got.exchange, got.symbol) == ("binance", "BTC/USDT")
        assert got.funding_rate == pytest.approx(0.0001)
        assert got.mark_price == pytest.approx(67500.12345678)
        assert got.index_price == pytest.approx(67490.0)
        assert got.next_funding_time == 1711036800000


class TestUnknownEvent:
    def test_unrecognized_type_maps_to_none(self) -> None:
        assert _norm().normalize({"e": "unknownEvent", "s": "BTCUSDT"}) is None

    def test_absent_event_field_maps_to_none(self) -> None:
        assert _norm().normalize({"data": "something"}) is None
