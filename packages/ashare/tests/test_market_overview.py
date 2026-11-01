# Absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 tick-stock-panel contributors
# Source: https://github.com/shy3130/tick-stock-panel
"""Market overview assembler tests — pure functions, no network, no real db.

The two properties worth pinning hardest, because both failed silently in
development:

* **The newest (partial) session must never enter the baseline.** On the real
  base 2026-10-19 carried 3973/5574 names while still filling; folding it in
  under-counts a third of the market without any error.
* **``market_daily_window.prev_close`` is a window-level constant**, not a
  per-bar shift. Using it per row manufactured 37- and 120-board streaks on
  names that never had them.
"""

from __future__ import annotations

import pandas as pd
import pytest

from selene_space_ashare import market_overview
from selene_space_ashare.market_overview import (
    MIN_COVERAGE,
    assemble_overview,
    compute_baseline,
    quotes_from_daily,
    quotes_from_snapshot,
)


@pytest.fixture()
def patch_window(monkeypatch):
    """Route ``compute_baseline``'s window read at a canned frame.

    ``market_daily_window`` also resolves display names through the cached
    akshare universe (a network call on a cold cache), and its own unit tests
    already pin its date/column contract. What these tests are about is
    ``compute_baseline``'s use of that frame, so the boundary is patched and the
    fixture keeps the one property the module depends on: ``prev_close`` is a
    **window-level constant**, not a per-bar shift.
    """

    def _install(frame: pd.DataFrame) -> None:
        def fake_window(db, *, start_date, end_date, symbols=None, adjust="raw"):
            return frame.copy()

        monkeypatch.setattr(market_overview, "market_daily_window", fake_window)

    return _install


def _frame(rows: list[dict]) -> pd.DataFrame:
    """Build a ``market_daily_window``-shaped frame from explicit rows."""
    columns = [
        "symbol",
        "name",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "prev_close",
    ]
    frame = pd.DataFrame(rows, columns=columns)
    for column in ("open", "high", "low", "close", "volume", "prev_close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


class _StubDB:
    """Minimal ``get_partition_range`` stand-in returning a canned frame."""

    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.calls: list[tuple[int, int]] = []

    def get_partition_range(self, exchange, market_type, interval, *, start_ms, end_ms):
        self.calls.append((start_ms, end_ms))
        return self.frame


def _series(symbol: str, closes: list[float], start: str = "2026-06-01") -> list[dict]:
    """One row per calendar day from ``start``.

    Real successive dates, not a zero-padded day-of-month: a 70-row series
    would otherwise emit ``2026-06-70`` and sort by luck rather than by date.
    """
    rows = []
    prev = closes[0] / 1.02
    first = pd.Timestamp(start)
    for offset, close in enumerate(closes):
        rows.append(
            {
                "symbol": symbol,
                "name": f"name-{symbol}",
                "date": (first + pd.Timedelta(days=offset)).date().isoformat(),
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 1000.0,
                "prev_close": prev,
            }
        )
        prev = close
    return rows


def test_newest_partial_session_never_enters_the_baseline(patch_window):
    """The scoring day's own rows must not contribute to any indicator."""
    full = _series("600000.SH", [10.0 + i * 0.1 for i in range(70)])
    today = [
        {
            "symbol": "600000.SH",
            "name": "name-600000.SH",
            "date": "2026-09-30",
            "open": 99.0,
            "high": 99.0,
            "low": 99.0,
            "close": 99.0,  # a wild print on the partial session
            "volume": 1e9,
            "prev_close": 99.0,
        }
    ]
    db = _StubDB(_frame(full + today))
    patch_window(_frame(full + today))

    baseline = compute_baseline(db, today="2026-09-30")

    row = baseline.frame.loc["600000.SH"]
    # 99.0 would drag ma5 to ~30; the true trailing mean is ~17.5.
    assert row["close"] == pytest.approx(full[-1]["close"])
    assert row["ma5"] < 20.0
    assert row["ma60"] is not None and row["ma60"] < 20.0
    assert baseline.as_of == full[-1]["date"]


def test_prev_close_is_a_per_bar_shift_not_the_window_constant(patch_window):
    """A streak must be counted against the previous *bar*, not a 120-old close.

    The stub's ``prev_close`` column is deliberately the first bar's previous
    close on every row — exactly what ``market_daily_window`` publishes.

    The plateau at the end is what makes this discriminating. On a purely
    rising +10% series both readings score the same streak: an older prev
    price is *lower*, so every later close clears its (also lower) limit, and
    the last bar's count happens to match. The damage shows up when the price
    stops rising: against the window constant (9.8039 → limit 10.78) every
    plateau print at 17.71 is still "≥ limit", so the streak keeps running to
    the end of the window (here 9); against the true previous bar (17.71) the
    plateau is flat and the streak stops at 6. On the real base this is what
    produced 37- and 120-board streaks on names that never had them.
    """
    closes = [10.0, 11.0, 12.1, 13.31, 14.64, 16.10, 17.71]  # six +10% boards
    closes += [17.71, 17.71, 17.71]  # three flat sessions break the streak
    closes += [19.48]  # then one fresh board off 17.71 (17.71 * 1.1 = 19.481)
    rows = _series("600000.SH", closes)
    constant_prev = rows[0]["prev_close"]
    for row in rows:
        row["prev_close"] = constant_prev  # window-level constant
    db = _StubDB(_frame(rows))
    patch_window(_frame(rows))

    baseline = compute_baseline(db, today="2026-08-01")

    # True shift: the plateau resets, so the last bar is a fresh 1-board.
    # Window constant: every bar since the first still clears its 10.78 limit,
    # so the "streak" would read 11.
    assert baseline.frame.loc["600000.SH", "boards"] == 1


def test_rolling_windows_do_not_leak_across_symbols(patch_window):
    """The concatenated frame must not carry one symbol's bars into another's MA.

    The second symbol carries 59 bars: too few for a 60-bar window of its own.
    Its indicator must therefore be ``NaN``, not a number borrowed from the
    previous symbol's tail. Ungrouped, the window would fabricate
    ``(1 * 100 + 59 * 5) / 60 ≈ 6.6`` for a name that never had a 60-day mean.
    """
    rows = _series("000001.SZ", [100.0] * 100) + _series("600000.SH", [5.0] * 59)
    db = _StubDB(_frame(rows))
    patch_window(_frame(rows))

    # Past both series: a `today` inside the second symbol's span would trim
    # its block to fewer bars for reasons that have nothing to do with grouping.
    baseline = compute_baseline(db, today="2026-09-01")

    assert baseline.frame.loc["000001.SZ", "ma60"] == pytest.approx(100.0)
    assert "600000.SH" in baseline.frame.index
    assert pd.isna(baseline.frame.loc["600000.SH", "ma60"])


def test_boards_reset_after_a_non_limit_day(patch_window):
    rows = _series("600000.SH", [10.0, 11.0, 12.1, 12.2, 13.42])
    db = _StubDB(_frame(rows))
    patch_window(_frame(rows))

    baseline = compute_baseline(db, today="2026-08-01")

    # 10→11(+10%), 11→12.1(+10%), 12.1→12.2(+0.8% resets), 12.2→13.42(+10%).
    assert baseline.frame.loc["600000.SH", "boards"] == 1


def test_baseline_rejects_too_short_a_window(patch_window):
    db = _StubDB(_frame([]))
    patch_window(_frame([]))
    with pytest.raises(ValueError, match="cannot support"):
        compute_baseline(db, today="2026-08-01", sessions=10)


def test_baseline_rejects_malformed_today(patch_window):
    db = _StubDB(_frame([]))
    patch_window(_frame([]))
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        compute_baseline(db, today="16-10-2026")


def test_quotes_from_daily_drops_stale_rows():
    """A suspended name keeps its old date; it must not become today's print."""
    frame = _frame(
        [
            {
                "symbol": "600000.SH",
                "name": "a",
                "date": "2026-10-16",
                "open": 10.0,
                "high": 10.5,
                "low": 9.9,
                "close": 10.2,
                "volume": 1000.0,
                "prev_close": 10.0,
            },
            {
                "symbol": "000001.SZ",
                "name": "halted",
                "date": "2026-09-01",  # suspended; no bar today
                "open": 5.0,
                "high": 5.0,
                "low": 5.0,
                "close": 5.0,
                "volume": 0.0,
                "prev_close": 5.0,
            },
        ]
    )

    quotes = quotes_from_daily(frame, today="2026-10-16")

    assert [q["symbol"] for q in quotes] == ["600000.SH"]


def test_quotes_from_snapshot_reads_attributes_and_dicts():
    class Row:
        symbol = "600000.SH"
        name = "a"
        last = 10.0
        prev_close = 9.0
        high = 10.2
        low = 8.9
        volume = 100.0
        amount = 1000.0

    quotes = quotes_from_snapshot(
        {
            "600000.SH": Row(),
            "000001.SZ": {
                "symbol": "000001.SZ",
                "name": "b",
                "last": 0.0,
                "prev_close": 1.0,
                "high": 1.0,
                "low": 1.0,
                "volume": 1.0,
                "amount": 1.0,
            },
        }
    )

    assert [q["symbol"] for q in quotes] == ["600000.SH"]
    assert quotes[0]["amount"] == 1000.0


def _overview(quotes, baseline=None, **kwargs):
    from selene_space_ashare.market_overview import Baseline

    if baseline is None:
        baseline = Baseline(
            as_of="2026-10-15", sessions=120, frame=pd.DataFrame(), symbols=0
        )
    return assemble_overview(
        today="2026-10-16", quotes=quotes, baseline=baseline, **kwargs
    )


def test_amount_is_unavailable_rather_than_zero_when_absent():
    quotes = [
        {
            "symbol": "600000.SH",
            "name": "a",
            "last": 11.0,
            "prev_close": 10.0,
            "high": 11.0,
            "low": 10.0,
            "volume": 1000.0,
            "amount": None,
        }
    ]

    overview = _overview(quotes, universe_size=1)

    assert overview["amount"]["available"] is False
    assert overview["amount"]["total"] is None
    assert overview["amount_leaders"] == []


def test_partial_coverage_reports_degraded():
    quotes = [
        {
            "symbol": "600000.SH",
            "name": "a",
            "last": 11.0,
            "prev_close": 10.0,
            "high": 11.0,
            "low": 10.0,
            "volume": 1000.0,
            "amount": None,
        }
    ]

    overview = _overview(quotes, universe_size=int(1 / MIN_COVERAGE) + 10)

    assert overview["status"] == "degraded"
    assert overview["reason"] == "partial_coverage"


def test_limit_up_extends_streak_and_broken_board_does_not():
    from selene_space_ashare.market_overview import Baseline

    baseline = Baseline(
        as_of="2026-10-15",
        sessions=120,
        frame=pd.DataFrame(
            {
                "name": ["a"],
                "last_date": ["2026-10-15"],
                "close": [11.0],
                "ma5": [10.0],
                "ma20": [9.5],
                "ma60": [9.0],
                "high60": [12.0],
                "low60": [8.0],
                "avg_vol5": [1000.0],
                "boards": [2],
            },
            index=["600000.SH"],
        ),
        symbols=1,
    )
    quotes = [
        {
            "symbol": "600000.SH",
            "name": "a",
            "last": 12.1,  # 11.0 * 1.10 = 12.10 → limit-up
            "prev_close": 11.0,
            "high": 12.1,
            "low": 11.0,
            "volume": 1500.0,
            "amount": None,
        },
        {
            "symbol": "000001.SZ",
            "name": "b",
            # Touched the limit intraday but closed below it → 炸板, streak reset.
            "last": 11.5,
            "prev_close": 11.0,
            "high": 12.1,
            "low": 11.0,
            "volume": 1000.0,
            "amount": None,
        },
    ]

    overview = _overview(quotes, baseline=baseline, universe_size=2)

    assert overview["limit"]["limit_up"] == 1
    assert overview["limit"]["broken"] == 1
    assert overview["limit"]["max_boards"] == 3  # 2 prior boards + today


def test_sealed_counts_reduce_limit_up_only_when_ready():
    quotes = [
        {
            "symbol": "600000.SH",
            "name": "a",
            "last": 11.0,
            "prev_close": 10.0,
            "high": 11.0,
            "low": 10.0,
            "volume": 1000.0,
            "amount": None,
        }
    ]

    not_ready = _overview(
        quotes, sealed={"ready": False, "fake_up": 1, "fake_down": 0}, universe_size=1
    )
    ready = _overview(
        quotes, sealed={"ready": True, "fake_up": 1, "fake_down": 0}, universe_size=1
    )

    assert not_ready["limit"]["limit_up"] == 1
    assert not_ready["limit"]["sealed_ready"] is False
    assert ready["limit"]["limit_up"] == 0
    assert ready["limit"]["fake_up"] == 1


def test_empty_quotes_is_empty_not_ok():
    overview = _overview([])

    assert overview["status"] == "empty"
    assert overview["reason"] == "no_quotes"
    # No cross-section means no reading. Averaging the neutral fallbacks would
    # publish a confident "42 / 震荡" computed entirely from absent data.
    assert overview["emotion"] == {"score": None, "label": "无数据"}
    assert overview["radar"] == []


def test_percentages_are_percent_numbers():
    """Every ``*_pct`` is a percent number (1.23 == +1.23%), never a fraction."""
    quotes = [
        {
            "symbol": f"60000{i}.SH",
            "name": "a",
            "last": 11.0 if i < 3 else 9.0,
            "prev_close": 10.0,
            "high": None,
            "low": None,
            "volume": None,
            "amount": None,
        }
        for i in range(5)
    ]

    overview = _overview(quotes, universe_size=5)

    assert overview["breadth"]["up_pct"] == pytest.approx(60.0)
    assert overview["breadth"]["down_pct"] == pytest.approx(40.0)
    assert overview["breadth"]["avg_pct"] == pytest.approx(2.0)


def test_board_classification():
    quotes = [
        {
            "symbol": symbol,
            "name": "a",
            "last": 10.0,
            "prev_close": 10.0,
            "high": None,
            "low": None,
            "volume": None,
            "amount": None,
        }
        for symbol in ("600000.SH", "000001.SZ", "300001.SZ", "688001.SH", "920001.BJ")
    ]

    overview = _overview(quotes, universe_size=5)

    boards = {b["board"] for b in overview["boards"]}
    assert boards == {"main", "cyb", "kcb", "bse"}


# ---------------------------------------------------------------------------
# Date-aware limit flags
# ---------------------------------------------------------------------------


def _st_quote() -> list[dict]:
    """One main-board 风险警示 name closing +5% off its previous close."""
    return [
        {
            "symbol": "600000.SH",
            "name": "ST某某",
            "last": 10.5,
            "prev_close": 10.0,
            "high": 10.5,
            "low": 10.0,
            "volume": 1000.0,
            "amount": None,
        }
    ]


def _count_limit_ups(today: str) -> int:
    from selene_space_ashare.market_overview import (
        Baseline,
        assemble_overview,
    )

    overview = assemble_overview(
        today=today,
        quotes=_st_quote(),
        baseline=Baseline(
            as_of="2026-06-30", sessions=120, frame=pd.DataFrame(), symbols=0
        ),
        universe_size=1,
    )
    return int(overview["limit"]["limit_up"])


def test_limit_flags_follow_the_sessions_own_band():
    """The overview scores a session with *that session's* rulebook.

    A main-board ST name closing +5% is a 涨停 before 2026-07-06 and an
    ordinary move after it. Threading ``today`` into the band rule is what
    makes the two differ; without it both calls resolve the current rulebook
    and the first assertion fails.
    """
    assert _count_limit_ups("2026-07-03") == 1
    assert _count_limit_ups("2026-07-06") == 0


def test_build_quote_rows_without_a_date_uses_the_current_rulebook():
    from selene_space_ashare.market_overview import (
        Baseline,
        build_quote_rows,
    )

    queries = [
        {
            "symbol": "600000.SH",
            "name": "ST某某",
            "last": 10.5,
            "prev_close": 10.0,
            "high": 10.5,
            "low": 10.0,
            "volume": 1000.0,
            "amount": None,
        }
    ]
    baseline = Baseline(
        as_of="2026-10-15", sessions=120, frame=pd.DataFrame(), symbols=0
    )

    undated = build_quote_rows(quotes=queries, baseline=baseline)
    legacy = build_quote_rows(
        quotes=queries, baseline=baseline, trade_date="2026-07-03"
    )

    assert undated[0]["limit_up"] is False
    assert legacy[0]["limit_up"] is True


def test_growth_board_st_is_not_five_percent_on_any_date():
    """20%-band boards never carried the ST discount, before the reform either."""
    from selene_space_ashare.market_overview import (
        Baseline,
        build_quote_rows,
    )

    rows = build_quote_rows(
        quotes=[
            {
                "symbol": "300750.SZ",
                "name": "ST某某",
                "last": 10.5,
                "prev_close": 10.0,
                "high": 10.5,
                "low": 10.0,
                "volume": 1000.0,
                "amount": None,
            }
        ],
        baseline=Baseline(
            as_of="2026-06-30", sessions=120, frame=pd.DataFrame(), symbols=0
        ),
        trade_date="2026-07-03",
    )

    assert rows[0]["limit_up"] is False


def test_ratio_cache_does_not_leak_a_band_across_the_reform():
    """One symbol, two sessions straddling 2026-07-06, in *both* orders.

    The ratio cache is keyed by ``(code, is_st, date)`` and ``_flag_limits``
    never states that its frame is sorted. Dropping the date from the key only
    breaks in one direction: an ascending frame caches the *narrower* legacy
    band first, which only loosens the prefilter (harmless); a frame whose
    post-reform row comes first caches 10% and then *excludes* a genuine
    10.50 = +5% limit-up from the legacy row — a silently missed limit-up.

    Both orders are asserted against the same expected per-row answer so the
    function is pinned as order-independent rather than accidentally so.
    """
    from selene_space_ashare.market_overview import _flag_limits

    def frame(days: list[str]) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "symbol": ["600001.SH", "600001.SH"],
                "name": ["ST测试", "ST测试"],
                "date": days,
                "prev_close": [10.0, 10.0],
                "close": [10.5, 10.5],
                "high": [10.5, 10.5],
            }
        )

    ascending = _flag_limits(frame(["2026-07-03", "2026-07-06"]))
    assert list(ascending["sig_limit_up"]) == [True, False]

    descending = _flag_limits(frame(["2026-07-06", "2026-07-03"]))
    assert list(descending["sig_limit_up"]) == [False, True]
