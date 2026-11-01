"""Tests for SSEBus per-listener queue design (optimization #5).

Verifies:
- push() delivers events to all listeners via per-listener queue
- Fresh connections (last_id=0) don't get replay
- Reconnect with last_id<N replays missing events
- Buffer rollover (last_id < oldest) warns + adjusts
- max_events trimming keeps ring buffer bounded
- unregister removes listener (no further pushes)
- Properties: listener_count, buffered_event_count, current_event_id
"""
from __future__ import annotations

import time


from tgwatcher.web.sse_bus import SSEBus


class TestPushAndDrain:
    def test_push_delivers_to_single_listener(self):
        bus = SSEBus(max_events=10)
        q, _ = bus.register_listener(0)
        bus.push("test", {"hello": "world"})
        event = q.get(timeout=1.0)
        assert event["type"] == "test"
        assert event["data"] == {"hello": "world"}
        assert event["id"] == 1

    def test_push_delivers_to_multiple_listeners(self):
        bus = SSEBus(max_events=10)
        q1, _ = bus.register_listener(0)
        q2, _ = bus.register_listener(0)
        bus.push("test", {"n": 1})
        e1 = q1.get(timeout=1.0)
        e2 = q2.get(timeout=1.0)
        assert e1 == e2
        assert e1["data"] == {"n": 1}

    def test_push_does_not_hold_lock_during_put(self):
        """Slow listener (full queue) must not block other listeners."""
        bus = SSEBus(max_events=10)
        # Fill one listener's queue manually to capacity
        full_q, _ = bus.register_listener(0)
        for i in range(1024):  # MAX_LISTENER_QUEUE = 1024
            full_q.put_nowait({"id": 0, "type": "filler", "data": {}})

        # Other listener — should still receive events immediately
        fast_q, _ = bus.register_listener(0)

        start = time.monotonic()
        bus.push("test", {"check": True})
        elapsed = time.monotonic() - start

        # If push held a shared lock during put to full_q, fast_q would lag.
        # With per-listener queue + put_nowait drop, fast_q.get returns fast.
        e = fast_q.get(timeout=1.0)
        assert e["data"] == {"check": True}
        assert elapsed < 0.5  # generous bound


class TestReplay:
    def test_fresh_connection_no_replay(self):
        bus = SSEBus(max_events=10)
        bus.push("a", {})
        bus.push("b", {})
        bus.push("c", {})
        # Fresh connection with last_id=0 → no replay
        q, _ = bus.register_listener(0)
        assert q.qsize() == 0

    def test_replay_after_disconnect(self):
        bus = SSEBus(max_events=10)
        bus.push("a", {"n": 1})  # id=1
        bus.push("b", {"n": 2})  # id=2
        bus.push("c", {"n": 3})  # id=3
        # Reconnect with last_id=1 → replay events 2,3
        q, _ = bus.register_listener(1)
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        assert len(events) == 2
        assert events[0]["id"] == 2
        assert events[1]["id"] == 3

    def test_buffer_rollover_reconnect_adjusts(self, caplog):
        bus = SSEBus(max_events=5)
        # Push 10 events → buffer keeps last 5 (ids 6-10)
        for i in range(10):
            bus.push("e", {"n": i})
        # Reconnect with last_id=3 (below buffer floor=6)
        with caplog.at_level("WARNING", logger="tgwatcher.web.sse_bus"):
            q, adjusted = bus.register_listener(3)
        # Should adjust to oldest-1 = 5, so events 6-10 are replayed
        assert adjusted == 5
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        assert len(events) == 5
        assert events[0]["id"] == 6
        assert events[-1]["id"] == 10
        assert any("older than buffer floor" in r.message for r in caplog.records)


class TestTrimAndBounded:
    def test_max_events_trims_oldest(self):
        bus = SSEBus(max_events=3)
        for i in range(5):
            bus.push("e", {"n": i})
        # Buffer should only keep last 3
        assert bus.buffered_event_count == 3
        events = bus.events_since(0)
        assert [e["id"] for e in events] == [3, 4, 5]


class TestUnregister:
    def test_unregister_stops_delivery(self):
        bus = SSEBus(max_events=10)
        q, _ = bus.register_listener(0)
        bus.unregister_listener(q)
        bus.push("test", {"after": True})
        # Queue should remain empty after unregister
        assert q.qsize() == 0
        # Listener count drops
        assert bus.listener_count == 0


class TestProperties:
    def test_listener_count_tracks_registers(self):
        bus = SSEBus(max_events=10)
        assert bus.listener_count == 0
        q1, _ = bus.register_listener(0)
        assert bus.listener_count == 1
        q2, _ = bus.register_listener(0)
        assert bus.listener_count == 2
        bus.unregister_listener(q1)
        assert bus.listener_count == 1
        bus.unregister_listener(q2)
        assert bus.listener_count == 0

    def test_current_event_id_increments(self):
        bus = SSEBus(max_events=10)
        assert bus.current_event_id == 0
        bus.push("a", {})
        assert bus.current_event_id == 1
        bus.push("b", {})
        assert bus.current_event_id == 2

    def test_oldest_event_id_none_when_empty(self):
        bus = SSEBus(max_events=10)
        assert bus.oldest_event_id is None
        bus.push("a", {})
        assert bus.oldest_event_id == 1

    def test_buffered_event_count(self):
        bus = SSEBus(max_events=10)
        assert bus.buffered_event_count == 0
        bus.push("a", {})
        bus.push("b", {})
        assert bus.buffered_event_count == 2
