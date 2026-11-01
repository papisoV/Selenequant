"""Tests for ReconciliationManager.get_last_diff_count() — Phase 5 observability."""

from __future__ import annotations

from unittest.mock import AsyncMock


from selene_space_exchange.base.reconciliation import ReconciliationManager
from selene_space_exchange.trading import TradingClient


def _make_mock_client() -> TradingClient:
    client = AsyncMock(spec=TradingClient)
    client.exchange_name = "mock"
    return client


def test_get_last_diff_count_returns_zero_initially() -> None:
    """Fresh ReconciliationManager → diff count = 0."""
    mgr = ReconciliationManager(_make_mock_client())
    assert mgr.get_last_diff_count() == 0


def test_record_diff_count_updates_getter() -> None:
    """record_diff_count(N) → get_last_diff_count() returns N."""
    mgr = ReconciliationManager(_make_mock_client())
    mgr.record_diff_count(3)
    assert mgr.get_last_diff_count() == 3


def test_record_diff_count_overwrites_previous() -> None:
    """Second reconciliation overwrites first diff count."""
    mgr = ReconciliationManager(_make_mock_client())
    mgr.record_diff_count(5)
    mgr.record_diff_count(0)  # next reconcile found 0 diff
    assert mgr.get_last_diff_count() == 0
