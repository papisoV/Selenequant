"""The alert loop: dedupe, failure isolation, and no-data silence.

The loop is where a monitoring feature quietly becomes a pager. These tests pin
the three behaviours that keep it from doing that.
"""

from __future__ import annotations

import pytest

from app.core.alert_evaluator import AlertContext
from app.core.alert_loop import AlertDeduper, run_alert_round


def _ctx(price=None, indicator=None):
    return AlertContext(
        price=lambda _s: price,
        indicator=lambda _s, _m: indicator,
    )


def _price_rule(rule_id="r1", value=100, symbol="BTC/USDT"):
    return {
        "id": rule_id,
        "name": rule_id.upper(),
        "type": "price",
        "symbol": symbol,
        "threshold": {"operator": "gt", "value": value},
    }


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.mark.asyncio
async def test_hit_is_delivered_once():
    sent = []
    hits = await run_alert_round(
        rules=[_price_rule()],
        ctx=_ctx(price=150.0),
        deduper=AlertDeduper(),
        notify=sent.append,
    )
    assert len(hits) == 1
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_same_rule_firing_twice_in_quiet_window_delivers_once():
    """A condition that stays true must not page every round."""
    clock = _Clock()
    deduper = AlertDeduper(window_seconds=1800, clock=clock)
    sent = []
    for _ in range(3):
        await run_alert_round(
            rules=[_price_rule()],
            ctx=_ctx(price=150.0),
            deduper=deduper,
            notify=sent.append,
        )
        clock.advance(30)
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_rule_fires_again_after_quiet_window_elapses():
    clock = _Clock()
    deduper = AlertDeduper(window_seconds=60, clock=clock)
    sent = []
    await run_alert_round(
        rules=[_price_rule()],
        ctx=_ctx(price=150.0),
        deduper=deduper,
        notify=sent.append,
    )
    clock.advance(61)
    await run_alert_round(
        rules=[_price_rule()],
        ctx=_ctx(price=150.0),
        deduper=deduper,
        notify=sent.append,
    )
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_miss_does_not_consume_the_window():
    """A rule that stops matching must fire again as soon as it matches.

    Otherwise the window would be armed by a *miss*, and a crossing right after
    it would be swallowed.
    """
    clock = _Clock()
    deduper = AlertDeduper(window_seconds=1800, clock=clock)
    sent = []
    await run_alert_round(
        rules=[_price_rule()], ctx=_ctx(price=50.0), deduper=deduper, notify=sent.append
    )
    await run_alert_round(
        rules=[_price_rule()],
        ctx=_ctx(price=150.0),
        deduper=deduper,
        notify=sent.append,
    )
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_quiet_window_is_per_rule():
    clock = _Clock()
    deduper = AlertDeduper(window_seconds=1800, clock=clock)
    sent = []
    rules = [_price_rule("a"), _price_rule("b")]
    await run_alert_round(
        rules=rules, ctx=_ctx(price=150.0), deduper=deduper, notify=sent.append
    )
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_no_data_produces_no_notification():
    sent = []
    hits = await run_alert_round(
        rules=[_price_rule()],
        ctx=_ctx(price=None),
        deduper=AlertDeduper(),
        notify=sent.append,
    )
    assert hits == []
    assert sent == []


@pytest.mark.asyncio
async def test_one_broken_rule_does_not_stop_the_others():
    """A rule whose evaluation raises must not end the round.

    The broken rule's ``threshold`` is not a dict, which ``evaluate_detail``
    reports as ``bad_threshold`` — the guard under test is the per-rule
    ``try`` in the loop, so this also covers a real raise from a future rule
    type whose evaluator throws.
    """
    broken = {
        "id": "bad",
        "name": "bad",
        "type": "price",
        "symbol": "X",
        "threshold": "not-a-dict",
    }
    sent = []
    hits = await run_alert_round(
        rules=[broken, _price_rule("good")],
        ctx=_ctx(price=150.0),
        deduper=AlertDeduper(),
        notify=sent.append,
    )
    assert [h.rule_id for h in hits] == ["good"]
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_notify_failure_does_not_stop_the_round():
    """One dead channel must not prevent the next rule's notification."""

    seen = []

    def _notify_then_record(text):
        seen.append(text)
        if len(seen) == 1:
            raise RuntimeError("channel down")
        return None

    rules = [_price_rule("a"), _price_rule("b")]
    hits = await run_alert_round(
        rules=rules,
        ctx=_ctx(price=150.0),
        deduper=AlertDeduper(),
        notify=_notify_then_record,
    )
    # Both rules were evaluated and the second one still delivered.
    assert len(seen) == 2
    assert len(hits) == 2


@pytest.mark.asyncio
async def test_prefetch_failure_leaves_indicator_rules_silent_not_fired():
    """A failed bar read means "no data", never a hit at a default value."""

    async def _boom(_rules):
        raise RuntimeError("db gone")

    rule = {
        "id": "i1",
        "name": "RSI",
        "type": "indicator",
        "symbol": "600519",
        "threshold": {"indicator": "RSI", "value": 0},
    }
    sent = []
    hits = await run_alert_round(
        rules=[rule],
        ctx=_ctx(indicator=99.0),  # would hit if it were used
        deduper=AlertDeduper(),
        notify=sent.append,
        prefetch_indicators=_boom,
    )
    assert hits == []
    assert sent == []


@pytest.mark.asyncio
async def test_indicator_values_come_from_the_prefetcher():
    async def _prefetch(_rules):
        return {("600519", "RSI"): 80.0}

    rule = {
        "id": "i1",
        "name": "RSI",
        "type": "indicator",
        "symbol": "600519",
        "threshold": {"indicator": "RSI", "value": 70},
    }
    sent = []
    hits = await run_alert_round(
        rules=[rule],
        ctx=_ctx(indicator=None),  # the ctx reader must NOT be the source
        deduper=AlertDeduper(),
        notify=sent.append,
        prefetch_indicators=_prefetch,
    )
    assert [h.rule_id for h in hits] == ["i1"]
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_price_reader_is_called_once_per_symbol_per_round():
    calls = []

    def _price(symbol):
        calls.append(symbol)
        return 150.0

    ctx = AlertContext(price=_price, indicator=lambda _s, _m: None)
    await run_alert_round(
        rules=[_price_rule("a"), _price_rule("b")],
        ctx=ctx,
        deduper=AlertDeduper(),
        notify=lambda _t: None,
    )
    assert calls == ["BTC/USDT"]


@pytest.mark.asyncio
async def test_deleted_rule_windows_are_pruned():
    clock = _Clock()
    deduper = AlertDeduper(window_seconds=10, clock=clock)
    await run_alert_round(
        rules=[_price_rule("gone")],
        ctx=_ctx(price=150.0),
        deduper=deduper,
        notify=lambda _t: None,
    )
    clock.advance(11)
    await run_alert_round(
        rules=[], ctx=_ctx(price=150.0), deduper=deduper, notify=lambda _t: None
    )
    # The map must not retain an entry per deleted rule forever.
    assert deduper._last_fired == {}


@pytest.mark.asyncio
async def test_voice_hit_is_delivered_and_text_names_the_keyword():
    rule = {
        "id": "v1",
        "name": "新闻",
        "type": "voice",
        "threshold": {"keyword": "爆仓"},
    }

    async def _prefetch(_rules):
        return {"v1": (2, "某所爆仓 3 亿")}

    sent = []
    hits = await run_alert_round(
        rules=[rule],
        ctx=_ctx(),
        deduper=AlertDeduper(),
        notify=sent.append,
        prefetch_news=_prefetch,
    )
    assert [h.rule_id for h in hits] == ["v1"]
    assert "爆仓" in sent[0]


@pytest.mark.asyncio
async def test_voice_prefetch_failure_is_silent_not_a_fire():
    """A news-store outage must never look like "the keyword matched"."""

    async def _boom(_rules):
        raise RuntimeError("news db gone")

    rule = {"id": "v1", "name": "新闻", "type": "voice", "threshold": {"keyword": "x"}}
    sent = []
    hits = await run_alert_round(
        rules=[rule],
        ctx=_ctx(),
        deduper=AlertDeduper(),
        notify=sent.append,
        prefetch_news=_boom,
    )
    assert hits == []
    assert sent == []


@pytest.mark.asyncio
async def test_voice_rules_without_a_prefetcher_stay_silent():
    rule = {"id": "v1", "name": "新闻", "type": "voice", "threshold": {"keyword": "x"}}
    sent = []
    hits = await run_alert_round(
        rules=[rule], ctx=_ctx(), deduper=AlertDeduper(), notify=sent.append
    )
    assert hits == []
    assert sent == []


@pytest.mark.asyncio
async def test_news_prefetch_is_skipped_when_no_voice_rule_is_enabled():
    """A crypto-only deployment must not pay for a news query every round."""
    calls = []

    async def _prefetch(rules):
        calls.append(rules)
        return {}

    await run_alert_round(
        rules=[_price_rule()],
        ctx=_ctx(price=1.0),
        deduper=AlertDeduper(),
        notify=lambda _t: None,
        prefetch_news=_prefetch,
    )
    assert calls == []
