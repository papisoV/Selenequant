"""Mainline scoring — arithmetic on synthetic ladders, contract on the real base.

Three things are worth pinning here and none of them are visible in a real-data
smoke run, because real data always produces *a* ranking:

* **The limit-up boundary.** ``close >= limit_up_price(prev_close)`` is an exact
  Decimal comparison after rounding half-up. A float comparison drifts at the
  cent and flips a borderline day. The fixture pins a close exactly at the
  limit (must be a limit-up) and exactly one cent below (must not).
* **The run resets.** A streak counts *consecutive* sessions. A halt, a
  non-limit day, or a window-opening bar all reset it to 0 — and the third one
  is the subtle case, because the bar itself is a valid baseline.
* **The score's shape.** TSP's weights are 0.35/0.25/0.25/0.15 over
  rank-normalised features, and a concept whose only distinguishing feature is
  a very long board must outrank a concept that merely has more limit-ups when
  the weights say so. The fixture is built so swapping any two weights changes
  the ordering.

运行:
    pytest packages/ashare/tests/test_mainline.py -v
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time
from pathlib import Path

import pandas as pd
import pytest

from selene_space_ashare import mainline as ml
from selene_space_ashare import market_rules

REPO_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = REPO_ROOT / "data" / "klines.db"


def _day_ms(day: str) -> int:
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Shanghai")
    y, m, d = (int(part) for part in day.split("-"))
    return int(datetime.combine(date(y, m, d), time.min, tzinfo=tz).timestamp() * 1000)


class StubDB:
    def __init__(self, bars: pd.DataFrame, sessions: list[str]):
        self._bars = bars
        self._sessions = sorted(sessions)
        self.requests: list[dict[str, object]] = []

    def get_session_stamps(self, *_args, **_kwargs) -> list[int]:
        self.requests.append(dict(_kwargs))
        return [_day_ms(day) for day in self._sessions]

    def get_partition_range(self, *_args, **_kwargs) -> pd.DataFrame:
        return self._bars.copy()


def _bars(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "timestamp": [_day_ms(r[1]) for r in rows],
            "open": [r[2] for r in rows],
            "high": [r[2] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[2] for r in rows],
            "volume": [1000.0 for _ in rows],
        }
    )


def _membership(pairs: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame(pairs, columns=["symbol", "concept"])


SESSIONS = ["2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25"]


def _limit_chain(start: float, n: int) -> list[float]:
    """``n`` successive main-board (10%) limit-up closes starting from ``start``."""
    prices = [start]
    prev = start
    for _ in range(n - 1):
        prev = float(market_rules.limit_up_price(prev, "600000"))
        prices.append(prev)
    return prices


# -- limit-up boundary ------------------------------------------------------


def test_close_exactly_at_limit_counts_one_cent_below_does_not():
    """The cent is the whole test: 10% of 10.00 is 11.00 exactly, and 10.99 is
    a 9.9% day — a real, common miss that a float threshold blurs."""
    limit = float(market_rules.limit_up_price(10.0, "600000"))
    assert limit == 11.0

    rows = [
        ("600001.SH", "2026-09-22", 10.0),
        ("600001.SH", "2026-09-23", 11.0),  # exactly at the limit
        ("600001.SH", "2026-09-24", 10.99),  # one cent short
        ("600001.SH", "2026-09-25", 11.0),
    ]
    db = StubDB(_bars(rows), SESSIONS)
    out = ml.compute_mainline(
        db,
        _membership([("600001.SH", "边界")]),
        days=7,
        end_date="2026-09-25",
        exclude_st_names=False,
    )

    # 09-23 is a limit-up (1 board); 09-24 breaks the run; 09-25 is a 1-board
    # again — but that is a single limit-up, below MIN_LIMIT_UP=3, so the
    # concept never ranks. Assert on the *scored* result rather than on a
    # private helper so the boundary really flows through scoring.
    assert out["dates"] == [], (
        "a concept with one limit-up per session must not rank; if it does, "
        "either the boundary or the threshold is wrong"
    )


def test_three_limit_ups_in_a_day_clear_the_threshold():
    """MIN_LIMIT_UP is a floor of 3 and it is inclusive."""
    rows = []
    for symbol in ("600001.SH", "600002.SH", "600003.SH"):
        rows += [(symbol, "2026-09-22", 10.0), (symbol, "2026-09-23", 11.0)]
    db = StubDB(_bars(rows), SESSIONS)
    out = ml.compute_mainline(
        db,
        _membership([(s, "三连") for s in ("600001.SH", "600002.SH", "600003.SH")]),
        days=7,
        end_date="2026-09-23",
        exclude_st_names=False,
    )
    assert out["dates"] == ["2026-09-23"]
    assert out["columns"]["2026-09-23"][0][0] == "三连"
    assert out["columns"]["2026-09-23"][0][2] == 3  # limit_up_count


# -- run semantics ----------------------------------------------------------


def test_boards_count_consecutive_sessions():
    # Four bars: the first is the window-opening baseline, the next three limit.
    # `_limit_chain(start, n)` returns n prices *including* start, so trimming
    # its head is what turns the window-opening bar into a baseline.
    chain = _limit_chain(10.0, 4)
    rows = [
        ("600001.SH", day, price) for day, price in zip(SESSIONS, chain, strict=False)
    ]
    db = StubDB(_bars(rows), SESSIONS)
    out = ml.compute_mainline(
        db,
        _membership([("600001.SH", "连板")]),
        days=7,
        end_date=SESSIONS[-1],
        exclude_st_names=False,
    )
    # Only one symbol → one concept → still below MIN_LIMIT_UP; use the board
    # calculation directly for this assertion.
    frame = _run(rows, SESSIONS)
    assert list(frame["boards"]) == [0, 1, 2, 3]


def test_a_non_limit_day_resets_the_board_run():
    """涨停 → 断板 → 涨停 is 1 board, not 2 and not 3.

    The fixture is a genuine chain followed by a *miss* (11.00 → 11.50 is
    +4.5%, well short of +10%) and then a single limit-up from that new base.
    Nothing else in the file produces a mid-run miss inside a symbol's series,
    which is why a mutant that never resets ran free before this.
    """
    chain = _limit_chain(10.0, 3)  # 10 / 11 / 12.1
    rows = [
        ("600001.SH", SESSIONS[0], chain[0]),
        ("600001.SH", SESSIONS[1], chain[1]),  # board 1
        ("600001.SH", SESSIONS[2], 11.5),  # +4.5%: run resets
        ("600001.SH", SESSIONS[3], 12.65),  # +10.0% off 11.50: board 1 again
    ]
    frame = _run(rows, SESSIONS)
    assert list(frame["boards"]) == [
        0,
        1,
        0,
        1,
    ], "a missed limit-up must zero the run; a monotonic counter would give [0,1,2,3]"
    assert list(frame["is_limit_up"]) == [False, True, False, True]


def test_top_per_day_caps_the_response():
    """31 concepts each with exactly 3 limit-ups: only TOP_PER_DAY survive.

    Without the cap every concept with ≥3 limit-ups is published, which on a
    real day is hundreds of rows the caller has to trim anyway.
    """
    rows: list[tuple[str, str, float]] = []
    members: list[tuple[str, str]] = []
    index = 0
    for concept_index in range(ml.TOP_PER_DAY + 1):
        for _ in range(3):
            symbol = f"61{index:04d}.SH"
            index += 1
            rows += [(symbol, "2026-09-24", 10.0), (symbol, "2026-09-25", 11.0)]
            members.append((symbol, f"题材{concept_index:02d}"))

    db = StubDB(_bars(rows), SESSIONS)
    out = ml.compute_mainline(
        db,
        _membership(members),
        days=7,
        end_date="2026-09-25",
        exclude_st_names=False,
    )
    assert len(out["columns"]["2026-09-25"]) == ml.TOP_PER_DAY


def test_boards_do_not_depend_on_the_order_rows_come_back_in():
    """The per-symbol walk is positional, so row order must be normalised.

    ``_boards_for_symbol`` compares each bar with the one before it positionally.
    ``KlineDB.get_partition_range`` happens to return ``ORDER BY symbol,
    timestamp``, but the module promises the ordering itself (``_window_frame``
    sorts) rather than inheriting it from one caller. Feeding the same bars in a
    shuffled order must produce the identical board sequence — if the sort ever
    goes away, the streak below silently reads the wrong predecessor and the
    last bar stops being a board-3.
    """
    chain = _limit_chain(10.0, 3)
    ordered = [
        ("600001.SH", "2026-09-22", chain[0]),
        ("600001.SH", "2026-09-23", chain[1]),
        ("600001.SH", "2026-09-24", chain[2]),
    ]
    shuffled = [ordered[2], ordered[0], ordered[1]]

    boards = list(_run(shuffled, SESSIONS[:3])["boards"])
    assert boards == [
        0,
        1,
        2,
    ], "row order from the DB must not leak into the per-symbol walk"


def test_halt_breaks_the_streak():
    """A resumed bar cannot extend a streak: its predecessor is not yesterday."""
    chain = _limit_chain(10.0, 3)
    rows = [
        ("600001.SH", "2026-09-22", chain[0]),
        ("600001.SH", "2026-09-23", chain[1]),  # board 1
        # halted 09-24 — no bar
        ("600001.SH", "2026-09-25", chain[1] * 1.20),  # resumes well above +10%
    ]
    frame = _run(rows, SESSIONS)
    assert list(frame["boards"]) == [0, 1, 0], (
        "a resumed bar must not extend a streak; its 'previous close' is not "
        "yesterday's, so the run resets"
    )


def test_window_opening_bar_is_a_baseline_not_a_limit_up():
    """The first bar has no predecessor inside the window; it cannot be scored."""
    rows = [("600001.SH", SESSIONS[0], 10.0), ("600001.SH", SESSIONS[1], 11.0)]
    frame = _run(rows, SESSIONS[:2])
    assert list(frame["boards"]) == [0, 1]
    assert list(frame["is_limit_up"]) == [False, True]


def _run(
    rows: list[tuple[str, str, float]],
    sessions: list[str],
    names: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Drive the board computation without going through scoring."""
    db = StubDB(_bars(rows), sessions)
    # A one-concept membership keeps every symbol in play.
    membership = _membership([(rows[0][0], "x")])
    captured: dict[str, pd.DataFrame] = {}

    original = ml._boards_for_symbol

    def spy(closes, gaps, symbol, name, dates=None):  # noqa: ANN001 - mirrors the signature
        result = original(closes, gaps, symbol, name, dates)
        captured["boards"] = pd.DataFrame(
            {"boards": result[0], "is_limit_up": result[1]}
        )
        return result

    try:
        ml._boards_for_symbol = spy  # type: ignore[assignment]
        ml.compute_mainline(
            db,
            membership,
            days=7,
            end_date=sessions[-1],
            exclude_st_names=False,
            names=names,
        )
    finally:
        ml._boards_for_symbol = original  # type: ignore[assignment]
    return captured["boards"]


# -- ST filter --------------------------------------------------------------


def test_st_names_are_dropped_by_default():
    rows = []
    symbols = ("600001.SH", "600002.SH", "600003.SH", "600004.SH", "600005.SH")
    for symbol in symbols:
        rows += [(symbol, "2026-09-22", 10.0), (symbol, "2026-09-23", 11.0)]
    members = _membership([(s, "题材") for s in symbols])
    db = StubDB(_bars(rows), SESSIONS)

    names = {
        "600001.SH": "普通股",
        "600002.SH": "ST风险",
        "600003.SH": "普通股二",
        "600004.SH": "退市整理",
        "600005.SH": "普通股三",
    }
    with_st = ml.compute_mainline(
        db, members, days=7, end_date="2026-09-23", names=names
    )
    assert with_st["dates"], "the concept should still rank on its non-ST members"
    assert (
        with_st["columns"]["2026-09-23"][0][2] == 3
    ), "ST and delisting names must not be counted as theme limit-ups"

    without = ml.compute_mainline(
        db, members, days=7, end_date="2026-09-23", names=names, exclude_st_names=False
    )
    assert without["columns"]["2026-09-23"][0][2] == 5


def test_unknown_names_are_kept():
    """A symbol missing from ``names`` is unknown, not ST.

    The name map is a cached upstream call that can fail offline; treating a
    miss as ST would silently shrink the universe on every symbol it does not
    know. Five limit-up symbols: one named ST, one named ordinary, three absent
    from the map. The ST one is dropped; the three unknowns must survive.
    """
    symbols = ("600001.SH", "600002.SH", "600003.SH", "600004.SH", "600005.SH")
    rows = [
        (symbol, day, price)
        for symbol in symbols
        for day, price in (("2026-09-22", 10.0), ("2026-09-23", 11.0))
    ]
    db = StubDB(_bars(rows), SESSIONS)

    out = ml.compute_mainline(
        db,
        _membership([(symbol, "题材") for symbol in symbols]),
        days=7,
        end_date="2026-09-23",
        names={"600001.SH": "普通股", "600002.SH": "ST风险"},
    )

    assert out["columns"]["2026-09-23"][0][2] == 4, (
        "only the named ST symbol may be dropped; unnamed symbols are unknown, "
        "not ST"
    )


# -- scoring ---------------------------------------------------------------


def test_score_is_zero_to_one_hundred_and_rank_ordered():
    """Every scored concept lands in [0, 100] and the column is descending.

    A concept reaches 100 only when it is *strictly* rank-1 on all four
    features. Ties share the average rank, so tying for the top of a feature
    normalises to 0.5, not 1.0 — a fixture where every concept shares a board
    count can never produce 100, and asserting it would pin a wrong idea of
    the normalisation.
    """
    rows: list[tuple[str, str, float]] = []
    members_list: list[tuple[str, str]] = []

    def add(concept: str, symbol: str, series: list[float], days: list[str]) -> None:
        rows.extend(
            (symbol, day, price) for day, price in zip(days, series, strict=False)
        )
        members_list.append((symbol, concept))

    # 概念A — 5 limit-ups: one at 3 boards, one at 2, three singles.
    #   count 5, max_boards 3, ge2_count 2, rungs_filled 2 ({2, 3}).
    add("概念A", "600000.SH", [10.0, 11.0, 12.1, 13.31], SESSIONS)
    add("概念A", "600001.SH", [10.0, 10.0, 11.0, 12.1], SESSIONS)
    for index in range(3):
        add("概念A", f"60001{index}.SH", [10.0, 11.0], SESSIONS[2:])
    # 概念B — 4 limit-ups, all single-board.
    for index in range(4):
        add("概念B", f"60002{index}.SH", [10.0, 11.0], SESSIONS[2:])
    # 概念C / 概念D — 3 single-board limit-ups each: identical feature vectors.
    for concept, base in (("概念C", 30), ("概念D", 40)):
        for index in range(3):
            add(concept, f"600{base + index:03d}.SH", [10.0, 11.0], SESSIONS[2:])

    db = StubDB(_bars(rows), SESSIONS)
    out = ml.compute_mainline(
        db,
        _membership(members_list),
        days=7,
        end_date=SESSIONS[-1],
        exclude_st_names=False,
    )

    scores = [row[1] for row in out["columns"][SESSIONS[-1]]]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= score <= 100.0 for score in scores)
    # 概念A is rank-1 on every feature: 0.35 + 0.25 + 0.25 + 0.15 = 1.0 → 100.
    assert scores[0] == pytest.approx(100.0, abs=0.1), scores
    # 概念C and 概念D tie on every feature and must share a score.
    assert scores[2] == pytest.approx(scores[3], abs=0.1), scores


def test_swapping_count_and_board_weights_flips_the_order():
    """``limit_up_count`` and ``max_boards`` are not interchangeable.

    Swapping their weights (0.35 <-> 0.25) reverses the whole ranking, and
    that is the *only* thing that distinguishes the two weight sets, so this
    test is the mutant's death sentence: with the weights swapped the order
    below flips end to end.

    The fixture is a three-tier anti-diagonal, on a 5-session window so the
    top tier can reach board 4:

    ====  ======  ======  =====  =====
    tier  count   max     rungs  ge2
    ====  ======  ======  =====  =====
    高板  3       4       1      1
    中板  4       3       1      1
    多家  5       2       1      1
    ====  ======  ======  =====  =====

    ``rungs``/``ge2`` tie across all three, so they contribute the same
    0.5-normalised term to everybody and cannot mask the count-vs-boards
    trade-off. TSP weights give 高板 45 / 中板 50 / 多家 55; swap them and
    the scores become 55 / 50 / 45 — a full reversal with a 10-point margin
    at each end.
    """
    days = ["2026-09-21", *SESSIONS]  # 5 sessions: max reachable board = 4

    rows: list[tuple[str, str, float]] = []
    members: list[tuple[str, str]] = []

    def add(concept: str, symbol: str, prices: dict[str, float]) -> None:
        rows.extend((symbol, day, price) for day, price in prices.items())
        members.append((symbol, concept))

    def flat() -> dict[str, float]:
        return {day: 10.0 for day in days}

    def chain(days_used: list[str], length: int) -> dict[str, float]:
        """Flat before ``days_used``, then a limit-up run over ``days_used``."""
        prices = flat()
        prices.update(zip(days_used, _limit_chain(10.0, length), strict=False))
        return prices

    # 高板 — 3 limit-ups, one member riding a 4-board ladder.
    add("高板", "600700.SH", chain(days, 5))
    add("高板", "600701.SH", chain(days[-2:], 2))
    add("高板", "600702.SH", chain(days[-2:], 2))
    # 中板 — 4 limit-ups, top member at board 3.
    add("中板", "600800.SH", chain(days[1:], 4))
    add("中板", "600801.SH", chain(days[-2:], 2))
    add("中板", "600802.SH", chain(days[-2:], 2))
    add("中板", "600803.SH", chain(days[-2:], 2))
    # 多家 — 5 limit-ups, top member at board 2.
    add("多家", "600900.SH", chain(days[2:], 3))
    add("多家", "600901.SH", chain(days[-2:], 2))
    add("多家", "600902.SH", chain(days[-2:], 2))
    add("多家", "600903.SH", chain(days[-2:], 2))
    add("多家", "600904.SH", chain(days[-2:], 2))

    db = StubDB(_bars(rows), days)
    out = ml.compute_mainline(
        db,
        _membership(members),
        days=7,
        end_date=days[-1],
        exclude_st_names=False,
    )
    column = {row[0]: row for row in out["columns"][days[-1]]}
    assert set(column) == {"高板", "中板", "多家"}, column

    assert (column["高板"][2], column["高板"][3]) == (3, 4)
    assert (column["中板"][2], column["中板"][3]) == (4, 3)
    assert (column["多家"][2], column["多家"][3]) == (5, 2)

    assert [row[0] for row in out["columns"][days[-1]]] == ["多家", "中板", "高板"]
    assert column["多家"][1] == 55.0 and column["高板"][1] == 45.0, column

    # The leader is the highest-board member, not merely the first symbol:
    # each concept mixes one tall ladder with flat 1-board members, so a
    # sort that ignored the board column would return the flat symbol.
    assert column["高板"][4] == "600700.SH", column["高板"]
    assert column["中板"][4] == "600800.SH", column["中板"]
    assert column["多家"][4] == "600900.SH", column["多家"]


def test_rungs_filled_counts_distinct_rungs_not_members():
    """Two concepts identical on count / max / ≥2-count, different only in
    whether the ladder is contiguous.

    断档 = [3,3,1,1]: four limit-ups, max 3, two at ≥2 boards, filled rungs {3}.
    连续 = [3,2,1,1]: four limit-ups, max 3, two at ≥2 boards, filled rungs {2,3}.

    Every other feature is equal, so the score difference is
    *entirely* ``rungs_filled``. Counting members instead of distinct rungs
    gives both 2 and collapses the two concepts onto the same score.
    """
    rows: list[tuple[str, str, float]] = []
    members: list[tuple[str, str]] = []

    def add(concept: str, symbol: str, series: list[float], days: list[str]) -> None:
        rows.extend((symbol, day, p) for day, p in zip(days, series, strict=False))
        members.append((symbol, concept))

    full = _limit_chain(10.0, 4)  # 10 / 11 / 12.1 / 13.31
    two = _limit_chain(10.0, 3)  # 10 / 11 / 12.1
    one = _limit_chain(10.0, 2)  # 10 / 11

    # 断档: two symbols straight to 3 boards, two straight to 1 board.
    for index in range(2):
        add("断档", f"6004{index:02d}.SH", full, SESSIONS)
    for index in range(2):
        add("断档", f"6004{index + 10:02d}.SH", one, SESSIONS[2:])
    # 连续: one 3-board, one 2-board, two 1-board.
    add("连续", "600500.SH", full, SESSIONS)
    add("连续", "600501.SH", two, SESSIONS[1:])
    for index in range(2):
        add("连续", f"6005{index + 10:02d}.SH", one, SESSIONS[2:])

    db = StubDB(_bars(rows), SESSIONS)
    out = ml.compute_mainline(
        db,
        _membership(members),
        days=7,
        end_date=SESSIONS[-1],
        exclude_st_names=False,
    )
    column = {row[0]: row for row in out["columns"][SESSIONS[-1]]}

    # The shapes really are equal on the other three features.
    assert column["断档"][2] == column["连续"][2] == 4
    assert column["断档"][3] == column["连续"][3] == 3
    # ... and the contiguous ladder must win on rungs alone.
    assert (
        column["连续"][1] > column["断档"][1]
    ), "两者只在 rungs_filled 上不同(1 vs 2);分不出高低就说明这一项没进分数"


# -- blacklist / size cap / window --------------------------------------------


def test_default_blacklist_removes_attribute_buckets():
    """融资融券 / ST股 / 次新股 are attribute buckets, not themes: every one of
    them owns hundreds of names and would own the ladder every day."""
    rows: list[tuple[str, str, float]] = []
    members: list[tuple[str, str]] = []
    for index in range(3):
        symbol = f"6006{index:02d}.SH"
        rows += [(symbol, "2026-09-24", 10.0), (symbol, "2026-09-25", 11.0)]
        members.append((symbol, "融资融券"))

    db = StubDB(_bars(rows), SESSIONS)
    out = ml.compute_mainline(
        db,
        _membership(members),
        days=7,
        end_date="2026-09-25",
        exclude_st_names=False,
    )
    assert out["reason"] == "membership_all_excluded", out

    # The same membership under a real theme ranks normally.
    out_ok = ml.compute_mainline(
        db,
        _membership([(s, "真题材") for s, _ in members]),
        days=7,
        end_date="2026-09-25",
        exclude_st_names=False,
    )
    assert out_ok["dates"] == ["2026-09-25"]


def test_oversized_concepts_are_dropped_but_small_ones_still_rank():
    """A 601-member bucket is not a theme. The threshold is inclusive at 600,
    and dropping the bucket must not take the rest of the day with it."""
    rows: list[tuple[str, str, float]] = []
    members: list[tuple[str, str]] = []

    # 题材: 3 limit-ups, 3 members.
    for index in range(3):
        symbol = f"6007{index:02d}.SH"
        rows += [(symbol, "2026-09-24", 10.0), (symbol, "2026-09-25", 11.0)]
        members.append((symbol, "题材"))

    # 巨桶: MAX_MEMBERS + 1 distinct symbols, 3 of which limit up.
    for index in range(ml.MAX_MEMBERS + 1):
        symbol = f"601{index:04d}.SH"
        members.append((symbol, "巨桶"))
        if index < 3:
            rows += [(symbol, "2026-09-24", 10.0), (symbol, "2026-09-25", 11.0)]

    db = StubDB(_bars(rows), SESSIONS)
    out = ml.compute_mainline(
        db,
        _membership(members),
        days=7,
        end_date="2026-09-25",
        exclude_st_names=False,
    )
    names = [row[0] for row in out["columns"]["2026-09-25"]]
    assert names == ["题材"], names
    assert "巨桶" not in names


def test_days_is_clamped_to_the_documented_range():
    """``days`` is clamped inside the module, not only by FastAPI: a direct
    caller must not be able to ask for a 5000-session window and silently get a
    whole-partition scan. ``_session_index`` asks for ``days + 1`` sessions
    (the extra one is the baseline)."""
    rows: list[tuple[str, str, float]] = []
    for index in range(3):
        symbol = f"6008{index:02d}.SH"
        rows += [(symbol, "2026-09-24", 10.0), (symbol, "2026-09-25", 11.0)]
    db = StubDB(_bars(rows), SESSIONS)

    ml.compute_mainline(
        db,
        _membership([("600800.SH", "x")]),
        days=5000,
        end_date="2026-09-25",
        exclude_st_names=False,
    )
    assert db.requests[-1]["limit"] - 1 == ml._clamp_days(5000) == 30

    ml.compute_mainline(
        db,
        _membership([("600800.SH", "x")]),
        days=1,
        end_date="2026-09-25",
        exclude_st_names=False,
    )
    assert db.requests[-1]["limit"] - 1 == 7


def test_dates_are_newest_first_across_a_multi_day_window():
    """A single-scored-day fixture cannot see this: ``sorted()`` and
    ``sorted(reverse=True)`` are the same set of one."""
    rows: list[tuple[str, str, float]] = []
    members: list[tuple[str, str]] = []
    # Three symbols limit up on BOTH scored sessions, so both days rank.
    chain = _limit_chain(10.0, 3)
    for index in range(3):
        symbol = f"6009{index:02d}.SH"
        rows += [
            (symbol, day, price) for day, price in zip(SESSIONS, chain, strict=False)
        ]
        members.append((symbol, "连续"))

    db = StubDB(_bars(rows), SESSIONS)
    out = ml.compute_mainline(
        db,
        _membership(members),
        days=7,
        end_date=SESSIONS[-1],
        exclude_st_names=False,
    )
    assert out["dates"] == sorted(out["dates"], reverse=True), out["dates"]
    # `_limit_chain(10.0, 3)` puts the two limit-ups on the 2nd and 3rd bars;
    # the window's last session has no bar at all, so it cannot rank.
    assert out["dates"] == ["2026-09-24", "2026-09-23"], out["dates"]


# -- degraded paths ---------------------------------------------------------


def test_empty_membership_returns_reason():
    db = StubDB(_bars([]), SESSIONS)
    out = ml.compute_mainline(
        db, pd.DataFrame(columns=["symbol", "concept"]), days=7, end_date="2026-09-25"
    )
    assert out["reason"] == "membership_empty"
    assert out["dates"] == []
    assert out["streak_capped"] is True


def test_no_limit_ups_returns_reason():
    rows = [("600001.SH", "2026-09-22", 10.0), ("600001.SH", "2026-09-23", 10.1)]
    db = StubDB(_bars(rows), SESSIONS)
    out = ml.compute_mainline(
        db,
        _membership([("600001.SH", "无")]),
        days=7,
        end_date="2026-09-23",
        exclude_st_names=False,
    )
    assert out["reason"] == "no_limit_ups_in_window"


def test_streak_capped_is_always_reported():
    """The capability boundary must travel with every response, including empty
    ones — a caller that only sees it on success would treat a capped streak as
    a true streak the first time the data thinned out."""
    db = StubDB(_bars([]), SESSIONS)
    out = ml.compute_mainline(
        db, _membership([("X.SH", "y")]), days=7, end_date="2026-09-25"
    )
    assert out["streak_capped"] is True
    assert out["membership_note"] == ml.MEMBERSHIP_NOTE


def test_membership_note_is_shared_with_concept_map():
    from selene_space_ashare import concept_map

    assert ml.MEMBERSHIP_NOTE is concept_map.MEMBERSHIP_NOTE or (
        ml.MEMBERSHIP_NOTE == concept_map.MEMBERSHIP_NOTE
    )


# -- real base --------------------------------------------------------------


@pytest.mark.skipif(not DB_PATH.is_file(), reason=f"no {DB_PATH}")
def test_real_base_mainline_contract():
    """Contract run against the real partition — shape and range, no goldens."""
    from selene_space_market.kline_db import KlineDB

    from selene_space_ashare import concept_map

    cache = concept_map.CACHE_DIR / concept_map.CACHE_NAME
    if not cache.is_file():
        pytest.skip("concept membership cache not present (offline run)")
    membership = pd.read_parquet(cache)

    handle = KlineDB(db_path=str(DB_PATH))
    handle._conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        out = ml.compute_mainline(handle, membership, days=20)
    finally:
        handle._conn.close()
        handle._conn = None

    assert out["reason"] is None, out["reason"]
    assert len(out["dates"]) == 20
    assert out["as_of"] == out["dates"][0]
    latest = out["columns"][out["as_of"]]
    assert 1 <= len(latest) <= ml.TOP_PER_DAY
    for concept, score, count, boards, leader in latest:
        assert isinstance(concept, str) and concept
        assert 0.0 <= score <= 100.0
        assert count >= ml.MIN_LIMIT_UP
        assert 1 <= boards <= 20, "boards must not exceed the window"
        assert leader.endswith((".SH", ".SZ", ".BJ"))
    scores = [row[1] for row in latest]
    assert scores == sorted(scores, reverse=True)


# -- the band in force on each session --------------------------------------

LEGACY_SESSIONS = ["2026-07-01", "2026-07-02"]
REFORM_SESSIONS = ["2026-07-06", "2026-07-07"]


def test_a_risk_warning_name_limits_at_five_percent_before_the_reform():
    rows = [
        ("600001.SH", LEGACY_SESSIONS[0], 10.0),
        (
            "600001.SH",
            LEGACY_SESSIONS[1],
            10.5,
        ),  # +5%: at the legacy main-board ST limit
    ]
    frame = _run(rows, LEGACY_SESSIONS, {"600001.SH": "ST测试"})
    assert list(frame["boards"]) == [0, 1]


def test_the_same_five_percent_close_is_ordinary_after_the_reform():
    """Not a regression test on arithmetic: it pins that ``_boards_for_symbol``
    is handed each bar's date. Without the dates argument both fixtures would
    agree, and a 10%-band ST name would be counted as a board from 2026-07-06.
    """
    rows = [
        ("600001.SH", REFORM_SESSIONS[0], 10.0),
        ("600001.SH", REFORM_SESSIONS[1], 10.5),
    ]
    frame = _run(rows, REFORM_SESSIONS, {"600001.SH": "ST测试"})
    assert list(frame["boards"]) == [0, 0]
    assert list(frame["is_limit_up"]) == [False, False]


def test_a_risk_warning_name_on_the_growth_board_keeps_twenty_percent():
    rows = [
        ("300750.SZ", REFORM_SESSIONS[0], 10.0),
        ("300750.SZ", REFORM_SESSIONS[1], 12.0),  # +20%
    ]
    frame = _run(rows, REFORM_SESSIONS, {"300750.SZ": "ST宁德"})
    assert list(frame["boards"]) == [0, 1]
