"""Tests for PortfolioMutex — cross-strategy symbol mutual exclusion."""

from __future__ import annotations

import pytest

from selene_space_risk.portfolio_mutex import (
    DEFAULT_LOCK_TTL_MS,
    PortfolioMutex,
    SymbolLock,
)


class FakeClock:
    """Injected clock so TTL expiry is testable without real sleeping."""

    def __init__(self, start_ms: int = 0) -> None:
        self._now_ms = start_ms

    def __call__(self) -> int:
        return self._now_ms

    def advance(self, ms: int) -> None:
        self._now_ms += ms


def _make_mutex(ttl_ms: int = DEFAULT_LOCK_TTL_MS) -> tuple[PortfolioMutex, FakeClock]:
    clock = FakeClock(start_ms=1_000_000)
    return PortfolioMutex(ttl_ms=ttl_ms, clock=clock), clock


class TestAcquire:
    def test_free_symbol_granted(self) -> None:
        mutex, _ = _make_mutex()
        assert mutex.acquire("BTC/USDT", "strategy_a") is True
        assert mutex.is_locked("BTC/USDT") is True
        assert mutex.owner_of("BTC/USDT") == "strategy_a"

    def test_same_strategy_reentrant(self) -> None:
        mutex, _ = _make_mutex()
        mutex.acquire("BTC/USDT", "strategy_a")
        assert mutex.acquire("BTC/USDT", "strategy_a") is True
        assert mutex.owner_of("BTC/USDT") == "strategy_a"

    def test_other_strategy_rejected(self) -> None:
        mutex, _ = _make_mutex()
        mutex.acquire("BTC/USDT", "strategy_a")
        assert mutex.acquire("BTC/USDT", "strategy_b") is False
        assert mutex.owner_of("BTC/USDT") == "strategy_a"

    def test_different_symbols_independent(self) -> None:
        mutex, _ = _make_mutex()
        assert mutex.acquire("BTC/USDT", "strategy_a") is True
        assert mutex.acquire("ETH/USDT", "strategy_b") is True


class TestRelease:
    def test_holder_releases(self) -> None:
        mutex, _ = _make_mutex()
        mutex.acquire("BTC/USDT", "strategy_a")
        mutex.release("BTC/USDT", "strategy_a")
        assert mutex.is_locked("BTC/USDT") is False
        assert mutex.owner_of("BTC/USDT") is None

    def test_non_holder_release_ignored(self) -> None:
        mutex, _ = _make_mutex()
        mutex.acquire("BTC/USDT", "strategy_a")
        mutex.release("BTC/USDT", "strategy_b")  # must not raise
        assert mutex.is_locked("BTC/USDT") is True
        assert mutex.owner_of("BTC/USDT") == "strategy_a"

    def test_release_unknown_symbol_ignored(self) -> None:
        mutex, _ = _make_mutex()
        mutex.release("BTC/USDT", "strategy_a")  # must not raise


class TestTTLExpiry:
    def test_expired_lock_auto_releases_on_access(self) -> None:
        mutex, clock = _make_mutex(ttl_ms=300_000)
        mutex.acquire("BTC/USDT", "strategy_a")
        clock.advance(300_000)  # exactly at expiry
        assert mutex.is_locked("BTC/USDT") is False
        assert mutex.owner_of("BTC/USDT") is None

    def test_acquire_after_expiry_granted_to_other(self) -> None:
        mutex, clock = _make_mutex(ttl_ms=300_000)
        mutex.acquire("BTC/USDT", "strategy_a")
        clock.advance(300_001)
        assert mutex.acquire("BTC/USDT", "strategy_b") is True

    def test_not_expired_before_ttl(self) -> None:
        mutex, clock = _make_mutex(ttl_ms=300_000)
        mutex.acquire("BTC/USDT", "strategy_a")
        clock.advance(299_999)
        assert mutex.is_locked("BTC/USDT") is True

    def test_reentry_refreshes_deadline(self) -> None:
        mutex, clock = _make_mutex(ttl_ms=300_000)
        mutex.acquire("BTC/USDT", "strategy_a")
        clock.advance(200_000)
        assert mutex.acquire("BTC/USDT", "strategy_a") is True  # refresh
        clock.advance(200_000)  # 400s total, but refreshed at 200s → still alive
        assert mutex.is_locked("BTC/USDT") is True


class TestSnapshot:
    def test_active_locks_returns_owner_map(self) -> None:
        mutex, _ = _make_mutex()
        mutex.acquire("BTC/USDT", "strategy_a")
        mutex.acquire("ETH/USDT", "strategy_b")
        assert mutex.active_locks() == {
            "BTC/USDT": "strategy_a",
            "ETH/USDT": "strategy_b",
        }

    def test_active_locks_omits_released(self) -> None:
        mutex, _ = _make_mutex()
        mutex.acquire("BTC/USDT", "strategy_a")
        mutex.release("BTC/USDT", "strategy_a")
        assert mutex.active_locks() == {}

    def test_active_locks_omits_expired(self) -> None:
        mutex, clock = _make_mutex(ttl_ms=300_000)
        mutex.acquire("BTC/USDT", "strategy_a")
        clock.advance(300_001)
        assert mutex.active_locks() == {}


class TestConstructor:
    def test_non_positive_ttl_rejected(self) -> None:
        with pytest.raises(ValueError):
            PortfolioMutex(ttl_ms=0)
        with pytest.raises(ValueError):
            PortfolioMutex(ttl_ms=-1)

    def test_symbol_lock_frozen(self) -> None:
        lock = SymbolLock(
            symbol="BTC/USDT", owner_strategy="a", held_since_ms=1, expires_at_ms=2
        )
        with pytest.raises(AttributeError):
            lock.owner_strategy = "b"  # type: ignore[misc]
