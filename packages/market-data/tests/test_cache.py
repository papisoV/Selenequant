"""Cache layer coverage — the generic TTL+LRU primitive and the
market-data-specific ticker/kline facade."""

from __future__ import annotations

import time

import pytest

from selene_space_market.cache import MarketDataCache, TTLLRUCache


def _cache(ttl: float = 10.0, capacity: int = 100) -> TTLLRUCache:
    return TTLLRUCache(ttl_seconds=ttl, max_size=capacity)


def _fill(cache: TTLLRUCache, entries: dict[str, object]) -> None:
    for key, value in entries.items():
        cache.put(key, value)


class TestTTLLRUBasics:
    """put/get/remove/size/clear on short-lived string entries."""

    def test_roundtrip(self) -> None:
        cache = _cache()
        _fill(cache, {"a": "alpha"})
        assert cache.get("a") == "alpha"

    def test_unknown_key_is_none(self) -> None:
        assert _cache().get("never-inserted") is None

    def test_overwrite_wins(self) -> None:
        cache = _cache()
        _fill(cache, {"a": "old"})
        cache.put("a", "new")
        assert cache.get("a") == "new"

    def test_remove_true_then_false(self) -> None:
        cache = _cache()
        _fill(cache, {"a": "alpha"})
        assert [cache.remove("a"), cache.remove("a")] == [True, False]
        assert cache.get("a") is None

    def test_size_grows_with_puts(self) -> None:
        cache = _cache()
        grown = []
        grown.append(cache.size)
        _fill(cache, {"a": "1"})
        grown.append(cache.size)
        assert grown == [0, 1]

    def test_clear_empties_everything(self) -> None:
        cache = _cache()
        _fill(cache, {"a": "1", "b": "2"})
        cache.clear()
        assert cache.size == 0


class TestTTLLRUEvictionAndExpiry:
    """Time-based expiry and capacity-based eviction."""

    def test_expired_entry_reads_as_none(self) -> None:
        cache = _cache(ttl=0.1)
        _fill(cache, {"a": "alpha"})
        before_expiry = cache.get("a")
        time.sleep(0.15)
        after_expiry = cache.get("a")
        assert (before_expiry, after_expiry) == ("alpha", None)

    def test_eviction_targets_least_recently_used(self) -> None:
        cache = _cache(ttl=60.0, capacity=3)
        _fill(cache, {"a": 1, "b": 2, "c": 3})
        cache.get("a")  # refresh a → b becomes oldest
        cache.put("d", 4)

        survivors = {k: cache.get(k) for k in ("a", "b", "c", "d")}
        assert survivors["b"] is None  # evicted
        assert survivors == {"a": 1, "b": None, "c": 3, "d": 4}

    def test_per_entry_ttl_override(self) -> None:
        """put(ttl_seconds=...) shortens one entry without touching the rest."""
        cache = _cache(ttl=60.0)
        cache.put("short", "s", ttl_seconds=0.05)
        cache.put("long", "l")
        time.sleep(0.1)
        assert (cache.get("short"), cache.get("long")) == (None, "l")

    @pytest.mark.parametrize(
        ("kwarg", "bad_value"),
        [("ttl_seconds", 0), ("max_size", 0)],
        ids=["nonpositive-ttl", "nonpositive-capacity"],
    )
    def test_zero_construction_rejected(self, kwarg: str, bad_value: int) -> None:
        with pytest.raises(ValueError, match=kwarg):
            TTLLRUCache(**{kwarg: bad_value})


def _ticker(price: float = 67000.0):
    from selene_space_types.market import TickerEvent

    return TickerEvent(
        exchange="binance",
        symbol="BTC/USDT",
        timestamp=1711000000000,
        last_price=price,
        bid=price - 0.5,
        ask=price + 0.5,
        volume_24h=12345.67,
        change_24h_pct=2.35,
    )


def _kline():
    from selene_space_types.market import KlineEvent

    return KlineEvent(
        exchange="binance",
        symbol="BTC/USDT",
        timestamp=1711000000000,
        interval="1h",
        open=66800.0,
        high=67200.0,
        low=66700.0,
        close=67000.0,
        volume=1234.56,
        closed=True,
    )


class TestMarketDataCacheFacade:
    """Ticker and kline stores behind one facade."""

    def test_ticker_roundtrip(self) -> None:
        cache = MarketDataCache()
        stored = _ticker()
        cache.put_ticker("BTC/USDT", stored)
        assert cache.get_ticker("BTC/USDT") == stored

    def test_kline_roundtrip(self) -> None:
        cache = MarketDataCache()
        stored = _kline()
        cache.put_kline("BTC/USDT", stored)
        assert cache.get_kline("BTC/USDT") == stored

    def test_both_stores_read_none_when_absent(self) -> None:
        cache = MarketDataCache()
        reads = [cache.get_ticker("NONE"), cache.get_kline("NONE")]
        assert reads == [None, None]

    def test_clear_wipes_both_stores(self) -> None:
        cache = MarketDataCache()
        cache.put_ticker("BTC/USDT", _ticker())
        cache.put_kline("BTC/USDT", _kline())
        cache.clear()
        wiped = (cache.get_ticker("BTC/USDT"), cache.get_kline("BTC/USDT"))
        assert wiped == (None, None)
