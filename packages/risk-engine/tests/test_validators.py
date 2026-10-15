"""TradeIntent validator coverage — price deviation, stop-loss sanity,
directional bracket checks, and the combined gate."""

from __future__ import annotations

import time

from selene_space_risk.validators import (
    validate,
    validate_direction,
    validate_price,
    validate_stop_loss,
)
from selene_space_types.agent import TradeIntent
from selene_space_types.trading import OrderSide, OrderType

ENTRY = 67_000.0


def _intent(**overrides) -> TradeIntent:
    fields = dict(
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        quantity=0.1,
        price=ENTRY,
        order_type=OrderType.LIMIT,
        reasoning="test",
        confidence=0.8,
        risk_params={"stop_loss": 65_000.0, "take_profit": 70_000.0},
        timestamp=int(time.time() * 1000),
    )
    fields.update(overrides)
    return TradeIntent(**fields)


class TestPriceDeviation:
    def test_quote_near_intent_passes(self) -> None:
        assert validate_price(_intent(price=ENTRY), ENTRY + 500.0) == []

    def test_quote_far_from_intent_flags(self) -> None:
        errors = validate_price(_intent(price=ENTRY), 60_000.0)
        assert len(errors) == 1
        assert "deviates" in errors[0]

    def test_absent_price_not_checked(self) -> None:
        assert validate_price(_intent(price=None), ENTRY) == []


class TestStopLossSanity:
    def test_positive_stop_passes(self) -> None:
        assert validate_stop_loss(_intent()) == []

    def test_missing_stop_flagged(self) -> None:
        errors = validate_stop_loss(_intent(risk_params={}))
        assert len(errors) == 1
        assert "Missing stop_loss" in errors[0]

    def test_negative_stop_flagged(self) -> None:
        errors = validate_stop_loss(_intent(risk_params={"stop_loss": -100}))
        assert len(errors) == 1
        assert "Invalid" in errors[0]


class TestDirectionalBracket:
    """Long brackets point down-up; short brackets point up-down."""

    def test_long_bracket_well_ordered(self) -> None:
        intent = _intent(
            side=OrderSide.BUY,
            price=ENTRY,
            risk_params={"stop_loss": 65_000.0, "take_profit": 70_000.0},
        )
        assert validate_direction(intent) == []

    def test_long_take_profit_under_entry_rejected(self) -> None:
        intent = _intent(
            side=OrderSide.BUY,
            price=ENTRY,
            risk_params={"stop_loss": 65_000.0, "take_profit": 66_000.0},
        )
        errors = validate_direction(intent)
        assert len(errors) == 1
        assert "BUY" in errors[0]

    def test_short_bracket_well_ordered(self) -> None:
        intent = _intent(
            side=OrderSide.SELL,
            price=ENTRY,
            risk_params={"stop_loss": 69_000.0, "take_profit": 64_000.0},
        )
        assert validate_direction(intent) == []

    def test_short_take_profit_over_entry_rejected(self) -> None:
        intent = _intent(
            side=OrderSide.SELL,
            price=ENTRY,
            risk_params={"stop_loss": 69_000.0, "take_profit": 70_000.0},
        )
        assert any("SELL" in e for e in validate_direction(intent))

    def test_long_stop_over_entry_rejected(self) -> None:
        intent = _intent(
            side=OrderSide.BUY,
            price=ENTRY,
            risk_params={"stop_loss": 68_000.0, "take_profit": 70_000.0},
        )
        assert any("stop_loss" in e for e in validate_direction(intent))


class TestCombinedGate:
    def test_clean_intent_accepted(self) -> None:
        result = validate(_intent(), ENTRY)
        assert (result.valid, result.errors) == (True, [])

    def test_two_faults_stack(self) -> None:
        result = validate(_intent(price=80_000.0, risk_params={}), ENTRY)
        assert result.valid is False
        assert len(result.errors) >= 2  # price deviation + missing stop_loss
