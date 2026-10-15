"""DEX-specific risk rules for SeleneQuant risk engine.

Additional rules for decentralized exchange trading, ported from the
formula-policy risk management logic:

  1. TrailingStopRule         — alert when price drops from peak after activation gain
  2. PartialTakeProfitRule    — alert to sell a fraction at TP, let remainder run
  3. AISignalExitRule         — alert when AI model score drops below threshold
  4. HoneypotDetectionRule    — CRITICAL alert when sell path verification fails
  5. LiquidityFilterRule      — CRITICAL alert for tokens below minimum liquidity
"""

from __future__ import annotations

import time
from typing import Any

from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskAlert, RiskLevel


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
# 1. TrailingStopRule
# ---------------------------------------------------------------------------


class TrailingStopRule:
    """Alert when price drops TRAILING_DROP from peak after activation gain.

    Context keys:
        positions (dict): symbol → position info with 'entry_price', 'highest_price'.
    """

    def __init__(
        self,
        trailing_activation: float = 0.05,
        trailing_drop: float = 0.03,
        *,
        enabled: bool = True,
    ) -> None:
        self._activation = trailing_activation
        self._drop = trailing_drop
        self._enabled = enabled

    @property
    def rule_id(self) -> str:
        return "trailing_stop"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        positions: dict[str, dict] = context.get("positions", {})
        pos = positions.get(intent.symbol)
        if not pos:
            return None

        entry_price = pos.get("entry_price", 0.0)
        highest_price = pos.get("highest_price", 0.0)
        current_price = intent.price or context.get("current_price", 0.0)

        if not entry_price or not current_price:
            return None

        max_gain = (highest_price - entry_price) / entry_price if entry_price > 0 else 0.0

        if max_gain < self._activation:
            return None

        drawdown = (highest_price - current_price) / highest_price if highest_price > 0 else 0.0

        if drawdown > self._drop:
            return _alert(
                self.rule_id, RiskLevel.DANGEROUS,
                f"追踪止损触发 {intent.symbol}：最大盈利 {max_gain:.1%}，回撤 {drawdown:.1%} > {self._drop:.0%}",
                symbol=intent.symbol, max_gain=max_gain, drawdown=drawdown, drop=self._drop,
            )
        return None


# ---------------------------------------------------------------------------
# 2. PartialTakeProfitRule
# ---------------------------------------------------------------------------


class PartialTakeProfitRule:
    """Alert to sell a fraction of position when take-profit reached,
    letting the remainder run (moonbag strategy).

    Context keys:
        positions (dict): symbol → position info with 'entry_price', 'is_moonbag'.
    """

    def __init__(
        self,
        take_profit_pct: float = 0.10,
        sell_ratio: float = 0.5,
        *,
        enabled: bool = True,
    ) -> None:
        self._tp_pct = take_profit_pct
        self._sell_ratio = sell_ratio
        self._enabled = enabled

    @property
    def rule_id(self) -> str:
        return "partial_take_profit"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        positions: dict[str, dict] = context.get("positions", {})
        pos = positions.get(intent.symbol)
        if not pos:
            return None

        is_moonbag = pos.get("is_moonbag", False)
        if is_moonbag:
            return None

        entry_price = pos.get("entry_price", 0.0)
        current_price = intent.price or context.get("current_price", 0.0)

        if not entry_price or not current_price:
            return None

        pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0.0

        if pnl_pct >= self._tp_pct:
            return _alert(
                self.rule_id, RiskLevel.RESTRICTED,
                f"部分止盈触发 {intent.symbol}：盈利 {pnl_pct:.1%} >= {self._tp_pct:.0%}，建议卖出 {self._sell_ratio:.0%}",
                symbol=intent.symbol, pnl_pct=pnl_pct, sell_ratio=self._sell_ratio,
            )
        return None


# ---------------------------------------------------------------------------
# 3. AISignalExitRule
# ---------------------------------------------------------------------------


class AISignalExitRule:
    """Alert when AI model score drops below threshold.

    Context keys:
        ai_scores (dict): symbol → current AI inference score (0-1).
    """

    def __init__(
        self,
        sell_threshold: float = 0.45,
        *,
        enabled: bool = True,
    ) -> None:
        self._threshold = sell_threshold
        self._enabled = enabled

    @property
    def rule_id(self) -> str:
        return "ai_signal_exit"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        positions: dict[str, dict] = context.get("positions", {})
        if intent.symbol not in positions:
            return None

        ai_scores: dict[str, float] = context.get("ai_scores", {})
        score = ai_scores.get(intent.symbol)

        if score is None:
            return None

        if score < self._threshold:
            return _alert(
                self.rule_id, RiskLevel.RESTRICTED,
                f"AI信号退出 {intent.symbol}：评分 {score:.2f} < 阈值 {self._threshold:.2f}",
                symbol=intent.symbol, score=score, threshold=self._threshold,
            )
        return None


# ---------------------------------------------------------------------------
# 4. HoneypotDetectionRule
# ---------------------------------------------------------------------------


class HoneypotDetectionRule:
    """CRITICAL alert when sell path verification fails (DEX-specific).

    Context keys:
        quote_provider (callable): async function(token_address) → quote or None.
        is_dex (bool): Whether this is a DEX trade.
    """

    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = enabled

    @property
    def rule_id(self) -> str:
        return "honeypot_detection"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        is_dex = context.get("is_dex", False)
        if not is_dex:
            return None

        verified = context.get("sell_path_verified")
        if verified is None:
            return None

        if not verified:
            return _alert(
                self.rule_id, RiskLevel.CRITICAL,
                f"蜜罐检测 {intent.symbol}：卖出路径验证失败，可能是骗局",
                symbol=intent.symbol,
            )
        return None


# ---------------------------------------------------------------------------
# 5. LiquidityFilterRule
# ---------------------------------------------------------------------------


class LiquidityFilterRule:
    """CRITICAL alert for tokens below minimum liquidity threshold.

    Context keys:
        token_liquidity (dict): symbol → liquidity in USD.
    """

    def __init__(
        self,
        min_liquidity_usd: float = 5000.0,
        *,
        enabled: bool = True,
    ) -> None:
        self._min_liquidity = min_liquidity_usd
        self._enabled = enabled

    @property
    def rule_id(self) -> str:
        return "liquidity_filter"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        token_liquidity: dict[str, float] = context.get("token_liquidity", {})
        liquidity = token_liquidity.get(intent.symbol)

        if liquidity is None:
            return None

        if liquidity < self._min_liquidity:
            return _alert(
                self.rule_id, RiskLevel.CRITICAL,
                f"{intent.symbol} 流动性过低：${liquidity:.0f} < 最低 ${self._min_liquidity:.0f}，极易被套",
                symbol=intent.symbol, liquidity=liquidity, min_liquidity=self._min_liquidity,
            )
        return None


# ---------------------------------------------------------------------------
# 6. LLMAdvisoryRule — block trading when LLM signal provider is degraded
# ---------------------------------------------------------------------------


class LLMAdvisoryRule:
    """CRITICAL alert when the LLM signal provider is degraded (v4 §阶段4).

    Returns a ``RiskAlert`` (advisory) — NOT a ``RiskDecision.DENY`` (C1 fix).
    The risk engine aggregates CRITICAL alerts into ``allowed=False`` and
    auto-triggers the KillSwitch.

    Scoping (v4 A2):
        The ``strategy_id`` is read from ``intent.risk_params['strategy_id']``
        (set by ``LLMSignalStrategy``) and copied into the returned alert's
        context. The risk engine's ``_auto_trigger_kill_switch`` reads
        ``context.get('strategy_id')`` to activate STRATEGY scope — only this
        strategy is blocked, A1/A2 keep trading.

    Trigger condition (v4 Mi1):
        When ``LLMSignalStrategy.fallback_exhausted_count()`` (public method,
        sliding-window count of fallback-exhaustion events) reaches the
        configured threshold, this rule raises CRITICAL.

    Args:
        llm_strategy: The ``LLMSignalStrategy`` instance (duck-typed; must
            expose ``fallback_exhausted_count() -> int``).
        enabled: Master switch for this rule.
    """

    def __init__(
        self,
        llm_strategy: Any,
        *,
        enabled: bool = True,
    ) -> None:
        self._llm = llm_strategy
        self._enabled = enabled

    @property
    def rule_id(self) -> str:
        return "llm_advisory"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        if self._llm is None:
            return None

        # Only evaluate intents that originated from the LLM signal strategy.
        # strategy_id lives in risk_params (set by LLMSignalStrategy._build_intent).
        strategy_id = intent.risk_params.get("strategy_id") if intent.risk_params else None
        if strategy_id is None:
            return None

        # Propagate strategy_id into the risk context dict so the engine's
        # _auto_trigger_kill_switch reads it from context.get("strategy_id")
        # (engine.py:208) and activates STRATEGY scope (v4 A2). The rule runs
        # inside the engine's rule loop, before _auto_trigger_kill_switch is
        # called (engine.py:143 loop → engine.py:173 trigger), so the mutation
        # is visible to the trigger.
        if not context.get("strategy_id"):
            context["strategy_id"] = strategy_id

        # Read exhaustion count via the public method (v4 Mi1).
        try:
            exhausted_count = self._llm.fallback_exhausted_count()
        except Exception:
            return None

        # Read threshold from the strategy's config if available; default 5.
        threshold = 5
        config = getattr(self._llm, "config", None)
        if config is not None:
            threshold = getattr(config, "fallback_exhausted_threshold", threshold)

        if exhausted_count >= threshold:
            return _alert(
                self.rule_id,
                RiskLevel.CRITICAL,
                (
                    f"LLM 信号提供方降级：fallback 耗尽 {exhausted_count} 次 "
                    f">= 阈值 {threshold}，暂停 {intent.symbol} LLM 信号交易"
                ),
                symbol=intent.symbol,
                strategy_id=strategy_id,
                exhausted_count=exhausted_count,
                threshold=threshold,
            )
        return None


# ---------------------------------------------------------------------------
# Convenience: DEX rule set
# ---------------------------------------------------------------------------

DEXRuleSet = (
    TrailingStopRule
    | PartialTakeProfitRule
    | AISignalExitRule
    | HoneypotDetectionRule
    | LiquidityFilterRule
    | LLMAdvisoryRule
)


def create_dex_rules(
    trailing_activation: float = 0.05,
    trailing_drop: float = 0.03,
    take_profit_pct: float = 0.10,
    sell_ratio: float = 0.5,
    ai_sell_threshold: float = 0.45,
    min_liquidity_usd: float = 5000.0,
) -> list[DEXRuleSet]:
    """Create the standard five DEX risk rules with default parameters."""
    return [
        TrailingStopRule(trailing_activation, trailing_drop),
        PartialTakeProfitRule(take_profit_pct, sell_ratio),
        AISignalExitRule(ai_sell_threshold),
        HoneypotDetectionRule(),
        LiquidityFilterRule(min_liquidity_usd),
    ]
