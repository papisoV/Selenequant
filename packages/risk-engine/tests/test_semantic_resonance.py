"""Tests for SemanticResonanceRule — narrative-group concentration.

Covers:
  - grouping by the built-in DEFAULT_NARRATIVES map (AI / L1 / Meme ...)
  - alert when a single narrative exceeds max_narrative_exposure (default 50%)
  - pass when diversified across narratives
  - unmapped symbols do not form groups
  - per-call context narratives override constructor/default mapping
  - constructor narratives override the default map
  - SELL intent reduces exposure (signed merge, then gross per symbol)
  - short positions still count as narrative exposure (grossed)
  - price_history enrichment reports max intra-group price correlation
  - disabled rule / zero equity short-circuit to None
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from selene_space_risk.portfolio_rules import DEFAULT_NARRATIVES, SemanticResonanceRule
from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskAlert, RiskLevel
from selene_space_types.trading import OrderSide, OrderType


def _make_intent(**overrides: Any) -> TradeIntent:
    defaults = {
        "symbol": "BTC/USDT",
        "side": OrderSide.BUY,
        "quantity": 0.1,
        "price": 67000.0,
        "order_type": OrderType.MARKET,
        "reasoning": "test",
        "confidence": 0.8,
        "risk_params": {"stop_loss": 65000.0, "take_profit": 70000.0},
        "timestamp": int(time.time() * 1000),
    }
    defaults.update(overrides)
    return TradeIntent(**defaults)


class TestSemanticResonanceRule:
    def test_default_narratives_map_cover_major_tokens(self) -> None:
        assert DEFAULT_NARRATIVES["BTC"] == "store_of_value"
        assert DEFAULT_NARRATIVES["SOL"] == "layer1"
        assert DEFAULT_NARRATIVES["FET"] == "ai"
        assert DEFAULT_NARRATIVES["DOGE"] == "meme"
        assert DEFAULT_NARRATIVES["USDT"] == "stablecoin"

    def test_alerts_when_narrative_over_50_pct(self) -> None:
        rule = SemanticResonanceRule(max_narrative_exposure=0.50)
        ctx = {
            "total_equity": 100_000.0,
            "positions": {
                "SOL/USDT": 30_000.0,
                "ETH/USDT": 30_000.0,  # L1: SOL + ETH = 60%
                "DOGE/USDT": 20_000.0,
            },
        }
        result = rule.check(_make_intent(), ctx)
        assert isinstance(result, RiskAlert)
        assert result.rule_id == "semantic_resonance"
        assert result.level == RiskLevel.RESTRICTED
        assert result.context["narrative"] == "layer1"
        assert result.context["symbols"] == ["ETH/USDT", "SOL/USDT"]
        assert result.context["narrative_exposure_pct"] == pytest.approx(0.60, abs=0.01)

    def test_intent_adds_to_group(self) -> None:
        """The intent itself is an L1 token that pushes L1 over the threshold."""
        rule = SemanticResonanceRule(max_narrative_exposure=0.50)
        ctx = {
            "total_equity": 100_000.0,
            "positions": {"SOL/USDT": 45_000.0, "DOGE/USDT": 30_000.0},
        }
        intent = _make_intent(symbol="ETH/USDT", quantity=1, price=10_000)  # L1 +10k → 55%
        result = rule.check(intent, ctx)
        assert isinstance(result, RiskAlert)
        assert result.context["narrative"] == "layer1"
        assert result.context["narrative_exposure_pct"] == pytest.approx(0.55, abs=0.01)

    def test_passes_when_diversified(self) -> None:
        rule = SemanticResonanceRule()
        ctx = {
            "total_equity": 100_000.0,
            "positions": {
                "SOL/USDT": 25_000.0,
                "DOGE/USDT": 25_000.0,
                "FET/USDT": 25_000.0,
            },
        }
        result = rule.check(_make_intent(), ctx)
        assert result is None  # L1 / meme / ai each 25%

    def test_unmapped_symbols_ignored(self) -> None:
        """Unknown symbols do not form groups → no alert even at large size."""
        rule = SemanticResonanceRule()
        ctx = {"total_equity": 100_000.0, "positions": {"FOO/USDT": 90_000.0}}
        result = rule.check(_make_intent(), ctx)
        assert result is None

    def test_context_narratives_override(self) -> None:
        """Per-call re-tagging removes the L1 group → diversified."""
        rule = SemanticResonanceRule()
        ctx = {
            "total_equity": 100_000.0,
            "positions": {"SOL/USDT": 40_000.0, "ETH/USDT": 40_000.0},
            "narratives": {"SOL/USDT": "meme"},  # SOL → meme; L1 left with 40%
        }
        result = rule.check(_make_intent(), ctx)
        assert result is None  # meme 40% < 50%, L1 40% < 50%

    def test_constructor_narratives_override(self) -> None:
        rule = SemanticResonanceRule(narratives={"ETH": "ai"})
        ctx = {
            "total_equity": 100_000.0,
            "positions": {"ETH/USDT": 60_000.0, "FET/USDT": 10_000.0},
        }
        result = rule.check(_make_intent(), ctx)
        assert isinstance(result, RiskAlert)
        assert result.context["narrative"] == "ai"  # 70k / 100k = 70%
        assert result.context["narrative_exposure_pct"] == pytest.approx(0.70, abs=0.01)

    def test_sell_intent_reduces_exposure(self) -> None:
        """Selling part of SOL cuts the L1 group (signed merge, then gross)."""
        rule = SemanticResonanceRule(max_narrative_exposure=0.50)
        ctx = {
            "total_equity": 100_000.0,
            "positions": {"SOL/USDT": 45_000.0, "ETH/USDT": 20_000.0},  # L1 65%
        }
        intent = _make_intent(symbol="SOL/USDT", side=OrderSide.SELL, quantity=1, price=5_000)
        result = rule.check(intent, ctx)
        assert isinstance(result, RiskAlert)
        # 45k - 5k + 20k = 60k → 60%; a naive abs-add would have said 70%
        assert result.context["narrative_exposure_pct"] == pytest.approx(0.60, abs=0.01)

    def test_short_positions_count_as_exposure(self) -> None:
        """Shorts are grossed into narrative exposure (squeeze / collateral risk)."""
        rule = SemanticResonanceRule(max_narrative_exposure=0.50)
        ctx = {
            "total_equity": 100_000.0,
            "positions": {
                "SOL/USDT": 40_000.0,
                "ETH/USDT": {"notional": -15_000.0, "side": "short"},
            },
        }
        result = rule.check(_make_intent(), ctx)
        assert isinstance(result, RiskAlert)
        # L1 = 40k + 15k = 55k → 55% > 50%
        assert result.context["narrative"] == "layer1"
        assert result.context["narrative_exposure_pct"] == pytest.approx(0.55, abs=0.01)

    def test_price_history_enriches_alert(self) -> None:
        """Scaling preserves returns exactly → intra-group correlation == 1.0."""
        rule = SemanticResonanceRule(max_narrative_exposure=0.50)
        base = [100.0 + (i % 7) * 3.0 for i in range(40)]
        ctx = {
            "total_equity": 100_000.0,
            "positions": {
                "SOL/USDT": 30_000.0,
                "ETH/USDT": 30_000.0,
                "DOGE/USDT": 20_000.0,
            },
            "price_history": {
                "SOL/USDT": [p * 1.0 for p in base],
                "ETH/USDT": [p * 30.0 for p in base],
                "DOGE/USDT": [p * 0.5 for p in base],
            },
        }
        result = rule.check(_make_intent(), ctx)
        assert isinstance(result, RiskAlert)
        assert result.context["max_intra_group_corr"] == pytest.approx(1.0, abs=1e-6)

    def test_no_price_history_skips_enrichment(self) -> None:
        rule = SemanticResonanceRule(max_narrative_exposure=0.50)
        ctx = {
            "total_equity": 100_000.0,
            "positions": {"SOL/USDT": 30_000.0, "ETH/USDT": 30_000.0},
        }
        result = rule.check(_make_intent(), ctx)
        assert isinstance(result, RiskAlert)
        assert "max_intra_group_corr" not in result.context

    def test_disabled_rule_returns_none(self) -> None:
        rule = SemanticResonanceRule(enabled=False)
        ctx = {
            "total_equity": 100_000.0,
            "positions": {"SOL/USDT": 60_000.0, "ETH/USDT": 40_000.0},
        }
        result = rule.check(_make_intent(), ctx)
        assert result is None

    def test_passes_when_no_equity(self) -> None:
        rule = SemanticResonanceRule()
        result = rule.check(_make_intent(), {"total_equity": 0, "positions": {}})
        assert result is None

    def test_invalid_threshold_raises(self) -> None:
        with pytest.raises(ValueError):
            SemanticResonanceRule(max_narrative_exposure=1.5)
