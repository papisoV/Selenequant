"""Tests for HistoricalFundingModel — historical funding rate application.

Verifies:
- Empty rates → always returns 0.0 (no exception).
- Correct rate applied at the right timestamp boundary.
- No double-application across consecutive calls at the same idx.
- Long pays positive rate, short receives positive rate.
- Zero rates are filtered out of the index.
- Catch-up across multiple funding events between calls.
"""

from __future__ import annotations

import pytest

from selene_space_backtest.funding import HistoricalFundingModel


class TestHistoricalFundingModel:
    def test_empty_rates_returns_zero(self) -> None:
        """Empty rates list → apply_funding always returns 0.0."""
        model = HistoricalFundingModel(rates=[])
        assert model.apply_funding(1000.0, "long", 100) == 0.0
        assert model.apply_funding(1000.0, "short", 200) == 0.0

    def test_applies_rate_at_correct_timestamp(self) -> None:
        """Rate at ts=100 applies to calls with ts in [100, 200)."""
        model = HistoricalFundingModel(rates=[
            (100, 0.001),
            (200, 0.002),
        ])
        # At ts=150, the most recent rate is ts=100 → 0.001
        # Long notional=1000 → pay 1000 * 0.001 = 1.0
        assert model.apply_funding(1000.0, "long", 150) == pytest.approx(1.0)

    def test_does_not_double_apply(self) -> None:
        """Consecutive calls at the same idx → second returns 0.0."""
        model = HistoricalFundingModel(rates=[(100, 0.001)])
        first = model.apply_funding(1000.0, "long", 150)
        second = model.apply_funding(1000.0, "long", 199)
        assert first == pytest.approx(1.0)
        assert second == 0.0

    def test_long_pays_positive_rate(self) -> None:
        """Positive rate → long pays (positive amount = cash deducted)."""
        model = HistoricalFundingModel(rates=[(100, 0.001)])
        # Long notional=1000, rate=0.001 → pay 1.0
        assert model.apply_funding(1000.0, "long", 150) == pytest.approx(1.0)

    def test_short_receives_positive_rate(self) -> None:
        """Positive rate → short receives (negative amount = cash credited)."""
        model = HistoricalFundingModel(rates=[(100, 0.001)])
        # Short notional=1000, rate=0.001 → receive 1.0 (returned as -1.0)
        assert model.apply_funding(1000.0, "short", 150) == pytest.approx(-1.0)

    def test_long_receives_negative_rate(self) -> None:
        """Negative rate → long receives (cash credited)."""
        model = HistoricalFundingModel(rates=[(100, -0.0005)])
        # Long notional=1000, rate=-0.0005 → receive 0.5 (returned as -0.5)
        assert model.apply_funding(1000.0, "long", 150) == pytest.approx(-0.5)

    def test_short_pays_negative_rate(self) -> None:
        """Negative rate → short pays (cash deducted)."""
        model = HistoricalFundingModel(rates=[(100, -0.0005)])
        # Short notional=1000, rate=-0.0005 → pay 0.5
        assert model.apply_funding(1000.0, "short", 150) == pytest.approx(0.5)

    def test_zero_rates_are_filtered(self) -> None:
        """Rate=0.0 entries don't get indexed (no-op events skipped)."""
        model = HistoricalFundingModel(rates=[
            (100, 0.0),   # filtered
            (200, 0.001),  # indexed
        ])
        # At ts=150: with the 0.0 entry filtered, no rate is ≤150 → 0.0
        assert model.apply_funding(1000.0, "long", 150) == 0.0
        # At ts=250: rate=0.001 applies
        assert model.apply_funding(1000.0, "long", 250) == pytest.approx(1.0)

    def test_catches_up_across_multiple_events(self) -> None:
        """apply_funding(ts=200) applies both ts=100 and ts=200 events in one call."""
        model = HistoricalFundingModel(rates=[
            (100, 0.001),
            (200, 0.002),
        ])
        # First call at ts=200 — applies ts=100 (1.0) AND ts=200 (2.0)
        # Long notional=1000 → total 1000 * (0.001 + 0.002) = 3.0
        assert model.apply_funding(1000.0, "long", 200) == pytest.approx(3.0)

    def test_unsorted_input_sorted_internally(self) -> None:
        """Unsorted rates list is handled (sorted internally)."""
        model = HistoricalFundingModel(rates=[
            (200, 0.002),
            (100, 0.001),
        ])
        # At ts=150, most recent ≤150 is ts=100 → 0.001
        assert model.apply_funding(1000.0, "long", 150) == pytest.approx(1.0)

    def test_before_first_event_returns_zero(self) -> None:
        """Call before the first funding event timestamp → 0.0."""
        model = HistoricalFundingModel(rates=[(100, 0.001)])
        # ts=50 is before the first event at ts=100
        assert model.apply_funding(1000.0, "long", 50) == 0.0

    def test_at_exact_funding_timestamp_applies(self) -> None:
        """Call at exactly the funding timestamp → rate applies (≤ boundary)."""
        model = HistoricalFundingModel(rates=[(100, 0.001)])
        # bisect_right returns insertion point AFTER ts=100, so idx-1 = 0
        assert model.apply_funding(1000.0, "long", 100) == pytest.approx(1.0)
