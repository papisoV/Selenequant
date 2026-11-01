"""Market data plane: ingestion, caching, storage, and live analytics.

The package's front door. ``MarketDataService`` wires exchange sources into
an internal event bus; around it sit the persistence layers (SQLite via
``KlineDB``, Redis via ``KlineStore``), the cache/store pair for hot books,
and the analytics engines (aggregators, regime classifier).
"""

from selene_space_market.aggregators import (
    AstockAnomalyDetector,
    LargeOrderDetector,
    LargeTradeDetector,
    LiquidationAggregator,
)
from selene_space_market.cache import MarketDataCache, TTLLRUCache
from selene_space_market.event_bus import EventBus
from selene_space_market.kline_db import KlineDB
from selene_space_market.kline_manager import KlineDataManager
from selene_space_market.service import (
    MarketDataService,
    MarketDataServiceError,
    MarketDataServiceNotRunning,
)
from selene_space_market.snapshot_store import SnapshotStore
from selene_space_market.source import ExchangeSource, ExchangeSourceConfig
from selene_space_market.sources import (
    AstockIntradaySource,
    AstockMarketSnapshotSource,
    AstockSource,
    BinanceSource,
    OKXSource,
    USStockSource,
)
from selene_space_market.state_engine import (
    InsufficientDataError,
    MarketStateEngine,
)
from selene_space_market.stream_manager import StreamManager, StreamType
from selene_space_market.tick_replay import Tick, TickReplay, TickType
from selene_space_market.versioning import (
    DataSnapshot,
    SnapshotStore as DataSnapshotStore,
    compute_data_hash,
    create_snapshot,
    verify_snapshot,
)

__all__ = [
    "AstockAnomalyDetector",
    "AstockIntradaySource",
    "AstockMarketSnapshotSource",
    "AstockSource",
    "BinanceSource",
    "DataSnapshot",
    "DataSnapshotStore",
    "EventBus",
    "ExchangeSource",
    "ExchangeSourceConfig",
    "InsufficientDataError",
    "KlineDB",
    "KlineDataManager",
    "LargeOrderDetector",
    "LargeTradeDetector",
    "LiquidationAggregator",
    "MarketDataCache",
    "MarketDataService",
    "MarketDataServiceError",
    "MarketDataServiceNotRunning",
    "MarketStateEngine",
    "OKXSource",
    "SnapshotStore",
    "StreamManager",
    "StreamType",
    "Tick",
    "TickReplay",
    "TickType",
    "TTLLRUCache",
    "USStockSource",
    "compute_data_hash",
    "create_snapshot",
    "verify_snapshot",
]
