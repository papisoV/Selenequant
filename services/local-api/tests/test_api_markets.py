"""Market data endpoints: symbols, ticker, kline, orderbook + source routing.

The API resolves one market service that fans out to per-venue sources keyed
by ``(exchange, market_type)``. These tests wire a fake service through the
dependency override layer and pin four behaviours:

1. symbol listing aggregates names and reports a count
2. ticker/kline/orderbook reads return the seeded payload shapes
3. explicit ``exchange`` / ``market_type`` query params route to the right
   source; unknown venues are 400, unregistered slots are 503
4. depth truncation and source-field echoing on both listing endpoints
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest
from app.core.dependencies import get_market_service, get_settings_service
from app.core.settings_service import SettingsService
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from selene_space_types.market import (
    KlineEvent,
    OrderBookL2Snapshot,
    PriceLevel,
    TickerEvent,
)

TS = 1700000000000


def _stub_secrets() -> Any:
    """Minimal secret-manager stand-in for SettingsService wiring."""

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


class _FakeSource:
    """One venue slot holding quotes, bars, books and subscribed symbols.

    Storage is plain dicts keyed by symbol; the getters mirror the real
    source protocol (sync reads + async subscribe) so the endpoint code path
    under test is the production one.
    """

    def __init__(self) -> None:
        self.symbols: list[str] = []
        self.quotes: dict[str, TickerEvent] = {}
        self.bars: dict[str, KlineEvent] = {}
        self.books: dict[str, OrderBookL2Snapshot] = {}

    def get_symbols(self) -> list[str]:
        return list(self.symbols)

    def get_ticker(self, symbol: str) -> TickerEvent | None:
        return self.quotes.get(symbol)

    def get_kline(self, symbol: str) -> KlineEvent | None:
        return self.bars.get(symbol)

    def get_orderbook(self, symbol: str) -> OrderBookL2Snapshot | None:
        return self.books.get(symbol)

    async def subscribe(self, symbol: str, **kwargs: Any) -> None:
        if symbol not in self.symbols:
            self.symbols.append(symbol)


class FakeMarketService:
    """Fans every lookup out to the source registered for the venue slot.

    Venue selection accepts EITHER positional ``(exchange, market_type)`` or
    keyword form, mirroring how the endpoint layer invokes the service; absent
    values fall back to binance spot.
    """

    DEFAULT_VENUE = ("binance", "spot")

    def __init__(self) -> None:
        self.sources: dict[tuple[str, str], _FakeSource] = {}

    def _slot_of(
        self, exchange: Any = None, market_type: Any = None, **extra: Any
    ) -> tuple[str, str]:
        exch = exchange if isinstance(exchange, str) else self.DEFAULT_VENUE[0]
        kind = market_type if isinstance(market_type, str) else self.DEFAULT_VENUE[1]
        return (exch, kind)

    def _read(self, read, exchange: Any = None, market_type: Any = None, **extra: Any):
        src = self.sources.get(self._slot_of(exchange, market_type))
        return read(src) if src is not None else None

    def get_source(self, exchange: Any = None, market_type: Any = None, **extra: Any):
        return self.sources.get(self._slot_of(exchange, market_type))

    def get_symbols(
        self, exchange: Any = None, market_type: Any = None, **extra: Any
    ) -> list[str]:
        found = self.sources.get(self._slot_of(exchange, market_type))
        if found is None:
            return []
        return found.get_symbols()

    def get_ticker(
        self, symbol: str, exchange: Any = None, market_type: Any = None, **extra: Any
    ) -> TickerEvent | None:
        return self._read(lambda s: s.get_ticker(symbol), exchange, market_type)

    def get_kline(
        self, symbol: str, exchange: Any = None, market_type: Any = None, **extra: Any
    ) -> KlineEvent | None:
        return self._read(lambda s: s.get_kline(symbol), exchange, market_type)

    def get_orderbook(
        self, symbol: str, exchange: Any = None, market_type: Any = None, **extra: Any
    ) -> OrderBookL2Snapshot | None:
        return self._read(lambda s: s.get_orderbook(symbol), exchange, market_type)

    async def add_symbol(
        self, symbol: str, exchange: Any = None, market_type: Any = None, **extra: Any
    ) -> None:
        src = self.sources.get(self._slot_of(exchange, market_type))
        if src is not None:
            await src.subscribe(symbol)

    async def fetch_klines_rest(
        self,
        symbol: str,
        exchange: Any = None,
        market_type: Any = None,
        *,
        interval: str = "1h",
        limit: int = 100,
        end_time: int | None = None,
        **extra: Any,
    ) -> list[KlineEvent]:
        bar = self.get_kline(symbol, exchange, market_type)
        return [bar] if bar else []


def _venue() -> dict[str, str]:
    """Common origin fields shared by every seeded payload."""
    return {"exchange": "binance", "market_type": "spot"}


def _quote() -> TickerEvent:
    body: dict[str, Any] = {**_venue(), "symbol": "BTC/USDT", "timestamp": TS}
    body.update(last_price=67000.0, bid=66999.0, ask=67001.0)
    body.update(volume_24h=1000.0, change_24h_pct=1.5)
    return TickerEvent(**body)


def _bar() -> KlineEvent:
    ohlc = {"open": 66500.0, "high": 67200.0, "low": 66400.0, "close": 67000.0}
    body: dict[str, Any] = {
        **_venue(),
        "symbol": "BTC/USDT",
        "timestamp": TS,
        "interval": "1h",
        **ohlc,
    }
    body.update(volume=500.0, closed=True)
    return KlineEvent(**body)


def _book() -> OrderBookL2Snapshot:
    ladder_bids = [PriceLevel(price=66999.0 - i, quantity=1.0) for i in range(25)]
    ladder_asks = [PriceLevel(price=67001.0 + i, quantity=1.0) for i in range(25)]
    return OrderBookL2Snapshot(
        bids=ladder_bids,
        asks=ladder_asks,
        sequence_id=100,
        timestamp=TS,
        symbol="BTC/USDT",
        **_venue(),
    )


def _seeded_service() -> FakeMarketService:
    """One fake source answering for both spot and futures binance slots."""
    one = _FakeSource()
    one.symbols.extend(["ETH/USDT", "BTC/USDT"])
    one.quotes["BTC/USDT"] = _quote()
    one.bars["BTC/USDT"] = _bar()
    one.books["BTC/USDT"] = _book()

    svc = FakeMarketService()
    svc.sources[("binance", "spot")] = one
    svc.sources[("binance", "futures")] = one
    return svc


@asynccontextmanager
async def markets_api(service: Any):
    """App wired with `service` (or none at all); yields a bound client."""
    app = create_app()
    if service is not None:
        app.dependency_overrides[get_market_service] = lambda: service
    settings = SettingsService(secret_manager=_stub_secrets())
    app.dependency_overrides[get_settings_service] = lambda: settings
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as c:
        yield c


async def _hit(service: Any, url: str) -> Any:
    """Issue one GET against the overridden service, errors swallowed."""
    async with markets_api(service) as c:
        return await c.get(url)


@pytest.mark.asyncio
async def test_symbol_listing_counts_and_names():
    resp = await _hit(_seeded_service(), "/api/v1/markets")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["count"] == 2
    assert set(body["data"]["symbols"]) == {"BTC/USDT", "ETH/USDT"}


@pytest.mark.asyncio
async def test_ticker_returns_last_price():
    resp = await _hit(_seeded_service(), "/api/v1/markets/BTC-USDT/ticker")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["symbol"] == "BTC/USDT"
    assert body["data"]["last_price"] == 67000.0


@pytest.mark.asyncio
async def test_explicit_source_query_is_accepted():
    resp = await _hit(
        _seeded_service(),
        "/api/v1/markets/BTC-USDT/ticker?exchange=binance&market_type=spot",
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_unknown_exchange_in_query_is_400():
    resp = await _hit(
        _seeded_service(),
        "/api/v1/markets/BTC-USDT/ticker?exchange=bybit&market_type=spot",
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_PARAMETER"


@pytest.mark.asyncio
async def test_source_without_registration_is_503():
    resp = await _hit(
        _seeded_service(),
        "/api/v1/markets/BTC-USDT/ticker?exchange=okx&market_type=futures",
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_absent_symbol_errors_out():
    resp = await _hit(_seeded_service(), "/api/v1/markets/DOGE-USDT/ticker")
    assert resp.status_code in (404, 500)


class _RefusingService(FakeMarketService):
    """Every auto-subscribe attempt raises, as when the venue refuses the frame."""

    def __init__(self, *, fail_times: int | None = None) -> None:
        super().__init__()
        self.subscribe_attempts = 0
        self.fail_times = fail_times  # None = always fail

    async def add_symbol(
        self, symbol: str, exchange: Any = None, market_type: Any = None, **extra: Any
    ) -> None:
        self.subscribe_attempts += 1
        if self.fail_times is None or self.subscribe_attempts <= self.fail_times:
            raise ConnectionError("received 1008 (policy violation) Too many requests")


def _refusing_service(
    *, fail_times: int | None = None, seed_quote: bool = False
) -> _RefusingService:
    svc = _RefusingService(fail_times=fail_times)
    one = _FakeSource()
    if seed_quote:
        # Quote present, but the symbol is NOT pre-subscribed — otherwise
        # ``_ensure_subscribed`` would short-circuit and never attempt a
        # subscribe at all, which is the path under test.
        one.quotes["BTC/USDT"] = _quote()
    svc.sources[("binance", "spot")] = one
    svc.sources[("binance", "futures")] = one
    return svc


@pytest.mark.asyncio
async def test_subscribe_failure_reports_503_not_a_misleading_404():
    """A refused auto-subscribe must surface as 503 with the real cause.

    2026-10 incident: ``_ensure_subscribed`` swallowed the venue rejection into
    ``logger.debug``, so the request flowed on and answered 404 "No ticker data
    for symbol" — pointing the operator at a missing symbol instead of a broken
    subscription. A 404 must mean "subscribed, no tick yet"; a failed subscribe
    is a service failure (503).
    """
    svc = _refusing_service()
    resp = await _hit(svc, "/api/v1/markets/DOGE-USDT/ticker")

    assert (
        svc.subscribe_attempts == 3
    ), f"expected one try plus two bounded retries, got {svc.subscribe_attempts}"
    assert resp.status_code == 503, f"got {resp.status_code}: {resp.json()}"
    assert resp.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    # The real cause must survive into the message, not just the status code.
    assert "Too many requests" in resp.json()["error"]["message"]


@pytest.mark.asyncio
async def test_transient_subscribe_failure_is_retried_before_giving_up():
    """One refusal is transient (rate limits decay); the request should still land."""
    svc = _refusing_service(fail_times=1, seed_quote=True)
    resp = await _hit(svc, "/api/v1/markets/BTC-USDT/ticker")

    assert svc.subscribe_attempts >= 2, "no retry after the first refusal"
    assert resp.status_code == 200
    assert resp.json()["data"]["last_price"] == 67000.0


class _GatedService(FakeMarketService):
    """Auto-subscribe blocks on a gate so concurrent requests can be observed."""

    def __init__(self) -> None:
        super().__init__()
        self.subscribe_attempts = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def add_symbol(
        self, symbol: str, exchange: Any = None, market_type: Any = None, **extra: Any
    ) -> None:
        self.subscribe_attempts += 1
        self.entered.set()
        await self.release.wait()
        src = self.sources.get(self._slot_of(exchange, market_type))
        if src is not None:
            await src.subscribe(symbol)


@pytest.mark.asyncio
async def test_concurrent_reads_share_one_subscribe_attempt():
    """The chart pane fires ticker + kline + orderbook at once for one symbol.

    Each firing its own subscribe (and its own retry ladder) multiplies the very
    burst that trips the venue's rate limit. One attempt per (venue, symbol) must
    be shared by all co-waiters.
    """
    svc = _GatedService()
    one = _FakeSource()
    one.quotes["BTC/USDT"] = _quote()
    svc.sources[("binance", "spot")] = one
    svc.sources[("binance", "futures")] = one

    async with markets_api(svc) as c:
        url = "/api/v1/markets/BTC-USDT/ticker"
        pending = [asyncio.create_task(c.get(url)) for _ in range(3)]
        await svc.entered.wait()
        # Let the other two reach the in-flight lookup while the first is held.
        await asyncio.sleep(0.2)
        while_gated = svc.subscribe_attempts
        svc.release.set()
        responses = await asyncio.gather(*pending)

    assert (
        while_gated == 1
    ), f"concurrent reads multiplied the venue burst ({while_gated} attempts)"
    assert all(r.status_code == 200 for r in responses)


@pytest.mark.asyncio
async def test_subscribed_symbol_without_a_tick_is_still_404():
    """The legitimate 404 must survive: already subscribed, but no tick yet.

    Without this, "fix the misleading 404" would silently turn a real
    "waiting for first tick" into a 503 and hide a different fault.
    """
    svc = _seeded_service()  # ETH/USDT subscribed, but no quote seeded
    resp = await _hit(svc, "/api/v1/markets/ETH-USDT/ticker")

    assert resp.status_code == 404
    assert "No ticker data" in resp.json()["error"]["message"]


@pytest.mark.asyncio
async def test_kline_payload_carries_bars():
    resp = await _hit(
        _seeded_service(), "/api/v1/markets/BTC-USDT/kline?interval=1h&limit=50"
    )
    assert resp.status_code == 200
    body = resp.json()
    bars = body["data"]["klines"]
    assert len(bars) == 1
    assert body["data"]["symbol"] == "BTC/USDT"
    assert bars[0]["close"] == 67000.0


@pytest.mark.asyncio
async def test_orderbook_depth_is_truncated_to_request():
    resp = await _hit(_seeded_service(), "/api/v1/markets/BTC-USDT/orderbook?depth=5")
    assert resp.status_code == 200
    sides = resp.json()["data"]
    assert len(sides["bids"]) == 5
    assert len(sides["asks"]) == 5


@pytest.mark.asyncio
async def test_missing_service_dependency_fails_request():
    resp = await _hit(None, "/api/v1/markets")
    assert resp.status_code in (500, 503)


class _RecordingService(FakeMarketService):
    """FakeMarketService that records the kwargs of each ``fetch_klines_rest``.

    Used to pin the F4 fix: the REST fallback must forward ``since`` as the
    exchange ``start_time`` param, or a hole older than the endpoint's rolling
    window can never be fetched back.
    """

    def __init__(self) -> None:
        super().__init__()
        self.rest_calls: list[dict[str, Any]] = []

    async def fetch_klines_rest(
        self, symbol: str, exchange: Any = None, market_type: Any = None, **extra: Any
    ) -> list[KlineEvent]:
        self.rest_calls.append(
            {
                "symbol": symbol,
                "exchange": exchange,
                "market_type": market_type,
                **extra,
            }
        )
        return [_bar()]


def _seeded_recording_service() -> _RecordingService:
    svc = _RecordingService()
    one = _FakeSource()
    one.symbols.extend(["BTC/USDT"])
    one.bars["BTC/USDT"] = _bar()
    svc.sources[("binance", "spot")] = one
    svc.sources[("binance", "futures")] = one
    return svc


@pytest.mark.asyncio
async def test_kline_fallback_forwards_since_as_start_time():
    # F4 regression: the REST fallback must pass start_time=since so holes
    # older than the rolling window are reachable. We force the fallback by
    # requesting an interval with no local/cache hit.
    svc = _seeded_recording_service()
    since = TS - 5 * 3_600_000
    resp = await _hit(
        svc, f"/api/v1/markets/BTC-USDT/kline?interval=1h&limit=50&since={since}"
    )
    assert resp.status_code == 200
    assert svc.rest_calls, "REST fallback was not exercised"
    call = svc.rest_calls[0]
    assert call.get("start_time") == since


@pytest.mark.asyncio
async def test_kline_fallback_start_time_none_without_since():
    svc = _seeded_recording_service()
    resp = await _hit(svc, "/api/v1/markets/BTC-USDT/kline?interval=1h&limit=50")
    assert resp.status_code == 200
    assert svc.rest_calls, "REST fallback was not exercised"
    assert svc.rest_calls[0].get("start_time") is None


@pytest.mark.asyncio
async def test_kline_response_echoes_source_fields():
    resp = await _hit(
        _seeded_service(),
        "/api/v1/markets/BTC-USDT/kline?interval=1h&exchange=binance&market_type=spot",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["market_type"] == "spot"
    assert body["data"]["exchange"] == "binance"


@pytest.mark.asyncio
async def test_symbol_listing_echoes_source_fields():
    resp = await _hit(
        _seeded_service(), "/api/v1/markets?exchange=binance&market_type=spot"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["market_type"] == "spot"
    assert body["data"]["exchange"] == "binance"


# --- A-share intraday L1 outage ledger (S2) ---------------------------------
#
# The ledger's read side. Rows are written by the poll source into klines.db's
# own `l1_snapshot_gaps` table; this endpoint is what makes them visible to an
# operator. The DB path is redirected with SELENE_KLINES_DB so the test never
# touches the real klines.db.


def _write_l1_gaps(path: str) -> None:
    from selene_space_market.kline_db import KlineDB

    db = KlineDB(path)
    db.open()
    try:
        # A closed episode carrying a volume bracket (Option A′), and one still
        # open (process died mid-outage) with no bracket to report.
        db.record_l1_gap(
            source="astock_intraday",
            opened_at_ms=1_700_000_000_000,
            closed_at_ms=1_700_000_030_000,
            duration_sec=30.0,
            symbols=["600519.SH"],
            volume_before=1000.0,
            volume_after=1750.0,
            volume_symbols=1,
        )
        db.record_l1_gap(
            source="astock_intraday",
            opened_at_ms=1_700_020_000_000,
            closed_at_ms=None,
            duration_sec=None,
            symbols=["300750.SZ"],
        )
    finally:
        db.close()


@pytest.mark.asyncio
async def test_l1_gaps_lists_recorded_outages(tmp_path, monkeypatch):
    db_path = tmp_path / "klines.db"
    _write_l1_gaps(str(db_path))
    monkeypatch.setenv("SELENE_KLINES_DB", str(db_path))

    resp = await _hit(_seeded_service(), "/api/v1/markets/l1-gaps")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["count"] == 2
    assert data["open_count"] == 1
    # Most-recent first.
    assert data["episodes"][0]["opened_at_ms"] == 1_700_020_000_000
    assert data["episodes"][0]["is_open"] is True
    assert data["episodes"][1]["symbols"] == ["600519.SH"]
    assert data["episodes"][1]["duration_sec"] == 30.0
    # The A′ bracket rides along, with the delta precomputed for the panel.
    assert data["episodes"][1]["volume_before"] == 1000.0
    assert data["episodes"][1]["volume_after"] == 1750.0
    assert data["episodes"][1]["volume_delta"] == 750.0
    assert data["episodes"][1]["volume_symbols"] == 1
    # An unbracketed (still-open) episode reports null, not zero.
    assert data["episodes"][0]["volume_delta"] is None
    assert data["episodes"][0]["volume_symbols"] == 0


@pytest.mark.asyncio
async def test_l1_gaps_since_filters(tmp_path, monkeypatch):
    db_path = tmp_path / "klines.db"
    _write_l1_gaps(str(db_path))
    monkeypatch.setenv("SELENE_KLINES_DB", str(db_path))

    resp = await _hit(
        _seeded_service(),
        "/api/v1/markets/l1-gaps?since=1700010000000",
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["count"] == 1
    assert data["episodes"][0]["opened_at_ms"] == 1_700_020_000_000


@pytest.mark.asyncio
async def test_l1_gaps_empty_when_no_outages(tmp_path, monkeypatch):
    db_path = tmp_path / "klines.db"
    from selene_space_market.kline_db import KlineDB

    KlineDB(str(db_path)).open()  # creates the schema, no rows
    monkeypatch.setenv("SELENE_KLINES_DB", str(db_path))

    resp = await _hit(_seeded_service(), "/api/v1/markets/l1-gaps")
    assert resp.status_code == 200
    assert resp.json()["data"] == {"episodes": [], "count": 0, "open_count": 0}
