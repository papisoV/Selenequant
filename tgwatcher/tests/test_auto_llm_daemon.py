"""Tests for tgwatcher.web.auto_llm_daemon — chained LLM+digest after crawl.

Verifies:
- trigger_after_crawl wakes the loop
- _run_llm_batch calls signal_engine.process_batch with stop_check
- _maybe_generate_digest respects 60min rate limit
- digest is generated when last_digest_at is None or older than interval
- failed LLM messages stay pending (existing WHERE llm_status='completed')
- shutdown signal terminates the loop
- SSE events pushed on digest_ready / llm_batch_start / llm_batch_done
- no trigger => no LLM call (60min timeout fallback skipped via mocked engine)
- pending count below min_signals => LLM skipped
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from tgwatcher.web.auto_llm_daemon import AutoLlmDaemon


@pytest.fixture()
def mock_engine():
    """Mock SignalEngine with process_batch returning a BatchResult-like."""
    engine = MagicMock()
    # BatchResult has total/completed/failed/skipped/errors
    result = MagicMock()
    result.total = 10
    result.completed = 8
    result.failed = 2
    result.skipped = 0
    result.errors = []
    engine.process_batch.return_value = result
    engine._llm = MagicMock()  # for generate_digest
    return engine


@pytest.fixture()
def mock_storage():
    """Mock Storage with get_session() context manager."""
    storage = MagicMock()
    # Default: pending count = 10
    sess = MagicMock()
    r = MagicMock()
    r.fetchone.return_value = (10,)
    sess.execute.return_value = r
    storage.get_session.return_value.__enter__.return_value = sess
    storage.get_session.return_value.__exit__.return_value = False
    return storage


@pytest.fixture()
def sse_events():
    """Captures SSE events pushed by the daemon."""
    events = []
    def _capture(event_type, data):
        events.append((event_type, data))
    return events, _capture


class TestTriggerAfterCrawl:
    def test_trigger_wakes_loop(self, mock_storage, mock_engine, sse_events):
        events, push = sse_events
        d = AutoLlmDaemon(mock_storage, mock_engine, push, digest_interval_minutes=60, min_signals=5)
        # Start loop in a thread
        t = threading.Thread(target=d.run_loop, daemon=True)
        t.start()
        # Trigger — should wake the loop
        d.trigger_after_crawl()
        # Wait for LLM batch to be called
        for _ in range(50):
            if mock_engine.process_batch.called:
                break
            time.sleep(0.02)
        d.signal_shutdown()
        t.join(timeout=2)
        assert mock_engine.process_batch.called

    def test_no_trigger_no_llm_call(self, mock_storage, mock_engine, sse_events):
        events, push = sse_events
        # Use a real Event that's never set. Patch wait() to block forever
        # but return True (signaling "shutdown") after we set _shutdown.
        d = AutoLlmDaemon(mock_storage, mock_engine, push, digest_interval_minutes=60, min_signals=5)
        original_wait = d._trigger_event.wait
        def fake_wait(timeout=None):
            # Block until shutdown is set, then return True to exit loop
            while not d._shutdown.is_set():
                time.sleep(0.01)
            return True
        d._trigger_event.wait = fake_wait
        t = threading.Thread(target=d.run_loop, daemon=True)
        t.start()
        time.sleep(0.2)  # loop is blocked in wait()
        d.signal_shutdown()
        t.join(timeout=2)
        # process_batch should NOT have been called (loop never reached body)
        assert not mock_engine.process_batch.called


class TestRunLlmBatch:
    def test_calls_process_batch_with_stop_check(self, mock_storage, mock_engine, sse_events):
        events, push = sse_events
        d = AutoLlmDaemon(mock_storage, mock_engine, push, min_signals=5)
        d._run_llm_batch()
        mock_engine.process_batch.assert_called_once()
        # stop_check should be callable
        kwargs = mock_engine.process_batch.call_args.kwargs
        assert "stop_check" in kwargs
        assert callable(kwargs["stop_check"])

    def test_pushes_llm_batch_start_and_done(self, mock_storage, mock_engine, sse_events):
        events, push = sse_events
        d = AutoLlmDaemon(mock_storage, mock_engine, push, min_signals=5)
        d._run_llm_batch()
        types = [e[0] for e in events]
        assert "llm_batch_start" in types
        assert "llm_batch_done" in types
        # Verify counts in done event
        done_event = next(e for ev_type, e in [(t, d) for t, d in events] if ev_type == "llm_batch_done")
        assert done_event["completed"] == 8
        assert done_event["failed"] == 2

    def test_skip_when_pending_below_min_signals(self, mock_storage, mock_engine, sse_events):
        events, push = sse_events
        # Override pending count to 3 (below min_signals=5).
        # count_pending() runs two queries: source1 (pending signal_factors)
        # and source2 (unprocessed messages). fetchone.side_effect returns
        # 3 for source1, 0 for source2 → total 3 < min_signals=5.
        r = MagicMock()
        r.fetchone.side_effect = [(3,), (0,)]
        mock_storage.get_session.return_value.__enter__.return_value.execute.return_value = r
        d = AutoLlmDaemon(mock_storage, mock_engine, push, min_signals=5)
        d._run_llm_batch()
        assert not mock_engine.process_batch.called

    def test_count_pending_failure_returns_zero(self, mock_storage, mock_engine, sse_events):
        events, push = sse_events
        # Make storage.get_session raise
        mock_storage.get_session.side_effect = Exception("DB down")
        d = AutoLlmDaemon(mock_storage, mock_engine, push, min_signals=5)
        # Should not raise — daemon continues with count=0, skips LLM
        d._run_llm_batch()
        assert not mock_engine.process_batch.called


class TestMaybeGenerateDigest:
    def test_generates_when_no_last_digest(self, mock_storage, mock_engine, sse_events):
        events, push = sse_events
        d = AutoLlmDaemon(mock_storage, mock_engine, push, digest_interval_minutes=60)
        with patch("tgwatcher.digest.generate_digest") as gd:
            result = MagicMock()
            result.id = 42
            result.signal_count = 15
            result.from_at = datetime(2026, 7, 25, 10, 0, 0)
            result.to_at = datetime(2026, 7, 25, 12, 0, 0)
            result.summary = "市场方向偏多..."
            gd.return_value = result
            d._maybe_generate_digest()
            gd.assert_called_once()
            assert d.last_digest_at is not None
            # SSE event pushed
            types = [e[0] for e in events]
            assert "digest_ready" in types

    def test_skips_when_within_rate_limit(self, mock_storage, mock_engine, sse_events):
        events, push = sse_events
        d = AutoLlmDaemon(mock_storage, mock_engine, push, digest_interval_minutes=60)
        # Pretend a digest was just generated
        recent = datetime(2026, 7, 25, 12, 0, 0)
        d.set_last_digest_at(recent)
        with patch("tgwatcher.digest.generate_digest") as gd, \
             patch("tgwatcher.web.auto_llm_daemon.utc_now") as un:
            un.return_value = recent + timedelta(minutes=10)  # 10min later
            d._maybe_generate_digest()
            gd.assert_not_called()

    def test_generates_when_past_rate_limit(self, mock_storage, mock_engine, sse_events):
        events, push = sse_events
        d = AutoLlmDaemon(mock_storage, mock_engine, push, digest_interval_minutes=60)
        recent = datetime(2026, 7, 25, 10, 0, 0)
        d.set_last_digest_at(recent)
        with patch("tgwatcher.digest.generate_digest") as gd, \
             patch("tgwatcher.web.auto_llm_daemon.utc_now") as un:
            un.return_value = recent + timedelta(minutes=90)  # 90min later
            result = MagicMock()
            result.id = 43
            result.signal_count = 5
            result.from_at = datetime(2026, 7, 25, 10, 0, 0)
            result.to_at = datetime(2026, 7, 25, 11, 30, 0)
            result.summary = "test"
            gd.return_value = result
            d._maybe_generate_digest()
            gd.assert_called_once()

    def test_disabled_when_interval_zero(self, mock_storage, mock_engine, sse_events):
        events, push = sse_events
        d = AutoLlmDaemon(mock_storage, mock_engine, push, digest_interval_minutes=0)
        with patch("tgwatcher.digest.generate_digest") as gd:
            d._maybe_generate_digest()
            gd.assert_not_called()

    def test_digest_exception_does_not_crash(self, mock_storage, mock_engine, sse_events):
        events, push = sse_events
        d = AutoLlmDaemon(mock_storage, mock_engine, push, digest_interval_minutes=60)
        with patch("tgwatcher.digest.generate_digest", side_effect=Exception("LLM down")):
            d._maybe_generate_digest()  # should not raise
            # error SSE event pushed
            types = [e[0] for e in events]
            assert "digest_error" in types
            # last_digest_at should NOT be updated (failed)
            assert d.last_digest_at is None


class TestShutdown:
    def test_shutdown_terminates_loop(self, mock_storage, mock_engine, sse_events):
        events, push = sse_events
        d = AutoLlmDaemon(mock_storage, mock_engine, push)
        t = threading.Thread(target=d.run_loop, daemon=True)
        t.start()
        time.sleep(0.05)
        d.signal_shutdown()
        t.join(timeout=2)
        assert not t.is_alive()


class TestFailedMessagesStayPending:
    """Existing WHERE llm_status='completed' filter in _fetch_signals
    ensures failed messages don't contaminate digest. This is a regression
    guard — the filter is in digest.py, this test verifies it's still there."""

    def test_fetch_signals_filters_failed_status(self):
        """Verify the SQL in _fetch_signals still has llm_status='completed'."""
        import inspect
        from tgwatcher import digest
        src = inspect.getsource(digest._fetch_signals)
        assert "llm_status = 'completed'" in src
        assert "is_signal = 1" in src


class TestSSEPushSafety:
    def test_push_callback_error_does_not_crash(self, mock_storage, mock_engine):
        # Push callback raises — daemon should not crash
        def bad_push(event_type, data):
            raise Exception("SSE bus broken")
        d = AutoLlmDaemon(mock_storage, mock_engine, bad_push, min_signals=5)
        # _push_sse swallows the exception
        d._push_sse("test_event", {"x": 1})  # should not raise
        # _run_llm_batch should still complete despite SSE failures
        d._run_llm_batch()
        assert mock_engine.process_batch.called


class TestGetStatus:
    def test_daemon_status_returns_5_fields(self, mock_storage, mock_engine):
        d = AutoLlmDaemon(storage=mock_storage, signal_engine=mock_engine)
        status = d.get_status()
        assert isinstance(status, dict)
        assert set(status.keys()) == {
            "running", "batch_running", "pending", "last_batch_at",
            "last_batch_count", "last_digest_at"
        }
        # Before any batch runs, batch fields are None / False
        assert status["batch_running"] is False
        assert status["last_batch_at"] is None
        assert status["last_batch_count"] is None
        assert status["last_digest_at"] is None
