"""Tests for DeribitOptionsFetcher.fetch_chain (options poller payload, ADR-1).

Covers the field mapping (expiration_timestamp pass-through, mark_iv ->
implied_volatility), the 5-min TTL cache, and write-through of the payload
into the MarketDataService stores via the single-writer entry point.
"""

from __future__ import annotations

from typing import Any

import pytest

from selene_space_market.options_fetcher import DeribitOptionsFetcher
from selene_space_market.service import MarketDataService


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeClient:
    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        self._responses = responses
        self.requests: list[str] = []

    async def get(self, url: str) -> FakeResponse:
        self.requests.append(url)
        for key, payload in self._responses.items():
            if key in url:
                return FakeResponse(payload)
        return FakeResponse({"result": {}})


def _make_fake_client() -> FakeClient:
    """One call + one put sharing an expiry, plus a perpetual ticker."""
    return FakeClient(
        {
            "/ticker": {"result": {"last_price": 100000.0}},
            "/get_instruments": {
                "result": [
                    {
                        "instrument_name": "BTC-28MAR25-80000-C",
                        "expiration_timestamp": 1_742_630_400_000,
                        "strike": 80000.0,
                        "option_type": "call",
                        "is_active": True,
                        "contract_size": 1.0,
                    },
                    {
                        "instrument_name": "BTC-28MAR25-70000-P",
                        "expiration_timestamp": 1_742_630_400_000,
                        "strike": 70000.0,
                        "option_type": "put",
                        "is_active": True,
                        "contract_size": 1.0,
                    },
                ]
            },
            "BTC-28MAR25-80000-C": {
                "result": {
                    "mark_price": 0.02,
                    "underlying_price": 100000.0,
                    "mark_iv": 0.65,
                    "open_interest": 5000.0,
                    "stats": {"volume": 1200.0},
                    "greeks": {"delta": 0.5, "gamma": 0.001},
                }
            },
            "BTC-28MAR25-70000-P": {
                "result": {
                    "mark_price": 0.03,
                    "underlying_price": 100000.0,
                    "mark_iv": 0.72,
                    "open_interest": 3000.0,
                    "stats": {"volume": 800.0},
                    "greeks": {"delta": -0.4, "gamma": 0.001},
                }
            },
        }
    )


def _fetcher_with(
    monkeypatch: pytest.MonkeyPatch, fake: FakeClient
) -> DeribitOptionsFetcher:
    fetcher = DeribitOptionsFetcher()

    async def _fake_get_client() -> FakeClient:
        return fake

    monkeypatch.setattr(fetcher, "_get_client", _fake_get_client)
    return fetcher


@pytest.mark.asyncio
async def test_fetch_chain_maps_deribit_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _make_fake_client()
    chain = await _fetcher_with(monkeypatch, fake).fetch_chain("BTC")

    assert chain is not None
    assert len(chain.instruments) == 2
    assert len(chain.tickers) == 2
    assert len(chain.summaries) == 1

    # Instrument: expiration_timestamp (ms) passes through as-is
    inst = chain.instruments[0]
    assert inst.instrument_name == "BTC-28MAR25-80000-C"
    assert inst.expiration_timestamp == 1_742_630_400_000
    assert inst.strike == 80000.0
    assert inst.option_type == "call"
    assert inst.base_currency == "BTC"

    # Ticker: mark_iv -> implied_volatility, stats.volume -> volume_24h (decimals)
    ticker = chain.tickers[0]
    assert ticker.implied_volatility == 0.65
    assert ticker.volume_24h == 1200.0
    assert ticker.open_interest == 5000.0
    assert ticker.underlying_price == 100000.0
    assert ticker.delta == 0.5
    assert ticker.gamma == 0.001

    # Summary: per-expiry aggregation
    summary = chain.summaries[0]
    assert summary.base_currency == "BTC"
    assert summary.expiration_timestamp == 1_742_630_400_000
    assert summary.total_call_oi == 5000.0
    assert summary.total_put_oi == 3000.0
    assert summary.put_call_ratio == pytest.approx(0.6)
    assert summary.call_iv_atm == pytest.approx(0.65)
    assert summary.put_iv_atm == pytest.approx(0.72)
    assert summary.total_call_volume == 1200.0
    assert summary.total_put_volume == 800.0


@pytest.mark.asyncio
async def test_fetch_chain_caches_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _make_fake_client()
    fetcher = _fetcher_with(monkeypatch, fake)

    first = await fetcher.fetch_chain("BTC")
    second = await fetcher.fetch_chain("BTC")

    assert first is not None
    assert second is first  # cached object identity within the 300s TTL
    assert sum(1 for u in fake.requests if "get_instruments" in u) == 1


@pytest.mark.asyncio
async def test_chain_feeds_fill_options_data_stores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = await _fetcher_with(monkeypatch, _make_fake_client()).fetch_chain("BTC")
    assert chain is not None

    svc = MarketDataService()
    svc.fill_options_data(chain.instruments, chain.tickers, chain.summaries)

    assert len(svc._options_instrument_store) == 2
    assert len(svc._options_ticker_store) == 2
    assert len(svc._options_summary_store) == 1
    assert svc._options_ticker_store["BTC-28MAR25-80000-C"].implied_volatility == 0.65
    assert "BTC:1742630400000" in svc._options_summary_store


@pytest.mark.asyncio
async def test_fetch_chain_returns_none_without_underlying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeClient({"/ticker": {"result": {}}})
    chain = await _fetcher_with(monkeypatch, fake).fetch_chain("BTC")
    assert chain is None


# ---------------------------------------------------------------------------
# MarketDataService.get_atm_iv
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_atm_iv_median_of_call_and_put(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = await _fetcher_with(monkeypatch, _make_fake_client()).fetch_chain("BTC")
    assert chain is not None

    svc = MarketDataService()
    assert svc.get_atm_iv("BTC") is None  # empty store before fill

    svc.fill_options_data(chain.instruments, chain.tickers, chain.summaries)
    # call_iv_atm=0.65, put_iv_atm=0.72 → median of two values = mean
    assert svc.get_atm_iv("BTC") == pytest.approx((0.65 + 0.72) / 2)


@pytest.mark.asyncio
async def test_get_atm_iv_unknown_currency_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = await _fetcher_with(monkeypatch, _make_fake_client()).fetch_chain("BTC")
    assert chain is not None

    svc = MarketDataService()
    svc.fill_options_data(chain.instruments, chain.tickers, chain.summaries)
    assert svc.get_atm_iv("ETH") is None


@pytest.mark.asyncio
async def test_get_atm_iv_zero_ivs_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = await _fetcher_with(monkeypatch, _make_fake_client()).fetch_chain("BTC")
    assert chain is not None

    svc = MarketDataService()
    zeroed = [
        s.model_copy(update={"call_iv_atm": 0.0, "put_iv_atm": 0.0})
        for s in chain.summaries
    ]
    svc.fill_options_data(chain.instruments, chain.tickers, zeroed)
    assert svc.get_atm_iv("BTC") is None
