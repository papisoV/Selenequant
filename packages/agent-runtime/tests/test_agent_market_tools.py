"""Behaviour locks for market tools: ticker, kline, and L2 orderbook lookup."""

from __future__ import annotations

from dataclasses import dataclass, field

from selene_space_agent.tools.market_tools import (
    MarketKlineTool,
    MarketOrderbookTool,
    MarketTickerTool,
)
from selene_space_types.market import (
    KlineEvent,
    OrderBookL2Snapshot,
    PriceLevel,
    TickerEvent,
)


@dataclass
class _StubMarketData:
    """MarketDataService stand-in with canned lookups keyed by symbol."""

    tickers: dict[str, TickerEvent] = field(default_factory=dict)
    klines: dict[str, KlineEvent] = field(default_factory=dict)
    orderbooks: dict[str, OrderBookL2Snapshot] = field(default_factory=dict)

    def get_ticker(self, symbol: str) -> TickerEvent | None:
        return self.tickers.get(symbol)

    def get_kline(self, symbol: str) -> KlineEvent | None:
        return self.klines.get(symbol)

    def get_orderbook(self, symbol: str) -> OrderBookL2Snapshot | None:
        return self.orderbooks.get(symbol)


_TS = 1_710_000_000_000
_PAIR = "SOL/USDT"

_TICKER = TickerEvent(
    exchange="okx",
    symbol=_PAIR,
    timestamp=_TS,
    last_price=148.20,
    bid=148.15,
    ask=148.25,
    volume_24h=987_654.32,
    change_24h_pct=-1.75,
)

_KLINE = KlineEvent(
    exchange="okx",
    symbol=_PAIR,
    timestamp=_TS,
    interval="4h",
    open=147.0,
    high=149.5,
    low=146.5,
    close=148.2,
    volume=12_500.0,
    closed=True,
)

_ORDERBOOK = OrderBookL2Snapshot(
    exchange="okx",
    symbol=_PAIR,
    timestamp=_TS,
    sequence_id=77,
    bids=[
        PriceLevel(price=148.0, quantity=4.0),
        PriceLevel(price=147.9, quantity=5.5),
        PriceLevel(price=147.8, quantity=6.0),
    ],
    asks=[
        PriceLevel(price=148.3, quantity=3.0),
        PriceLevel(price=148.4, quantity=2.5),
        PriceLevel(price=148.5, quantity=1.5),
    ],
)


def _service(*, ticker: bool = True, kline: bool = True, orderbook: bool = True) -> _StubMarketData:
    return _StubMarketData(
        tickers={_PAIR: _TICKER} if ticker else {},
        klines={_PAIR: _KLINE} if kline else {},
        orderbooks={_PAIR: _ORDERBOOK} if orderbook else {},
    )


class TestTickerTool:
    def test_hit_formats_price_and_change(self) -> None:
        out = MarketTickerTool(_service()).execute({"symbol": _PAIR})
        checks = (out.error is None, _PAIR in out.output, "148.20" in out.output, "-1.75%" in out.output)
        assert all(checks)

    def test_miss_reports_unavailability(self) -> None:
        out = MarketTickerTool(_service(ticker=False)).execute({"symbol": _PAIR})
        assert "No ticker data available" in out.output

    def test_symbol_arg_required(self) -> None:
        out = MarketTickerTool(_service()).execute({})
        checks = (out.error is not None, "symbol" in out.error)
        assert all(checks)


class TestKlineTool:
    def test_hit_reports_closed_candle(self) -> None:
        out = MarketKlineTool(_service()).execute({"symbol": _PAIR})
        checks = (out.error is None, _PAIR in out.output, "closed" in out.output, "148.20" in out.output)
        assert all(checks)

    def test_miss_reports_unavailability(self) -> None:
        out = MarketKlineTool(_service(kline=False)).execute({"symbol": _PAIR})
        assert "No kline data available" in out.output

    def test_symbol_arg_required(self) -> None:
        assert MarketKlineTool(_service()).execute({}).error is not None


class TestOrderbookTool:
    def test_hit_shows_ladder_and_spread(self) -> None:
        out = MarketOrderbookTool(_service()).execute({"symbol": _PAIR})
        checks = (
            out.error is None,
            _PAIR in out.output,
            "Asks:" in out.output,
            "Bids:" in out.output,
            "Spread" in out.output,
        )
        assert all(checks)

    def test_depth_limits_levels_per_side(self) -> None:
        out = MarketOrderbookTool(_service()).execute({"symbol": _PAIR, "depth": 2})
        # depth=2 hides the 3rd level (147.8) on the bid side
        assert out.error is None
        deep_bids = [ln for ln in out.output.split("\n") if "147.8" in ln]
        assert deep_bids == []

    def test_miss_reports_unavailability(self) -> None:
        out = MarketOrderbookTool(_service(orderbook=False)).execute({"symbol": _PAIR})
        assert "No orderbook data available" in out.output

    def test_symbol_arg_required(self) -> None:
        assert MarketOrderbookTool(_service()).execute({}).error is not None
