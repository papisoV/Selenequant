"""RiskEngine — turn a pile of per-rule alerts into one RiskDecision.

The contract is advisory-first: only CRITICAL findings revoke permission. A
CRITICAL additionally arms the kill switch (when one is attached) and drops an
asynchronous flatten request scoped to the intent's strategy.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskAlert, RiskAlertConfig, RiskDecision, RiskLevel

if TYPE_CHECKING:
    from selene_space_risk.flatten_hook import FlattenHook
    from selene_space_risk.kill_switch import KillSwitch

__all__ = ["RiskEngine", "RiskRuleProtocol"]


def _clock_ms() -> int:
    return int(time.time() * 1000)


# Severity ladder: the enum declares its members weakest-to-strongest, so list
# position doubles as rank. INFO occupies slot 0.
_LADDER: list[RiskLevel] = [
    RiskLevel.INFO,
    RiskLevel.WARNING,
    RiskLevel.RESTRICTED,
    RiskLevel.DANGEROUS,
    RiskLevel.CRITICAL,
]


def _rank(level: RiskLevel) -> int:
    return _LADDER.index(level)


@runtime_checkable
class RiskRuleProtocol(Protocol):
    """Anything that quacks like this can patrol the pre-trade gate."""

    @property
    def rule_id(self) -> str: ...

    @property
    def enabled(self) -> bool: ...

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None: ...


def _market_domain(symbol: str) -> str | None:
    """Which venue family a symbol belongs to; None when routing is absent."""
    try:
        from selene_space_types.routing import detect_market_domain

        return detect_market_domain(symbol).value
    except Exception:
        return None


class RiskEngine:
    """Fans an intent out across every armed rule, then folds the verdicts."""

    def __init__(
        self,
        rules: list[RiskRuleProtocol] | None = None,
        alert_config: RiskAlertConfig | None = None,
        kill_switch: KillSwitch | None = None,
        flatten_hook: FlattenHook | None = None,
    ) -> None:
        self._rules: list[RiskRuleProtocol] = list(rules) if rules else []
        self._alert_config = alert_config if alert_config is not None else RiskAlertConfig()
        self.kill_switch = kill_switch
        self.flatten_hook = flatten_hook

    # ── rule registry ────────────────────────────────────────────────────

    @property
    def rules(self) -> list[RiskRuleProtocol]:
        return list(self._rules)

    def add_rule(self, rule: RiskRuleProtocol) -> None:
        """Enlist another rule into the patrol."""
        self._rules.append(rule)

    def remove_rule(self, rule_id: str) -> bool:
        """Retire the first rule carrying ``rule_id``; report whether one left."""
        for index, candidate in enumerate(self._rules):
            if candidate.rule_id == rule_id:
                del self._rules[index]
                return True
        return False

    # ── display configuration ────────────────────────────────────────────

    @property
    def alert_config(self) -> RiskAlertConfig:
        return self._alert_config

    @alert_config.setter
    def alert_config(self, config: RiskAlertConfig) -> None:
        self._alert_config = config

    # ── evaluation pipeline ──────────────────────────────────────────────

    def pre_check(
        self,
        intent: TradeIntent,
        context: dict[str, Any] | None = None,
    ) -> RiskDecision:
        """Interrogate every applicable rule and fold results into a verdict."""
        ctx = context if context is not None else {}
        stamp = _clock_ms()

        found, critical_ids = self._collect_alerts(intent, ctx)
        shown, worst = self._pick_worst(found, critical_ids)
        allowed, reason = self._finalize(shown, worst, critical_ids, ctx)

        return RiskDecision(
            allowed=allowed,
            level=worst,
            reason=reason,
            alerts=shown,
            timestamp=stamp,
        )

    # ── stages ───────────────────────────────────────────────────────────

    def _collect_alerts(
        self,
        intent: TradeIntent,
        context: dict[str, Any],
    ) -> tuple[list[tuple[str, RiskAlert]], list[str]]:
        """Run each eligible rule; return (id-tagged alerts, critical ids)."""
        tagged: list[tuple[str, RiskAlert]] = []
        flagged_critical: list[str] = []

        for rule in self._rules:
            if not getattr(rule, "enabled", False):
                continue
            if not self._within_domains(rule, intent.symbol):
                continue
            verdict = rule.check(intent, context)
            if verdict is not None:
                verdict.timestamp = _clock_ms()
                tagged.append((rule.rule_id, verdict))
                if verdict.level == RiskLevel.CRITICAL:
                    flagged_critical.append(rule.rule_id)

        return tagged, flagged_critical

    def _pick_worst(
        self,
        tagged: list[tuple[str, RiskAlert]],
        critical_ids: list[str],
    ) -> tuple[list[RiskAlert], RiskLevel]:
        """Apply display filters; derive headline level.

        A CRITICAL that display settings keep off-screen still owns the
        headline when nothing visible outranks it — the user must not mistake
        a muted panel for an all-clear.
        """
        shown = [alert for _, alert in tagged if self._passes_display(alert)]

        top = RiskLevel.INFO
        for alert in shown:
            if _rank(alert.level) > _rank(top):
                top = alert.level
        if not shown and critical_ids:
            top = RiskLevel.CRITICAL

        return shown, top

    def _finalize(
        self,
        shown: list[RiskAlert],
        worst: RiskLevel,
        critical_ids: list[str],
        context: dict[str, Any],
    ) -> tuple[bool, str]:
        """Decide permission + reason string; fire side effects on a block."""
        blocked = bool(critical_ids)
        reason = ""
        if shown:
            ceiling = _rank(worst)
            reason = next(
                alert.message for alert in shown if _rank(alert.level) == ceiling
            )
        if blocked:
            self._trip_emergency(critical_ids, context)

        return not blocked, reason

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _within_domains(rule: RiskRuleProtocol, symbol: str) -> bool:
        supported = getattr(rule, "supported_domains", None)
        if not supported:
            return True
        domain = _market_domain(symbol)
        if domain is None:
            return True
        return domain in supported

    def _passes_display(self, alert: RiskAlert) -> bool:
        """Would the user-facing config let this alert be seen?"""
        cfg = self._alert_config
        if not cfg.enabled:
            return False

        effective = alert.level
        tweak = cfg.rules.get(alert.rule_id)
        if isinstance(tweak, dict):
            if tweak.get("enabled") is False:
                return False
            named = tweak.get("level")
            if isinstance(named, str):
                try:
                    effective = RiskLevel(named.lower())
                except ValueError:
                    pass  # unrecognized level text — leave the original standing

        return _rank(effective) >= _rank(cfg.min_level)

    def _trip_emergency(
        self,
        critical_rule_ids: list[str],
        context: dict[str, Any],
    ) -> None:
        """Consequences of a hard block: arm the kill switch, request flatten."""
        raw_strategy = context.get("strategy_id")
        strategy_id = raw_strategy if isinstance(raw_strategy, str) else None
        who = ", ".join(critical_rule_ids)

        breaker = self.kill_switch
        if breaker is not None and not breaker.is_active:
            breaker.auto_activate(
                reason=f"CRITICAL risk rules: {who}",
                triggered_by_rule=critical_rule_ids[0],
                strategy_id=strategy_id,
            )

        hook = self.flatten_hook
        if hook is not None:
            hook.fire_and_forget(strategy_id or None, reason=f"CRITICAL risk rules: {who}")
