"""Rule-write validation: the four supported types and their required fields."""

from __future__ import annotations

from app.core.alert_rule_schema import (
    ALERT_TYPES,
    PRIMARY_FIELD,
    sanitize_threshold,
    validate_rule,
)


def _rule(**overrides):
    base = {
        "name": "规则",
        "type": "price",
        "threshold": {"operator": "gt", "value": 100},
        "symbol": "BTC/USDT",
    }
    base.update(overrides)
    return base


def test_supported_types_are_the_four_with_data_sources():
    """``onchain`` is deliberately absent — no Glassnode key, stale cache only."""
    assert ALERT_TYPES == ("price", "indicator", "large_trade", "voice")
    assert "onchain" not in ALERT_TYPES


def test_every_type_declares_a_primary_field():
    assert set(PRIMARY_FIELD) == set(ALERT_TYPES)


def test_price_rule_is_valid():
    assert validate_rule(_rule()) == []


def test_onchain_is_rejected():
    errors = validate_rule(_rule(type="onchain", threshold={"min_value": 1}))
    assert any("type must be one of" in e for e in errors)


def test_name_is_required():
    errors = validate_rule(_rule(name="   "))
    assert any("name" in e for e in errors)


def test_symbol_required_for_price_and_indicator():
    assert validate_rule(_rule(symbol="")) != []
    assert (
        validate_rule(
            _rule(
                type="indicator",
                threshold={"indicator": "RSI", "value": 70},
                symbol="",
            )
        )
        != []
    )


def test_symbol_not_required_for_large_trade_and_voice():
    assert (
        validate_rule(
            _rule(type="large_trade", threshold={"min_notional_usd": 1}, symbol="")
        )
        == []
    )
    assert (
        validate_rule(_rule(type="voice", threshold={"keyword": "爆仓"}, symbol=""))
        == []
    )


def test_threshold_must_be_an_object():
    errors = validate_rule(_rule(threshold="nope"))
    assert any("threshold must be an object" in e for e in errors)


def test_numeric_primary_field_required_for_price():
    assert validate_rule(_rule(threshold={"operator": "gt"})) != []
    assert validate_rule(_rule(threshold={"operator": "gt", "value": "100"})) != []


def test_boolean_is_not_a_number():
    assert validate_rule(_rule(threshold={"operator": "gt", "value": True})) != []


def test_price_operator_must_be_known():
    assert validate_rule(_rule(threshold={"operator": "bogus", "value": 1})) != []
    assert validate_rule(_rule(threshold={"operator": "lt", "value": 1})) == []


def test_indicator_metric_must_be_computable_from_bars():
    for metric in ("SOPR", "NVT"):
        errors = validate_rule(
            _rule(
                type="indicator",
                threshold={"indicator": metric, "value": 1},
                symbol="BTC/USDT",
            )
        )
        assert errors != [], metric
    assert (
        validate_rule(
            _rule(
                type="indicator",
                threshold={"indicator": "MACD", "value": 0},
                symbol="BTC/USDT",
            )
        )
        == []
    )


def test_voice_keyword_must_be_non_empty_string():
    assert (
        validate_rule(_rule(type="voice", threshold={"keyword": ""}, symbol="")) != []
    )
    assert validate_rule(_rule(type="voice", threshold={"keyword": 5}, symbol="")) != []


# ---- threshold sanitising --------------------------------------------------


def test_sanitize_keeps_only_the_fields_the_type_reads():
    assert sanitize_threshold("price", {"operator": "gt", "value": 1, "extra": 2}) == {
        "operator": "gt",
        "value": 1,
    }


def test_sanitize_drops_the_previous_types_field_after_a_switch():
    """Switching price→large_trade must not leave ``value`` behind.

    ``large_trade`` ignores it, so a leftover would be invisible in the UI but
    present in the stored rule — and would come back if the type were switched
    again.
    """
    assert sanitize_threshold("large_trade", {"operator": "gt", "value": 100}) == {}


def test_sanitize_unknown_type_keeps_nothing():
    assert sanitize_threshold("onchain", {"min_value": 1}) == {}


def test_sanitize_passthrough_of_missing_threshold():
    assert sanitize_threshold("price", None) == {}
