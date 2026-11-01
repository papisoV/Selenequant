"""Polymarket prediction-market schemas.

Markets are questions with binary outcomes; tokens are YES/NO positions
priced 0-1 (probability). Positions track shares held per outcome leg and
their redemption lifecycle after a market resolves.

Names track the venue payloads so raw responses validate directly; the
redemption models add local bookkeeping (success/error/aggregate counters)
on top.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

#: Venue serves prices as probabilities inside this band.
PROB_PRICE_MIN = 0.0
PROB_PRICE_MAX = 1.0


class PolymarketToken(BaseModel):
    """One YES/NO outcome leg of a market."""

    outcome: str = ""
    price: float = PROB_PRICE_MIN
    winner: bool = False
    token_id: str


class PolymarketMarket(BaseModel):
    condition_id: str
    question_id: str = ""
    question: str
    description: str = ""
    market_slug: str = ""
    end_date_iso: str | None = None
    active: bool = True
    closed: bool = False
    resolved: bool = False
    resolution_source: str = ""
    winning_outcome: str = ""
    tokens: list[PolymarketToken] = Field(default_factory=list)

    #: Traded/notional stats, USDC-denominated.
    volume: float = PROB_PRICE_MIN
    volume_24h: float = PROB_PRICE_MIN
    liquidity: float = PROB_PRICE_MIN

    def token_by_outcome(self, outcome: str) -> PolymarketToken | None:
        wanted = outcome.strip().lower()
        for entry in self.tokens:
            if entry.outcome.lower() == wanted:
                return entry
        return None

    @property
    def is_tradeable(self) -> bool:
        return self.active and not self.closed and not self.resolved


class PolymarketOrderBook(BaseModel):
    """String-priced book straight off the CLOB; deliberately uncoerced."""

    bids: list[dict[str, str]] = Field(default_factory=list)
    asks: list[dict[str, str]] = Field(default_factory=list)
    asset_id: str
    market: str = ""
    timestamp: str = ""
    last_trade_price: str = ""
    tick_size: str = "0.01"
    min_order_size: str = "1"

    def best_bid(self) -> float | None:
        return float(self.bids[0]["price"]) if self.bids else None

    def best_ask(self) -> float | None:
        return float(self.asks[0]["price"]) if self.asks else None


class PolymarketPrice(BaseModel):
    token_id: str
    price: float
    side: str = ""


class PolymarketPositionStatus(str, Enum):
    REDEEMED = "redeemed"
    OPEN = "open"
    CLOSED = "closed"
    REDEEMABLE = "redeemable"


class PolymarketPosition(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    condition_id: str = Field(description="Market condition ID")
    size: float = Field(0.0, ge=0)
    avg_price: float = Field(0.0, ge=0)
    current_price: float = Field(0.0, ge=0)
    cost_basis: float = Field(0.0, ge=0)
    current_value: float = Field(0.0, ge=0, description="Position worth in USDC right now")
    cash_pnl: float = Field(0.0, description="Realized PnL, USDC")
    percent_pnl: float = Field(0.0)

    asset_id: str = Field("", description="ERC-1155 token behind this leg")
    title: str = Field("", description="Question text of the market")
    outcome: str = Field("", description="Which side is held (YES/NO)")

    # Lifecycle flags stay independent of *status*: the venue tags them
    # separately and the auto-redeem sweep keys on each one.
    redeemable: bool = Field(False, description="True once the CTF allows burn")
    is_winner: bool = Field(False, description="Held the resolving side")
    status: PolymarketPositionStatus = PolymarketPositionStatus.OPEN


class RedemptionResult(BaseModel):
    condition_id: str = Field(description="Market condition ID")
    error: str = ""
    success: bool = False
    outcome: str = ""
    shares_redeemed: float = Field(0.0, ge=0, description="Shares burned")
    usdc_received: float = Field(0.0, ge=0, description="USDC.e paid out")
    transaction_hash: str = ""


class AutoRedeemSummary(BaseModel):
    results: list[RedemptionResult] = Field(default_factory=list)
    markets_checked: int = 0
    redeemable_found: int = 0
    redemptions_attempted: int = 0
    redemptions_succeeded: int = 0
    total_usdc_redeemed: float = 0.0

    def merge(self, result: RedemptionResult) -> None:
        self.results.append(result)
        if result.success:
            self.redemptions_succeeded += 1
            self.total_usdc_redeemed += result.usdc_received
