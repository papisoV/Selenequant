"""Tests for sealed-at-limit classification from level-1 depth.

Offline and deterministic. The fixtures are shaped like real snapshot rows and
the payloads are the live-measured layouts (Sina ``(vol, price)`` pairs from
field 10/20; 腾讯 ``price~vol`` from 9/19) — not idealised stand-ins, so a
field-index regression bites here.

Two rows carry the exact shape of the live findings from the 2026-10-16
session: a truly sealed 涨停 (``sell1_vol == 0``) and ``000678.SZ``, a 假封板
with 523789 shares still offered at the limit. The pair is the whole point —
the price-only test the ladder used before cannot tell them apart.
"""

from __future__ import annotations

import os as _os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


from selene_space_ashare.sealed import (
    DOWN,
    NONE,
    REASON_NO_DEPTH,
    REASON_NO_PREV_CLOSE,
    REASON_NO_QUOTE_TIME,
    REASON_NOT_AT_LIMIT,
    REASON_NOT_CONTINUOUS,
    REASON_OPEN,
    REASON_SEALED,
    UP,
    classify_sealed,
    in_continuous_trading,
    sealed_counts,
)

_CST = timezone(timedelta(hours=8))


def _ms(hour: int, minute: int, day: int = 16, month: int = 10) -> int:
    """Epoch ms for 2026-``month``-``day`` at ``hour:minute`` CST.

    Production stamps are CST epoch-ms; a naive ``.timestamp()`` on a UTC host
    would land 8h earlier and roll into a different session window, which is
    exactly the bug the guard must not have.

    ``month`` exists because the price-limit band is date-aware: a 5% print is
    only a 涨停 on a main-board ST name before 2026-07-06, so the fixtures that
    exercise that band need a session on the right side of the boundary.
    """
    return int(datetime(2026, month, day, hour, minute, tzinfo=_CST).timestamp() * 1000)


@dataclass(frozen=True)
class Row:
    """Stand-in for ``SnapshotRow`` — duck-typed, as the real one is."""

    symbol: str = "600519.SH"
    name: str = "贵州茅台"
    last: float = 10.0
    prev_close: float = 10.0
    bid1_vol: float | None = 100.0
    ask1_vol: float | None = 0.0
    timestamp: int | None = _ms(10, 0)


# 000678.SZ on 2026-10-16: last == limit-up price with 523789 shares still
# offered — the 假封板. prev_close 9.94 → limit 10.934 rounds to 10.93.
_FAKE = Row(
    symbol="000678.SZ",
    last=10.93,
    prev_close=9.94,
    bid1_vol=109200.0,
    ask1_vol=523789.0,
)


class TestClassifySealedUp:
    def test_no_resting_offer_is_sealed(self) -> None:
        state = classify_sealed(Row(last=11.0, prev_close=10.0, ask1_vol=0.0))
        assert state.direction == UP
        assert state.sealed is True
        assert state.reason == REASON_SEALED

    def test_sealed_vol_is_the_supporting_buy_side(self) -> None:
        """封单量 = the side holding the limit (买一 for a 涨停), not the empty side."""
        state = classify_sealed(
            Row(last=11.0, prev_close=10.0, bid1_vol=8_900_000.0, ask1_vol=0.0)
        )
        assert state.vol == 8_900_000.0

    def test_resting_offer_is_not_sealed(self) -> None:
        """The 假封板 case: at the limit, but offers still resting."""
        state = classify_sealed(_FAKE)
        assert state.direction == UP
        assert state.sealed is False
        assert state.reason == REASON_OPEN
        assert state.vol is None  # no seal to size

    def test_missing_depth_is_undecided_not_sealed(self) -> None:
        """``None`` depth must never collapse to the sealed ``0`` answer."""
        state = classify_sealed(Row(last=11.0, prev_close=10.0, ask1_vol=None))
        assert state.sealed is None
        assert state.reason == REASON_NO_DEPTH

    def test_price_above_limit_still_classifies(self) -> None:
        # Overshooting the limit (rounding on the print) must not fall through.
        state = classify_sealed(Row(last=11.02, prev_close=10.0, ask1_vol=0.0))
        assert state.sealed is True


class TestClassifySealedDown:
    def test_no_resting_bid_is_sealed_down(self) -> None:
        # 20% board: prev 10 → limit-down 8.0.
        state = classify_sealed(
            Row(
                symbol="300750.SZ",
                last=8.0,
                prev_close=10.0,
                bid1_vol=0.0,
                ask1_vol=100.0,
            )
        )
        assert state.direction == DOWN
        assert state.sealed is True

    def test_sealed_down_vol_is_the_ask_side(self) -> None:
        state = classify_sealed(
            Row(
                symbol="300750.SZ",
                last=8.0,
                prev_close=10.0,
                bid1_vol=0.0,
                ask1_vol=4_200.0,
            )
        )
        assert state.vol == 4_200.0

    def test_resting_bid_is_fake_down(self) -> None:
        state = classify_sealed(
            Row(
                symbol="300750.SZ",
                last=8.0,
                prev_close=10.0,
                bid1_vol=7.0,
                ask1_vol=0.0,
            )
        )
        assert state.direction == DOWN
        assert state.sealed is False


class TestNotAtLimit:
    def test_ordinary_move_is_none(self) -> None:
        state = classify_sealed(Row(last=10.5, prev_close=10.0))
        assert state.direction == NONE
        assert state.sealed is None
        assert state.reason == REASON_NOT_AT_LIMIT

    def test_st_board_uses_five_percent_before_reform(self) -> None:
        """A main-board ST name limited at 5% before 2026-07-06.

        The stamp carries the session because the band is date-aware: the same
        10.50 print is a 涨停 in the old regime and an ordinary move after it.
        """
        state = classify_sealed(
            Row(
                symbol="600519.SH",
                name="ST某某",
                last=10.5,
                prev_close=10.0,
                ask1_vol=0.0,
                timestamp=_ms(10, 0, day=2, month=7),
            )
        )
        assert state.direction == UP
        assert state.sealed is True

    def test_st_board_is_ten_percent_after_reform(self) -> None:
        state = classify_sealed(
            Row(
                symbol="600519.SH",
                name="ST某某",
                last=10.5,
                prev_close=10.0,
                ask1_vol=0.0,
                timestamp=_ms(10, 0, day=7, month=7),
            )
        )
        assert state.direction == NONE
        assert state.reason == REASON_NOT_AT_LIMIT

    def test_st_on_growth_board_keeps_twenty_percent(self) -> None:
        # 10.50 off a 10.00 base is not a limit on a 20% band, ST or not.
        state = classify_sealed(
            Row(
                symbol="300750.SZ",
                name="ST某某",
                last=10.5,
                prev_close=10.0,
                ask1_vol=0.0,
                timestamp=_ms(10, 0, day=2, month=7),
            )
        )
        assert state.direction == NONE

    def test_first_day_listing_has_no_limit(self) -> None:
        state = classify_sealed(Row(last=10.0, prev_close=0.0))
        assert state.direction == NONE
        assert state.reason == REASON_NO_PREV_CLOSE


class TestContinuousTradingGuard:
    def test_morning_window_open(self) -> None:
        assert in_continuous_trading(datetime(2026, 10, 16, 10, 0, tzinfo=_CST))

    def test_auction_prep_rejected(self) -> None:
        """12:58 is the afternoon call-auction prep — the window TSP got burned on."""
        assert not in_continuous_trading(datetime(2026, 10, 16, 12, 58, tzinfo=_CST))

    def test_closing_auction_tail_is_still_decidable(self) -> None:
        """14:57-15:00 is the closing call auction and the port keeps it inside.

        This pins the deliberate divergence-free choice: TSP's guard ends at
        15:00 inclusive, and a row stamped 15:00:00 is the settled closing book.
        Measured live 2026-10-16: the only two rows that decided were stamped
        exactly 15:00:00. Narrowing to 14:57 would make selene disagree with TSP
        about the close, so the boundary is asserted, not left implicit.
        """
        assert in_continuous_trading(datetime(2026, 10, 16, 14, 57, tzinfo=_CST))
        assert in_continuous_trading(datetime(2026, 10, 16, 15, 0, tzinfo=_CST))
        assert not in_continuous_trading(datetime(2026, 10, 16, 15, 0, 1, tzinfo=_CST))

    def test_lunch_break_rejected(self) -> None:
        assert not in_continuous_trading(datetime(2026, 10, 16, 12, 0, tzinfo=_CST))

    def test_pre_open_rejected(self) -> None:
        assert not in_continuous_trading(datetime(2026, 10, 16, 9, 20, tzinfo=_CST))

    def test_weekend_rejected(self) -> None:
        assert not in_continuous_trading(datetime(2026, 10, 17, 10, 0, tzinfo=_CST))

    def test_naive_datetime_read_as_cst(self) -> None:
        """A naive stamp must be read as CST, not as host-local (8h off on UTC)."""
        assert in_continuous_trading(datetime(2026, 10, 16, 10, 0))
        assert not in_continuous_trading(datetime(2026, 10, 16, 3, 0))

    def test_naive_stamp_is_host_tz_independent(self) -> None:
        """The naive-stamp rule, forced to disagree with a UTC host.

        On this UTC+8 box a naive stamp read as local *equals* CST, so the two
        behaviours are indistinguishable and ``test_naive_datetime_read_as_cst``
        passes with the bug present. Running the child under ``TZ=UTC`` makes
        the readings 8h apart: 10:00 CST (in session) vs 18:00 CST (not).
        """
        import subprocess
        import sys

        snippet = (
            "from datetime import datetime;"
            "from selene_space_ashare.sealed import in_continuous_trading;"
            "print(in_continuous_trading(datetime(2026, 10, 16, 10, 0)))"
        )
        out = subprocess.run(
            [sys.executable, "-c", snippet],
            env={**_os.environ, "TZ": "UTC"},
            capture_output=True,
            text=True,
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "True"

    def test_off_session_quote_is_undecided(self) -> None:
        """A sealed-looking row stamped 12:58 gets no verdict, not a free pass."""
        state = classify_sealed(
            Row(last=11.0, prev_close=10.0, ask1_vol=0.0, timestamp=_ms(12, 58))
        )
        assert state.sealed is None
        assert state.reason == REASON_NOT_CONTINUOUS

    def test_missing_quote_time_is_undecided(self) -> None:
        state = classify_sealed(
            Row(last=11.0, prev_close=10.0, ask1_vol=0.0, timestamp=None)
        )
        assert state.sealed is None
        assert state.reason == REASON_NO_QUOTE_TIME

    def test_guard_uses_quote_time_not_wall_clock(self) -> None:
        """Same row, same verdict, regardless of when the test runs."""
        row = Row(last=11.0, prev_close=10.0, ask1_vol=0.0, timestamp=_ms(14, 30))
        assert classify_sealed(row).sealed is True


class TestSealedCounts:
    def test_mixed_cross_section(self) -> None:
        rows = {
            "A.SH": Row(symbol="A.SH", last=11.0, prev_close=10.0, ask1_vol=0.0),
            "B.SH": Row(symbol="B.SH", last=11.0, prev_close=10.0, ask1_vol=500.0),
            "C.SH": Row(symbol="C.SH", last=10.0, prev_close=10.0),  # not at limit
        }
        counts = sealed_counts(rows)
        assert counts["real_up"] == 1
        assert counts["fake_up"] == 1
        assert counts["ready"] is True

    def test_undecided_counted_separately(self) -> None:
        """A limit-up with no depth is 'undecided', not silently a real seal."""
        rows = {
            "A.SH": Row(symbol="A.SH", last=11.0, prev_close=10.0, ask1_vol=0.0),
            "B.SH": Row(symbol="B.SH", last=11.0, prev_close=10.0, ask1_vol=None),
        }
        counts = sealed_counts(rows)
        assert counts["real_up"] == 1
        assert counts["undecided"] == 1
        assert counts["fake_up"] == 0

    def test_all_undecided_reports_not_ready(self) -> None:
        """Nothing decidable ⇒ ready False, so a 0 is never rendered as a tally."""
        rows = {"B.SH": Row(symbol="B.SH", last=11.0, prev_close=10.0, ask1_vol=None)}
        counts = sealed_counts(rows)
        assert counts["ready"] is False

    def test_empty_cross_section(self) -> None:
        counts = sealed_counts({})
        assert counts["ready"] is False
        assert counts["real_up"] == 0

    def test_down_side_counted(self) -> None:
        rows = {
            "D.SZ": Row(
                symbol="300750.SZ",
                last=8.0,
                prev_close=10.0,
                bid1_vol=0.0,
                ask1_vol=1.0,
            ),
        }
        counts = sealed_counts(rows)
        assert counts["real_down"] == 1
        assert counts["real_up"] == 0


class TestDuckTyping:
    def test_mapping_rows_accepted(self) -> None:
        """A JSON round-trip yields dicts; the contract is duck-typed."""
        row = {
            "symbol": "600519.SH",
            "name": "贵州茅台",
            "last": 11.0,
            "prev_close": 10.0,
            "bid1_vol": 900.0,
            "ask1_vol": 0.0,
            "timestamp": _ms(10, 0),
        }
        assert classify_sealed(row).sealed is True

    def test_row_without_depth_fields_is_undecided(self) -> None:
        """A pre-depth row (no bid1_vol/ask1_vol keys at all) must not crash."""
        state = classify_sealed(
            {
                "symbol": "X.SH",
                "last": 11.0,
                "prev_close": 10.0,
                "timestamp": _ms(10, 0),
            }
        )
        assert state.sealed is None
        assert state.reason == REASON_NO_DEPTH


class TestFakeVsRealAreDistinguishable:
    def test_the_pair_the_price_test_cannot_split(self) -> None:
        """Both read as limit-up by price; only the book tells them apart."""
        real = Row(
            symbol="000099.SZ",
            last=14.75,
            prev_close=13.41,
            bid1_vol=4_941_181.0,
            ask1_vol=0.0,
        )
        fake = _FAKE
        assert classify_sealed(real).sealed is True
        assert classify_sealed(fake).sealed is False
