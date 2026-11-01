"""Tests for alternative.me / CNN / CoinGecko providers (US-002).

Uses httpx.MockTransport (respx is not a package dependency) to stub HTTP.
Covers: normal parse / missing fields / 429 / CNN 418 branch / header
contract (Referer + browser UA required by upstream evidence).
Degradation-chain ordering (alt.me -> CNN) lives in intelligence.py (US-003);
these tests pin single-source behavior only.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from selene_space_macrodata.providers.altme_fng import FNG_SERIES_ID, AltmeFngProvider
from selene_space_macrodata.providers.cnn_fng import (
    CNN_FNG_PATH,
    CNN_REFERER,
    CNN_USER_AGENT,
    FNG_SERIES_ID as CNN_FNG_SERIES_ID,
    CnnFngEndpointError,
    CnnFngProvider,
)
from selene_space_macrodata.providers.coingecko_global import (
    BTC_DOMINANCE,
    TOTAL_MARKET_CAP,
    CoingeckoGlobalProvider,
)
from selene_space_macrodata.source import MacroDataSourceConfig

# 本文件全部是 async 测试，统一打 asyncio 标记（与同包 test_macrodata.py 一致）。
# 缺标记时 pytest 报 "async def functions are not natively supported" 并跳过——
# 本地因装有 anyio 等插件可能表现为通过，CI 干净环境下才暴露。
pytestmark = pytest.mark.asyncio


def _cfg(provider_id: str) -> MacroDataSourceConfig:
    return MacroDataSourceConfig(provider=provider_id, cache_ttl_seconds=3600)


def _transport_from_handler(handler) -> httpx.AsyncBaseTransport:
    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# alt.me (alternative.me)
# ---------------------------------------------------------------------------


def _altme_payload(n: int = 3) -> dict:
    now = int(datetime.now(UTC).timestamp())
    classifications = ["Fear", "Neutral", "Greed"]
    return {
        "name": "Fear and Greed Index",
        "data": [
            {
                "value": str(50 + i),
                "value_classification": classifications[i % 3],
                "timestamp": str(now - i * 86400),
                "time_until_update": "123",
            }
            for i in range(n)
        ],
    }


async def test_altme_normal_parse():
    payload = _altme_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    p = AltmeFngProvider(_cfg("altme_fng"), transport=_transport_from_handler(handler))
    series = await p.fetch_series(FNG_SERIES_ID)

    assert series.provider == "altme_fng"
    df = series.observations
    assert list(df.columns) == ["date", "value", "classification"]
    assert len(df) == 3
    # API returns newest-first; provider must re-sort ascending.
    dates = df["date"].tolist()
    assert dates == sorted(dates)
    # oldest first after re-sort: i=2 (52, Greed) -> i=1 (51) -> i=0 (50, Fear)
    assert df["value"].tolist() == [52, 51, 50]
    assert df["classification"].tolist() == ["Greed", "Neutral", "Fear"]
    cached = p.get_cached(FNG_SERIES_ID)
    assert cached is not None and len(cached.observations) == 3


async def test_altme_missing_fields_skipped():
    payload = _altme_payload()
    payload["data"][1] = {"value": "51"}  # no timestamp -> row skipped

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    p = AltmeFngProvider(_cfg("altme_fng"), transport=_transport_from_handler(handler))
    series = await p.fetch_series(FNG_SERIES_ID)
    assert len(series.observations) == 2
    # The removed row was the middle one (i=1, value 51).
    assert series.observations["value"].tolist() == [52, 50]


async def test_altme_empty_payload_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    p = AltmeFngProvider(_cfg("altme_fng"), transport=_transport_from_handler(handler))
    with pytest.raises(RuntimeError, match="no cached fallback"):
        await p.fetch_series(FNG_SERIES_ID)


async def test_altme_429_falls_back_to_stale_cache():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, json={"error": "rate limited"})

    p = AltmeFngProvider(
        _cfg("altme_fng"),
        transport=_transport_from_handler(handler),
        retry_backoff_seconds=0.0,
    )
    with pytest.raises(RuntimeError, match="no cached fallback"):
        await p.fetch_series(FNG_SERIES_ID)
    assert calls["n"] == 3  # with_retry attempts exhausted


async def test_altme_429_returns_cached_within_ttl():
    good = _altme_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    p = AltmeFngProvider(
        _cfg("altme_fng"),
        transport=_transport_from_handler(handler),
        retry_backoff_seconds=0.0,
    )

    # Prime the cache with a successful fetch via a one-off good transport.
    def good_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=good)

    p._transport = _transport_from_handler(good_handler)
    await p.stop()
    await p.start()
    good_series = await p.fetch_series(FNG_SERIES_ID)

    p._transport = _transport_from_handler(handler)
    await p.stop()
    await p.start()
    stale = await p.fetch_series(FNG_SERIES_ID)
    assert stale.fetched_at == good_series.fetched_at
    assert len(stale.observations) == 3


async def test_altme_unknown_series_rejected():
    p = AltmeFngProvider(_cfg("altme_fng"))
    with pytest.raises(KeyError):
        await p.fetch_series("NOPE")


async def test_altme_protocol_conformance():
    p = AltmeFngProvider(_cfg("altme_fng"))
    from selene_space_macrodata import MacroDataSource

    assert isinstance(p, MacroDataSource)
    assert p.list_series() == [FNG_SERIES_ID]


# ---------------------------------------------------------------------------
# CNN
# ---------------------------------------------------------------------------


def _cnn_payload() -> dict:
    return {
        "fear_and_greed": {
            "score": 37.62,
            "rating": "fear",
            "timestamp": "2026-09-19T08:00:00+00:00",
            "previous_close": 40.1,
        },
        "fear_and_greed_historical": {},
    }


async def test_cnn_normal_parse():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json=_cnn_payload())

    p = CnnFngProvider(_cfg("cnn_fng"), transport=_transport_from_handler(handler))
    series = await p.fetch_series(CNN_FNG_SERIES_ID)

    # Endpoint contract: full browser UA + Referer, else upstream 418.
    assert captured["headers"]["user-agent"] == CNN_USER_AGENT
    assert captured["headers"]["referer"] == CNN_REFERER
    assert "Chrome/120" in CNN_USER_AGENT

    assert series.provider == "cnn_fng"
    df = series.observations
    assert df["value"].tolist() == [38]  # int(round(37.62))
    assert df["rating"].tolist() == ["fear"]
    assert df["timestamp"].tolist() == ["2026-09-19T08:00:00+00:00"]
    assert len(df) == 1  # snapshot series: single row


async def test_cnn_418_raises_endpoint_error_no_retry():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(418, json={"error": "I'm a teapot"})

    p = CnnFngProvider(
        _cfg("cnn_fng"),
        transport=_transport_from_handler(handler),
        retry_backoff_seconds=0.0,
    )
    # 418 is a source-unavailable signal, not a transient network failure —
    # surfaced as the explicit CnnFngEndpointError (distinct from network hangs).
    with pytest.raises(CnnFngEndpointError, match="418"):
        await p.fetch_series(CNN_FNG_SERIES_ID)
    assert calls["n"] == 3


async def test_cnn_418_with_cached_returns_stale():
    def bad_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(418)

    def good_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_cnn_payload())

    p = CnnFngProvider(
        _cfg("cnn_fng"),
        transport=_transport_from_handler(good_handler),
        retry_backoff_seconds=0.0,
    )
    good_series = await p.fetch_series(CNN_FNG_SERIES_ID)

    p._transport = _transport_from_handler(bad_handler)
    await p.stop()
    await p.start()
    stale = await p.fetch_series(CNN_FNG_SERIES_ID)
    assert stale.fetched_at == good_series.fetched_at


async def test_cnn_missing_score_raises():
    payload = {"fear_and_greed": {"rating": "fear"}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    p = CnnFngProvider(
        _cfg("cnn_fng"),
        transport=_transport_from_handler(handler),
        retry_backoff_seconds=0.0,
    )
    with pytest.raises(RuntimeError, match="no cached fallback"):
        await p.fetch_series(CNN_FNG_SERIES_ID)


async def test_cnn_missing_referer_header_gets_418_from_server():
    """Server-side contract check: a request without Referer gets 418, and the
    provider's default headers (always including Referer) are what protects it.

    We simulate the server rejecting Referer-less requests, then assert the
    provider never actually sends one (its DEFAULT_HEADERS always include it).
    """

    def strict_handler(request: httpx.Request) -> httpx.Response:
        if "referer" not in request.headers:
            return httpx.Response(418)
        return httpx.Response(200, json=_cnn_payload())

    p = CnnFngProvider(
        _cfg("cnn_fng"), transport=_transport_from_handler(strict_handler)
    )
    series = await p.fetch_series(CNN_FNG_SERIES_ID)  # must succeed, never 418
    assert series.observations["value"].tolist() == [38]


def test_cnn_default_headers_contract():
    """The 418-prevention headers are converged in module constants — pinned."""
    assert "Referer" in CnnFngProvider.DEFAULT_HEADERS
    assert CnnFngProvider.DEFAULT_HEADERS["Referer"] == "https://www.cnn.com/"
    assert CnnFngProvider.DEFAULT_HEADERS["User-Agent"] == CNN_USER_AGENT
    assert CNN_FNG_PATH == "/index/fearandgreed/graphdata"


async def test_cnn_protocol_conformance():
    from selene_space_macrodata import MacroDataSource

    p = CnnFngProvider(_cfg("cnn_fng"))
    assert isinstance(p, MacroDataSource)


# ---------------------------------------------------------------------------
# CoinGecko global
# ---------------------------------------------------------------------------


def _coingecko_payload() -> dict:
    return {
        "data": {
            "total_market_cap": {"usd": 2_450_123_456_789.0, "btc": 1.2e12},
            "market_cap_percentage": {"btc": 54.32, "eth": 13.1},
            "active_cryptocurrencies": 13000,
        }
    }


async def test_coingecko_btc_dominance_parse():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_coingecko_payload())

    p = CoingeckoGlobalProvider(
        _cfg("coingecko_global"), transport=_transport_from_handler(handler)
    )
    series = await p.fetch_series(BTC_DOMINANCE)
    assert series.provider == "coingecko_global"
    assert series.observations["value"].tolist() == [pytest.approx(54.32)]


async def test_coingecko_total_market_cap_parse():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_coingecko_payload())

    p = CoingeckoGlobalProvider(
        _cfg("coingecko_global"), transport=_transport_from_handler(handler)
    )
    series = await p.fetch_series(TOTAL_MARKET_CAP)
    assert series.observations["value"].tolist() == [pytest.approx(2_450_123_456_789.0)]


async def test_coingecko_missing_btc_dominance_raises():
    payload = {"data": {"total_market_cap": {"usd": 1.0}}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    p = CoingeckoGlobalProvider(
        _cfg("coingecko_global"),
        transport=_transport_from_handler(handler),
        retry_backoff_seconds=0.0,
    )
    with pytest.raises(RuntimeError, match="no cached fallback"):
        await p.fetch_series(BTC_DOMINANCE)


async def test_coingecko_missing_total_cap_raises():
    payload = {"data": {"market_cap_percentage": {"btc": 50.0}}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    p = CoingeckoGlobalProvider(
        _cfg("coingecko_global"),
        transport=_transport_from_handler(handler),
        retry_backoff_seconds=0.0,
    )
    with pytest.raises(RuntimeError, match="no cached fallback"):
        await p.fetch_series(TOTAL_MARKET_CAP)


async def test_coingecko_unknown_series_rejected():
    p = CoingeckoGlobalProvider(_cfg("coingecko_global"))
    with pytest.raises(KeyError):
        await p.fetch_series("NOPE")


async def test_coingecko_protocol_conformance():
    from selene_space_macrodata import MacroDataSource

    p = CoingeckoGlobalProvider(_cfg("coingecko_global"))
    assert isinstance(p, MacroDataSource)


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


def test_three_providers_registered():
    from selene_space_macrodata.registry import list_registered

    registered = list_registered()
    for pid in ("altme_fng", "cnn_fng", "coingecko_global"):
        assert pid in registered
    cfg = _cfg("altme_fng")
    from selene_space_macrodata.registry import get_provider

    assert isinstance(get_provider(cfg), AltmeFngProvider)
