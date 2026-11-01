"""Large-trade rules fire from the event stream, not from a poll.

Three things are load-bearing here and each has a test: only qualifying prints
fire, a burst on one instrument pages once, and the bus handler never blocks.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from app.core.alert_large_trade import LargeTradeAlertBridge
from app.core.alert_loop import AlertDeduper


def _rule(rule_id="lt1", minimum=50_000, enabled=True):
    return {
        "id": rule_id,
        "name": rule_id.upper(),
        "type": "large_trade",
        "enabled": enabled,
        "threshold": {"min_notional_usd": minimum},
    }


class _Event:
    def __init__(self, notional_usd, symbol="BTC/USDT"):
        self.notional_usd = notional_usd
        self.symbol = symbol


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _bridge(rules, sent, **kwargs):
    return LargeTradeAlertBridge(
        load_rules=lambda: list(rules),
        notify=sent.append,
        loop=asyncio.get_running_loop(),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_qualifying_event_fires_once():
    sent = []
    bridge = _bridge([_rule()], sent)
    hits = await bridge.handle_event(_Event(80_000))
    assert [h.rule_id for h in hits] == ["lt1"]
    assert len(sent) == 1
    assert "80000" in sent[0]


@pytest.mark.asyncio
async def test_below_threshold_event_is_silent():
    sent = []
    bridge = _bridge([_rule(minimum=50_000)], sent)
    assert await bridge.handle_event(_Event(49_999)) == []
    assert sent == []


@pytest.mark.asyncio
async def test_event_without_numeric_notional_is_ignored():
    """A malformed event must not be treated as a zero-size (thus hitting) trade."""
    sent = []
    bridge = _bridge([_rule(minimum=0)], sent)
    assert await bridge.handle_event(_Event("big")) == []
    assert sent == []


@pytest.mark.asyncio
async def test_only_large_trade_rules_are_used():
    """The rule's ``type`` decides which evaluator runs.

    A payload carrying both a foreign type and a ``min_notional_usd`` key is
    reachable in practice: rules written before the threshold sanitiser existed
    still carry whatever keys the client sent. Firing one because a *different*
    type's field happened to be present would page the user for a rule they
    wrote about something else.
    """
    sent = []
    mislabelled = {
        "id": "p1",
        "name": "P",
        "type": "price",
        "symbol": "BTC/USDT",
        "threshold": {"operator": "gt", "min_notional_usd": 1},
    }
    bridge = _bridge([mislabelled], sent)
    assert await bridge.handle_event(_Event(1_000_000)) == []
    assert sent == []


@pytest.mark.asyncio
async def test_zero_notional_is_not_a_large_trade():
    """``min_notional_usd: 0`` is a legal threshold, so a zero-size event would
    clear it. Zero is not a trade; it is a gap in the feed."""
    sent = []
    bridge = _bridge([_rule(minimum=0)], sent)
    assert await bridge.handle_event(_Event(0)) == []
    assert sent == []


@pytest.mark.asyncio
async def test_notify_runs_off_the_event_loop_thread():
    """``dispatch`` does blocking network I/O.

    Called inline it stalls the loop this process shares with the market
    ingestion path, so the notification must go through a worker thread.
    """
    seen: list[int] = []
    loop_thread = threading.get_ident()

    bridge = _bridge([_rule()], [])
    bridge._notify = lambda _t: seen.append(threading.get_ident())
    await bridge.handle_event(_Event(100_000))

    assert len(seen) == 1
    assert seen[0] != loop_thread, "dispatch 在主线程跑 = 行情被网络调用堵住"


@pytest.mark.asyncio
async def test_one_rule_that_raises_does_not_hide_the_others():
    class _Boom(dict):
        """A threshold whose read throws — the type filter must still work on
        other keys, so only the threshold field blows up."""

        def get(self, key, default=None):
            if key == "min_notional_usd":
                raise RuntimeError("threshold unreadable")
            return super().get(key, default)

    sent = []
    broken = {"id": "bad", "name": "bad", "type": "large_trade", "threshold": _Boom()}
    bridge = _bridge([broken, _rule("good")], sent)

    hits = await bridge.handle_event(_Event(100_000))

    assert [h.rule_id for h in hits] == ["good"]
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_a_malformed_event_does_not_end_the_consumer():
    """One bad event must not stop the pump: the next whale print is still a
    user's alert. Kills the ``try`` around ``handle_event`` in ``run``."""

    class _Bad:
        @property
        def notional_usd(self):
            raise RuntimeError("event shape changed")

        symbol = "BTC/USDT"

    sent = []
    bridge = _bridge([_rule()], sent)
    bridge._enqueue(_Bad())
    bridge._enqueue(_Event(100_000, "ETH/USDT"))

    task = asyncio.create_task(bridge.run())
    try:
        for _ in range(100):
            if sent:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert len(sent) == 1
    assert "ETH/USDT" in sent[0]


@pytest.mark.asyncio
async def test_burst_on_one_instrument_pages_once_but_other_symbol_still_fires():
    clock = _Clock()
    sent = []
    bridge = _bridge(
        [_rule()],
        sent,
        deduper=AlertDeduper(window_seconds=1800, clock=clock),
    )
    for _ in range(5):
        await bridge.handle_event(_Event(100_000, "BTC/USDT"))
    assert len(sent) == 1

    await bridge.handle_event(_Event(100_000, "ETH/USDT"))
    assert len(sent) == 2, "静默窗口按规则|标的,不能把 ETH 一起吃掉"


@pytest.mark.asyncio
async def test_window_rearms_after_it_elapses():
    clock = _Clock()
    sent = []
    bridge = _bridge(
        [_rule()],
        sent,
        deduper=AlertDeduper(window_seconds=60, clock=clock),
    )
    await bridge.handle_event(_Event(100_000))
    clock.advance(61)
    await bridge.handle_event(_Event(100_000))
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_notify_failure_does_not_stop_later_rules():
    seen = []

    def _notify(text):
        seen.append(text)
        if len(seen) == 1:
            raise RuntimeError("channel down")

    bridge = LargeTradeAlertBridge(
        load_rules=lambda: [_rule("a"), _rule("b")],
        notify=_notify,
        loop=asyncio.get_running_loop(),
    )
    hits = await bridge.handle_event(_Event(100_000))
    assert len(seen) == 2
    assert [h.rule_id for h in hits] == ["b"], "失败的那条不算送达"


@pytest.mark.asyncio
async def test_rule_list_is_cached_between_events():
    calls = []
    bridge = LargeTradeAlertBridge(
        load_rules=lambda: (calls.append(1), [_rule()])[1],
        notify=lambda _t: None,
        loop=asyncio.get_running_loop(),
        rule_ttl_seconds=2.0,
    )
    for _ in range(5):
        await bridge.handle_event(_Event(100_000))
    assert len(calls) == 1, "每条成交都读一次库会把行情线程拖垮"


@pytest.mark.asyncio
async def test_rule_list_reloads_after_ttl_and_sees_new_rules():
    clock = _Clock()
    rules = []
    bridge = LargeTradeAlertBridge(
        load_rules=lambda: list(rules),
        notify=lambda _t: None,
        loop=asyncio.get_running_loop(),
        rule_ttl_seconds=2.0,
        clock=clock,
    )
    assert await bridge.handle_event(_Event(100_000)) == []
    rules.append(_rule())
    clock.advance(3)
    assert len(await bridge.handle_event(_Event(100_000, "ETH/USDT"))) == 1


@pytest.mark.asyncio
async def test_store_failure_keeps_the_previous_snapshot():
    """A store hiccup must not silently stop monitoring an existing rule."""
    state = {"fail": False}

    def _load():
        if state["fail"]:
            raise RuntimeError("db locked")
        return [_rule()]

    clock = _Clock()
    bridge = LargeTradeAlertBridge(
        load_rules=_load,
        notify=lambda _t: None,
        loop=asyncio.get_running_loop(),
        rule_ttl_seconds=1.0,
        clock=clock,
    )
    assert len(await bridge.handle_event(_Event(100_000, "BTC/USDT"))) == 1
    state["fail"] = True
    clock.advance(2)
    assert len(await bridge.handle_event(_Event(100_000, "ETH/USDT"))) == 1


@pytest.mark.asyncio
async def test_bus_handler_queues_without_touching_the_store():
    """``on_event`` runs on the ingestion path: it may only enqueue."""
    reads = []
    bridge = LargeTradeAlertBridge(
        load_rules=lambda: (reads.append(1), [_rule()])[1],
        notify=lambda _t: None,
        loop=asyncio.get_running_loop(),
    )
    bridge.on_event(_Event(100_000))
    assert reads == [], "订阅回调里做 I/O 会卡住行情摄取"
    await asyncio.sleep(0)  # let call_soon_threadsafe land
    assert bridge._queue.qsize() == 1


@pytest.mark.asyncio
async def test_consumer_drains_queued_events():
    sent = []
    bridge = _bridge([_rule()], sent)
    bridge.on_event(_Event(100_000, "BTC/USDT"))
    bridge.on_event(_Event(100_000, "ETH/USDT"))
    task = asyncio.create_task(bridge.run())
    try:
        for _ in range(50):
            if len(sent) == 2:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_full_queue_drops_instead_of_growing():
    bridge = LargeTradeAlertBridge(
        load_rules=lambda: [_rule()],
        notify=lambda _t: None,
        loop=asyncio.get_running_loop(),
        queue_depth=2,
    )
    for _ in range(5):
        bridge._enqueue(_Event(100_000))
    assert bridge._queue.qsize() == 2
    assert bridge._dropped == 3


@pytest.mark.asyncio
async def test_handler_after_loop_close_does_not_raise():
    """Shutdown closes the loop before the bus is drained; the handler must
    count that as a drop rather than raise into the bus.

    A loop of its own is closed here — closing the running one would leave
    pytest-asyncio driving a dead loop for the rest of the suite.
    """
    bridge = LargeTradeAlertBridge(
        load_rules=lambda: [_rule()],
        notify=lambda _t: None,
        loop=asyncio.get_running_loop(),
    )
    dead = asyncio.new_event_loop()
    dead.close()
    bridge._loop = dead

    bridge.on_event(_Event(100_000))  # must not raise
    assert bridge._dropped == 1
