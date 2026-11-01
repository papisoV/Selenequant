"""Behavior locks for selene_space_security.guardrails.overtrading:
hourly/daily order-rate gates, side-reversal counting, evaluate, reset."""

import time

import pytest

from selene_space_security.guardrails.overtrading import (
    OvertradingConfig,
    OvertradingDetector,
)
from selene_space_types import OrderSide, RiskLevel

SYMBOL = "BTC/USDT"


@pytest.fixture
def clock() -> float:
    return time.time()


def build(**limits) -> OvertradingDetector:
    return OvertradingDetector(OvertradingConfig(**limits))


def place_run(detector: OvertradingDetector, total: int, anchor: float, spacing: float = 1.0) -> None:
    """Record `total` BUY orders walking backwards from `anchor`."""
    for offset in range(total):
        detector.record_order(SYMBOL, OrderSide.BUY, timestamp=anchor - offset * spacing)


# --- hourly gate -----------------------------------------------------------------


def test_four_orders_under_five_stay_quiet(clock) -> None:  # noqa: ARG001
    probe = build(max_orders_per_hour=5)
    place_run(probe, 4, time.time())
    assert probe.check_hourly_rate(now=time.time()) is None


def test_fifth_order_inside_hour_triggers_restricted() -> None:
    probe = build(max_orders_per_hour=5)
    moment = time.time()
    place_run(probe, 5, moment)

    tripwire = probe.check_hourly_rate(now=moment)
    assert tripwire is not None
    assert (tripwire.rule, tripwire.severity) == ("hourly_rate", RiskLevel.RESTRICTED)


def test_orders_outside_the_hour_do_not_count() -> None:
    probe = build(max_orders_per_hour=5)
    moment = time.time()

    for n in range(10):  # two hours stale — must be ignored by the window
        probe.record_order(SYMBOL, OrderSide.BUY, timestamp=moment - 7200 - n)
    place_run(probe, 3, moment)

    assert probe.check_hourly_rate(now=moment) is None


# --- daily gate --------------------------------------------------------------------


def test_nine_orders_under_ten_stay_quiet() -> None:
    probe = build(max_orders_per_day=10)
    moment = time.time()
    place_run(probe, 9, moment, spacing=100.0)

    assert probe.check_daily_rate(now=moment) is None


def test_tenth_order_of_day_raises_dangerous() -> None:
    probe = build(max_orders_per_day=10)
    moment = time.time()
    place_run(probe, 10, moment, spacing=100.0)

    tripwire = probe.check_daily_rate(now=moment)
    assert tripwire is not None
    assert (tripwire.rule, tripwire.severity) == ("daily_rate", RiskLevel.DANGEROUS)


# --- reversal counting ------------------------------------------------------------------


def test_repeating_same_side_is_not_a_reversal() -> None:
    probe = build(max_reversals_per_hour=2)
    moment = time.time()
    probe.record_order(SYMBOL, OrderSide.BUY, timestamp=moment - 60)
    probe.record_order(SYMBOL, OrderSide.BUY, timestamp=moment - 30)

    assert probe.check_reversal(SYMBOL, OrderSide.BUY, now=moment) is None


def test_alternating_sides_exhausts_reversal_budget() -> None:
    probe = build(max_reversals_per_hour=2, reversal_window_seconds=300)
    moment = time.time()
    # BUY → SELL → BUY creates 2 reversals
    probe.record_order(SYMBOL, OrderSide.BUY, timestamp=moment - 120)
    probe.record_order(SYMBOL, OrderSide.SELL, timestamp=moment - 60)
    # Now checking if a BUY would create a 3rd reversal

    tripwire = probe.check_reversal(SYMBOL, OrderSide.BUY, now=moment)
    assert tripwire is not None and tripwire.rule == "frequent_reversal"


def test_reversal_history_is_per_symbol() -> None:
    probe = build(max_reversals_per_hour=2)
    moment = time.time()
    probe.record_order("BTC/USDT", OrderSide.BUY, timestamp=moment - 60)
    probe.record_order("ETH/USDT", OrderSide.SELL, timestamp=moment - 30)

    # Only 1 reversal on BTC/USDT, should not trigger with limit=2
    assert probe.check_reversal("BTC/USDT", OrderSide.SELL, now=moment) is None


# --- evaluate -------------------------------------------------------------------------------


def test_evaluate_reports_every_breached_rule_at_once() -> None:
    probe = build(max_orders_per_hour=3, max_orders_per_day=5)
    moment = time.time()
    place_run(probe, 5, moment)

    breached = {alert.rule for alert in probe.evaluate(SYMBOL, OrderSide.BUY, now=moment)}
    assert breached >= {"hourly_rate", "daily_rate"}


def test_evaluate_on_fresh_detector_returns_empty() -> None:
    probe = OvertradingDetector()
    moment = time.time()
    probe.record_order(SYMBOL, OrderSide.BUY, timestamp=moment)

    assert probe.evaluate(SYMBOL, OrderSide.BUY, now=moment) == []


# --- reset -------------------------------------------------------------------------------------


def test_reset_wipes_rate_history() -> None:
    probe = build(max_orders_per_hour=2)
    moment = time.time()
    place_run(probe, 5, moment)
    assert probe.check_hourly_rate(now=moment) is not None

    probe.reset()
    assert probe.check_hourly_rate(now=moment) is None
