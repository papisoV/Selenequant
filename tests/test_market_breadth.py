"""US-007: MarketBreadthFilter tests (A6 whole-market advance/decline).

Breadth = advancers - decliners across many symbols over a trailing window.
The filter alerts (RESTRICTED) when breadth falls below ``min_breadth`` for
a long entry — a weak tape is a poor backdrop for going long.
"""

from __future__ import annotations

import pytest

from selene_space_risk.market_breadth import (
    MarketBreadthFilter,
    compute_market_breadth,
    market_breadth_counts,
)
from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskLevel
from selene_space_types.trading import OrderSide, OrderType


def _intent(symbol: str = "BTC/USDT", side: OrderSide = OrderSide.BUY) -> TradeIntent:
    return TradeIntent(
        symbol=symbol,
        side=side,
        quantity=1.0,
        price=50000.0,
        order_type=OrderType.MARKET,
        reasoning="test",
        confidence=0.8,
        timestamp=1711000000000,
    )


# ---------------------------------------------------------------------------
# market_breadth_counts / compute_market_breadth (pure functions)
# ---------------------------------------------------------------------------

class TestMarketBreadthCounts:
    def test_all_advancing(self) -> None:
        prices = {
            "A/USDT": [100.0, 101.0],
            "B/USDT": [100.0, 102.0],
            "C/USDT": [100.0, 103.0],
        }
        assert market_breadth_counts(prices) == (3, 0)

    def test_mixed_up_down(self) -> None:
        prices = {
            "A/USDT": [100.0, 101.0],   # up
            "B/USDT": [100.0, 99.0],    # down
            "C/USDT": [100.0, 98.0],    # down
        }
        assert market_breadth_counts(prices) == (1, 2)

    def test_flat_move_counts_neither(self) -> None:
        prices = {
            "A/USDT": [100.0, 101.0],   # up
            "B/USDT": [100.0, 100.0],   # flat
            "C/USDT": [100.0, 99.0],    # down
        }
        assert market_breadth_counts(prices) == (1, 1)

    def test_insufficient_data_excluded(self) -> None:
        prices = {
            "A/USDT": [100.0, 101.0],   # up, len 2
            "B/USDT": [100.0],          # too short -> excluded
        }
        assert market_breadth_counts(prices) == (1, 0)

    def test_window_parameter(self) -> None:
        # window=2: compare series[-1] vs series[-2] for each symbol
        prices = {
            "A/USDT": [100.0, 100.5, 101.0],   # +1.0 over 2 bars -> up
            "B/USDT": [100.0, 101.0, 100.0],   # 0 over 2 bars -> flat
        }
        assert market_breadth_counts(prices, window=2) == (1, 0)


class TestComputeMarketBreadth:
    def test_difference(self) -> None:
        prices = {
            "A/USDT": [100.0, 101.0],
            "B/USDT": [100.0, 99.0],
            "C/USDT": [100.0, 98.0],
            "D/USDT": [100.0, 102.0],
        }
        assert compute_market_breadth(prices) == 0  # 2 up - 2 down

    def test_empty(self) -> None:
        assert compute_market_breadth({}) == 0


# ---------------------------------------------------------------------------
# MarketBreadthFilter
# ---------------------------------------------------------------------------

class TestMarketBreadthFilterSignature:
    def test_rule_id(self) -> None:
        assert MarketBreadthFilter().rule_id == "market_breadth"

    def test_enable_disable(self) -> None:
        rule = MarketBreadthFilter()
        assert rule.enabled is True
        rule.enabled = False
        assert rule.enabled is False

    def test_invalid_window(self) -> None:
        with pytest.raises(ValueError, match="window"):
            MarketBreadthFilter(window=0)


class TestMarketBreadthFilterCheck:
    WEAK = {
        "A/USDT": [100.0, 101.0],   # up
        "B/USDT": [100.0, 99.0],    # down
        "C/USDT": [100.0, 98.0],    # down  -> breadth = -1
    }
    STRONG = {
        "A/USDT": [100.0, 101.0],
        "B/USDT": [100.0, 102.0],
        "C/USDT": [100.0, 103.0],   # breadth = +3
    }

    def test_weak_market_filters_long(self) -> None:
        alert = MarketBreadthFilter().check(_intent("BTC/USDT"), {"price_history": self.WEAK})
        assert alert is not None
        assert alert.level == RiskLevel.RESTRICTED
        assert alert.context["breadth"] == -1
        assert alert.context["advancers"] == 1
        assert alert.context["decliners"] == 2
        assert "涨跌家数差" in alert.message

    def test_strong_market_no_alert(self) -> None:
        alert = MarketBreadthFilter().check(_intent("BTC/USDT"), {"price_history": self.STRONG})
        assert alert is None

    def test_boundary_breadth_equal_min(self) -> None:
        equal = {
            "A/USDT": [100.0, 101.0],   # up
            "B/USDT": [100.0, 99.0],    # down  -> breadth = 0
        }
        alert = MarketBreadthFilter(min_breadth=0).check(_intent(), {"price_history": equal})
        assert alert is None  # 0 < 0 is False

    def test_positive_min_breadth_filters_neutral(self) -> None:
        equal = {
            "A/USDT": [100.0, 101.0],   # up
            "B/USDT": [100.0, 99.0],    # down  -> breadth = 0
        }
        alert = MarketBreadthFilter(min_breadth=1).check(_intent(), {"price_history": equal})
        assert alert is not None  # 0 < 1

    def test_short_side_not_filtered_by_weak_tape(self) -> None:
        alert = MarketBreadthFilter().check(
            _intent("BTC/USDT", side=OrderSide.SELL), {"price_history": self.WEAK}
        )
        assert alert is None

    def test_disabled_no_alert(self) -> None:
        alert = MarketBreadthFilter(enabled=False).check(
            _intent("BTC/USDT"), {"price_history": self.WEAK}
        )
        assert alert is None

    def test_empty_price_history_no_alert(self) -> None:
        alert = MarketBreadthFilter().check(_intent(), {"price_history": {}})
        assert alert is None  # breadth 0 not below min 0
