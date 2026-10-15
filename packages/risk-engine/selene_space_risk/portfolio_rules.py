"""Portfolio-level risk rules — aggregate exposure checks.

Four rules:
  1. TotalExposureRule     — sum(|notional|) / equity (gross exposure)
  2. NetExposureRule       — (longs - shorts) / equity (directional bias)
  3. MarginUtilizationRule — total_margin / equity (with notional/leverage proxy fallback)
  4. SemanticResonanceRule — narrative-group exposure / equity (semantic resonance)

Unlike per-symbol rules in rules.py, these evaluate the entire portfolio
against a single TradeIntent, checking whether adding this trade would
push the aggregate portfolio beyond risk limits.
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
# 1. TotalExposureRule
# ---------------------------------------------------------------------------


class TotalExposureRule:
    """Alert when gross notional exposure exceeds a fraction of equity.

    Gross exposure = sum(abs(position_notional)) / total_equity.
    A 2x default means the portfolio can hold up to 2× equity in total
    notional (e.g. 100k equity → 200k total positions).

    Each position's notional is scaled by its leverage for effective exposure.

    Context keys:
        total_equity (float): Total account equity.
        positions (dict[str, float | dict]): symbol → notional value,
            or symbol → {notional, leverage, ...} for rich position data.
    """

    def __init__(self, max_gross_exposure: float = 2.0, *, enabled: bool = True) -> None:
        self._max_gross = max_gross_exposure
        self._enabled = enabled

    @property
    def rule_id(self) -> str:
        return "total_exposure"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        total_equity: float = context.get("total_equity", 0.0)
        if total_equity <= 0:
            return None

        positions: dict[str, Any] = context.get("positions", {})

        intent_notional = intent.quantity * (intent.price or 0.0)
        intent_leverage: float = float(intent.risk_params.get("leverage", 1.0))
        effective_new = intent_notional * intent_leverage

        existing_total = 0.0
        for symbol, pos in positions.items():
            if symbol == intent.symbol:
                continue
            notional, lev = self._extract_notional_and_leverage(pos)
            effective_total = notional * lev
            existing_total += effective_total

        # Add existing notional for the same symbol
        existing_same = 0.0
        if intent.symbol in positions:
            notional, lev = self._extract_notional_and_leverage(positions[intent.symbol])
            existing_same = notional * lev

        gross_exposure = existing_total + existing_same + effective_new
        gross_pct = gross_exposure / total_equity

        if gross_pct > self._max_gross:
            return _alert(
                self.rule_id, RiskLevel.DANGEROUS,
                f"总敞口 {gross_pct:.1%} 超过上限 {self._max_gross:.0%}（{gross_exposure:.0f} / {total_equity:.0f}）",
                gross_exposure_pct=gross_pct,
                gross_exposure=gross_exposure,
                total_equity=total_equity,
                max_gross_exposure=self._max_gross,
            )
        return None

    @staticmethod
    def _extract_notional_and_leverage(pos: Any) -> tuple[float, float]:
        if isinstance(pos, dict):
            notional = float(pos.get("notional", 0.0))
            leverage = float(pos.get("leverage", 1.0))
        else:
            notional = float(pos)
            leverage = 1.0
        return abs(notional), leverage


# ---------------------------------------------------------------------------
# 2. NetExposureRule
# ---------------------------------------------------------------------------


class NetExposureRule:
    """Alert when net directional exposure exceeds bounds.

    Net exposure = (total longs - total shorts) / equity.
    Positive = net long bias, negative = net short bias.

    Context keys:
        total_equity (float): Total account equity.
        positions (dict[str, float | dict]): symbol → notional value.
            Negative notional = short position.
    """

    def __init__(
        self,
        max_net_long: float = 1.0,
        max_net_short: float = -1.0,
        *,
        enabled: bool = True,
    ) -> None:
        self._max_long = max_net_long
        self._max_short = max_net_short
        self._enabled = enabled

    @property
    def rule_id(self) -> str:
        return "net_exposure"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        total_equity: float = context.get("total_equity", 0.0)
        if total_equity <= 0:
            return None

        positions: dict[str, Any] = context.get("positions", {})

        intent_notional = intent.quantity * (intent.price or 0.0)
        if intent.side.value == "sell":
            intent_notional = -intent_notional

        existing_net = 0.0
        for symbol, pos in positions.items():
            if symbol == intent.symbol:
                continue
            notional = self._extract_notional(pos)
            existing_net += notional

        existing_same = 0.0
        if intent.symbol in positions:
            existing_same = self._extract_notional(positions[intent.symbol])

        net_exposure = existing_net + existing_same + intent_notional
        net_pct = net_exposure / total_equity

        if net_pct > self._max_long:
            return _alert(
                self.rule_id, RiskLevel.RESTRICTED,
                f"净多头敞口 {net_pct:.1%} 超过上限 {self._max_long:.0%}（净 {net_exposure:.0f} / {total_equity:.0f}）",
                net_exposure_pct=net_pct,
                net_exposure=net_exposure,
                total_equity=total_equity,
                max_net_long=self._max_long,
            )
        if net_pct < self._max_short:
            return _alert(
                self.rule_id, RiskLevel.RESTRICTED,
                f"净空头敞口 {net_pct:.1%} 超过上限 {self._max_short:.0%}（净 {net_exposure:.0f} / {total_equity:.0f}）",
                net_exposure_pct=net_pct,
                net_exposure=net_exposure,
                total_equity=total_equity,
                max_net_short=self._max_short,
            )
        return None

    @staticmethod
    def _extract_notional(pos: Any) -> float:
        if isinstance(pos, dict):
            notional = float(pos.get("notional", 0.0))
            side = pos.get("side", "")
            if side == "sell" or side == "short":
                notional = -abs(notional)
        else:
            notional = float(pos)
        return notional


# ---------------------------------------------------------------------------
# 3. MarginUtilizationRule
# ---------------------------------------------------------------------------


class MarginUtilizationRule:
    """Alert when total margin utilization exceeds a threshold.

    When positions carry margin data (dict with 'margin' key), uses actual
    margin. Falls back to notional / default_leverage as a conservative proxy
    for live-engine contexts that only provide dict[str, float].

    Context keys:
        total_equity (float): Total account equity.
        positions (dict[str, float | dict]): symbol → notional or {notional, margin, ...}.
    """

    def __init__(
        self,
        max_margin_utilization: float = 0.90,
        default_leverage: float = 10.0,
        *,
        enabled: bool = True,
    ) -> None:
        self._max_util = max_margin_utilization
        self._default_leverage = default_leverage
        self._enabled = enabled

    @property
    def rule_id(self) -> str:
        return "margin_utilization"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        total_equity: float = context.get("total_equity", 0.0)
        if total_equity <= 0:
            return None

        positions: dict[str, Any] = context.get("positions", {})

        existing_margin = self._sum_margins(positions)
        intent_notional = intent.quantity * (intent.price or 0.0)
        intent_leverage = float(intent.risk_params.get("leverage", self._default_leverage))
        new_margin = intent_notional / intent_leverage if intent_leverage > 0 else intent_notional

        total_margin = existing_margin + new_margin
        margin_pct = total_margin / total_equity

        if margin_pct >= 1.0:
            return _alert(
                self.rule_id, RiskLevel.CRITICAL,
                f"保证金 {margin_pct:.1%} 超过权益，有强平风险（保证金 {total_margin:.0f} / 权益 {total_equity:.0f}）",
                margin_utilization_pct=margin_pct,
                total_margin=total_margin,
                total_equity=total_equity,
                max_margin_utilization=self._max_util,
            )
        if margin_pct > self._max_util:
            return _alert(
                self.rule_id, RiskLevel.DANGEROUS,
                f"保证金利用率 {margin_pct:.1%} 超过上限 {self._max_util:.0%}（保证金 {total_margin:.0f} / 权益 {total_equity:.0f}）",
                margin_utilization_pct=margin_pct,
                total_margin=total_margin,
                total_equity=total_equity,
                max_margin_utilization=self._max_util,
            )
        return None

    def _sum_margins(self, positions: dict[str, Any]) -> float:
        total = 0.0
        for pos in positions.values():
            if isinstance(pos, dict) and "margin" in pos:
                total += float(pos["margin"])
            elif isinstance(pos, dict):
                notional = float(pos.get("notional", 0.0))
                leverage = float(pos.get("leverage", self._default_leverage))
                total += abs(notional) / leverage if leverage > 0 else abs(notional)
            else:
                notional = float(pos)
                total += abs(notional) / self._default_leverage
        return total


# ---------------------------------------------------------------------------
# 4. SemanticResonanceRule — narrative-group concentration (absorbed)
# ---------------------------------------------------------------------------
# Absorbed from ai-berkshire (https://github.com/xbtlin/ai-berkshire), MIT License (c) 2026 xbtlin.
# Source: skills/portfolio-review.md — 移植为组合级"语义共振相关性"规则（追加于 portfolio_rules.py）。
# 见 docs/ABSORPTION_FROM_AI_BERKSHIRE.md。

# 默认叙事分组（key = base asset，可被构造参数 / context 逐层覆盖）。
# 同一赛道（AI/DeFi/L1/Meme/Stablecoin...）的币种在叙事转向时往往同向下跌，
# 即使价格相关性历史尚未体现 —— 语义共振是价格相关性（correlation.py）之外的前瞻信号。
DEFAULT_NARRATIVES: dict[str, str] = {
    "BTC": "store_of_value",
    "ETH": "layer1",
    "SOL": "layer1",
    "AVAX": "layer1",
    "ADA": "layer1",
    "DOT": "layer1",
    "ATOM": "layer1",
    "SUI": "layer1",
    "APT": "layer1",
    "TIA": "layer1",
    "BNB": "exchange",
    "LINK": "oracle",
    "UNI": "defi",
    "AAVE": "defi",
    "LDO": "defi",
    "MKR": "defi",
    "CRV": "defi",
    "OP": "layer2",
    "ARB": "layer2",
    "MATIC": "layer2",
    "DOGE": "meme",
    "SHIB": "meme",
    "PEPE": "meme",
    "BONK": "meme",
    "WIF": "meme",
    "NEAR": "ai",
    "FET": "ai",
    "RNDR": "ai",
    "TAO": "ai",
    "WLD": "ai",
    "GRT": "ai",
    "AKT": "ai",
    "USDT": "stablecoin",
    "USDC": "stablecoin",
    "DAI": "stablecoin",
    "USDE": "stablecoin",
    "FDUSD": "stablecoin",
    "PYUSD": "stablecoin",
}


class SemanticResonanceRule:
    """Alert when a single narrative/thematic group exceeds an exposure limit.

    Crypto analog of ai-berkshire portfolio-review's 同主题风险共振 check
    ("是否有超过 50% 的仓位暴露在同一个主题/行业"). Symbols are grouped by a
    narrative/track label (AI / DeFi / L1 / Meme / Stablecoin / ...); when the
    summed gross notional within one group exceeds ``max_narrative_exposure``
    of equity, the rule alerts with the offending group and its symbols.

    This is forward-looking relative to CorrelationRiskRule: price correlation
    is backward-looking (it needs prices to already co-move), whereas a shared
    narrative warns about co-movement before it is priced in. When
    ``price_history`` is supplied in context, the alert additionally reports the
    maximum intra-group pairwise price correlation for context.

    Context keys:
        total_equity (float): Total account equity.
        positions (dict[str, float | dict]): symbol → notional, or symbol →
            {notional, side, ...}. Negative notional / side in {"sell","short"}
            are treated as short positions (grossed for exposure).
        narratives (dict[str, str], optional): per-call symbol → narrative
            override, highest priority. Keys may be full symbols ("SOL/USDT")
            or base assets ("SOL").
        price_history (dict[str, list[float]], optional): symbol → close prices,
            used to enrich the alert with intra-group price correlation.
    """

    def __init__(
        self,
        max_narrative_exposure: float = 0.50,
        *,
        narratives: dict[str, str] | None = None,
        enabled: bool = True,
    ) -> None:
        """Initialize semantic resonance rule.

        Args:
            max_narrative_exposure: Max fraction of equity in one narrative
                group. Default 0.50 (methodology: >50% in one theme → warn).
            narratives: symbol → narrative overrides on top of the built-in
                DEFAULT_NARRATIVES map (below the per-call context override).
            enabled: Whether the rule is active.
        """
        if max_narrative_exposure < 0 or max_narrative_exposure > 1:
            raise ValueError("max_narrative_exposure must be in [0, 1]")
        self._max_exposure = max_narrative_exposure
        self._narratives = {**DEFAULT_NARRATIVES, **(narratives or {})}
        self._enabled = enabled

    @property
    def rule_id(self) -> str:
        return "semantic_resonance"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        if not self._enabled:
            return None

        total_equity: float = context.get("total_equity", 0.0)
        if total_equity <= 0:
            return None

        positions: dict[str, Any] = context.get("positions", {})

        # Per-call narrative override wins over constructor/default mapping.
        narratives: dict[str, str] = self._narratives
        call_narratives = context.get("narratives")
        if call_narratives:
            narratives = {**self._narratives, **dict(call_narratives)}

        # Merge the new intent into the position (signed), mirroring how
        # CorrelationRiskRule folds intent value into existing positions.
        intent_notional = intent.quantity * (intent.price or 0.0)
        if intent.side.value == "sell":
            intent_notional = -intent_notional
        updated_positions = dict(positions)
        updated_positions[intent.symbol] = (
            self._signed_notional(positions.get(intent.symbol, 0.0)) + intent_notional
        )

        # Group gross notional by narrative. Short positions still count as
        # exposure to the narrative (squeeze / funding / collateral risk).
        group_notional: dict[str, float] = {}
        group_symbols: dict[str, set[str]] = {}
        for symbol, pos in updated_positions.items():
            narrative = self._resolve_narrative(symbol, narratives)
            if narrative is None:
                continue  # unmapped symbols cannot resonate by definition
            notional = abs(self._signed_notional(pos))
            group_notional[narrative] = group_notional.get(narrative, 0.0) + notional
            group_symbols.setdefault(narrative, set()).add(symbol)

        # The highest-exposure narrative drives the check.
        worst_narrative: str | None = None
        worst_exposure = 0.0
        for narrative, notional in group_notional.items():
            exposure = notional / total_equity
            if exposure > worst_exposure:
                worst_exposure = exposure
                worst_narrative = narrative

        if worst_narrative is None or worst_exposure <= self._max_exposure:
            return None

        symbols = sorted(group_symbols[worst_narrative])
        message = (
            f"叙事共振 [{worst_narrative}] 持仓 {worst_exposure:.1%}，"
            f"超过上限 {self._max_exposure:.0%}（{len(symbols)} 个同赛道标的）"
        )
        alert_ctx: dict[str, Any] = {
            "narrative": worst_narrative,
            "symbols": symbols,
            "narrative_exposure_pct": worst_exposure,
            "narrative_notional": group_notional[worst_narrative],
            "max_narrative_exposure": self._max_exposure,
        }

        # Optional enrichment: report max intra-group price correlation so the
        # reader can see whether the resonance is already visible in prices.
        price_history: dict[str, list[float]] | None = context.get("price_history")
        max_corr = self._max_intra_group_corr(symbols, price_history)
        if max_corr is not None:
            alert_ctx["max_intra_group_corr"] = max_corr
            message += f"（组内价格相关性最高 {max_corr:.2f}）"

        return _alert(self.rule_id, RiskLevel.RESTRICTED, message, **alert_ctx)

    @staticmethod
    def _signed_notional(pos: Any) -> float:
        """Signed notional for a position (negative = short)."""
        if isinstance(pos, dict):
            notional = float(pos.get("notional", 0.0))
            side = pos.get("side", "")
            if side in ("sell", "short"):
                notional = -abs(notional)
        else:
            notional = float(pos)
        return notional

    @classmethod
    def _resolve_narrative(cls, symbol: str, narratives: dict[str, str]) -> str | None:
        """Resolve a symbol to its narrative; exact symbol beats base asset."""
        if symbol in narratives:
            return narratives[symbol]
        return narratives.get(symbol.split("/")[0].upper())

    @staticmethod
    def _max_intra_group_corr(
        symbols: list[str],
        price_history: dict[str, list[float]] | None,
    ) -> float | None:
        """Max pairwise price correlation within a narrative group (or None)."""
        if not price_history or len(symbols) < 2:
            return None
        from selene_space_risk.correlation import compute_returns, pearson_correlation

        max_corr: float | None = None
        for i, sym_a in enumerate(symbols):
            prices_a = price_history.get(sym_a)
            if not prices_a:
                continue
            returns_a = compute_returns(list(prices_a))
            for sym_b in symbols[i + 1:]:
                prices_b = price_history.get(sym_b)
                if not prices_b:
                    continue
                corr = pearson_correlation(returns_a, compute_returns(list(prices_b)))
                if corr is not None and (max_corr is None or abs(corr) > abs(max_corr)):
                    max_corr = corr
        return max_corr
