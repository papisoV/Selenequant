"""EventBus behaviour coverage — typed routing, listener bookkeeping,
error containment, and the two async publish paths."""

from __future__ import annotations

import asyncio

import pytest

from selene_space_market.event_bus import EventBus


class _Payload:
    def __init__(self, n: int = 0) -> None:
        self.n = n


class _Unrelated:
    pass


class TestTypedRouting:
    """Subscribers only see events of their subscribed type."""

    def test_sync_listener_receives_published_instance(self) -> None:
        bus = EventBus()
        seen: list[_Payload] = []
        bus.subscribe(_Payload, seen.append)

        bus.publish(_Payload(42))

        assert len(seen) == 1
        assert seen[0].n == 42

    def test_two_listeners_both_fire(self) -> None:
        bus = EventBus()
        first: list[_Payload] = []
        second: list[_Payload] = []
        bus.subscribe(_Payload, first.append)
        bus.subscribe(_Payload, second.append)

        bus.publish(_Payload(1))
        assert len(first) == 1
        assert len(second) == 1

    def test_types_do_not_cross(self) -> None:
        bus = EventBus()
        payload_hits: list[_Payload] = []
        stray_hits: list[_Unrelated] = []
        bus.subscribe(_Payload, payload_hits.append)
        bus.subscribe(_Unrelated, stray_hits.append)

        bus.publish(_Payload(1))
        assert len(payload_hits) == 1
        assert len(stray_hits) == 0


class TestListenerBookkeeping:
    """unsubscribe / handler_count / clear."""

    def test_removed_listener_stops_receiving(self) -> None:
        bus = EventBus()
        seen: list[_Payload] = []
        bus.subscribe(_Payload, seen.append)
        bus.unsubscribe(_Payload, seen.append)

        bus.publish(_Payload(1))
        assert len(seen) == 0

    def test_removing_unknown_listener_is_noop(self) -> None:
        bus = EventBus()
        bus.unsubscribe(_Payload, lambda e: None)  # must not raise

    def test_handler_count_tracks_subscriptions(self) -> None:
        bus = EventBus()
        assert bus.handler_count(_Payload) == 0
        bus.subscribe(_Payload, lambda e: None)
        assert bus.handler_count(_Payload) == 1

    def test_clear_drops_every_listener(self) -> None:
        bus = EventBus()
        bus.subscribe(_Payload, lambda e: None)
        bus.clear()
        assert bus.handler_count(_Payload) == 0


class TestErrorContainment:
    """A raising handler must not starve later subscribers."""

    def test_raising_handler_does_not_block_successor(self) -> None:
        bus = EventBus()
        seen: list[_Payload] = []

        def broken(e: _Payload) -> None:
            raise RuntimeError("boom")

        bus.subscribe(_Payload, broken)
        bus.subscribe(_Payload, seen.append)

        bus.publish(_Payload(1))
        assert len(seen) == 1


class TestAsyncPaths:
    """publish_async awaits coroutines; publish schedules them."""

    @pytest.mark.asyncio
    async def test_publish_async_runs_coroutine_handler(self) -> None:
        bus = EventBus()
        seen: list[_Payload] = []

        async def handler(e: _Payload) -> None:
            seen.append(e)

        bus.subscribe(_Payload, handler)
        await bus.publish_async(_Payload(99))
        assert len(seen) == 1
        assert seen[0].n == 99

    @pytest.mark.asyncio
    async def test_sync_publish_schedules_async_handlers(self) -> None:
        bus = EventBus()
        values: list[int] = []

        async def handler(e: _Payload) -> None:
            values.append(e.n)

        bus.subscribe(_Payload, handler)
        bus.publish(_Payload(7))
        await asyncio.sleep(0.05)
        assert values == [7]
