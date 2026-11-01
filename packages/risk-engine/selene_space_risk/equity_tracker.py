"""EquityTracker — monotonic peak equity with UTC daily reset + persistence.

Phase 2b: GREEN implementation.

State machine:
    INIT (peak=0, peak_date=None)
        ↓ update(equity, ts)
    if peak_date is None or ts.date(UTC) != peak_date:
        RESET — peak=equity, peak_date=ts.date(UTC)
    elif equity > peak:
        peak=equity  (monotonic increase, intraday)

See docs/risk-fills-ledger-design.md section 2 for full design.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any


class EquityTracker:
    """Tracks peak equity with intraday monotonic increase + UTC daily reset."""

    def __init__(self, repo: Any | None = None, account_id: str = "") -> None:
        self._repo = repo
        self._account_id = account_id
        self._peak: float = 0.0
        self._peak_date: date | None = None

    def update(self, equity: float, timestamp: int) -> None:
        """Update peak_equity with monotonic + daily-reset logic.

        Args:
            equity: Current total equity.
            timestamp: ms epoch. UTC date derived from this.
        """
        ts_date = self._utc_date(timestamp)

        if self._peak_date is None or ts_date != self._peak_date:
            # First update ever, or new UTC day → reset
            self._peak = equity
            self._peak_date = ts_date
            return

        # Same UTC day — monotonic: peak only goes up
        if equity > self._peak:
            self._peak = equity

    @property
    def peak(self) -> float:
        """Current peak equity (0.0 if never updated)."""
        return self._peak

    @property
    def peak_date(self) -> date | None:
        """UTC date of current peak (None if never updated)."""
        return self._peak_date

    def get_drawdown(self, current_equity: float) -> float:
        """Returns drawdown ratio: (peak - current) / peak. 0.0 if peak <= 0.

        Args:
            current_equity: Current total equity.

        Returns:
            Drawdown ratio (0.0 to 1.0+). 0.0 if peak <= 0 or never updated.
        """
        if self._peak <= 0.0:
            return 0.0
        return (self._peak - current_equity) / self._peak

    def reset_for_new_day(self, equity: float, timestamp: int) -> None:
        """Force reset peak to current equity (called at UTC 00:00 boundary).

        Args:
            equity: Current total equity (becomes new peak).
            timestamp: ms epoch.
        """
        self._peak = equity
        self._peak_date = self._utc_date(timestamp)

    def save_state(self) -> None:
        """Persist peak + peak_date to LiveStateRepository."""
        if self._repo is None:
            return
        if self._peak_date is None:
            return
        self._repo.save_equity_peak(
            self._account_id,
            self._peak,
            self._peak_date.isoformat(),
        )

    def load_state(self) -> None:
        """Restore peak + peak_date from LiveStateRepository.

        If persisted peak_date == today (UTC), restore peak (same day, still valid).
        If persisted peak_date < today, treat as cross-day: leave peak_date as-is
        so the next update() call triggers a reset (the equity passed to update
        becomes the new peak).
        """
        if self._repo is None:
            return
        result = self._repo.load_equity_peak(self._account_id)
        if result is None:
            return
        peak, peak_date_str = result
        try:
            loaded_date = date.fromisoformat(peak_date_str)
        except ValueError:
            return

        today_utc = datetime.now(timezone.utc).date()
        if loaded_date == today_utc:
            # Same UTC day — restore peak
            self._peak = peak
            self._peak_date = loaded_date
        # else: cross-day — leave state as-is; next update() will reset

    @staticmethod
    def _utc_date(timestamp_ms: int) -> date:
        """Convert ms epoch to UTC date."""
        return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).date()
