"""``GET /stock-profile/{symbol}`` —— 信封、装配层绑定、块降级(📋6)。

与 ``test_api_skill_weights.py`` 同法:``dependency_overrides`` 换掉每个源。
这里只锁**路由层与装配层**(六个源怎么绑到块上、缺源如何降级、信封形状),
聚合器本身的口径在 ``test_stock_profile_service.py``。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from app.api.v1.stock_profile import (
    get_news_source,
    get_price_alert_source,
    router as profile_router,
)
from app.core.dependencies import (
    get_db_manager,
    get_market_service,
    get_paper_account_manager,
    get_paper_position_manager,
)
from app.main import create_app
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


class _FakeTicker:
    def __init__(self, symbol: str, last_price: float):
        self.symbol = symbol
        self.last_price = last_price

    def model_dump(self) -> dict:
        return {"symbol": self.symbol, "last_price": self.last_price}


class _FakeKline:
    def __init__(self, ts: int):
        self.ts = ts

    def model_dump(self) -> dict:
        return {"ts": self.ts}


class _FakeMarketService:
    def __init__(self, *, ticker=None, klines=None):
        self._ticker = ticker
        self._klines = klines or []

    def get_ticker(self, symbol: str):
        return self._ticker

    def get_klines(self, symbol: str, *, limit: int):
        return self._klines[:limit]


class _FakePosition:
    def __init__(self, symbol: str):
        self.symbol = symbol


class _FakeAccount:
    def __init__(self, account_id: str):
        self.id = account_id


class _FakeAccountManager:
    def __init__(self, accounts):
        self._accounts = accounts

    def list_accounts(self):
        return self._accounts


class _FakePositionManager:
    def __init__(self, by_account):
        self._by_account = by_account

    def get_positions(self, account_id: str):
        return self._by_account.get(account_id, [])


class _FakeAlertSource:
    def __init__(self, alerts):
        self._alerts = alerts

    def list_alerts(self):
        return self._alerts


class _FakeNewsSource:
    def __init__(self, items):
        self._items = items

    def query_news(self, *, symbol=None, limit=50):
        return self._items[:limit]


def _client(
    *,
    db_manager=None,
    market_service=None,
    account_manager=None,
    position_manager=None,
    alert_source=None,
    news_source=None,
) -> AsyncClient:
    """Build a client whose every profile source is overridden.

    Defaults are ``None`` (all sources absent) so a test only passes the
    blocks it cares about. Parameters are explicit rather than ``**kwargs``
    so each maps to its dependency *object* — spreading a dict would key on
    the parameter name and silently miss the override.
    """
    app = create_app()
    app.dependency_overrides[get_db_manager] = lambda: db_manager
    app.dependency_overrides[get_market_service] = lambda: market_service
    app.dependency_overrides[get_paper_account_manager] = lambda: account_manager
    app.dependency_overrides[get_paper_position_manager] = lambda: position_manager
    app.dependency_overrides[get_price_alert_source] = lambda: alert_source
    app.dependency_overrides[get_news_source] = lambda: news_source
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest_asyncio.fixture
async def db():
    from selene_space_storage.migrations import MigrationRunner
    from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
    from selene_space_storage.sqlite import AsyncSQLiteManager

    runner = MigrationRunner(list(ALL_MIGRATIONS))
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as mgr:
        yield mgr


async def test_route_is_registered_and_returns_envelope():
    # assert on the router itself — the project idiom (see
    # test_api_mission_thesis.py), and independent of source wiring
    paths = [getattr(route, "path", None) for route in profile_router.routes]
    assert "/stock-profile/{symbol:path}" in paths


async def test_all_sources_absent_returns_unavailable_profile():
    async with _client() as client:
        resp = await client.get("/api/v1/stock-profile/AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    data = body["data"]
    assert data["symbol"] == "AAPL"
    assert data["evidence_quality"]["status"] == "unavailable"
    assert data["quote"]["limitations"] == ["quote_source_not_configured"]


async def test_blank_symbol_is_rejected_with_400():
    async with _client() as client:
        resp = await client.get("/api/v1/stock-profile/%20")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_PARAMETER"


async def test_quote_and_history_blocks_bind_to_market_service():
    market = _FakeMarketService(
        ticker=_FakeTicker("AAPL", 191.5),
        klines=[_FakeKline(1), _FakeKline(2), _FakeKline(3)],
    )
    async with _client(market_service=market) as client:
        resp = await client.get("/api/v1/stock-profile/aapl?history_days=2")
    data = resp.json()["data"]
    assert data["symbol"] == "AAPL"
    assert data["quote"]["status"] == "fresh"
    assert data["quote"]["data"]["last_price"] == 191.5
    assert data["history"]["status"] == "fresh"
    assert data["history"]["data"]["count"] == 2  # history_days capped the window


async def test_missing_ticker_degrades_quote_only():
    market = _FakeMarketService(ticker=None, klines=[_FakeKline(1)])
    async with _client(market_service=market) as client:
        resp = await client.get("/api/v1/stock-profile/AAPL")
    data = resp.json()["data"]
    assert data["quote"]["status"] == "unavailable"
    assert data["history"]["status"] == "fresh"


async def test_portfolio_block_reports_held_accounts():
    accounts = _FakeAccountManager([_FakeAccount("a1"), _FakeAccount("a2")])
    positions = _FakePositionManager(
        {"a1": [_FakePosition("AAPL")], "a2": [_FakePosition("MSFT")]}
    )
    async with _client(account_manager=accounts, position_manager=positions) as client:
        resp = await client.get("/api/v1/stock-profile/AAPL")
    portfolio = resp.json()["data"]["portfolio"]
    assert portfolio["status"] == "partial"
    assert portfolio["data"] == {"held": True, "account_ids": ["a1"]}
    assert portfolio["limitations"] == ["paper_accounts_only"]


async def test_monitors_block_counts_matching_alerts_only():
    alerts = _FakeAlertSource(
        [
            {"id": 1, "symbol": "AAPL"},
            {"id": 2, "symbol": "MSFT"},
            {"id": 3, "symbol": "aapl"},
        ]
    )
    async with _client(alert_source=alerts) as client:
        resp = await client.get("/api/v1/stock-profile/AAPL")
    monitors = resp.json()["data"]["monitors"]
    assert monitors["status"] == "fresh"
    assert monitors["data"]["total_alert_count"] == 2
    assert monitors["data"]["alert_ids"] == [1, 3]


async def test_intelligence_block_reads_news_store():
    news = _FakeNewsSource([{"id": 1, "title": "AAPL beats"}, {"id": 2}])
    async with _client(news_source=news) as client:
        resp = await client.get("/api/v1/stock-profile/AAPL")
    intelligence = resp.json()["data"]["intelligence"]
    assert intelligence["status"] == "fresh"
    assert intelligence["data"]["items"][0]["title"] == "AAPL beats"


async def test_research_block_reads_analysis_records(db):
    from selene_space_storage.repositories.analysis_records import (
        AnalysisRecordRepository,
    )

    repo = AnalysisRecordRepository(db)
    record_id = await repo.save(
        {
            "input_id": "in-1",
            "input_data": {"symbol": "AAPL", "interval": "1d"},
            "record_json": {"meta": {}, "decision": "buy"},
        }
    )
    async with _client(db_manager=db) as client:
        resp = await client.get("/api/v1/stock-profile/AAPL")
    research = resp.json()["data"]["research"]
    assert research["status"] == "fresh"
    assert len(research["data"]["recent_records"]) == 1
    assert research["data"]["latest_record"]["meta"]["record_id"] == record_id


async def test_research_block_is_unavailable_without_matching_records(db):
    async with _client(db_manager=db) as client:
        resp = await client.get("/api/v1/stock-profile/ZZZZ")
    research = resp.json()["data"]["research"]
    assert research["status"] == "unavailable"
    assert research["limitations"] == ["research_unavailable"]


async def test_evidence_quality_reflects_mixed_sources():
    market = _FakeMarketService(ticker=_FakeTicker("AAPL", 191.5))
    async with _client(market_service=market) as client:
        resp = await client.get("/api/v1/stock-profile/AAPL")
    quality = resp.json()["data"]["evidence_quality"]
    assert quality["status"] == "partial"
    assert quality["blocks"]["quote"] == "fresh"
    assert quality["blocks"]["intelligence"] == "unavailable"
