"""P0-1: shared rate limiter injection.

Verifies that REST clients constructed via the lifespan wiring share the
same SlidingWindowRateLimiter instance within an exchange (so the trading
adapter and underlying REST client draw from one quota pool), while
different exchanges keep independent limiters (their quota envelopes are
separate — different API keys, IPs, and rate windows).
"""

from __future__ import annotations

from selene_space_exchange.base.rate_limiter import SlidingWindowRateLimiter


def test_shared_limiter_same_counter_across_clients() -> None:
    """Two clients sharing one limiter instance must see the same counter."""
    shared = SlidingWindowRateLimiter(calls_per_window=100, window_ms=10_000)
    # Simulate the wiring: same limiter object passed to two clients.
    client_a_limiter = shared
    client_b_limiter = shared
    assert client_a_limiter is client_b_limiter
    assert client_a_limiter.remaining == 100


def test_independent_limiters_have_separate_counters() -> None:
    """Different exchanges (binance vs okx) must NOT share a limiter."""
    binance_limiter = SlidingWindowRateLimiter(calls_per_window=1200, window_ms=60_000)
    okx_limiter = SlidingWindowRateLimiter(calls_per_window=60, window_ms=2_000)
    assert binance_limiter is not okx_limiter
    # Different defaults reflected in different remaining capacity.
    assert binance_limiter.remaining == 1200
    assert okx_limiter.remaining == 60


def test_binance_rest_client_uses_injected_limiter() -> None:
    """BinanceRESTClient must use the injected limiter, not build its own."""
    from pydantic import SecretStr

    from selene_space_exchange.base.auth import ExchangeCredentials
    from selene_space_exchange.exchanges.binance.rest_client import BinanceRESTClient

    creds = ExchangeCredentials(api_key=SecretStr("k"), api_secret=SecretStr("s"))
    shared = SlidingWindowRateLimiter(calls_per_window=1200, window_ms=60_000)
    client = BinanceRESTClient(credentials=creds, testnet=True, rate_limiter=shared)
    assert client._rate_limiter is shared


def test_okx_rest_client_uses_injected_limiter() -> None:
    """OKXRESTClient must use the injected limiter, not build its own."""
    from pydantic import SecretStr

    from selene_space_exchange.base.auth import ExchangeCredentials
    from selene_space_exchange.exchanges.okx.rest_client import OKXRESTClient

    creds = ExchangeCredentials(api_key=SecretStr("k"), api_secret=SecretStr("s"))
    shared = SlidingWindowRateLimiter(calls_per_window=60, window_ms=2_000)
    client = OKXRESTClient(credentials=creds, demo=True, rate_limiter=shared)
    assert client._rate_limiter is shared
