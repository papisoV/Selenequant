"""selene_space_stock — Stock market signal scoring, indicators, and daily report engine.

Migrated from ai-market-pulse with Pydantic v2 model references via
selene_space_types.stock.
"""

from selene_space_stock.config import StockConfig, load_stock_config
from selene_space_stock.indicators import calculate_indicators
from selene_space_stock.market_data import MarketDataError, fetch_history
from selene_space_stock.scoring import score_asset

try:
    from selene_space_stock.engine import run_analysis
except ImportError:
    # engine depends on optional modules (news, llm); still export when available
    run_analysis = None  # type: ignore[assignment]

__all__ = [
    "MarketDataError",
    "StockConfig",
    "calculate_indicators",
    "fetch_history",
    "load_stock_config",
    "run_analysis",
    "score_asset",
]
