"""400/404 drops the symbol from the REST poll set; other errors don't."""

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
