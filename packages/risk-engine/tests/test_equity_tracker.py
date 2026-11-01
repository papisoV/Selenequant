"""RED tests for EquityTracker (Phase 2a).

All tests should FAIL with NotImplementedError — Phase 2b will implement GREEN.
"""

from __future__ import annotations

from datetime import date, timezone, datetime

import pytest

from selene_space_risk.equity_tracker import EquityTracker


def _ts(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    """UTC datetime → ms epoch."""
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp() * 1000)


@pytest.fixture
def tracker() -> EquityTracker:
    return EquityTracker()


def test_peak_monotonic_increasing(tracker: EquityTracker) -> None:
    """equity 下降时 peak 不降 (intraday monotonic)."""
    # Day 1 09:00 — peak = 100
    tracker.update(100.0, _ts(2026, 7, 26, 9, 0))
    assert tracker.peak == pytest.approx(100.0)

    # Day 1 14:00 — equity drops to 70, peak stays 100
    tracker.update(70.0, _ts(2026, 7, 26, 14, 0))
    assert tracker.peak == pytest.approx(100.0)

    # Drawdown = (100 - 70) / 100 = 0.30
    assert tracker.get_drawdown(70.0) == pytest.approx(0.30)


def test_peak_updates_on_new_high(tracker: EquityTracker) -> None:
    """equity 创新高时 peak 更新."""
    tracker.update(100.0, _ts(2026, 7, 26, 9, 0))
    tracker.update(120.0, _ts(2026, 7, 26, 14, 0))
    assert tracker.peak == pytest.approx(120.0)
    assert tracker.peak_date == date(2026, 7, 26)


def test_peak_resets_at_utc_midnight(tracker: EquityTracker) -> None:
    """UTC 00:00 跨日重置 — new day's first update becomes new peak."""
    # Day 1 23:59 — peak = 120
    tracker.update(120.0, _ts(2026, 7, 26, 23, 59))
    assert tracker.peak == pytest.approx(120.0)
    assert tracker.peak_date == date(2026, 7, 26)

    # Day 2 00:01 — equity = 115 (lower than Day 1 peak, but new day)
    tracker.update(115.0, _ts(2026, 7, 27, 0, 1))
    # Peak resets to 115 (first update of new UTC day)
    assert tracker.peak == pytest.approx(115.0)
    assert tracker.peak_date == date(2026, 7, 27)

    # Drawdown is now 0 (peak == current)
    assert tracker.get_drawdown(115.0) == pytest.approx(0.0)


def test_peak_persistence_across_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """save_state + load_state 后 peak 恢复 (uses in-memory stub repo)."""
    # Test uses fixed 2026-07-26 timestamps, but production load_state() uses
    # datetime.now(timezone.utc).date() to compare against loaded_date.
    # Without patching, today (real 2026-07-27) != loaded 2026-07-26 → cross-day
    # branch leaves peak at 0.0. Patch datetime.now so "today" = 2026-07-26,
    # while preserving other datetime classmethods (e.g. fromtimestamp in _utc_date).
    import selene_space_risk.equity_tracker as et_mod

    _real_datetime = datetime

    class _FixedDatetime(_real_datetime):
        @classmethod
        def now(cls, tz=None):
            return _real_datetime(2026, 7, 26, 12, 0, tzinfo=tz)

    monkeypatch.setattr(et_mod, "datetime", _FixedDatetime)

    repo = InMemoryRepo()
    tracker1 = EquityTracker(repo=repo, account_id="pa-1")
    tracker1.update(100.0, _ts(2026, 7, 26, 9, 0))
    tracker1.update(120.0, _ts(2026, 7, 26, 14, 0))
    tracker1.save_state()

    # Simulate restart: new tracker instance, same repo
    tracker2 = EquityTracker(repo=repo, account_id="pa-1")
    tracker2.load_state()
    assert tracker2.peak == pytest.approx(120.0)
    assert tracker2.peak_date == date(2026, 7, 26)


def test_drawdown_calculation(tracker: EquityTracker) -> None:
    """drawdown = (peak - current) / peak."""
    tracker.update(100.0, _ts(2026, 7, 26, 9, 0))
    # 10% drawdown
    assert tracker.get_drawdown(90.0) == pytest.approx(0.10)
    # 25% drawdown
    assert tracker.get_drawdown(75.0) == pytest.approx(0.25)
    # No drawdown (at peak)
    assert tracker.get_drawdown(100.0) == pytest.approx(0.0)
    # Negative equity → still computed (drawdown > 100%)
    # Note: Phase 2b may handle equity <= 0 specially


class InMemoryRepo:
    """Minimal in-memory stub repo for RED tests (Phase 2a).

    Phase 2b will replace with real LiveStateRepository.
    """

    def __init__(self) -> None:
        self._state: dict[str, tuple[float, str]] = {}

    def save_equity_peak(self, account_id: str, peak: float, peak_date: str) -> None:
        self._state[account_id] = (peak, peak_date)

    def load_equity_peak(self, account_id: str) -> tuple[float, str] | None:
        return self._state.get(account_id)
