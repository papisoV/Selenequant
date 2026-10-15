"""Tests for the live ladder cross-section (``live_ladder_row``).

Synthetic cross-sections shaped like the real snapshot source: each row carries
the exchange's official ``prev_close`` and a real ``high``, codes carry an
exchange suffix. Zero network.

The fixtures deliberately mirror the endpoint's shape rather than idealising it
— a snapshot row has no close *series* to shift, so ``prev_close`` arrives as a
field, and a first-day listing reports ``prev_close == 0``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pytest

from selene_space_ashare.ladder import (
    live_ladder_row,
    next_run_length,
    run_length,
)
from selene_space_ashare.market_phase import _REQUIRED_COLUMNS, rule_labels

# 2024-10-15 10:35:35 CST — a real CST epoch (naive .timestamp() on a UTC host
# would land 8h earlier and roll the date back; pinned so a host-tz bug bites).
_TS = 1728959735000


@dataclass(frozen=True)
class Row:
    """Stand-in for ``SnapshotRow`` — duck-typed, as the real one is."""

    symbol: str
    name: str
    last: float
    prev_close: float
    high: float
    timestamp: int = _TS


_EMPTY_HIST = pd.DataFrame(columns=["symbol", "date", "consecutive_boards"])


def _hist(rows: list[tuple[str, str, int]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["symbol", "date", "consecutive_boards"])


class TestNextRunLength:
    def test_extends_on_flag(self) -> None:
        assert next_run_length(2, True) == 3

    def test_resets_on_no_flag(self) -> None:
        assert next_run_length(5, False) == 0

    def test_none_prev_counts_as_zero(self) -> None:
        assert next_run_length(None, True) == 1

    def test_nan_prev_counts_as_zero(self) -> None:
        assert next_run_length(float("nan"), True) == 1

    def test_fold_agrees_with_run_length(self) -> None:
        # The live path folds one day at a time; it must match the batch fold
        # exactly, or a stock's board count would depend on which path ran it.
        flags = [True, True, False, True, True, True]
        expected = run_length(flags)
        acc = 0
        for flag, want in zip(flags, expected):
            acc = next_run_length(acc, flag)
            assert acc == want


class TestLiveLadderRowAggregation:
    def _rows(self) -> dict[str, Row]:
        # main-board limit = prev * 1.10, rounded half-up to the cent.
        return {
            "600001.SH": Row("600001.SH", "甲", 11.00, 10.00, 11.00),  # sealed
            "600002.SH": Row("600002.SH", "乙", 22.00, 20.00, 22.00),  # sealed
            "600003.SH": Row("600003.SH", "丙", 33.00, 30.00, 33.00),  # sealed
            "600004.SH": Row("600004.SH", "丁", 5.20, 5.00, 5.50),  # 炸板
            "600005.SH": Row("600005.SH", "ST戊", 10.50, 10.00, 10.50),  # 5% sealed
            "600006.SH": Row("600006.SH", "己", 8.80, 8.00, 8.80),  # sealed
        }

    def test_breadth_matches_hand_count(self) -> None:
        # yesterday: 甲 = 2 板, 丁 = 3 板, 己 = 4 板
        # today boards: 甲 3, 乙 1, 丙 1, 丁 0(炸板), 戊 1, 己 5
        hist = _hist(
            [
                ("600001", "20241014", 2),
                ("600004", "20241014", 3),
                ("600006", "20241014", 4),
            ]
        )
        out = live_ladder_row(self._rows(), hist, today="20241015")
        assert out["date"] == "20241015"
        assert out["max_consecutive"] == 5  # 己
        assert out["first_board"] == 3  # 乙, 丙, 戊
        assert out["ge2_count"] == 2  # 甲, 己
        assert out["ge3_count"] == 2  # 甲(3), 己(5)
        assert out["ge5_count"] == 1  # 己

    def test_seal_rate_counts_touched_and_sealed(self) -> None:
        # 丁's high touches the limit but its close does not -> touched, not sealed
        hist = _hist([("600004", "20241014", 3)])
        out = live_ladder_row(self._rows(), hist, today="20241015")
        assert out["limit_up_count"] == 5
        assert out["touched_count"] == 6
        assert abs(out["seal_rate"] - 5 / 6) < 1e-12

    def test_board_run_extends_and_resets(self) -> None:
        # 甲 promotes 2 -> 3; 丁 was 3 板 yesterday and broke today -> 0, so the
        # promotion denominator counts it but it does not promote.
        hist = _hist(
            [
                ("600001", "20241014", 2),
                ("600004", "20241014", 3),
                ("600006", "20241014", 4),
            ]
        )
        out = live_ladder_row(self._rows(), hist, today="20241015")
        assert out["promo_pool"] == 3  # 甲, 丁, 己 were all on yesterday's board
        assert out["promo_ok"] == 2  # 甲 (2->3), 己 (4->5); 丁 broke

    def test_output_carries_required_columns(self) -> None:
        out = live_ladder_row(self._rows(), _EMPTY_HIST, today="20241015")
        missing = [c for c in _REQUIRED_COLUMNS if c not in out]
        assert not missing

    def test_output_is_accepted_by_phase_classifier(self) -> None:
        out = live_ladder_row(self._rows(), _EMPTY_HIST, today="20241015")
        labels = rule_labels(pd.DataFrame([out, dict(out, date="20241016")]))
        assert len(labels) == 2

    def test_counts_are_python_ints_not_floats(self) -> None:
        # The seal-rate merge upcasts count columns to float64; a live row must
        # still be type-identical to the EOD panel's row.
        out = live_ladder_row(self._rows(), _EMPTY_HIST, today="20241015")
        for col in ("first_board", "ge2_count", "max_consecutive", "limit_up_count"):
            assert isinstance(out[col], int), (col, type(out[col]))

    def test_inputs_are_not_mutated(self) -> None:
        rows = self._rows()
        hist = _hist([("600001", "20241014", 2)])
        rows_before = dict(rows)
        hist_before = hist.copy()
        live_ladder_row(rows, hist, today="20241015")
        assert rows == rows_before
        pd.testing.assert_frame_equal(hist, hist_before)


class TestLiveLadderRowBoundaries:
    def test_missing_prev_close_is_not_a_limit(self) -> None:
        # A first-day listing reports prev_close == 0; it must score 0 boards,
        # never a limit-up (a naive limit_up_price(0) would emit 0.00).
        rows = {"600001.SH": Row("600001.SH", "新股", 12.00, 0.0, 12.00)}
        out = live_ladder_row(rows, _EMPTY_HIST, today="20241015")
        assert out["limit_up_count"] == 0
        assert out["max_consecutive"] == 0

    def test_symbol_absent_from_history_starts_fresh(self) -> None:
        rows = {"600002.SH": Row("600002.SH", "乙", 11.00, 10.00, 11.00)}
        out = live_ladder_row(rows, _EMPTY_HIST, today="20241015")
        assert out["first_board"] == 1
        assert out["max_consecutive"] == 1

    def test_symbol_not_in_cross_section_is_absent(self) -> None:
        # A name on yesterday's board but missing from today's tape must not
        # leak into today's breadth.
        rows = {"600002.SH": Row("600002.SH", "乙", 22.00, 20.00, 22.00)}
        hist = _hist([("600099", "20241014", 7)])
        out = live_ladder_row(rows, hist, today="20241015")
        assert out["max_consecutive"] == 1  # 乙 only, not 600099's 7

    def test_same_day_history_row_does_not_leak(self) -> None:
        # Re-running mid-session must not treat an already-written today row as
        # its own baseline (which would double-count the run).
        rows = {"600001.SH": Row("600001.SH", "甲", 11.00, 10.00, 11.00)}
        hist = _hist([("600001", "20241014", 2), ("600001", "20241015", 99)])
        out = live_ladder_row(rows, hist, today="20241015")
        assert out["max_consecutive"] == 3  # 2 (yesterday) + 1, not 99 + 1

    def test_baseline_is_the_latest_prior_date(self) -> None:
        # Two prior sessions with different heights: the run extends from the
        # *most recent* one (2 板), not from an earlier taller one (5 板).
        rows = {"600001.SH": Row("600001.SH", "甲", 11.00, 10.00, 11.00)}
        hist = _hist([("600001", "20241010", 5), ("600001", "20241014", 2)])
        out = live_ladder_row(rows, hist, today="20241015")
        assert out["max_consecutive"] == 3  # 2 + 1, not 5 + 1

    def test_price_within_tolerance_of_limit_is_sealed(self) -> None:
        # Float noise must not drop a genuine seal: a last a hair under the
        # limit (10.9999999995 vs 11.00) is still at the limit within _LIMIT_TOL.
        rows = {"600001.SH": Row("600001.SH", "甲", 11.00 - 5e-10, 10.00, 11.00)}
        assert (
            live_ladder_row(rows, _EMPTY_HIST, today="20241015")["limit_up_count"] == 1
        )

    def test_price_clearly_below_limit_is_not_sealed(self) -> None:
        # ...but a real gap is a real miss.
        rows = {"600001.SH": Row("600001.SH", "甲", 10.999, 10.00, 11.00)}
        assert (
            live_ladder_row(rows, _EMPTY_HIST, today="20241015")["limit_up_count"] == 0
        )

    def test_st_five_percent_limit(self) -> None:
        # ST name -> 5% limit; 10.50 from 10.00 seals for ST but not for main.
        st = {"600005.SH": Row("600005.SH", "ST戊", 10.50, 10.00, 10.50)}
        main = {"600005.SH": Row("600005.SH", "戊", 10.50, 10.00, 10.50)}
        assert live_ladder_row(st, _EMPTY_HIST, today="20241015")["limit_up_count"] == 1
        assert (
            live_ladder_row(main, _EMPTY_HIST, today="20241015")["limit_up_count"] == 0
        )

    def test_one_word_board_high_equals_last(self) -> None:
        # 一字板: open == high == last == limit; must still count as sealed once.
        rows = {"600001.SH": Row("600001.SH", "甲", 11.00, 10.00, 11.00)}
        out = live_ladder_row(rows, _EMPTY_HIST, today="20241015")
        assert out["limit_up_count"] == 1
        assert out["touched_count"] == 1
        assert out["seal_rate"] == 1.0

    def test_empty_cross_section_raises(self) -> None:
        # A closed market and a failed fetch look identical; scoring it as a
        # dead tape would report a silent 冰点. Fail loudly instead.
        with pytest.raises(ValueError, match="快照为空"):
            live_ladder_row({}, _EMPTY_HIST, today="20241015")

    def test_today_derived_from_timestamps(self) -> None:
        rows = {"600001.SH": Row("600001.SH", "甲", 11.00, 10.00, 11.00)}
        assert live_ladder_row(rows, _EMPTY_HIST)["date"] == "20241015"

    def test_missing_timestamp_and_no_today_raises(self) -> None:
        rows = {
            "600001.SH": {"name": "甲", "last": 11.0, "prev_close": 10.0, "high": 11.0}
        }
        with pytest.raises(ValueError, match="没有 timestamp"):
            live_ladder_row(rows, _EMPTY_HIST)

    def test_history_missing_columns_raises(self) -> None:
        rows = {"600001.SH": Row("600001.SH", "甲", 11.00, 10.00, 11.00)}
        bad = pd.DataFrame({"symbol": ["600001"], "date": ["20241014"]})
        with pytest.raises(ValueError, match="daily_history 缺少列"):
            live_ladder_row(rows, bad, today="20241015")

    def test_accepts_plain_dict_rows(self) -> None:
        # The duck-typed contract: any object exposing the fields works, so a
        # caller need not import the market-data package.
        rows = {
            "600001.SH": {
                "symbol": "600001.SH",
                "name": "甲",
                "last": 11.0,
                "prev_close": 10.0,
                "high": 11.0,
                "timestamp": _TS,
            }
        }
        out = live_ladder_row(rows, _EMPTY_HIST, today="20241015")
        assert out["limit_up_count"] == 1


class TestSnapshotDateTimezone:
    def test_date_is_cst_not_host_local(self) -> None:
        # The exact host-tz trap: a naive fromtimestamp() on a UTC cloud host
        # (the deployment target) shifts a late-evening CST stamp back a day.
        # 2024-10-15 00:30 CST = 2024-10-14 16:30 UTC.
        late_night_cst = 1728923400000  # 2024-10-15 00:30:00 CST
        rows = {"600001.SH": Row("600001.SH", "甲", 11.0, 10.0, 11.0, late_night_cst)}
        assert live_ladder_row(rows, _EMPTY_HIST)["date"] == "20241015"

    def test_date_is_host_tz_independent(self) -> None:
        """The derived session date must not move with the host timezone.

        On this UTC+8 dev box ``datetime.fromtimestamp()`` (naive → local)
        happens to coincide with Beijing, so :meth:`test_date_is_cst_not_host_local`
        passes even with the tz dropped. Forcing the child into UTC makes them
        disagree: 00:30 CST becomes 16:30 UTC the *previous* day, so a
        tz-naive derivation yields 20241014 and this fails.
        """
        import os as _os
        import subprocess
        import sys

        # A plain-dict row + an empty history keeps the child snippet short;
        # the only thing under test is how the epoch becomes a session date.
        snippet = (
            "import pandas as pd;"
            "from selene_space_ashare.ladder import live_ladder_row;"
            "rows={'600001.SH':{'name':'甲','last':11.0,'prev_close':10.0,"
            "'high':11.0,'timestamp':1728923400000}};"
            "empty=pd.DataFrame(columns=['symbol','date','consecutive_boards']);"
            "print(live_ladder_row(rows, empty)['date'])"
        )
        out = subprocess.run(
            [sys.executable, "-c", snippet],
            env={**_os.environ, "TZ": "UTC"},
            capture_output=True,
            text=True,
        )
        assert out.returncode == 0, out.stderr
        # 2024-10-15 00:30 Beijing, not 2024-10-14 16:30 UTC.
        assert out.stdout.strip() == "20241015"

    def test_date_from_cross_section_uses_latest_stamp(self) -> None:
        # A live cross-section is one session; a straggler row at the start of
        # the prior session must not drag the date back.
        rows = {
            "600001.SH": Row("600001.SH", "甲", 11.0, 10.0, 11.0, 1728950000000),
            "600002.SH": Row("600002.SH", "乙", 22.0, 20.0, 22.0, 1728959735000),
        }
        assert live_ladder_row(rows, _EMPTY_HIST)["date"] == "20241015"
