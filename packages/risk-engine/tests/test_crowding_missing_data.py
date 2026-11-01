"""CrowdingRiskRule: an absent funding column must not fabricate an alert.

The rule ranks the current funding rate against its recent history. When the
funding indicators report all-NaN (no derivatives data loaded), the NaN must
be dropped before ranking: a NaN slipping into the comparison makes every
``<=`` test False, pinning the percentile at 0.0 and firing a spurious
"crowded short" warning off data that does not exist.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from selene_space_risk.crowding import CrowdingRiskRule, funding_percentile
from selene_space_types.agent import TradeIntent
from selene_space_types.trading import OrderSide, OrderType


def _intent() -> TradeIntent:
    return TradeIntent(
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        quantity=1.0,
        order_type=OrderType.MARKET,
        reasoning="test",
        confidence=0.5,
        timestamp=1_700_000_000_000,
    )


class TestAbsentFundingDataMakesNoAlert:
    def test_ohlcv_only_frame_fires_nothing(self) -> None:
        """A frame without a funding column yields no crowding alert."""
        rule = CrowdingRiskRule(lookback=5)
        frame = pd.DataFrame(
            {
                "open": np.arange(20, dtype=float) + 100,
                "high": np.arange(20, dtype=float) + 101,
                "low": np.arange(20, dtype=float) + 99,
                "close": np.arange(20, dtype=float) + 100,
                "volume": np.full(20, 10.0),
            }
        )

        assert rule.check(_intent(), {"funding_data": frame}) is None

    def test_nan_list_entries_are_dropped(self) -> None:
        """NaN values in an explicit series are ignored, not ranked."""
        rule = CrowdingRiskRule(lookback=5)

        assert rule.check(_intent(), {"funding_series": [float("nan")] * 8}) is None

    def test_real_history_still_ranks(self) -> None:
        """Populated history keeps working (guard against over-filtering)."""
        rule = CrowdingRiskRule(lookback=5, high_percentile=0.8)
        # current value is the top of a rising series → crowded long expected
        series = [0.0001, 0.0002, 0.0003, 0.0004, 0.0005, 0.0006, 0.0009]

        alert = rule.check(_intent(), {"funding_series": series})

        assert alert is not None
        assert alert.context.get("direction") == "long"


class TestFundingPercentile:
    def test_empty_history_is_zero(self) -> None:
        assert funding_percentile([], 0.5) == 0.0

    def test_top_value_is_one(self) -> None:
        assert funding_percentile([0.1, 0.2, 0.3], 0.3) == 1.0
