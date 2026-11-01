"""Tests for LiveStateRepository equity_peak persistence (Phase 2b US-003)."""

from __future__ import annotations

from pathlib import Path

import pytest

from selene_space_exchange.execution.live_state import LiveStateRepository


@pytest.fixture
def repo(tmp_path: Path) -> LiveStateRepository:
    return LiveStateRepository(state_dir=tmp_path)


def test_save_then_load_equity_peak(repo: LiveStateRepository) -> None:
    """save_equity_peak followed by load_equity_peak returns the saved values."""
    repo.save_equity_peak("pa-1", 12500.50, "2026-07-26")
    result = repo.load_equity_peak("pa-1")
    assert result is not None
    peak, peak_date = result
    assert peak == pytest.approx(12500.50)
    assert peak_date == "2026-07-26"


def test_load_missing_returns_none(repo: LiveStateRepository) -> None:
    """load_equity_peak for unknown account returns None."""
    assert repo.load_equity_peak("pa-nonexistent") is None


def test_save_overwrites_existing_equity_peak(repo: LiveStateRepository) -> None:
    """Repeated save_equity_peak for same account_id overwrites the previous value."""
    repo.save_equity_peak("pa-1", 100.0, "2026-07-25")
    repo.save_equity_peak("pa-1", 150.0, "2026-07-26")
    result = repo.load_equity_peak("pa-1")
    assert result is not None
    peak, peak_date = result
    assert peak == pytest.approx(150.0)
    assert peak_date == "2026-07-26"
