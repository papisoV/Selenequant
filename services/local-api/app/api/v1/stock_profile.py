# Absorbed from daily_stock_analysis (MIT License)
# Copyright (c) 2026 ZhuLinsen
# Source: https://github.com/ZhuLinsen/daily_stock_analysis
# Adapted for SeleneQuant (AGPL-3.0-only); selene-ized: DSA's monolithic
# ``StockProfileService`` is split into an IO-free aggregator (see
# ``app/services/stock_profile_service.py``) plus this router, which is the
# only place that binds Selene's real per-symbol sources to the six blocks.
"""``GET /stock-profile/{symbol}`` — one partial-data view of a symbol.

Each of the six blocks is bound to a Selene source that actually exists:
analysis records (research), the news store (intelligence), paper positions
(portfolio), in-memory price alerts (monitors), and the market-data service
(quote/history). A block whose source is absent or fails degrades to
``unavailable``; the profile as a whole still returns.

Every source is resolved through a ``Depends`` so the whole route can be
exercised with ``app.dependency_overrides`` — no monkeypatching of globals.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from app.core.dependencies import (
    build_response_meta,
    get_db_manager,
    get_market_service,
    get_paper_account_manager,
    get_paper_position_manager,
)
from app.services.stock_profile_service import (
    InvalidStockProfileSymbol,
    StockProfileService,
)
from selene_space_types.common import APIResponse
from selene_space_types.errors import ErrorCode, SeleneSpaceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stock-profile", tags=["stock-profile"])

#: How many recent analysis records / news items a block surfaces.
_RECENT_LIMIT = 5
_NEWS_LIMIT = 10

# local-api 进程内可直接导入 news-ingest 包(不依赖 pip install -e)。
# parents[5]: v1(0) → api(1) → app(2) → local-api(3) → services(4) → repo root(5)
_ROOT = Path(__file__).resolve().parents[5]
if str(_ROOT / "packages" / "news-ingest") not in sys.path:
    sys.path.insert(0, str(_ROOT / "packages" / "news-ingest"))


def get_price_alert_source() -> Any:
    """Resolve the in-memory price-alert registry (a ``Depends`` for tests)."""
    from app.core.price_alert_service import get_price_alert_service

    return get_price_alert_service()


def get_news_source() -> Any:
    """Resolve the news store (a ``Depends`` so tests stay hermetic)."""
    from selene_space_news_ingest.news_store import get_news_store

    return get_news_store()


def _build_service(
    *,
    db_manager: Any,
    market_service: Any,
    account_manager: Any,
    position_manager: Any,
    alert_source: Any,
    news_source: Any,
) -> StockProfileService:
    """Bind the six blocks to their Selene sources.

    A provider is only attached when its source is present; an absent source
    stays ``None`` so the aggregator reports ``*_source_not_configured``
    (distinct from a present-but-empty source, which reports ``*_unavailable``).
    Every provider is a closure over an injected dependency so the service
    itself stays import-free; heavy packages are imported lazily inside.
    """

    def quote_provider(symbol: str) -> Any:
        ticker = market_service.get_ticker(symbol)
        if ticker is None:
            return None
        return ticker.model_dump() if hasattr(ticker, "model_dump") else ticker

    def history_provider(symbol: str, *, history_days: int) -> Any:
        limit = max(1, min(int(history_days), 1000))
        events = market_service.get_klines(symbol, limit=limit)
        if not events:
            return None
        return {
            "interval": "1d",
            "count": len(events),
            "klines": [
                event.model_dump() if hasattr(event, "model_dump") else event
                for event in events
            ],
        }

    async def research_provider(symbol: str) -> Any:
        from selene_space_storage.repositories.analysis_records import (
            AnalysisRecordRepository,
        )

        records = await AnalysisRecordRepository(db_manager).list_records(
            symbol=symbol, limit=_RECENT_LIMIT
        )
        if not records:
            return None
        return {"latest_record": records[0], "recent_records": records}

    async def intelligence_provider(symbol: str) -> Any:
        # The store is synchronous and blocking; keep the loop responsive.
        items = await asyncio.to_thread(
            news_source.query_news, symbol=symbol, limit=_NEWS_LIMIT
        )
        if not items:
            return None
        return {"items": items}

    def portfolio_provider(symbol: str) -> Any:
        matched: list[str] = []
        for account in account_manager.list_accounts():
            for position in position_manager.get_positions(account.id):
                if str(getattr(position, "symbol", "")).upper() == symbol:
                    matched.append(str(account.id))
        return {
            "status": "partial",
            "data": {"held": bool(matched), "account_ids": sorted(set(matched))},
            "limitations": ["paper_accounts_only"],
        }

    def monitors_provider(symbol: str) -> Any:
        alerts = [
            alert
            for alert in alert_source.list_alerts()
            if str(alert.get("symbol", "")).upper() == symbol
        ]
        return {
            "total_alert_count": len(alerts),
            "alert_ids": [int(a["id"]) for a in alerts if a.get("id") is not None],
        }

    return StockProfileService(
        quote_provider=quote_provider if market_service is not None else None,
        history_provider=history_provider if market_service is not None else None,
        research_provider=research_provider if db_manager is not None else None,
        intelligence_provider=(
            intelligence_provider if news_source is not None else None
        ),
        portfolio_provider=(
            portfolio_provider
            if account_manager is not None and position_manager is not None
            else None
        ),
        monitors_provider=monitors_provider if alert_source is not None else None,
    )


@router.get("/{symbol:path}")
async def get_stock_profile(
    symbol: str,
    request: Request,
    history_days: int = Query(60, ge=1, le=1000, description="History window in days"),
    db_manager: Any = Depends(get_db_manager),
    market_service: Any = Depends(get_market_service),
    account_manager: Any = Depends(get_paper_account_manager),
    position_manager: Any = Depends(get_paper_position_manager),
    alert_source: Any = Depends(get_price_alert_source),
    news_source: Any = Depends(get_news_source),
) -> APIResponse[dict[str, Any]]:
    """Aggregate a six-block profile for *symbol* under a partial-data contract.

    Inspect ``evidence_quality.status`` first (``fresh`` / ``partial`` /
    ``unavailable``), then read only the blocks whose status you need.
    """
    service = _build_service(
        db_manager=db_manager,
        market_service=market_service,
        account_manager=account_manager,
        position_manager=position_manager,
        alert_source=alert_source,
        news_source=news_source,
    )
    try:
        profile = await service.get_profile(symbol, history_days=history_days)
    except InvalidStockProfileSymbol as exc:
        raise SeleneSpaceError(
            code=ErrorCode.INVALID_PARAMETER, message=str(exc)
        ) from exc
    return APIResponse(data=profile, meta=build_response_meta(request), error=None)


__all__ = ["router"]
