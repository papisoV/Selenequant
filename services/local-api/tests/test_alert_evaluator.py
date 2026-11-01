"""Evaluator: hit / miss / no-data across every rule type.

The distinction these tests exist to protect is ``None`` (could not evaluate)
versus "evaluated and did not fire". Collapsing them would mean a rule fires —
or stays silent — for a reason that has nothing to do with the market.
"""

from __future__ import annotations

import pytest

from app.core.alert_evaluator import (
    AlertContext,
    evaluate,
    evaluate_detail,
    evaluate_large_trade,
    evaluate_news,
    indicator_min_bars,
)


def _ctx(price=None, indicator=None):
    return AlertContext(
        price=lambda _s: price,
        indicator=lambda _s, _m: indicator,
    )


def _price_rule(**threshold):
    return {
        "id": "r1",
        "name": "BTC",
        "type": "price",
        "symbol": "BTC/USDT",
        "threshold": {"operator": "gt", **threshold},
    }


# ---- price -----------------------------------------------------------------


def test_price_gt_hits_when_crossed():
    hit = evaluate(_price_rule(value=100), _ctx(price=150.0))
    assert hit is not None
    assert hit.rule_id == "r1"
    assert hit.observed == 150.0
    assert hit.threshold == 100.0


def test_price_gt_misses_below_threshold():
    assert evaluate(_price_rule(value=100), _ctx(price=99.9)) is None


def test_price_lt_hits_when_fallen_through():
    rule = _price_rule(operator="lt", value=100)
    hit = evaluate(rule, _ctx(price=99.0))
    assert hit is not None
    assert hit.observed == 99.0


def test_price_lt_misses_above_threshold():
    rule = _price_rule(operator="lt", value=100)
    assert evaluate(rule, _ctx(price=100.1)) is None


def test_price_boundary_is_inclusive():
    """The form labels the operators "突破(≥)" / "跌破(≤)", so equality fires."""
    assert evaluate(_price_rule(value=100), _ctx(price=100.0)) is not None
    assert (
        evaluate(_price_rule(operator="lt", value=100), _ctx(price=100.0)) is not None
    )


def test_price_with_no_quote_is_no_data_not_a_hit():
    hit, reason = evaluate_detail(_price_rule(value=100), _ctx(price=None))
    assert hit is None
    assert reason == "no_data"


def test_price_without_symbol_is_rejected_as_bad_threshold():
    rule = _price_rule(value=100)
    rule["symbol"] = ""
    hit, reason = evaluate_detail(rule, _ctx(price=150.0))
    assert hit is None
    assert reason == "bad_threshold"


def test_price_non_numeric_threshold_does_not_compare():
    hit, reason = evaluate_detail(_price_rule(value="100"), _ctx(price=150.0))
    assert hit is None
    assert reason == "bad_threshold"


def test_boolean_threshold_is_not_accepted_as_a_number():
    """``True`` is an ``int`` in Python; a stray bool must not become 1.0."""
    hit, reason = evaluate_detail(_price_rule(value=True), _ctx(price=0.5))
    assert hit is None
    assert reason == "bad_threshold"


def test_unknown_operator_does_not_fire():
    """An unrecognised operator must not be guessed.

    ``gt`` and ``lt`` are exact opposites, so defaulting a typo'd operator to
    either side can invert the user's intent — a misspelled "lt" becoming a
    breakout alert at the same price. Refusing to evaluate is the only reading
    that cannot be wrong in the dangerous direction.
    """
    rule = {
        "id": "r",
        "name": "n",
        "type": "price",
        "symbol": "X",
        "threshold": {"operator": "bogus", "value": 100},
    }
    hit, reason = evaluate_detail(rule, _ctx(price=150.0))
    assert hit is None
    assert reason == "bad_threshold"


# ---- indicator -------------------------------------------------------------


def _indicator_rule(indicator="RSI", value=70):
    return {
        "id": "i1",
        "name": "RSI",
        "type": "indicator",
        "symbol": "600519",
        "threshold": {"indicator": indicator, "value": value},
    }


def test_indicator_hits_at_or_above():
    assert evaluate(_indicator_rule(value=70), _ctx(indicator=70.0)) is not None
    assert evaluate(_indicator_rule(value=70), _ctx(indicator=85.0)) is not None


def test_indicator_misses_below():
    assert evaluate(_indicator_rule(value=70), _ctx(indicator=69.9)) is None


def test_indicator_without_data_is_silent():
    hit, reason = evaluate_detail(_indicator_rule(), _ctx(indicator=None))
    assert hit is None
    assert reason == "no_data"


def test_indicator_min_bars_is_positive():
    assert indicator_min_bars() >= 14  # RSI's default window must fit


# ---- non-pollable types ----------------------------------------------------


@pytest.mark.parametrize("rule_type", ["large_trade", "voice", "onchain", ""])
def test_polling_evaluator_declines_event_and_unknown_types(rule_type):
    """``large_trade``/``voice`` are evaluated by their own paths.

    The polling evaluator must decline them loudly (in the reason) rather than
    silently treat them as "not met": a rule that is never eligible for the
    poll loop is a wiring bug, and ``not_pollable`` is what makes it visible.
    """
    rule = {"id": "x", "type": rule_type, "threshold": {"min_notional_usd": 1}}
    hit, reason = evaluate_detail(rule, _ctx())
    assert hit is None
    assert reason == "not_pollable"


def test_missing_threshold_is_reported_not_raised():
    hit, reason = evaluate_detail({"id": "x", "type": "price"}, _ctx(price=1.0))
    assert hit is None
    assert reason == "bad_threshold"


# ---- large_trade -----------------------------------------------------------


def _large_rule(minimum=1_000_000):
    return {
        "id": "lt1",
        "name": "大单",
        "type": "large_trade",
        "threshold": {"min_notional_usd": minimum},
    }


def test_large_trade_fires_at_or_above_threshold():
    hit = evaluate_large_trade(_large_rule(1_000_000), 1_000_000.0, "BTC/USDT")
    assert hit is not None
    assert hit.observed == 1_000_000.0


def test_large_trade_ignores_smaller_notional():
    assert evaluate_large_trade(_large_rule(1_000_000), 999_999.0, "BTC/USDT") is None


def test_large_trade_without_numeric_threshold_never_fires():
    rule = {"id": "lt", "name": "n", "type": "large_trade", "threshold": {}}
    assert evaluate_large_trade(rule, 10**9, "BTC/USDT") is None


# ---- voice (news keyword) --------------------------------------------------


def _news_rule(keyword="爆仓"):
    return {
        "id": "n1",
        "name": "新闻",
        "type": "voice",
        "threshold": {"keyword": keyword},
    }


def test_news_fires_when_matches_exist():
    hit = evaluate_news(_news_rule(), 2, "某公司爆仓")
    assert hit is not None
    assert "爆仓" in hit.text
    assert hit.observed == 2.0


def test_news_is_silent_with_zero_matches():
    assert evaluate_news(_news_rule(), 0, None) is None


def test_news_text_omits_dangling_separator_without_title():
    hit = evaluate_news(_news_rule(), 1, None)
    assert hit is not None
    assert not hit.text.rstrip().endswith(":")


# ---- voice, wired through the round context --------------------------------


def test_voice_rule_reads_its_count_from_the_news_reader():
    ctx = AlertContext(
        price=lambda _s: None,
        indicator=lambda _s, _m: None,
        news=lambda rule_id: (3, "某所爆仓") if rule_id == "n1" else None,
    )
    hit, reason = evaluate_detail(_news_rule(), ctx)
    assert reason == "hit"
    assert hit is not None and hit.observed == 3.0


def test_voice_without_a_news_reader_is_not_pollable():
    """A deployment with no news store must not invent a count of zero."""
    ctx = AlertContext(price=lambda _s: None, indicator=lambda _s, _m: None)
    hit, reason = evaluate_detail(_news_rule(), ctx)
    assert hit is None
    assert reason == "not_pollable"


def test_voice_with_a_failed_query_is_no_data_not_not_met():
    ctx = AlertContext(
        price=lambda _s: None,
        indicator=lambda _s, _m: None,
        news=lambda _rule_id: None,
    )
    hit, reason = evaluate_detail(_news_rule(), ctx)
    assert hit is None
    assert reason == "no_data"


def test_voice_with_zero_matches_is_not_met():
    ctx = AlertContext(
        price=lambda _s: None,
        indicator=lambda _s, _m: None,
        news=lambda _rule_id: (0, None),
    )
    hit, reason = evaluate_detail(_news_rule(), ctx)
    assert hit is None
    assert reason == "not_met"


def test_voice_with_a_blank_keyword_is_bad_threshold():
    rule = _news_rule(keyword="   ")
    ctx = AlertContext(
        price=lambda _s: None,
        indicator=lambda _s, _m: None,
        news=lambda _rule_id: (5, "x"),
    )
    hit, reason = evaluate_detail(rule, ctx)
    assert hit is None
    assert reason == "bad_threshold"


def test_voice_never_consults_price_or_indicator():
    """A voice rule has no symbol; touching the price reader would be a
    wasted (and, for A-shares, a potentially slow) call every round."""

    def _boom(_arg, *rest):
        raise AssertionError("voice must not read price/indicator")

    ctx = AlertContext(price=_boom, indicator=_boom, news=lambda _rule_id: (1, "t"))
    hit, _reason = evaluate_detail(_news_rule(), ctx)
    assert hit is not None
