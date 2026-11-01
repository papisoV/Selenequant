"""Regression tests for 2026-07-24 bugfix batch.

Verifies:
- AC-4: _auto_poll_loop exits within 1.2s after _auto_poll_shutdown is set
- AC-5: _iso_z() helper adds Z suffix to naive UTC datetimes, passes aware through
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone


from tgwatcher.web.api import _auto_poll_loop, _auto_poll_shutdown, _iso_z


class TestAutoPollShutdown:
    """AC-4: _auto_poll_loop graceful shutdown via _auto_poll_shutdown event."""

    def setup_method(self):
        _auto_poll_shutdown.clear()

    def teardown_method(self):
        _auto_poll_shutdown.clear()

    def test_loop_exits_on_shutdown(self):
        """Loop thread should exit within 1.2s of shutdown event being set."""
        t = threading.Thread(target=_auto_poll_loop, daemon=True, name="test-auto-poll")
        t.start()
        time.sleep(0.3)  # let it enter the loop
        _auto_poll_shutdown.set()
        t.join(timeout=2)
        assert not t.is_alive(), "auto_poll_loop did not shut down within 2s"

    def test_loop_continues_when_shutdown_not_set(self):
        """Loop should keep running when shutdown event is clear (smoke test)."""
        t = threading.Thread(target=_auto_poll_loop, daemon=True, name="test-auto-poll-2")
        t.start()
        time.sleep(0.3)
        assert t.is_alive(), "auto_poll_loop should still be running"
        _auto_poll_shutdown.set()
        t.join(timeout=2)
        assert not t.is_alive(), "auto_poll_loop should have exited"


class TestIsoZHelper:
    """AC-5: _iso_z helper produces Z-suffixed ISO for naive UTC datetimes."""

    def test_naive_utc_gets_z_suffix(self):
        """Naive datetime (project DB convention) should get Z suffix."""
        naive = datetime(2026, 7, 24, 10, 0, 0)
        result = _iso_z(naive)
        assert result == "2026-07-24T10:00:00Z", f"Expected Z suffix, got {result}"

    def test_aware_utc_keeps_offset(self):
        """Aware datetime should keep its offset, not get double Z."""
        aware = datetime(2026, 7, 24, 10, 0, 0, tzinfo=timezone.utc)
        result = _iso_z(aware)
        assert result == "2026-07-24T10:00:00+00:00", f"Expected offset, got {result}"

    def test_none_returns_none(self):
        assert _iso_z(None) is None

    def test_non_datetime_returns_none(self):
        assert _iso_z("2026-07-24") is None
        assert _iso_z(12345) is None

    def test_microseconds_preserved(self):
        naive = datetime(2026, 7, 24, 10, 0, 0, 123456)
        result = _iso_z(naive)
        assert result == "2026-07-24T10:00:00.123456Z", f"Got {result}"
