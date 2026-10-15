"""OKX payload → unified events: symbol conversion, ticker math, candles."""

from selene_space_exchange.exchanges.okx.normalizer import OKXNormalizer, _okx_symbol_to_unified

TS = "1700000000000"


class TestInstIdConversion:
    def test_spot_pair(self) -> None:
        assert _okx_symbol_to_unified("BTC-USDT") == "BTC/USDT"
        assert _okx_symbol_to_unified("ETH-USDC") == "ETH/USDC"

    def test_swap_suffix_discarded(self) -> None:
        """SWAP tail is dropped — the unified form is always BASE/QUOTE."""
        assert _okx_symbol_to_unified("BTC-USDT-SWAP") == "BTC/USDT"


def _ticker_payload(**overrides) -> dict:
    body = {
        "instType": "SPOT",
        "instId": "BTC-USDT",
        "last": "70550.5",
        "lastSz": "0.1",
        "askPx": "70551.0",
        "askSz": "11",
        "bidPx": "70550.0",
        "bidSz": "5",
        "open24h": "69000",
        "high24h": "71000",
        "low24h": "68500",
        "vol24h": "12345",
        "ts": TS,
    }
    body.update(overrides)
    return body


class TestTickerMapping:
    def test_full_payload_maps_every_field(self) -> None:
        event = OKXNormalizer().normalize_ticker(_ticker_payload(), "BTC-USDT")
        got = (
            event.exchange,
            event.symbol,
            event.last_price,
            event.bid,
            event.ask,
            event.volume_24h,
            event.timestamp,
        )
        want = ("okx", "BTC/USDT", 70550.5, 70550.0, 70551.0, 12345.0, int(TS))
        assert got == want

    def test_rising_market_yields_positive_change(self) -> None:
        assert OKXNormalizer().normalize_ticker(_ticker_payload(), "BTC-USDT").change_24h_pct > 0

    def test_zero_open_short_circuits_change_math(self) -> None:
        flat = dict(last="100.0", askPx="101", bidPx="99", open24h="0", vol24h="50", ts=TS)
        event = OKXNormalizer().normalize_ticker(flat, "ETH-USDT")
        assert event.change_24h_pct == 0.0


def _candle_row(confirm: str, **prices: str) -> list[str]:
    base = ["1700000000000", "70000.0", "71000.0", "69000.0", "70500.0", "1234.5", "86000000", "86000000"]
    if prices:
        base[1:5] = [prices["o"], prices["h"], prices["l"], prices["c"]]
    return [*base, confirm]


class TestCandleMapping:
    def test_closed_hourly_candle(self) -> None:
        event = OKXNormalizer().normalize_candle(_candle_row("1"), "BTC-USDT", "candle1H")
        checks = {
            "exchange": event.exchange == "okx",
            "symbol": event.symbol == "BTC/USDT",
            "interval": event.interval == "1h",
            "ohlc": (event.open, event.high, event.close) == (70000.0, 71000.0, 70500.0),
            "volume": event.volume == 1234.5,
            "closed": event.closed is True,
        }
        assert all(checks.values()), f"failed: {[k for k, ok in checks.items() if not ok]}"

    def test_open_daily_candle_keeps_unconfirmed_flag(self) -> None:
        live = _candle_row("0", o="100", h="110", l="90", c="105")
        event = OKXNormalizer().normalize_candle(live, "ETH-USDT", "candle1D")
        assert (event.closed, event.interval) == (False, "1d")

    def test_minute_bar_interval_lowercased(self) -> None:
        minute = _candle_row("1", o="100", h="110", l="90", c="105")
        event = OKXNormalizer().normalize_candle(minute, "ETH-USDT", "candle5m")
        assert event.interval == "5m"
