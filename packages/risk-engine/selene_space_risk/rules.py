"""Pre-trade guard rails speaking the RiskRuleProtocol dialect.

Each built-in rule poses exactly one risk question about an incoming
TradeIntent and replies with at most one :class:`RiskAlert` — ``None`` means
"no objection". Only SymbolBlacklistRule may shout CRITICAL (the sole hard
block); every other member merely advises at a softer level.
"""

from __future__ import annotations

import math
import time
from typing import Any, ClassVar

from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskAlert, RiskLevel

__all__ = [
    "CooldownRule",
    "DailyLossLimitRule",
    "MaxPositionRule",
    "MaxSingleRiskRule",
    "RuleSet",
    "SymbolBlacklistRule",
    "create_default_rules",
]


def _clock_ms() -> int:
    """Current time as epoch milliseconds."""
    return round(time.time() * 1000)


def _to_number(raw: object) -> float | None:
    """Coerce to float when the value is genuinely numeric; otherwise None.

    bools are rejected outright even though Python counts them as ints.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    return None


class _toggle:
    """Descriptor mapping the public ``enabled`` flag onto ``_armed`` storage.

    Declaring it once on the shared base keeps five rule classes from repeating
    the same getter/setter pair.
    """

    def __get__(self, obj: Any, objtype: type | None = None) -> bool:
        return bool(getattr(obj, "_armed", False))

    def __set__(self, obj: Any, value: bool) -> None:
        setattr(obj, "_armed", bool(value))


class _AdvisoryRule:
    """Plumbing shared by all built-in rules.

    Subclasses only declare their identity via ``_label``; identity access and
    alert construction live here so no rule repeats that boilerplate.
    """

    #: Identifier surfaced on every alert this rule emits.
    _label: ClassVar[str] = ""
    #: Backed by the :class:`_toggle` descriptor exposed as ``enabled``.
    enabled = _toggle()

    def __init__(self, *, armed: bool = True) -> None:
        self._armed = armed

    @property
    def rule_id(self) -> str:
        if not self._label:
            raise NotImplementedError("subclass must set the _label marker")
        return self._label

    def _stamp(
        self,
        level: RiskLevel,
        message: str,
        **facts: Any,
    ) -> RiskAlert:
        """Wrap findings into an alert stamped with this rule's id and now."""
        return RiskAlert(
            rule_id=self.rule_id,
            level=level,
            message=message,
            context=dict(facts),
            timestamp=_clock_ms(),
        )


class MaxPositionRule(_AdvisoryRule):
    """Limit how much of the account one symbol may occupy.

    Past the raw size cap there is a second lens: rolling Pearson correlation
    between the candidate and existing holdings. A correlation finding stands
    alone — it replaces the cap verdict instead of stacking beside it.
    """

    _label = "max_position"

    def __init__(
        self,
        max_position_pct: float = 0.20,
        max_correlation: float = 0.7,
        correlation_window: int = 30,
        *,
        enabled: bool = True,
    ) -> None:
        super().__init__(armed=enabled)
        self.max_position_pct = max_position_pct
        self.max_correlation = max_correlation
        self.correlation_window = correlation_window

    @staticmethod
    def _rolling_correlation(a: list[float], b: list[float]) -> float | None:
        """Pearson r over the tail-aligned overlap of two series.

        ``None`` when fewer than three points pair up or either slice sits
        perfectly flat — constant windows carry no direction worth correlating.
        """
        span = min(len(a), len(b))
        if span < 3:
            return None

        xs = a[-span:]
        ys = b[-span:]
        mean_x = sum(xs) / span
        mean_y = sum(ys) / span

        pairs = [(x - mean_x, y - mean_y) for x, y in zip(xs, ys)]
        co_dev = sum(ex * ey for ex, ey in pairs)
        dev_x = sum(ex * ex for ex, _ in pairs)
        dev_y = sum(ey * ey for _, ey in pairs)

        scale = math.sqrt(dev_x) * math.sqrt(dev_y)
        return co_dev / scale if scale > 0.0 else None

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        if not self.enabled:
            return None

        book = _to_number(context.get("total_equity"))
        if book is None or book <= 0:
            return None

        held = context.get("positions")
        held = held if isinstance(held, dict) else {}

        quote = intent.price or 0.0
        sitting = _to_number(held.get(intent.symbol)) or 0.0
        bare_share = (sitting + intent.quantity * quote) / book
        gearing = _to_number(context.get("portfolio_leverage")) or 1.0
        loaded_share = bare_share * gearing

        if loaded_share > self.max_position_pct:
            return self._stamp(
                RiskLevel.RESTRICTED,
                f"{intent.symbol} 加上这一单后有效仓位 {loaded_share:.1%}，"
                f"顶破了单标的 {self.max_position_pct:.1%} 的天花板",
                symbol=intent.symbol,
                pct=loaded_share,
                nominal_pct=bare_share,
                max_pct=self.max_position_pct,
                portfolio_leverage=gearing,
            )

        twins = self._correlated_holdings(intent.symbol, held, context)
        if twins:
            return self._stamp(
                RiskLevel.WARNING,
                f"{intent.symbol} 与在持的 {', '.join(twins)} 走势高度同步"
                f"（滚动相关性越过 {self.max_correlation:.2f}），篮子集中度偏高",
                symbol=intent.symbol,
                correlated_with=twins,
            )

        return None

    def _correlated_holdings(
        self,
        symbol: str,
        positions: dict[str, Any],
        context: dict[str, Any],
    ) -> list[str]:
        """Held symbols whose recent price path shadows ours too closely."""
        tape = context.get("price_history")
        if not isinstance(tape, dict):
            return []
        mine = tape.get(symbol)
        if not isinstance(mine, list) or not mine:
            return []

        need = self.correlation_window
        found: list[str] = []
        for other_symbol in positions:
            if other_symbol == symbol:
                continue
            theirs = tape.get(other_symbol)
            if not isinstance(theirs, list) or not theirs:
                continue
            if min(len(mine), len(theirs)) < need:
                continue

            r = self._rolling_correlation(
                [float(point) for point in mine],
                [float(point) for point in theirs],
            )
            if r is not None and abs(r) > self.max_correlation:
                found.append(other_symbol)

        return found


class MaxSingleRiskRule(_AdvisoryRule):
    """Bound the leveraged damage one trade could take if its stop is hit."""

    _label = "max_single_risk"

    def __init__(self, max_risk_pct: float = 0.02, *, enabled: bool = True) -> None:
        super().__init__(armed=enabled)
        self.max_risk_pct = max_risk_pct

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        if not self.enabled:
            return None

        cfg = intent.risk_params or {}
        stop = _to_number(cfg.get("stop_loss"))
        entry = (
            intent.price
            if intent.price is not None
            else _to_number(context.get("current_price"))
        )
        book = _to_number(context.get("total_equity"))

        if book is None or book <= 0:
            return None

        gross_damage = abs(entry - stop) * abs(intent.quantity) if entry is not None and stop is not None else None
        if gross_damage is None:
            return None

        gearing = _to_number(cfg.get("leverage")) or 1.0
        loaded_damage = gross_damage * gearing
        allowance = book * self.max_risk_pct

        if loaded_damage <= allowance:
            return None

        share_now = loaded_damage / book

        return self._stamp(
            RiskLevel.RESTRICTED,
            f"{intent.symbol} 这笔若打止损将亏损 {loaded_damage:g}"
            f"（{gearing:g}x 杠杆放大后），超出账户风险预算 {allowance:g}"
            f"（权益的 {self.max_risk_pct:.1%}）",
            potential_loss=gross_damage,
            leveraged_loss=loaded_damage,
            leverage=gearing,
            pct=share_now,
            nominal_pct=gross_damage / book,
            max_pct=self.max_risk_pct,
        )


class DailyLossLimitRule(_AdvisoryRule):
    """Speak up once today's realized losses eat into the daily quota."""

    _label = "daily_loss_limit"

    def __init__(self, max_daily_loss_pct: float = 0.05, *, enabled: bool = True) -> None:
        super().__init__(armed=enabled)
        self.max_daily_loss_pct = max_daily_loss_pct

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        if not self.enabled:
            return None

        book = _to_number(context.get("total_equity"))
        today = _to_number(context.get("daily_realized_pnl"))
        if book is None or book <= 0 or today is None or today >= 0:
            return None

        burnt = -today
        quota = book * self.max_daily_loss_pct
        if burnt < quota:
            return None

        # Equity already reflects leveraged P&L — no second gearing applied.
        gearing = _to_number(context.get("portfolio_leverage")) or 1.0
        share = burnt / book
        return self._stamp(
            RiskLevel.DANGEROUS,
            f"当日实现亏损 {burnt:g} 已达权益的 {share:.1%}"
            f"（红线 {self.max_daily_loss_pct:.1%}，杠杆 {gearing:.1f}x），请控制节奏",
            daily_loss=burnt,
            effective_loss=burnt,
            nominal_pct=share,
            effective_pct=share,
            max_pct=self.max_daily_loss_pct,
            portfolio_leverage=gearing,
        )


class SymbolBlacklistRule(_AdvisoryRule):
    """Absolute veto: nothing on the blacklist gets traded, ever."""

    _label = "symbol_blacklist"

    def __init__(self, blacklist: set[str] | None = None, *, enabled: bool = True) -> None:
        super().__init__(armed=enabled)
        self._banned: set[str] = set(blacklist) if blacklist else set()

    @property
    def blacklist(self) -> set[str]:
        """Snapshot copy of the ban list — mutate through add()/remove()."""
        return set(self._banned)

    def add(self, symbol: str) -> None:
        """Ban a symbol."""
        self._banned.add(symbol)

    def remove(self, symbol: str) -> None:
        """Lift a ban."""
        self._banned.discard(symbol)

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        if not self.enabled:
            return None
        if intent.symbol not in self._banned:
            return None

        return self._stamp(
            RiskLevel.CRITICAL,
            f"{intent.symbol} 名列黑名单，此单不予放行",
            symbol=intent.symbol,
        )


class CooldownRule(_AdvisoryRule):
    """Enforce breathing room between back-to-back trades on one symbol."""

    _label = "cooldown"

    def __init__(self, cooldown_seconds: float = 300.0, *, enabled: bool = True) -> None:
        super().__init__(armed=enabled)
        self.cooldown_seconds = cooldown_seconds

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        if not self.enabled:
            return None

        ledger = context.get("last_trade_times")
        previous = ledger.get(intent.symbol) if isinstance(ledger, dict) else None
        last_seen = _to_number(previous)
        if last_seen is None:
            return None

        quiet_for = time.time() - last_seen
        if quiet_for >= self.cooldown_seconds:
            return None

        hold_on = self.cooldown_seconds - quiet_for
        return self._stamp(
            RiskLevel.WARNING,
            f"{intent.symbol} 距上一笔成交还不满 {self.cooldown_seconds:g}s，"
            f"建议再候约 {hold_on:.0f}s",
            remaining=hold_on,
            cooldown=self.cooldown_seconds,
            symbol=intent.symbol,
        )


_AnyBasicRule = (
    MaxPositionRule,
    MaxSingleRiskRule,
    DailyLossLimitRule,
    SymbolBlacklistRule,
    CooldownRule,
)
# Type alias for callers annotating mixed-rule collections.
RuleSet = MaxPositionRule | MaxSingleRiskRule | DailyLossLimitRule | SymbolBlacklistRule | CooldownRule


def create_default_rules() -> list[Any]:
    """Build the standard roster: 5 basic + 7 advanced + 3 portfolio guards.

    The heavier rosters are imported lazily so importing this module stays cheap
    even when those modules drag in extras of their own.
    """
    from selene_space_risk.advanced_rules import create_advanced_rules
    from selene_space_risk.portfolio_rules import (
        MarginUtilizationRule,
        NetExposureRule,
        TotalExposureRule,
    )

    basic: list[Any] = [rule_cls() for rule_cls in _AnyBasicRule]
    advanced: list[Any] = list(create_advanced_rules())
    portfolio: list[Any] = [
        TotalExposureRule(),
        NetExposureRule(),
        MarginUtilizationRule(),
    ]
    return basic + advanced + portfolio
