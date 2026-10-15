"""AI agent collaboration models: roles, market perception, intents, chat.

The multi-agent pipeline perceives the market as a :class:`MarketState`, reasons
in chat via :class:`ChatMessage`, and commits decisions as a
:class:`TradeIntent`. Streaming responses from an agent run are delivered as a
sequence of :class:`AgentStreamEvent` chunks.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from selene_space_types.trading import OrderSide, OrderType


class AgentRole(str, Enum):
    """The four specialist roles in the strategy-development agent team."""

    MARKET_ANALYST = "market_analyst"
    STRATEGY_ARCHITECT = "strategy_architect"
    RISK_GUARDIAN = "risk_guardian"
    STRATEGY_CODER = "strategy_coder"


class MarketRegime(str, Enum):
    """Coarse classification of current market behavior."""

    TRENDING = "trending"
    RANGING = "ranging"
    VOLATILE = "volatile"


class AgentStreamEventType(str, Enum):
    """Chunk kinds emitted while an agent streams a response."""

    TEXT_DELTA = "text_delta"
    THINKING = "thinking"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    REFLECTION = "reflection"
    DONE = "done"
    # Emitted before a state-writing tool runs, so the client can render an
    # approval prompt. The run pauses until POST /agent/approve resolves it.
    TOOL_APPROVAL_REQUEST = "tool_approval_request"


class TradeIntent(BaseModel):
    """A concrete trade proposal produced by an agent decision step.

    Not yet an order -- must pass risk validation first. ``price`` is the
    optional limit target (None means market execution).

    P1-4: ``leverage`` / ``reduce_only`` are optional execution-level hints
    carried through to live backends when the driver supports them; they
    default to conservative values so existing construction sites (risk
    pre-checks, daily-cycle, paper execution) remain source-compatible.
    """

    symbol: str
    side: OrderSide
    quantity: float
    price: float | None = None
    order_type: OrderType
    reasoning: str
    confidence: float
    risk_params: dict = {}
    timestamp: int
    leverage: int = 1
    reduce_only: bool = False


class MarketState(BaseModel):
    """An agent's summarized perception of one instrument at a point in time."""

    symbol: str
    regime: MarketRegime
    trend_strength: float
    volatility: float
    timestamp: int


class ChatMessage(BaseModel):
    """One turn in an agent conversation."""

    role: str
    content: str
    timestamp: int
    metadata: dict | None = None


class AgentStreamEvent(BaseModel):
    """One streamed chunk of an agent response."""

    type: AgentStreamEventType
    data: dict
    timestamp: int
