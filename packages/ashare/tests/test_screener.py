# Absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 tick-stock-panel contributors
# Source: https://github.com/shy3130/tick-stock-panel
"""Screener core tests — pure masks over quote rows, no db, no network.

Properties pinned here, in the order they can go wrong:

* **Missing is not a match.** A 量比 floor of 1.5 asks "today's volume is at
  least 1.5× the prior 5-day average". A name with no 5-day baseline cannot
  answer that question; scoring it as a hit is a fabricated signal.
* **Units are percent for ``change_pct``, yuan for market cap / turnover.**
  Both are asserted explicitly because the sibling panel (``/ashare/deviation``)
  uses fractions.
* **Turnover口径 is per row.** Snapshot rows carry the exchange's own amount;
  daily rows only support ``close × volume`` and must say so.
* **Ordering is total.** Ties break on symbol, so the same input always yields
  the same page.
"""

from __future__ import annotations

import pandas as pd
import pytest

from selene_space_ashare import screener
from selene_space_ashare.market_overview import Baseline
from selene_space_ashare.screener import (
    BOARDS,
    MAX_LIMIT,
    ScreenSpec,
    attach_market_cap,
    attach_turnover,
    screen,
)


def _row(**overrides) -> dict:
    """A ``build_quote_rows``-shaped row with sane defaults."""
    row = {
        "symbol": "600000.SH",
        "name": "浦发银行",
        "last": 10.0,
        "prev_close": 9.9,
        "high": 10.1,
        "low": 9.8,
        "volume": 1.0e6,
        "amount": None,
        "change_pct": 1.01,
        "baseline_close": 9.9,
        "ma5": 9.95,
        "ma20": 9.5,
        "ma60": 9.0,
        "high60": 10.3,
        "low60": 8.5,
        "avg_vol5": 1.0e6,
        "prev_boards": 0,
        "volume_ratio": 1.0,
        "limit_up": False,
        "limit_down": False,
        "broken": False,
        "boards": 0,
    }
    row.update(overrides)
    return row


class TestSpecValidation:
    def test_low_above_high_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="倒挂"):
            ScreenSpec(change_pct_min=5.0, change_pct_max=1.0)

    def test_equal_bounds_are_allowed(self) -> None:
        spec = ScreenSpec(price_min=10.0, price_max=10.0)
        assert spec.price_min == spec.price_max

    def test_unknown_board_rejected(self) -> None:
        with pytest.raises(ValueError, match="未知板块"):
            ScreenSpec(board_types=("star",))  # type: ignore[arg-type]

    def test_unknown_sort_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="sort_by"):
            ScreenSpec(sort_by="rsi")

    def test_limit_bounds(self) -> None:
        with pytest.raises(ValueError, match="limit"):
            ScreenSpec(limit=0)
        with pytest.raises(ValueError, match="limit"):
            ScreenSpec(limit=MAX_LIMIT + 1)

    def test_above_ma_whitelist(self) -> None:
        with pytest.raises(ValueError, match="above_ma"):
            ScreenSpec(above_ma="ma10")

    def test_non_finite_bound_rejected(self) -> None:
        with pytest.raises(ValueError, match="有限数值"):
            ScreenSpec(price_min=float("nan"))

    def test_needs_market_cap_only_when_cap_used(self) -> None:
        assert ScreenSpec().needs_market_cap is False
        assert ScreenSpec(price_min=1.0).needs_market_cap is False
        assert ScreenSpec(float_mv_max=1e10).needs_market_cap is True
        assert ScreenSpec(total_mv_min=1e9).needs_market_cap is True


class TestBoundedMasks:
    def test_range_is_inclusive_on_both_ends(self) -> None:
        rows = [
            _row(symbol="A.SH", change_pct=1.0),
            _row(symbol="B.SH", change_pct=2.0),
            _row(symbol="C.SH", change_pct=3.0),
        ]
        outcome = screen(
            rows, ScreenSpec(change_pct_min=2.0, change_pct_max=3.0, limit=10)
        )
        assert [r["symbol"] for r in outcome.rows] == ["C.SH", "B.SH"]

    def test_missing_value_fails_a_set_bound(self) -> None:
        """No baseline ⇒ no answer; a set bound must not treat it as a pass."""
        rows = [
            _row(symbol="A.SH", volume_ratio=None),
            _row(symbol="B.SH", volume_ratio=2.0),
        ]
        outcome = screen(rows, ScreenSpec(volume_ratio_min=1.5, limit=10))
        assert [r["symbol"] for r in outcome.rows] == ["B.SH"]

    def test_missing_value_passes_when_unconstrained(self) -> None:
        rows = [
            _row(symbol="A.SH", volume_ratio=None),
            _row(symbol="B.SH", volume_ratio=0.3),
        ]
        outcome = screen(rows, ScreenSpec(limit=10))
        assert outcome.matched == 2

    def test_nan_and_inf_are_missing(self) -> None:
        rows = [
            _row(symbol="A.SH", last=float("nan")),
            _row(symbol="B.SH", last=float("inf")),
            _row(symbol="C.SH", last=5.0),
        ]
        outcome = screen(rows, ScreenSpec(price_max=100.0, limit=10))
        assert [r["symbol"] for r in outcome.rows] == ["C.SH"]

    def test_upper_bound_only(self) -> None:
        rows = [_row(symbol="A.SH", last=5.0), _row(symbol="B.SH", last=50.0)]
        outcome = screen(rows, ScreenSpec(price_max=10.0, limit=10))
        assert [r["symbol"] for r in outcome.rows] == ["A.SH"]


class TestCategoricalMasks:
    def test_board_types_official_codes(self) -> None:
        rows = [
            _row(symbol="600000.SH"),  # main
            _row(symbol="688981.SH"),  # kcb
            _row(symbol="300750.SZ"),  # cyb
            _row(symbol="830799.BJ"),  # bse
        ]
        outcome = screen(rows, ScreenSpec(board_types=("kcb", "cyb"), limit=10))
        assert {r["symbol"] for r in outcome.rows} == {"688981.SH", "300750.SZ"}

    def test_exclude_st_drops_st_and_delisting(self) -> None:
        rows = [
            _row(symbol="A.SH", name="正常股份"),
            _row(symbol="B.SH", name="*ST某某"),
            _row(symbol="C.SH", name="某某退"),
        ]
        outcome = screen(rows, ScreenSpec(exclude_st=True, limit=10))
        assert [r["symbol"] for r in outcome.rows] == ["A.SH"]

    def test_exclude_st_keeps_lowercase_st(self) -> None:
        """``is_st`` is case-sensitive by contract; the screener must not widen it."""
        rows = [_row(symbol="A.SH", name="st某某")]
        outcome = screen(rows, ScreenSpec(exclude_st=True, limit=10))
        assert outcome.matched == 1

    def test_limit_up_true_and_false(self) -> None:
        rows = [_row(symbol="A.SH", limit_up=True), _row(symbol="B.SH", limit_up=False)]
        assert [
            r["symbol"] for r in screen(rows, ScreenSpec(limit_up=True, limit=10)).rows
        ] == ["A.SH"]
        assert [
            r["symbol"] for r in screen(rows, ScreenSpec(limit_up=False, limit=10)).rows
        ] == ["B.SH"]

    def test_limit_up_none_is_no_filter(self) -> None:
        rows = [_row(symbol="A.SH", limit_up=True), _row(symbol="B.SH", limit_up=False)]
        assert screen(rows, ScreenSpec(limit_up=None, limit=10)).matched == 2


class TestTrendMasks:
    def test_above_ma_uses_ge(self) -> None:
        rows = [
            _row(symbol="A.SH", last=10.0, ma20=10.0),  # exactly at MA → counts
            _row(symbol="B.SH", last=9.99, ma20=10.0),
            _row(symbol="C.SH", last=11.0, ma20=None),  # no baseline → unknown
        ]
        outcome = screen(rows, ScreenSpec(above_ma="ma20", limit=10))
        assert [r["symbol"] for r in outcome.rows] == ["A.SH"]

    def test_new_high_is_high60_reach(self) -> None:
        rows = [
            _row(symbol="A.SH", last=10.3, high60=10.3),
            _row(symbol="B.SH", last=10.2, high60=10.3),
        ]
        outcome = screen(rows, ScreenSpec(new_high=True, limit=10))
        assert [r["symbol"] for r in outcome.rows] == ["A.SH"]

    def test_new_low_is_low60_reach(self) -> None:
        rows = [
            _row(symbol="A.SH", last=8.5, low60=8.5),
            _row(symbol="B.SH", last=8.6, low60=8.5),
        ]
        outcome = screen(rows, ScreenSpec(new_low=True, limit=10))
        assert [r["symbol"] for r in outcome.rows] == ["A.SH"]

    def test_boards_streak_range(self) -> None:
        rows = [
            _row(symbol="A.SH", boards=0),
            _row(symbol="B.SH", boards=2),
            _row(symbol="C.SH", boards=5),
        ]
        outcome = screen(rows, ScreenSpec(boards_min=1, boards_max=3, limit=10))
        assert [r["symbol"] for r in outcome.rows] == ["B.SH"]


class TestTurnover:
    def test_snapshot_amount_wins_over_estimate(self) -> None:
        rows = [attach_turnover([_row(amount=2.5e8)])[0]]
        assert rows[0]["turnover"] == 2.5e8
        assert rows[0]["turnover_est"] is False

    def test_daily_row_is_marked_estimated(self) -> None:
        row = attach_turnover([_row(amount=None, last=10.0, volume=1.0e6)])[0]
        assert row["turnover"] == pytest.approx(1.0e7)
        assert row["turnover_est"] is True

    def test_no_price_and_no_amount_is_none_not_zero(self) -> None:
        row = attach_turnover([_row(amount=None, last=None, volume=1.0e6)])[0]
        assert row["turnover"] is None
        assert row["turnover_est"] is False

    def test_zero_volume_is_none(self) -> None:
        row = attach_turnover([_row(amount=None, volume=0.0)])[0]
        assert row["turnover"] is None

    def test_turnover_floor_filters_estimated_rows_consistently(self) -> None:
        rows = [
            _row(symbol="A.SH", amount=1e8),
            _row(symbol="B.SH", amount=None, last=10.0, volume=1e3),
        ]
        outcome = screen(rows, ScreenSpec(turnover_min=1e7, limit=10))
        assert [r["symbol"] for r in outcome.rows] == ["A.SH"]

    def test_attach_turnover_does_not_mutate_input(self) -> None:
        original = _row(amount=None)
        attach_turnover([original])
        assert "turnover" not in original


class TestMarketCapJoin:
    @staticmethod
    def _caps(rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows)

    def test_bare_code_joins_suffixed_symbol(self) -> None:
        caps = self._caps(
            [
                {
                    "symbol": "600000",
                    "date": "2026-10-20",
                    "float_mv": 3e11,
                    "total_mv": 4e11,
                }
            ]
        )
        out, missing = attach_market_cap([_row(symbol="600000.SH")], caps)
        assert out[0]["float_mv"] == 3e11
        assert out[0]["total_mv"] == 4e11
        assert missing == 0

    def test_missing_cap_is_none_and_counted(self) -> None:
        caps = self._caps(
            [
                {
                    "symbol": "600000",
                    "date": "2026-10-20",
                    "float_mv": 3e11,
                    "total_mv": 4e11,
                }
            ]
        )
        out, missing = attach_market_cap(
            [_row(symbol="600000.SH"), _row(symbol="301999.SZ")], caps
        )
        assert out[1]["float_mv"] is None
        assert missing == 1

    def test_none_frame_keeps_rows(self) -> None:
        out, missing = attach_market_cap([_row(), _row()], None)
        assert len(out) == 2
        assert missing == 2

    def test_multi_date_frame_takes_latest(self) -> None:
        caps = self._caps(
            [
                {
                    "symbol": "600000",
                    "date": "2026-10-19",
                    "float_mv": 1e9,
                    "total_mv": 2e9,
                },
                {
                    "symbol": "600000",
                    "date": "2026-10-20",
                    "float_mv": 3e9,
                    "total_mv": 4e9,
                },
            ]
        )
        out, _ = attach_market_cap([_row(symbol="600000.SH")], caps)
        assert out[0]["float_mv"] == 3e9

    def test_cap_filter_excludes_rows_without_cap(self) -> None:
        caps = self._caps(
            [
                {
                    "symbol": "600000",
                    "date": "2026-10-20",
                    "float_mv": 3e11,
                    "total_mv": 4e11,
                }
            ]
        )
        rows = [_row(symbol="600000.SH"), _row(symbol="301999.SZ")]
        outcome = screen(rows, ScreenSpec(float_mv_min=1e10, limit=10), market_cap=caps)
        assert [r["symbol"] for r in outcome.rows] == ["600000.SH"]
        assert any("无市值数据" in note for note in outcome.notes)


class TestOrderingAndTruncation:
    def test_descending_puts_nulls_last(self) -> None:
        rows = [
            _row(symbol="A.SH", change_pct=None),
            _row(symbol="B.SH", change_pct=-1.0),
            _row(symbol="C.SH", change_pct=5.0),
        ]
        outcome = screen(
            rows, ScreenSpec(sort_by="change_pct", sort_desc=True, limit=10)
        )
        assert [r["symbol"] for r in outcome.rows] == ["C.SH", "B.SH", "A.SH"]

    def test_ascending_also_puts_nulls_last(self) -> None:
        rows = [
            _row(symbol="A.SH", change_pct=None),
            _row(symbol="B.SH", change_pct=5.0),
            _row(symbol="C.SH", change_pct=-1.0),
        ]
        outcome = screen(
            rows, ScreenSpec(sort_by="change_pct", sort_desc=False, limit=10)
        )
        assert [r["symbol"] for r in outcome.rows] == ["C.SH", "B.SH", "A.SH"]

    def test_ties_break_on_symbol(self) -> None:
        rows = [
            _row(symbol="C.SH", change_pct=1.0),
            _row(symbol="A.SH", change_pct=1.0),
        ]
        outcome = screen(rows, ScreenSpec(sort_by="change_pct", limit=10))
        assert [r["symbol"] for r in outcome.rows] == ["A.SH", "C.SH"]

    def test_matched_counts_before_limit(self) -> None:
        rows = [_row(symbol=f"{i:06d}.SH", change_pct=float(i)) for i in range(10)]
        outcome = screen(rows, ScreenSpec(limit=3))
        assert outcome.matched == 10
        assert len(outcome.rows) == 3
        assert outcome.truncated is True
        assert outcome.scanned == 10

    def test_not_truncated_when_under_limit(self) -> None:
        outcome = screen([_row()], ScreenSpec(limit=50))
        assert outcome.truncated is False


class TestEmptyAndCombined:
    def test_empty_input_is_not_an_error(self) -> None:
        outcome = screen([], ScreenSpec(change_pct_min=1.0))
        assert outcome.rows == []
        assert outcome.matched == 0
        assert outcome.scanned == 0

    def test_predicates_are_anded(self) -> None:
        rows = [
            _row(symbol="A.SH", change_pct=5.0, volume_ratio=3.0),
            _row(symbol="B.SH", change_pct=5.0, volume_ratio=0.5),
            _row(symbol="C.SH", change_pct=-5.0, volume_ratio=3.0),
        ]
        spec = ScreenSpec(change_pct_min=1.0, volume_ratio_min=1.5, limit=10)
        assert [r["symbol"] for r in screen(rows, spec).rows] == ["A.SH"]

    def test_result_rows_are_copies(self) -> None:
        rows = [_row()]
        outcome = screen(rows, ScreenSpec())
        outcome.rows[0]["last"] = 999.0
        assert rows[0]["last"] == 10.0


class TestBoardVocabulary:
    def test_boards_constant_matches_market_rules(self) -> None:
        from selene_space_ashare.market_rules import board_of

        assert set(BOARDS) == {
            board_of("600000"),
            board_of("688981"),
            board_of("300750"),
            board_of("830799"),
        }


class TestDescribeSpec:
    def test_round_trips_normalized_values(self) -> None:
        spec = ScreenSpec(
            change_pct_min=1.0, board_types=("main",), exclude_st=True, limit=20
        )
        described = screener.describe_spec(spec)
        assert described["change_pct"] == [1.0, None]
        assert described["board_type"] == ["main"]
        assert described["exclude_st"] is True
        assert described["limit"] == 20


class TestSharedRowContract:
    """The screener and the overview must score the same stock the same way.

    Both panels render ``change_pct`` / ``boards`` / ``volume_ratio`` for the
    same name on the same day. The claim in ``build_quote_rows``' docstring —
    that they cannot disagree because they share one row builder — only holds
    if the *consumers* read the fields the builder actually emits, under the
    same names and units. A hand-built fixture cannot prove that: it can drift
    from the producer and stay green (the fixture would then be describing a
    shape that no longer exists).

    So these tests build rows with the real ``build_quote_rows`` and assert
    the two consumers agree on the numbers they show.
    """

    @staticmethod
    def _baseline(monkeypatch) -> Baseline:
        """Install a two-symbol baseline with a known prior board count."""
        from selene_space_ashare import market_overview

        frame = pd.DataFrame(
            [
                {
                    "name": "name-a",
                    "last_date": "2026-10-15",
                    "close": 10.0,
                    "ma5": 10.0,
                    "ma20": 10.0,
                    "ma60": 10.0,
                    "high60": 12.0,
                    "low60": 8.0,
                    "avg_vol5": 1_000.0,
                    "boards": 2,
                },
                {
                    "name": "name-b",
                    "last_date": "2026-10-15",
                    "close": 20.0,
                    "ma5": 20.0,
                    "ma20": 20.0,
                    "ma60": 20.0,
                    "high60": 24.0,
                    "low60": 16.0,
                    "avg_vol5": 1_000.0,
                    "boards": 0,
                },
            ],
            index=pd.Index(["600000.SH", "000001.SZ"], name="symbol"),
        )
        baseline = Baseline(
            as_of="2026-10-15",
            sessions=120,
            frame=frame,
            symbols=2,
            last_dates=("2026-10-15",),
        )
        monkeypatch.setattr(market_overview, "_flag_limits", _fake_flag_limits)
        return baseline

    # 600000.SH closes limit-up off a 2-board baseline → boards 3.
    # 000001.SZ closes flat → boards 0.
    _QUOTES = [
        {
            "symbol": "600000.SH",
            "name": "浦发银行",
            "last": 11.0,
            "prev_close": 10.0,
            "high": 11.0,
            "low": 10.2,
            "volume": 2_000.0,
            "amount": 22_000.0,
        },
        {
            "symbol": "000001.SZ",
            "name": "平安银行",
            "last": 20.0,
            "prev_close": 20.0,
            "high": 20.3,
            "low": 19.8,
            "volume": 500.0,
            "amount": 10_000.0,
        },
    ]

    def test_overview_reads_the_fields_build_quote_rows_emits(self, monkeypatch):
        """The overview's own numbers must come from the shared row shape.

        ``_overview`` elsewhere in this suite feeds hand-built quotes; this
        test pins the *end-to-end* path the page actually uses: real builder →
        ``assemble_overview`` aggregate. If the builder renamed or rescaled a
        field, breadth would drift before the page did.
        """
        from selene_space_ashare.market_overview import assemble_overview

        baseline = self._baseline(monkeypatch)
        overview = assemble_overview(
            today="2026-10-16",
            quotes=self._QUOTES,
            baseline=baseline,
            universe_size=2,
        )

        breadth = overview["breadth"]
        assert breadth["total"] == 2
        assert breadth["up"] == 1
        assert breadth["down"] == 0
        # (11.0 - 10.0) / 10.0 * 100 = 10.00 and (20-20)/20 = 0.00 → mean 5.0
        assert breadth["avg_pct"] == pytest.approx(5.0)
        assert overview["limit"]["limit_up"] == 1
        assert overview["limit"]["max_boards"] == 3
        # The same row tops the gainer list the page renders.
        top = overview["top_gainers"][0]
        assert top["symbol"] == "600000.SH"
        assert top["change_pct"] == pytest.approx(10.0)
        assert top["boards"] == 3

    def test_screener_sees_the_same_numbers_as_the_overview(self, monkeypatch):
        """Same row in, same ``change_pct`` / ``boards`` / ``volume_ratio`` out.

        The failure this guards is silent by construction: if the screener read
        a field name the builder never emits (``boards`` vs ``board``, say),
        every row would fall through the "unknown → excluded" branch and the
        panel would show an empty list with no error anywhere.
        """
        from selene_space_ashare.market_overview import (
            assemble_overview,
            build_quote_rows,
        )

        baseline = self._baseline(monkeypatch)
        rows = build_quote_rows(quotes=self._QUOTES, baseline=baseline)

        # A filter that a broken field name would silently empty.
        hit = screen(rows, ScreenSpec(boards_min=3, limit=10))
        assert hit.matched == 1
        assert hit.rows[0]["symbol"] == "600000.SH"

        # And the numbers it shows match the overview's per-stock view.
        overview = assemble_overview(
            today="2026-10-16",
            quotes=self._QUOTES,
            baseline=baseline,
            universe_size=2,
        )
        top = overview["top_gainers"][0]
        assert hit.rows[0]["change_pct"] == pytest.approx(top["change_pct"])
        assert hit.rows[0]["boards"] == top["boards"]
        assert hit.rows[0]["volume_ratio"] == pytest.approx(top["volume_ratio"])

        # 2,000 shares vs a 1,000-share 5-day average → 2.0× on both sides.
        assert hit.rows[0]["volume_ratio"] == pytest.approx(2.0)

    def test_volume_ratio_is_the_same_number_on_both_panels(self, monkeypatch):
        """量比 is the field most likely to be rescaled by a consumer."""
        from selene_space_ashare.market_overview import (
            assemble_overview,
            build_quote_rows,
        )

        baseline = self._baseline(monkeypatch)
        rows = build_quote_rows(quotes=self._QUOTES, baseline=baseline)
        by_symbol = {r["symbol"]: r for r in rows}
        overview = assemble_overview(
            today="2026-10-16",
            quotes=self._QUOTES,
            baseline=baseline,
            universe_size=2,
        )

        leaders = {r["symbol"]: r for r in overview["vol_ratio_leaders"]}
        for symbol, ratio in by_symbol.items():
            assert leaders[symbol]["volume_ratio"] == pytest.approx(
                ratio["volume_ratio"]
            )


def _fake_flag_limits(frame: pd.DataFrame) -> pd.DataFrame:
    """Limit flags without the exchange's Decimal ratio rules.

    ``_flag_limits`` resolves 10%/20%/30% by board + ST status from the real
    rule table; that table is covered by ``market_rules``' own tests. Here the
    question is whether the *rows* carry the shared fields, so a 10% move off
    the previous close is treated as a board.
    """
    frame = frame.copy()
    prev = pd.to_numeric(frame["prev_close"], errors="coerce")
    close = pd.to_numeric(frame["close"], errors="coerce")
    high = pd.to_numeric(frame["high"], errors="coerce")
    up = (prev > 0) & (close >= prev * 1.1 - 1e-9)
    down = (prev > 0) & (close <= prev * 0.9 + 1e-9)
    frame["sig_limit_up"] = up.fillna(False).astype(bool)
    frame["sig_limit_down"] = down.fillna(False).astype(bool)
    frame["sig_broken"] = ((high >= prev * 1.1 - 1e-9) & ~up).fillna(False).astype(bool)
    return frame
