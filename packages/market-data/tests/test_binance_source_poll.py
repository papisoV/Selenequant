"""400/404 drops the symbol from the REST poll set; other errors don't."""

from types import SimpleNamespace

import pytest

from selene_space_market.sources.binance_source import BinanceSource


class _StubSource(BinanceSource):
    def __init__(self) -> None:  # skips super().__init__ — testing pure helper
        self._watched = {"PEPE", "AAPL", "BTC/USDT"}


def test_drop_dead_symbol_removes_on_permanent_error() -> None:
    src = _StubSource()
    src._drop_dead_symbol("PEPE", "ticker", 400)
    src._drop_dead_symbol("AAPL", "funding", 404)
    assert "PEPE" not in src._watched
    assert "AAPL" not in src._watched
    assert "BTC/USDT" in src._watched


def test_drop_dead_symbol_keeps_on_transient_error() -> None:
    src = _StubSource()
    for status in (429, 418, 500, 503):
        src._drop_dead_symbol("BTC/USDT", "ticker", status)
    assert "BTC/USDT" in src._watched


class _RefusingWS:
    """Venue stream client whose subscribe always throws."""

    async def subscribe_ticker(self, symbols: list[str]) -> None:
        raise ConnectionError("received 1008 (policy violation) Too many requests")

    async def subscribe_agg_trade(self, symbols: list[str]) -> None:
        raise ConnectionError("received 1008 (policy violation) Too many requests")


class _SubscribeStub(BinanceSource):
    """Bare source exercising subscribe() without the full wiring."""

    def __init__(self, ws: _RefusingWS) -> None:
        # super().__init__ skipped on purpose: this test only needs the
        # subscription bookkeeping, not the HTTP/L2 machinery behind it.
        # The assignments below deliberately violate the real attribute types
        # (_ws is a stub, _buffers/_config are placeholders) — that is the point
        # of the double, so the mismatch is ignored rather than papered over by
        # widening the source declarations.
        self._running = True
        self._ws = ws  # type: ignore[assignment]
        self._is_futures = False
        self._intervals = ["1h"]
        self._l2 = None
        self._watched: set[str] = set()
        self._buffers: dict[str, object] = {}  # type: ignore[assignment]
        self._config = SimpleNamespace(market_type="spot")  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_symbol_joins_poll_set_even_when_subscribe_is_refused() -> None:
    """The REST poll set must not depend on the WS subscribe succeeding.

    Regression for the 2026-10 Binance incident: ``_watched.add`` sat *after*
    the subscribe awaits, so a refused subscribe left the symbol out of the
    poll set too — the futures REST ticker/funding fallback could never cover
    it, and every later read answered 404 with no route back to health.
    """
    src = _SubscribeStub(_RefusingWS())

    with pytest.raises(ConnectionError):
        await src.subscribe(
            "BTC/USDT", ticker=True, trade=True, depth=False, kline=False
        )

    assert "BTC/USDT" in src._watched
