"""Tests for /api/v1/vchart — professional chart payload.

The endpoint used to return only ``closes`` (plus ``close_times``), which forced
the front-end candle renderer to invent high/low from the close series
(``close * 1.001`` / ``close * 0.999``) and to build a fake ``T0..Tn`` x-axis.
These tests pin the real contract: every bar carries true OHLCV taken from the
kline objects, and ``t`` is the bar's own timestamp (ms) — never a synthetic
index.

Mock isolation: a fake market service is injected via the dependency override
layer (same pattern as test_api_markets.py), so no network or keys are needed.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
from app.core.dependencies import get_market_service, get_settings_service
from app.core.settings_service import SettingsService
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from selene_space_types.market import KlineEvent

BASE_TS = 1791991800000

# Deliberately non-degenerate OHLC: the second bar has a real upper wick
# (high > max(open, close)) and the third a real lower wick, so a renderer
# that fabricates wicks from close alone cannot reproduce these values.
_BARS: list[dict[str, float]] = [
    {"open": 100.0, "high": 100.0, "low": 98.0, "close": 99.0, "volume": 10.0},
    {"open": 99.0, "high": 104.0, "low": 98.5, "close": 100.0, "volume": 20.0},
    {"open": 100.0, "high": 101.0, "low": 95.0, "close": 96.0, "volume": 30.0},
]


def _stub_secrets() -> Any:
    class _Secrets:
        def keyring_available(self) -> bool:
            return True

        async def exists(self, ref: Any) -> bool:
            return False

        async def store(self, ref: Any, value: str) -> None:
            return None

        async def delete(self, ref: Any) -> None:
            return None

    return _Secrets()


def _kline(idx: int, body: dict[str, float]) -> KlineEvent:
    return KlineEvent(
        exchange="binance",
        market_type="spot",
        symbol="BTC/USDT",
        timestamp=BASE_TS + idx * 3_600_000,
        interval="1h",
        open=body["open"],
        high=body["high"],
        low=body["low"],
        close=body["close"],
        volume=body["volume"],
        closed=True,
    )


class _FakeSource:
    def __init__(self, bars: list[KlineEvent]) -> None:
        self._bars = bars
        self.symbols: list[str] = ["BTC/USDT"]

    def get_symbols(self) -> list[str]:
        return list(self.symbols)

    def get_klines(self, symbol: str, limit: int = 100) -> list[KlineEvent]:
        # Mirror the real service: return the MOST RECENT `limit` bars.
        return self._bars[-limit:]

    async def subscribe(self, symbol: str, **kwargs: Any) -> None:
        if symbol not in self.symbols:
            self.symbols.append(symbol)


class _FakeMarketService:
    def __init__(self, bars: list[KlineEvent]) -> None:
        self._source = _FakeSource(bars)

    def get_source(self, exchange: Any = None, market_type: Any = None, **extra: Any):
        return self._source

    def get_klines(
        self, symbol: str, exchange: Any = None, market_type: Any = None, **extra: Any
    ):
        return self._source.get_klines(symbol, extra.get("limit", 100))

    def get_symbols(self, exchange: Any = None, market_type: Any = None, **extra: Any):
        return self._source.get_symbols()

    async def add_symbol(
        self, symbol: str, exchange: Any = None, market_type: Any = None, **extra: Any
    ) -> None:
        await self._source.subscribe(symbol)


@asynccontextmanager
async def vchart_api(bars: list[KlineEvent]):
    app = create_app()
    app.dependency_overrides[get_market_service] = lambda: _FakeMarketService(bars)
    settings = SettingsService(secret_manager=_stub_secrets())
    app.dependency_overrides[get_settings_service] = lambda: settings
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as c:
        yield c


async def _get(
    url: str = "/api/v1/vchart?symbol=BTC/USDT&interval=1h&limit=30", bars=None
):
    async with vchart_api(
        bars if bars is not None else [_kline(i, b) for i, b in enumerate(_BARS)]
    ) as c:
        return await c.get(url)


@pytest.mark.asyncio
async def test_bars_carry_true_ohlcv_not_close_derived():
    resp = await _get()
    assert resp.status_code == 200
    data = resp.json()["data"]
    bars = data["bars"]
    assert len(bars) == len(_BARS)

    for bar, src in zip(bars, _BARS):
        assert bar["o"] == src["open"]
        assert bar["h"] == src["high"]
        assert bar["l"] == src["low"]
        assert bar["c"] == src["close"]
        assert bar["v"] == src["volume"]


@pytest.mark.asyncio
async def test_bar_timestamp_is_real_bar_time_not_synthetic_index():
    """The front-end previously used a fake ``T0..Tn`` category axis."""
    resp = await _get()
    data = resp.json()["data"]
    stamps = [b["t"] for b in data["bars"]]
    assert stamps == [BASE_TS, BASE_TS + 3_600_000, BASE_TS + 7_200_000]
    # ...and they agree with the legacy close_times field.
    assert stamps == data["close_times"]


@pytest.mark.asyncio
async def test_wicks_survive_the_round_trip():
    """A close-only payload cannot produce a bar whose high exceeds both open
    and close — that is exactly the fabrication this endpoint removed."""
    resp = await _get()
    bars = resp.json()["data"]["bars"]
    assert bars[1]["h"] > max(bars[1]["o"], bars[1]["c"])
    assert bars[2]["l"] < min(bars[2]["o"], bars[2]["c"])


@pytest.mark.asyncio
async def test_limit_truncates_bars_and_closes_together():
    # limit has ge=30, so seed more than that and ask for exactly 30.
    many = [
        _kline(
            i,
            {
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0 + i,
                "volume": 1.0,
            },
        )
        for i in range(40)
    ]
    resp = await _get("/api/v1/vchart?symbol=BTC/USDT&interval=1h&limit=30", bars=many)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["bars"]) == 30
    assert len(data["closes"]) == 30
    # limit keeps the most recent bars
    assert data["bars"][-1]["c"] == many[-1].close


@pytest.mark.asyncio
async def test_missing_data_is_404_not_empty_bars():
    resp = await _get(bars=[])
    assert resp.status_code == 404
