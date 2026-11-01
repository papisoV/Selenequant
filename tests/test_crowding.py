"""US-007: CrowdingRiskRule tests (B7 funding-percentile crowding).

The rule reuses FundingRateIndicator (selene_space_strategy.indicators.funding_rate)
to read the funding-rate series, computes the percentile of the current value
within its trailing lookback window, and emits a RiskAlert when the percentile
is extreme (crowded long above high_percentile, crowded short below low_percentile).

Acceptance:
  - simulated funding series -> correct percentile + RiskAlert
  - plain list and DataFrame (FUNDING_RATE column) paths agree
  - middle-percentile / disabled / short-history cases return None
"""

from __future__ import annotations

import pandas as pd
import pytest

from selene_space_risk.crowding import CrowdingRiskRule, funding_percentile
from selene_space_strategy.indicators.funding_rate import FundingRateIndicator
from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskLevel
from selene_space_types.trading import OrderSide, OrderType


def _intent(symbol: str = "BTC/USDT") -> TradeIntent:
    return TradeIntent(
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=1.0,
        price=50000.0,
        order_type=OrderType.MARKET,
        reasoning="test",
        confidence=0.8,
        timestamp=1711000000000,
    )


# ---------------------------------------------------------------------------
# funding_percentile (pure function)
# ---------------------------------------------------------------------------

class TestFundingPercentile:
    def test_empty_history_zero(self) -> None:
        assert funding_percentile([], 0.001) == 0.0

    def test_value_above_all_is_one(self) -> None:
        assert funding_percentile([0.0001, 0.0002, 0.0003, 0.0004], 0.001) == 1.0

    def test_value_below_all_is_zero(self) -> None:
        assert funding_percentile([0.0002, 0.0003, 0.0004, 0.0005], 0.0001) == 0.0

    def test_middle_value_counts_below(self) -> None:
        history = [0.0001, 0.0002, 0.0004, 0.0005]
        assert funding_percentile(history, 0.0003) == 0.5  # 2 of 4 <= 0.0003

    def test_ties_counted_inclusive(self) -> None:
        assert funding_percentile([0.001, 0.001, 0.001], 0.001) == 1.0


# ---------------------------------------------------------------------------
# CrowdingRiskRule
# ---------------------------------------------------------------------------

class TestCrowdingRuleSignature:
    def test_reuses_funding_rate_indicator(self) -> None:
        rule = CrowdingRiskRule()
        assert isinstance(rule._indicator, FundingRateIndicator)

    def test_rule_id(self) -> None:
        assert CrowdingRiskRule().rule_id == "crowding_risk"

    def test_enable_disable(self) -> None:
        rule = CrowdingRiskRule()
        assert rule.enabled is True
        rule.enabled = False
        assert rule.enabled is False

    def test_invalid_params(self) -> None:
        with pytest.raises(ValueError, match="lookback"):
            CrowdingRiskRule(lookback=0)
        with pytest.raises(ValueError, match="high_percentile"):
            CrowdingRiskRule(high_percentile=0.5)
        with pytest.raises(ValueError, match="high_percentile"):
            CrowdingRiskRule(high_percentile=1.5)
        with pytest.raises(ValueError, match="low_percentile"):
            CrowdingRiskRule(low_percentile=0.5)
        with pytest.raises(ValueError, match="low_percentile"):
            CrowdingRiskRule(low_percentile=-0.1)


class TestCrowdingRuleCheck:
    def test_crowded_long_emits_alert(self) -> None:
        series = [0.0001, 0.0002, 0.0003, 0.0004, 0.0010]
        alert = CrowdingRiskRule().check(_intent("BTC/USDT"), {"funding_series": series})
        assert alert is not None
        assert alert.level == RiskLevel.WARNING
        assert alert.context["direction"] == "long"
        assert alert.context["percentile"] == pytest.approx(1.0)
        assert "拥挤做多" in alert.message

    def test_crowded_short_emits_alert(self) -> None:
        series = [0.0010, 0.0004, 0.0003, 0.0002, -0.0005]
        alert = CrowdingRiskRule().check(_intent("BTC/USDT"), {"funding_series": series})
        assert alert is not None
        assert alert.level == RiskLevel.WARNING
        assert alert.context["direction"] == "short"
        assert alert.context["percentile"] == pytest.approx(0.0)
        assert "拥挤做空" in alert.message

    def test_middle_percentile_no_alert(self) -> None:
        series = [0.0001, 0.0002, 0.0010, 0.0004, 0.0003]  # last -> pct 0.5
        alert = CrowdingRiskRule().check(_intent(), {"funding_series": series})
        assert alert is None

    def test_short_history_no_alert(self) -> None:
        alert = CrowdingRiskRule().check(_intent(), {"funding_series": [0.0002]})
        assert alert is None
        alert = CrowdingRiskRule().check(_intent(), {"funding_series": []})
        assert alert is None

    def test_disabled_no_alert(self) -> None:
        series = [0.0001, 0.0002, 0.0003, 0.0004, 0.0010]
        alert = CrowdingRiskRule(enabled=False).check(_intent(), {"funding_series": series})
        assert alert is None

    def test_lookback_limits_baseline(self) -> None:
        """Old extreme values are excluded when lookback is small."""
        series = [0.0010, 0.0002, 0.0003, 0.0004, 0.0005]  # last=0.0005, not extreme
        alert = CrowdingRiskRule(lookback=2).check(_intent(), {"funding_series": series})
        # window = series[-3:-1] = [0.0003, 0.0004]; pct of 0.0005 = 1.0 -> crowded long
        assert alert is not None
        assert alert.context["percentile"] == pytest.approx(1.0)

    def test_dataframe_path_matches_list(self) -> None:
        series = [0.0001, 0.0002, 0.0003, 0.0004, 0.0010]
        df = pd.DataFrame({"FUNDING_RATE": series})
        rule = CrowdingRiskRule()
        from_list = rule.check(_intent(), {"funding_series": series})
        from_df = rule.check(_intent(), {"funding_data": df})
        assert from_df is not None
        assert from_list is not None
        assert from_df.context["percentile"] == pytest.approx(from_list.context["percentile"])
        assert from_df.context["direction"] == "long"

    def test_alert_context_fields(self) -> None:
        series = [0.0001, 0.0002, 0.0003, 0.0004, 0.0010]
        alert = CrowdingRiskRule().check(_intent("ETH/USDT"), {"funding_series": series})
        assert alert.rule_id == "crowding_risk"
        assert alert.context["symbol"] == "ETH/USDT"
        assert alert.context["current_funding"] == pytest.approx(0.0010)
        assert alert.context["lookback"] == 4
