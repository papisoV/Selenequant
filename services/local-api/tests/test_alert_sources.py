"""Source readers: routing, fallback order, and honest ``None`` on no data.

These tests use fake sources and fake market services, so they pin the routing
decisions without touching a network. What matters is that a symbol is never
sent to a venue that does not serve its market, and that "no quote" comes back
as ``None`` rather than a placeholder price.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.core.alert_sources import (
    make_indicator_prefetch,
    make_price_reader,
    read_indicator,
)


class _Source:
    def __init__(self, get_ticker=None, get_klines=None, rows=None):
        self._get_ticker = get_ticker
        self._klines = get_klines
        self._rows = rows if rows is not None else {}
        self.calls: list[str] = []

    def get_ticker(self, symbol):
        self.calls.append(symbol)
        return self._get_ticker(symbol) if self._get_ticker else None

    def get_market_rows(self):
        return self._rows


class _Market:
    def __init__(self, sources):
        self.sources = sources

    def get_ticker(self, symbol, exchange="binance", market_type="spot"):
        source = self.sources.get((exchange, market_type))
        return source.get_ticker(symbol) if source is not None else None


class _Ticker:
    def __init__(self, price):
        self.last_price = price


class _Row:
    def __init__(self, last):
        self.last = last


def test_price_uses_the_crypto_venue_for_a_crypto_pair():
    binance = _Source(get_ticker=lambda _s: _Ticker(50000.0))
    astock = _Source(get_ticker=lambda _s: _Ticker(999.0))
    market = _Market({("binance", "spot"): binance, ("astock", "spot"): astock})

    assert make_price_reader(market)("BTC/USDT") == 50000.0
    # The A-share venue must never be asked about a crypto pair.
    assert astock.calls == []


def test_price_routes_an_a_share_code_to_the_astock_source():
    binance = _Source(get_ticker=lambda _s: _Ticker(50000.0))
    astock = _Source(get_ticker=lambda _s: _Ticker(12.5))
    market = _Market({("binance", "spot"): binance, ("astock", "spot"): astock})

    assert make_price_reader(market)("600519.SH") == 12.5
    assert binance.calls == []


def test_price_tries_the_suffixed_form_for_a_bare_a_share_code():
    """Favorites store ``600519``; the feed keys ``600519.SH``."""
    seen = []

    def _ticker(symbol):
        seen.append(symbol)
        return _Ticker(12.5) if symbol == "600519.SH" else None

    market = _Market({("astock", "spot"): _Source(get_ticker=_ticker)})
    assert make_price_reader(market)("600519") == 12.5
    assert "600519.SH" in seen


def test_price_falls_back_to_the_whole_market_snapshot_for_ashare():
    """``AstockSource.get_ticker`` returns None by contract (kline-only feed).

    The cross-section is the feed that actually carries a last price for an
    instrument nobody subscribed to.
    """
    market = _Market(
        {
            ("astock", "spot"): _Source(get_ticker=lambda _s: None),
            ("astock", "market_snapshot"): _Source(rows={"600519.SH": _Row(1680.0)}),
        }
    )
    assert make_price_reader(market)("600519") == 1680.0


def test_whole_market_snapshot_is_not_polled_for_a_single_symbol():
    """It is a cross-section, not a per-symbol feed (``serves_symbols``)."""
    snapshot = _Source(get_ticker=lambda _s: _Ticker(1.0), rows={})
    market = _Market(
        {("astock", "market_snapshot"): snapshot, ("astock", "spot"): _Source()}
    )
    assert make_price_reader(market)("600519.SH") is None
    assert snapshot.calls == []


def test_price_is_none_when_no_source_serves_the_market():
    market = _Market({("binance", "spot"): _Source(get_ticker=lambda _s: _Ticker(1.0))})
    assert make_price_reader(market)("AAPL") is None


def test_price_is_none_without_a_market_service():
    assert make_price_reader(None)("BTC/USDT") is None


def test_price_is_none_when_the_quote_carries_no_price():
    market = _Market({("binance", "spot"): _Source(get_ticker=lambda _s: _Ticker(0.0))})
    assert make_price_reader(market)("BTC/USDT") is None


def test_a_raising_source_does_not_abort_the_read():
    def _boom(_symbol):
        raise RuntimeError("ws down")

    ok = _Source(get_ticker=lambda _s: _Ticker(2.0))
    market = _Market(
        {("binance", "futures"): _Source(get_ticker=_boom), ("binance", "spot"): ok}
    )
    assert make_price_reader(market)("BTC/USDT") == 2.0


# ---- indicator -------------------------------------------------------------


class _KlineManager:
    def __init__(self, frame=None, raises=False):
        self._frame = frame
        self._raises = raises

    async def get_klines(self, exchange, market_type, symbol, interval, source="auto"):
        if self._raises:
            raise RuntimeError("db locked")
        return self._frame


def _frame(n=120):
    closes = [100.0 + i * 0.5 for i in range(n)]
    return pd.DataFrame({"close": closes})


@pytest.mark.asyncio
async def test_indicator_returns_none_without_a_kline_manager():
    market = _Market({("astock", "spot"): _Source()})
    assert await read_indicator(market, None, "600519.SH", "RSI") is None


@pytest.mark.asyncio
async def test_indicator_returns_none_when_bars_are_too_few():
    """Fewer bars than RSI needs must be "no data", not a number off the tail."""
    market = _Market({("astock", "spot"): _Source()})
    mgr = _KlineManager(frame=_frame(5))
    assert await read_indicator(market, mgr, "600519.SH", "RSI") is None


@pytest.mark.asyncio
async def test_indicator_computes_rsi_from_enough_bars():
    market = _Market({("astock", "spot"): _Source()})
    mgr = _KlineManager(frame=_frame(120))
    value = await read_indicator(market, mgr, "600519.SH", "RSI")
    assert value is not None
    assert 0.0 <= value <= 100.0


@pytest.mark.asyncio
async def test_indicator_returns_none_when_the_store_raises():
    market = _Market({("astock", "spot"): _Source()})
    mgr = _KlineManager(raises=True)
    assert await read_indicator(market, mgr, "600519.SH", "RSI") is None


@pytest.mark.asyncio
async def test_prefetch_dedupes_symbol_metric_pairs():
    """Two rules on the same instrument cost one bar read and one computation."""
    reads = []

    class _Counting(_KlineManager):
        async def get_klines(
            self, exchange, market_type, symbol, interval, source="auto"
        ):
            reads.append(symbol)
            return _frame(120)

    market = _Market({("astock", "spot"): _Source()})
    prefetch = make_indicator_prefetch(market, _Counting())
    rules = [
        {
            "id": "a",
            "type": "indicator",
            "symbol": "600519.SH",
            "threshold": {"indicator": "RSI", "value": 70},
        },
        {
            "id": "b",
            "type": "indicator",
            "symbol": "600519.SH",
            "threshold": {"indicator": "RSI", "value": 60},
        },
    ]
    out = await prefetch(rules)
    assert len(reads) == 1
    assert ("600519.SH", "RSI") in out


@pytest.mark.asyncio
async def test_prefetch_ignores_non_indicator_rules():
    class _Never(_KlineManager):
        async def get_klines(self, *_a, **_k):  # pragma: no cover - must not run
            raise AssertionError("should not read bars for a price rule")

    market = _Market({("binance", "spot"): _Source()})
    prefetch = make_indicator_prefetch(market, _Never())
    out = await prefetch(
        [{"id": "p", "type": "price", "symbol": "BTC/USDT", "threshold": {"value": 1}}]
    )
    assert out == {}
