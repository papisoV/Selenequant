"""Market-specific risk rules for US stocks and A-shares.

These rules only apply to their designated market domain:
  - PDTRule (US_STOCK) — Pattern Day Trader rule
  - T1SettlementRule (A_SHARE) — T+1 settlement restriction
  - PriceLimitRule (A_SHARE) — Daily price limit (涨跌停)

Rules with empty supported_domains apply to all markets (backward compat).
The RiskEngine filters rules by domain before evaluation.
"""

from __future__ import annotations

import time
from datetime import timezone, timedelta
from typing import Any

from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskAlert, RiskLevel
from selene_space_types.routing import MarketDomain

_CST = timezone(timedelta(hours=8))


def _now_ms() -> int:
    return int(time.time() * 1000)


def _alert(rule_id: str, level: RiskLevel, message: str, **context: Any) -> RiskAlert:
    return RiskAlert(
        rule_id=rule_id,
        level=level,
        message=message,
        context=context,
        timestamp=_now_ms(),
    )


# ---------------------------------------------------------------------------
# PDT Rule (US Stock)
# ---------------------------------------------------------------------------


class PDTRule:
    """Pattern Day Trader rule for US stock accounts.

    FINRA rule: accounts with equity < $25,000 are limited to
    3 day trades within a rolling 5 business day window.
    A 4th day trade is blocked.

    Context keys:
        account_equity (float): Total account equity in USD.
        day_trades_5d (int): Number of day trades in last 5 business days.
    """

    supported_domains: list[MarketDomain] = [MarketDomain.US_STOCK]

    def __init__(
        self,
        min_equity: float = 25_000.0,
        max_day_trades: int = 3,
        *,
        enabled: bool = True,
    ) -> None:
        self._min_equity = min_equity
        self._max_day_trades = max_day_trades
        self._enabled = enabled

    @property
    def rule_id(self) -> str:
        return "pdt_rule"

    @property
    def enabled(self) -> bool:
        return self._enabled

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        equity = context.get("account_equity", 0)
        day_trades = context.get("day_trades_5d", 0)

        if equity >= self._min_equity:
            return None

        if day_trades < self._max_day_trades:
            return None

        return _alert(
            self.rule_id,
            RiskLevel.CRITICAL,
            f"PDT rule: account equity ${equity:,.0f} < ${self._min_equity:,.0f} "
            f"and {day_trades} day trades in 5 days (max {self._max_day_trades}). "
            f"Further day trades blocked.",
            account_equity=equity,
            day_trades_5d=day_trades,
        )


# ---------------------------------------------------------------------------
# T+1 Settlement Rule (A-Share)
# ---------------------------------------------------------------------------


class T1SettlementRule:
    """A-share T+1 settlement rule.

    In China's A-share market, shares purchased today cannot be
    sold until the next trading day (T+1 settlement).

    Context keys:
        today_purchases (dict[str, float]): symbol → quantity bought today.
    """

    supported_domains: list[MarketDomain] = [MarketDomain.A_SHARE]

    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = enabled

    @property
    def rule_id(self) -> str:
        return "t1_settlement"

    @property
    def enabled(self) -> bool:
        return self._enabled

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        # Only applies to sell orders
        from selene_space_types.trading import OrderSide
        if intent.side != OrderSide.SELL:
            return None

        today_purchases: dict[str, float] = context.get("today_purchases", {})
        bought_today = today_purchases.get(intent.symbol, 0)

        if bought_today <= 0:
            return None

        return _alert(
            self.rule_id,
            RiskLevel.CRITICAL,
            f"A-share T+1 rule: cannot sell {intent.symbol} — "
            f"{bought_today} shares purchased today. "
            f"Shares available for sale starting next trading day.",
            symbol=intent.symbol,
            purchased_today=bought_today,
        )


# ---------------------------------------------------------------------------
# Price Limit Rule (A-Share)
# ---------------------------------------------------------------------------


class PriceLimitRule:
    """A-share daily price limit rule (涨跌停).

    Main board: ±10% from previous close.
    ChiNext (创业板, 300xxx.SZ) / STAR (科创板, 688xxx.SH): ±20%.
    ST stocks: ±5%.

    Context keys:
        prev_close (dict[str, float]): symbol → previous day's close price.
        stock_names (dict[str, str]): symbol → stock name (for ST detection).
    """

    supported_domains: list[MarketDomain] = [MarketDomain.A_SHARE]

    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = enabled

    @property
    def rule_id(self) -> str:
        return "ashare_price_limit"

    @property
    def enabled(self) -> bool:
        return self._enabled

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        # Only applies to limit orders with a price
        if intent.price is None or intent.price <= 0:
            return None

        prev_closes: dict[str, float] = context.get("prev_close", {})
        prev_close = prev_closes.get(intent.symbol)
        if prev_close is None or prev_close <= 0:
            return None

        limit_pct = self._get_limit_pct(intent.symbol, context)
        limit_up = prev_close * (1 + limit_pct)
        limit_down = prev_close * (1 - limit_pct)

        if intent.price > limit_up:
            return _alert(
                self.rule_id,
                RiskLevel.CRITICAL,
                f"A-share price limit: {intent.symbol} order price {intent.price:.2f} "
                f"exceeds daily limit-up {limit_up:.2f} (prev close {prev_close:.2f}, "
                f"limit ±{limit_pct:.0%})",
                symbol=intent.symbol,
                order_price=intent.price,
                limit_up=limit_up,
                prev_close=prev_close,
            )

        if intent.price < limit_down:
            return _alert(
                self.rule_id,
                RiskLevel.CRITICAL,
                f"A-share price limit: {intent.symbol} order price {intent.price:.2f} "
                f"below daily limit-down {limit_down:.2f} (prev close {prev_close:.2f}, "
                f"limit ±{limit_pct:.0%})",
                symbol=intent.symbol,
                order_price=intent.price,
                limit_down=limit_down,
                prev_close=prev_close,
            )

        return None

    @staticmethod
    def _get_limit_pct(symbol: str, context: dict[str, Any]) -> float:
        """Determine the daily price limit percentage for a symbol."""
        stock_names: dict[str, str] = context.get("stock_names", {})
        name = stock_names.get(symbol, "")

        # ST stocks: ±5%
        if name.startswith("ST") or name.startswith("*ST"):
            return 0.05

        # ChiNext (300xxx.SZ) or STAR (688xxx.SH): ±20%
        parts = symbol.split(".")
        if len(parts) == 2:
            code, suffix = parts[0], parts[1].upper()
            if (code.startswith("300") and suffix == "SZ") or \
               (code.startswith("688") and suffix == "SH"):
                return 0.20

        # Main board: ±10%
        return 0.10


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_market_rules() -> list:
    """Create all market-specific risk rules."""
    return [
        PDTRule(),
        T1SettlementRule(),
        PriceLimitRule(),
    ]
