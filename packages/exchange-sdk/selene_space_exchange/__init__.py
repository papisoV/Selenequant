"""selene_space_exchange -- Native exchange WebSocket and REST adapters."""

from selene_space_exchange.base.auth import (
BinanceAuthenticator,
OKXAuthenticator,
BaseAuthenticator,
ExchangeCredentials,

)
from selene_space_exchange.base.gap_detector import WSGapDetector
from selene_space_exchange.base.rate_limiter import SlidingWindowRateLimiter
from selene_space_exchange.base.reconciliation import ReconciliationManager
from selene_space_exchange.base.reconnect import ReconnectManager
from selene_space_exchange.base.rest_client import BaseRESTClient
from selene_space_exchange.base.stall_watchdog import StallTimeoutMeta, StallWatchdog
from selene_space_exchange.base.ws_client import BaseWSClient

# -- exceptions ---------------------------------------------------------------

from selene_space_exchange.exceptions import (
ExchangeAPIError,
InvalidOrderError,
OrderRejectedError,
SequenceGapError,
StallTimeoutError,
WebSocketSubscriptionError,
AuthenticationError,
InsufficientBalanceError,
OrderNotFoundError,
RateLimitExceededError,
SnapshotRecoveryError,
WebSocketConnectionError,

)

# -- Binance adapters ------------------------------------------------------------

from selene_space_exchange.exchanges.binance.l2_manager import BinanceL2Manager
from selene_space_exchange.exchanges.binance.normalizer import (
    BinanceDepthDelta,
    BinanceNormalizer,
)
from selene_space_exchange.exchanges.binance.rest_client import (
BinanceRESTClient,
BinanceOrderType,
BinanceTimeInForce,

)
from selene_space_exchange.exchanges.binance.user_data_client import (
    BinanceUserDataClient,
    BinanceUserDataNormalizer,
)
from selene_space_exchange.exchanges.binance.ws_client import BinanceWSClient

# -- OKX adapters -------------------------------------------------------------------

from selene_space_exchange.exchanges.okx.normalizer import OKXNormalizer
from selene_space_exchange.exchanges.okx.private_client import (
    OKXPrivateNormalizer,
    OKXPrivateWSClient,
)
from selene_space_exchange.exchanges.okx.rest_client import (
OKXRESTClient,
OKXOrderType,
OKXTradeMode,

)
from selene_space_exchange.exchanges.okx.ws_client import OKXWSClient

# -- Polymarket adapters -----------------------------------------------------------------

from selene_space_exchange.exchanges.polymarket.client import PolymarketClient
from selene_space_exchange.exchanges.polymarket.models import (
PolymarketMarket,
PolymarketPosition,
PolymarketPrice,
AutoRedeemSummary,
PolymarketOrderBook,
PolymarketPositionStatus,
PolymarketToken,

)
from selene_space_exchange.exchanges.polymarket.redemption import (
    PolymarketRedemptionClient,
)
from selene_space_exchange.exchanges.polymarket.trading import (
PolymarketOrderType,
PolymarketTradingClient,
PolymarketCredentials,
PolymarketSide,

)
from selene_space_exchange.exchanges.polymarket.ws_client import (
PolymarketOrderEventType,
PolymarketWSClient,
PolymarketEventType,
PolymarketTradeStatus,

)

# -- execution layer -------------------------------------------------------------------------

from selene_space_exchange.execution.confirmation import OrderConfirmationManager, OrderPreview
from selene_space_exchange.execution.engine_registry import EngineRegistry
from selene_space_exchange.execution.live_engine import LiveExecutionEngine
from selene_space_exchange.execution.live_state import LiveStateRepository
from selene_space_exchange.execution.mode_guard import ExecutionModeGuard, ModeGuardError
from selene_space_exchange.execution.order_journal import JournalEntry, OrderJournal
from selene_space_exchange.execution.shadow_engine import ShadowExecutionEngine
from selene_space_exchange.execution.startup_reconciliation import (
    ReconciliationReport,
    StartupReconciler,
)

# -- shared plumbing ------------------------------------------------------------------------------

from selene_space_exchange.normalizers.symbol import ExchangeSymbolRule, SymbolNormalizer
from selene_space_exchange.routing import ExchangeInfo, RoutingDecision, SmartOrderRouter
from selene_space_exchange.trading import (
BinanceFuturesTradingAdapter,
OKXTradingAdapter,
OrderResponse,
TradingClient,
BalanceInfo,
BinanceTradingAdapter,
OrderRequest,
PolymarketTradingAdapter,

)
from selene_space_exchange.dex import (
DecibelTradingAdapter,
DEXMarket,
DEXPriceUpdate,
ExtendedTradingAdapter,
GridOrderInfo,
PerpetualDEXMixin,
RisexTradingAdapter,
DecibelConfig,
DEXFill,
DEXPosition,
ExtendedConfig,
GridMode,
OutOfRangeAction,
RisexConfig,
create_dex_adapter,

)

__all__ = [
    # base abstractions + plumbing
    "BaseAuthenticator",
    "BaseRESTClient",
    "BaseWSClient",
    "BinanceAuthenticator",
    "ExchangeCredentials",
    "OKXAuthenticator",
    "ReconciliationManager",
    "ReconnectManager",
    "SlidingWindowRateLimiter",
    "StallTimeoutMeta",
    "StallWatchdog",
    "SymbolNormalizer",
    "ExchangeSymbolRule",
    "WSGapDetector",
    # unified trading interface
    "BalanceInfo",
    "EngineRegistry",
    "ExecutionModeGuard",
    "JournalEntry",
    "LiveExecutionEngine",
    "LiveStateRepository",
    "ModeGuardError",
    "OrderConfirmationManager",
    "OrderJournal",
    "OrderPreview",
    "OrderRequest",
    "OrderResponse",
    "ReconciliationReport",
    "ShadowExecutionEngine",
    "StartupReconciler",
    "TradingClient",
    "BinanceFuturesTradingAdapter",
    "BinanceTradingAdapter",
    "OKXTradingAdapter",
    "PolymarketTradingAdapter",
    # binance
    "BinanceDepthDelta",
    "BinanceL2Manager",
    "BinanceNormalizer",
    "BinanceOrderType",
    "BinanceRESTClient",
    "BinanceTimeInForce",
    "BinanceUserDataClient",
    "BinanceUserDataNormalizer",
    "BinanceWSClient",
    # okx
    "OKXNormalizer",
    "OKXOrderType",
    "OKXPrivateNormalizer",
    "OKXPrivateWSClient",
    "OKXRESTClient",
    "OKXTradeMode",
    "OKXWSClient",
    # polymarket
    "AutoRedeemSummary",
    "PolymarketClient",
    "PolymarketCredentials",
    "PolymarketEventType",
    "PolymarketMarket",
    "PolymarketOrderBook",
    "PolymarketOrderEventType",
    "PolymarketPosition",
    "PolymarketPositionStatus",
    "PolymarketPrice",
    "PolymarketRedemptionClient",
    "PolymarketSide",
    "PolymarketToken",
    "PolymarketTradeStatus",
    "PolymarketTradingClient",
    "PolymarketWSClient",
    "RedemptionResult",
    # routing
    "ExchangeInfo",
    "RoutingDecision",
    "SmartOrderRouter",
    # dex adapters (Decibel / Extended / RISEx)
    "create_dex_adapter",
    "DecibelConfig",
    "DecibelTradingAdapter",
    "DEXFill",
    "DEXMarket",
    "DEXPosition",
    "DEXPriceUpdate",
    "ExtendedConfig",
    "ExtendedTradingAdapter",
    "GridMode",
    "GridOrderInfo",
    "OutOfRangeAction",
    "PerpetualDEXMixin",
    "RisexConfig",
    "RisexTradingAdapter",
    # errors
    "AuthenticationError",
    "ExchangeAPIError",
    "InsufficientBalanceError",
    "InvalidOrderError",
    "OrderNotFoundError",
    "OrderRejectedError",
    "RateLimitExceededError",
    "SequenceGapError",
    "SnapshotRecoveryError",
    "StallTimeoutError",
    "WebSocketConnectionError",
    "WebSocketSubscriptionError",
]
