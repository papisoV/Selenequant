"""Tests for /api/v1/trading/latency-stats endpoint (Phase 2 — latency monitoring).

Tests two layers:
1. Route registration + signature contract (no HTTP call needed)
2. Percentile math via direct journal injection (in-process, no server)
"""

from __future__ import annotations

import inspect
import tempfile
from pathlib import Path

import pytest

from app.api.v1.trading import get_latency_stats, router as trading_router
from app.core.dependencies import set_order_journal
from selene_space_exchange.execution.order_journal import OrderJournal


class TestLatencyStatsEndpointContract:
    """Route-registration + signature tests."""

    def test_endpoint_function_exists_and_is_async(self) -> None:
        assert callable(get_latency_stats)
        assert inspect.iscoroutinefunction(get_latency_stats)

    def test_endpoint_has_correct_path_attribute(self) -> None:
        paths: list[str] = []
        for route in trading_router.routes:
            path = getattr(route, "path", None)
            if path is not None:
                paths.append(path)
        assert "/trading/latency-stats" in paths, f"got paths: {paths}"

    def test_endpoint_declares_get_method(self) -> None:
        for route in trading_router.routes:
            path = getattr(route, "path", None)
            methods = getattr(route, "methods", None)
            if path == "/trading/latency-stats" and methods:
                assert "GET" in methods
                return
        raise AssertionError("no /trading/latency-stats route found on trading router")


class _FakeRequest:
    """Minimal stand-in for FastAPI Request to satisfy build_response_meta."""

    def __init__(self) -> None:
        class _State:
            request_id: str | None = None

        self.state = _State()


class TestLatencyStatsPercentileMath:
    """Verify percentile calculation via direct journal injection."""

    def setup_method(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        self._journal = OrderJournal(journal_dir=Path(self._tmpdir))
        set_order_journal(self._journal)

    def teardown_method(self) -> None:
        # Restore global to None so other tests don't see our temp journal.
        set_order_journal(None)

    @pytest.mark.asyncio
    async def test_empty_journal_returns_none(self) -> None:
        """Empty journal → all percentiles None, sample_count=0."""
        result = await get_latency_stats(request=_FakeRequest(), window=3600, user=None)
        data = result.data
        assert data.sample_count == 0
        assert data.intent_to_submit_p50_ms is None
        assert data.submit_to_ack_p99_ms is None
        assert data.ack_to_fill_p50_ms is None

    @pytest.mark.asyncio
    async def test_submit_to_ack_percentiles_correct(self) -> None:
        """100 rows with known submit→ack delta → p50/p95/p99 math correct."""
        # Build 100 intents, each submitted at t+0 and confirmed at t+{i}ms
        # where i is the row index. So deltas are 0..99ms.
        for i in range(100):
            co_id = f"co-{i}"
            # Insert directly via DB to control created_at precisely
            self._journal.append_intent(
                client_order_id=co_id,
                symbol="BTC-USDT",
                side="buy",
                order_type="market",
                quantity=1.0,
            )
            # Override created_at via raw SQL to align with i-ms timeline
            import sqlite3
            import time
            base_ms = int(time.time() * 1000) - 200_000  # older than window edge-safe
            with sqlite3.connect(str(self._journal._db_path)) as con:  # noqa: SLF001
                con.execute(
                    "UPDATE order_journal SET created_at = ? WHERE client_order_id = ?",
                    (base_ms + i, co_id),
                )
                con.execute(
                    "UPDATE order_journal SET submitted_at = ? WHERE client_order_id = ?",
                    (base_ms + i, co_id),
                )
                con.execute(
                    "UPDATE order_journal SET acknowledged_at = ? WHERE client_order_id = ?",
                    (base_ms + i + 50, co_id),  # 50ms submit→ack for every row
                )
                # Need status='submitted' for mark_confirmed to fire, but since
                # we're injecting directly, also set status to 'confirmed':
                con.execute(
                    "UPDATE order_journal SET status = 'confirmed' WHERE client_order_id = ?",
                    (co_id,),
                )

        result = await get_latency_stats(request=_FakeRequest(), window=3600, user=None)
        data = result.data
        assert data.sample_count == 100
        # All submit→ack deltas are exactly 50ms → all percentiles = 50.0
        assert data.submit_to_ack_p50_ms == 50.0
        assert data.submit_to_ack_p95_ms == 50.0
        assert data.submit_to_ack_p99_ms == 50.0

    @pytest.mark.asyncio
    async def test_window_filter_excludes_old_rows(self) -> None:
        """Rows older than window should NOT count toward sample_count."""
        import sqlite3
        import time

        # Insert one old row (24h+1s ago) and one fresh row (now)
        self._journal.append_intent(
            client_order_id="co-old",
            symbol="BTC-USDT",
            side="buy",
            order_type="market",
            quantity=1.0,
        )
        old_ms = int(time.time() * 1000) - (25 * 3600 * 1000)  # 25h ago
        with sqlite3.connect(str(self._journal._db_path)) as con:  # noqa: SLF001
            con.execute(
                "UPDATE order_journal SET created_at = ? WHERE client_order_id = ?",
                (old_ms, "co-old"),
            )

        self._journal.append_intent(
            client_order_id="co-new",
            symbol="BTC-USDT",
            side="buy",
            order_type="market",
            quantity=1.0,
        )

        result = await get_latency_stats(request=_FakeRequest(), window=3600, user=None)
        # Only co-new is within the 1h window
        assert result.data.sample_count == 1

    @pytest.mark.asyncio
    async def test_partial_rows_handled(self) -> None:
        """Row with acknowledged_at=NULL counts toward intent→submit bucket
        but NOT submit→ack bucket."""
        import sqlite3
        import time

        self._journal.append_intent(
            client_order_id="co-partial",
            symbol="BTC-USDT",
            side="buy",
            order_type="market",
            quantity=1.0,
        )
        now_ms = int(time.time() * 1000)
        with sqlite3.connect(str(self._journal._db_path)) as con:  # noqa: SLF001
            con.execute(
                "UPDATE order_journal SET created_at = ?, submitted_at = ?, "
                "acknowledged_at = NULL, filled_at = NULL, status = 'submitted' "
                "WHERE client_order_id = ?",
                (now_ms - 1000, now_ms - 900, "co-partial"),
            )

        result = await get_latency_stats(request=_FakeRequest(), window=3600, user=None)
        data = result.data
        assert data.sample_count == 1
        # intent→submit bucket has 1 sample (100ms)
        assert data.intent_to_submit_p50_ms == 100.0
        # submit→ack bucket has 0 samples (acknowledged_at is NULL)
        assert data.submit_to_ack_p50_ms is None
        # ack→fill bucket has 0 samples
        assert data.ack_to_fill_p50_ms is None

    @pytest.mark.asyncio
    async def test_response_model_shape(self) -> None:
        """All 11 LatencyStats fields appear in response."""
        result = await get_latency_stats(request=_FakeRequest(), window=3600, user=None)
        data = result.data
        # Pydantic v2: model_fields on the class (not instance) to avoid deprecation
        field_names = set(type(data).model_fields.keys())
        expected = {
            "window_seconds",
            "sample_count",
            "intent_to_submit_p50_ms",
            "intent_to_submit_p95_ms",
            "intent_to_submit_p99_ms",
            "submit_to_ack_p50_ms",
            "submit_to_ack_p95_ms",
            "submit_to_ack_p99_ms",
            "ack_to_fill_p50_ms",
            "ack_to_fill_p95_ms",
            "ack_to_fill_p99_ms",
        }
        assert field_names == expected, f"missing: {expected - field_names}, extra: {field_names - expected}"
