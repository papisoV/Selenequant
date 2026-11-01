"""Concept RPS — arithmetic and drift contracts on synthetic bars, shape on the real base.

The synthetic half pins the things a real-base smoke test cannot see: an
equal-weight mean that silently becomes price-weighted, a symbol halted on
either window edge that gets a return anyway, a rank that comes out backwards,
a stability band off by one, or an ``is_strong`` flag that forgets the
membership half of its rule. Real data returns *a* ranking either way — just
the wrong one.

The real-base half is a shape contract: the module must run against
``("astock","daily_all")`` without network access and produce the documented
keys and ranges. No golden values; the base gains a session every trading day.

运行:
    pytest packages/ashare/tests/test_concept_rps.py -v
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time
from pathlib import Path

import pandas as pd
import pytest

from selene_space_ashare import concept_rps as rps

REPO_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = REPO_ROOT / "data" / "klines.db"


def _day_ms(day: str) -> int:
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Shanghai")
    y, m, d = (int(part) for part in day.split("-"))
    return int(datetime.combine(date(y, m, d), time.min, tzinfo=tz).timestamp() * 1000)


# 25 sessions: enough for a legal 20-session lookback (MIN_LOOKBACK) plus one
# baseline session, so the clamp floor never produces "insufficient_sessions"
# unless a test asks for it.
SESSIONS = [f"2026-09-{d:02d}" for d in range(1, 26)]
LAST = SESSIONS[-1]
BASE_20 = SESSIONS[-21]


def _end_of(day: str) -> int:
    """The inclusive end-of-day bound ``build_concept_rps(end_ms=...)`` expects."""
    return _day_ms(day) + 86_399_999


class StubDB:
    """The two KlineDB read methods the module needs, from literal frames.

    ``get_session_stamps`` honours ``limit`` (returning the tail) because the
    module's window resolution depends on it; a stub that ignored the argument
    would silently test the wrong window.
    """

    def __init__(self, bars: pd.DataFrame, sessions: list[str]):
        self._bars = bars
        self._sessions = sorted(sessions)
        self.session_requests: list[int] = []

    def get_session_stamps(self, *_args, **kwargs) -> list[int]:
        """The tail of the calendar at or before ``end_ms``.

        ``end_ms`` must be honoured: a stub returning the calendar tail
        regardless would make an explicit ``end_ms`` look honoured while the
        module silently scored the newest session (this exact stub bug was
        caught by ``test_explicit_end_ms_is_honoured``).
        """
        limit = kwargs.get("limit")
        self.session_requests.append(limit)
        end_ms = kwargs.get("end_ms")
        stamps = [_day_ms(day) for day in self._sessions]
        if end_ms is not None:
            stamps = [stamp for stamp in stamps if stamp <= int(end_ms)]
        return stamps[-limit:] if limit else stamps

    def get_partition_range(self, *_args, **_kwargs) -> pd.DataFrame:
        return self._bars.copy()


def _bars(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    """``(symbol, date, close)`` triples → the frame ``get_partition_range`` returns."""
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


def _flat_series(
    symbol: str, *, base: str, last: str, base_price: float, last_price: float
) -> list[tuple[str, str, float]]:
    """Every session a flat close, except the two edge prices.

    Filler rows matter here: a symbol present only on the two edges would be
    indistinguishable from one that traded every session, and the point of
    several tests is that the *edges* — not the row count — decide.
    """
    return [
        (symbol, day, last_price if day == last else base_price) for day in SESSIONS
    ]


def _flat_concept(symbols: list[str], *, ret: float) -> list[tuple[str, str, float]]:
    """Rows for ``symbols`` all moving ``ret`` (a fraction) across the 20-session window."""
    rows: list[tuple[str, str, float]] = []
    for symbol in symbols:
        rows += _flat_series(
            symbol, base=BASE_20, last=LAST, base_price=10.0, last_price=10.0 * (1 + ret)
        )
    return rows


# -- arithmetic -------------------------------------------------------------


def test_equal_weight_mean_is_not_price_weighted():
    """Members contribute by percentage move, not by price.

    Both rise 10% from wildly different price levels: a price-weighted mean
    would land far from 10%. Two members, threshold lowered deliberately so
    the arithmetic — not the size gate — is what is under test.
    """
    rows = _flat_series("A.SZ", base=BASE_20, last=LAST, base_price=10.0, last_price=11.0)
    rows += _flat_series("B.SZ", base=BASE_20, last=LAST, base_price=1000.0, last_price=1100.0)
    db = StubDB(_bars(rows), SESSIONS)

    out = rps.build_concept_rps(
        db,
        _membership([("A.SZ", "主题X"), ("B.SZ", "主题X")]),
        end_ms=_end_of(LAST),
        lookback=20,
        min_members=2,
    )

    assert out["reason"] is None
    assert out["as_of"] == LAST
    concept = next(c for c in out["concepts"] if c["concept"] == "主题X")
    assert concept["ret"] == pytest.approx(10.0, abs=0.01)


def test_ret_is_a_percentage_number_not_a_fraction():
    rows = _flat_series("A.SZ", base=BASE_20, last=LAST, base_price=10.0, last_price=10.522)
    rows += _flat_series("B.SZ", base=BASE_20, last=LAST, base_price=10.0, last_price=10.0)
    db = StubDB(_bars(rows), SESSIONS)

    out = rps.build_concept_rps(
        db,
        _membership([("A.SZ", "主题X"), ("B.SZ", "主题X")]),
        end_ms=_end_of(LAST),
        lookback=20,
        min_members=2,
    )
    concept = next(c for c in out["concepts"] if c["concept"] == "主题X")
    # 5.22% for member A averaged with a flat 0% member → 2.61%.
    assert concept["ret"] == pytest.approx(2.61, abs=0.01)


def test_symbol_missing_a_bar_on_the_baseline_edge_gets_no_return():
    """The baseline edge is what "20 sessions" means.

    B has no bar on BASE_20 (it was suspended or listed later) but does have
    bars on every later session. A row-based shift would happily use one of
    those and report a shorter-span return; the session-edge implementation
    must instead exclude B entirely — which drops the concept below
    ``min_members=3``.
    """
    rows = _flat_concept(["A.SZ", "C.SZ"], ret=0.10)
    rows += [
        ("B.SZ", day, 100.0)
        for day in SESSIONS
        if day != BASE_20 and day >= BASE_20
    ]
    db = StubDB(_bars(rows), SESSIONS)

    out = rps.build_concept_rps(
        db,
        _membership([("A.SZ", "缺边"), ("B.SZ", "缺边"), ("C.SZ", "缺边")]),
        end_ms=_end_of(LAST),
        lookback=20,
        min_members=3,
    )
    # Only A and C survive the edge requirement → concept falls below the gate.
    assert out["concepts"] == []
    assert out["reason"] == "all_concepts_below_min_members"


def test_symbol_missing_a_bar_on_the_asof_edge_gets_no_return():
    """Symmetric to the baseline edge: no last bar → no return, no stale borrow."""
    rows = _flat_concept(["A.SZ", "C.SZ"], ret=0.10)
    rows += [
        ("B.SZ", day, 100.0)
        for day in SESSIONS
        if day != LAST and day >= BASE_20
    ]
    db = StubDB(_bars(rows), SESSIONS)

    out = rps.build_concept_rps(
        db,
        _membership([("A.SZ", "缺边"), ("B.SZ", "缺边"), ("C.SZ", "缺边")]),
        end_ms=_end_of(LAST),
        lookback=20,
        min_members=3,
    )
    assert out["concepts"] == []
    assert out["reason"] == "all_concepts_below_min_members"


def test_symbol_missing_a_bar_on_the_asof_edge_is_not_counted_as_a_member():
    """A missing as-of bar must remove the member, not just its return.

    Four members, one of which has no bar on the last session. The concept
    still clears ``min_members=3``, so it appears — but with ``member_count``
    counting *four* only if the edge filter dropped the suspended name
    outright. Letting a NaN through instead leaves ``count=3`` reporting the
    wrong population: on a 100-member concept where 90 halted that is the
    difference between ``solid`` and ``thin``.
    """
    rows = _flat_concept(["A.SZ", "B.SZ", "C.SZ"], ret=0.10)
    rows += [
        ("D.SZ", day, 100.0)
        for day in SESSIONS
        if day != LAST and day >= BASE_20
    ]
    db = StubDB(_bars(rows), SESSIONS)

    out = rps.build_concept_rps(
        db,
        _membership([(symbol, "缺边") for symbol in ("A.SZ", "B.SZ", "C.SZ", "D.SZ")]),
        end_ms=_end_of(LAST),
        lookback=20,
        min_members=3,
    )
    concept = next(c for c in out["concepts"] if c["concept"] == "缺边")
    assert concept["member_count"] == 3
    # D's absence must not drag the mean: A/B/C all return +10%.
    assert concept["ret"] == pytest.approx(10.0, abs=0.01)


def test_row_count_does_not_decide_the_window():
    """Extra rows must not move the baseline.

    This is the ``rps_breakout`` failure mode the module docstring names: a
    row-based ``shift(20)`` counts *rows*, so one duplicated bar inside the
    window shifts the baseline a session forward. B's price rises by 1.0 per
    session, which makes the two baselines numerically distinct:

    * session-edge baseline (session 4): 34/14 − 1 = +142.86%
    * row-based baseline (row −21, session 5): 34/15 − 1 = +126.67%

    With two flat members the concept mean is 47.62% vs 42.22% — the
    assertion rejects the row-based answer rather than merely describing it.
    """
    rows = _flat_concept(["A.SZ", "C.SZ"], ret=0.0)
    rows += [("B.SZ", day, 10.0 + index) for index, day in enumerate(SESSIONS)]
    # A second bar on an interior session; not on either edge, so it must be
    # ignored by the edge extraction and yet still foil a row shift.
    rows.append(("B.SZ", SESSIONS[10], 999.0))
    db = StubDB(_bars(rows), SESSIONS)

    out = rps.build_concept_rps(
        db,
        _membership([("A.SZ", "重复行"), ("B.SZ", "重复行"), ("C.SZ", "重复行")]),
        end_ms=_end_of(LAST),
        lookback=20,
        min_members=3,
    )
    concept = next(c for c in out["concepts"] if c["concept"] == "重复行")
    assert concept["ret"] == pytest.approx(47.62, abs=0.01)


def test_rank_is_percentile_across_eligible_concepts():
    """Highest return → highest RPS; rank is a 0-100 percentile, not a raw return."""
    rows = _flat_concept(["A.SZ", "D.SZ"], ret=0.30)
    rows += _flat_concept(["B.SZ", "E.SZ"], ret=0.10)
    rows += _flat_concept(["C.SZ", "F.SZ"], ret=-0.10)
    db = StubDB(_bars(rows), SESSIONS)
    membership = _membership(
        [("A.SZ", "强"), ("D.SZ", "强"),
         ("B.SZ", "中"), ("E.SZ", "中"),
         ("C.SZ", "弱"), ("F.SZ", "弱")]
    )
    out = rps.build_concept_rps(db, membership, end_ms=_end_of(LAST), lookback=20, min_members=2)

    by_name = {c["concept"]: c for c in out["concepts"]}
    assert by_name["强"]["rps"] > by_name["中"]["rps"] > by_name["弱"]["rps"]
    assert 0.0 < by_name["弱"]["rps"] < 50.0 < by_name["强"]["rps"] <= 100.0


def test_concepts_are_sorted_by_rps_descending():
    rows = _flat_concept(["A.SZ", "D.SZ"], ret=-0.10)
    rows += _flat_concept(["B.SZ", "E.SZ"], ret=0.10)
    rows += _flat_concept(["C.SZ", "F.SZ"], ret=0.30)
    db = StubDB(_bars(rows), SESSIONS)
    membership = _membership(
        [("A.SZ", "弱"), ("D.SZ", "弱"),
         ("B.SZ", "中"), ("E.SZ", "中"),
         ("C.SZ", "强"), ("F.SZ", "强")]
    )
    out = rps.build_concept_rps(db, membership, end_ms=_end_of(LAST), lookback=20, min_members=2)
    assert [c["concept"] for c in out["concepts"]][0] == "强"
    rps_values = [c["rps"] for c in out["concepts"]]
    assert rps_values == sorted(rps_values, reverse=True)


# -- membership hygiene -----------------------------------------------------


def test_duplicate_membership_rows_do_not_change_the_result():
    """The snapshot on disk carries duplicate (symbol, concept) rows.

    ``sum()``-style counts double them (measured: 机器人 with_daily=1250 >
    members=1227). The concept mean and ``member_count`` both read the
    membership frame, so the module must dedupe on entry. Feed the same rows
    three times and require an identical result.
    """
    rows = _flat_concept(["A.SZ", "B.SZ", "C.SZ"], ret=0.10)
    db = StubDB(_bars(rows), SESSIONS)
    clean = [("A.SZ", "主题X"), ("B.SZ", "主题X"), ("C.SZ", "主题X")]
    tripled = clean + clean + clean

    one = rps.build_concept_rps(
        db, _membership(clean), end_ms=_end_of(LAST), lookback=20, min_members=3
    )
    many = rps.build_concept_rps(
        db, _membership(tripled), end_ms=_end_of(LAST), lookback=20, min_members=3
    )

    assert one["concepts"] == many["concepts"]
    assert one["concepts"][0]["member_count"] == 3
    assert one["concepts"][0]["ret"] == pytest.approx(10.0, abs=0.01)


def test_blacklist_removes_attribute_buckets():
    rows = _flat_concept(["A.SZ", "B.SZ", "C.SZ"], ret=0.10)
    db = StubDB(_bars(rows), SESSIONS)
    membership = _membership(
        [("A.SZ", "融资融券"), ("B.SZ", "融资融券"), ("C.SZ", "融资融券"),
         ("A.SZ", "真主题"), ("B.SZ", "真主题"), ("C.SZ", "真主题")]
    )

    out = rps.build_concept_rps(db, membership, end_ms=_end_of(LAST), lookback=20, min_members=3)
    names = {c["concept"] for c in out["concepts"]}
    assert "融资融券" not in names
    assert "真主题" in names

    kept = rps.build_concept_rps(
        db, membership, end_ms=_end_of(LAST), lookback=20, min_members=3, exclude=frozenset()
    )
    assert "融资融券" in {c["concept"] for c in kept["concepts"]}


def test_min_members_threshold_is_the_documented_default():
    """The default gate must be 3, not merely *some* threshold.

    Call without ``min_members=`` and straddle the boundary: 3 members
    survive, 2 do not. Every other test passes the argument explicitly, so
    without this the constant could drift to any value unnoticed.
    """
    assert rps.MIN_RPS_MEMBERS == 3
    rows = _flat_concept(["A.SZ", "B.SZ", "C.SZ"], ret=0.10)
    db = StubDB(_bars(rows), SESSIONS)
    membership = _membership(
        [("A.SZ", "两人"), ("B.SZ", "两人"),
         ("A.SZ", "三人"), ("B.SZ", "三人"), ("C.SZ", "三人")]
    )

    out = rps.build_concept_rps(db, membership, end_ms=_end_of(LAST), lookback=20)
    names = {c["concept"] for c in out["concepts"]}
    assert "三人" in names
    assert "两人" not in names


# -- stability bands and is_strong -----------------------------------------


def _members_for(size: int) -> list[str]:
    return [f"S{index:03d}.SZ" for index in range(size)]


@pytest.mark.parametrize(
    "size,expected",
    [(7, "thin"), (8, "fair"), (20, "fair"), (21, "solid")],
)
def test_stability_band_boundaries(size: int, expected: str) -> None:
    """All four boundaries, not just one: a ``>=`` replaced by ``>`` moves two."""
    symbols = _members_for(size)
    db = StubDB(_bars(_flat_concept(symbols, ret=0.10)), SESSIONS)
    out = rps.build_concept_rps(
        db,
        _membership([(symbol, "主题") for symbol in symbols]),
        end_ms=_end_of(LAST),
        lookback=20,
    )
    assert out["concepts"][0]["member_count"] == size
    assert out["concepts"][0]["stability"] == expected


def _two_concepts(stable_move: float, thin_move: float) -> tuple[StubDB, pd.DataFrame]:
    """One 21-member (solid) and one 4-member (thin) concept, returns controlled.

    Each concept's members all share one move, so its concept return is that
    move exactly — which makes the RPS ordering deterministic.
    """
    solid_symbols = _members_for(21)
    thin_symbols = [f"T{index:03d}.SZ" for index in range(4)]
    rows = _flat_concept(solid_symbols, ret=stable_move)
    rows += _flat_concept(thin_symbols, ret=thin_move)
    membership = _membership(
        [(symbol, "稳概念") for symbol in solid_symbols]
        + [(symbol, "薄概念") for symbol in thin_symbols]
    )
    return StubDB(_bars(rows), SESSIONS), membership


def test_a_high_rps_on_a_thin_concept_is_not_strong():
    """The whole point of the drift note: a 4-name concept's rank is not a signal.

    The thin concept wins on return (RPS 100) but must not raise the highlight
    — ``is_strong`` is ``rps >= 90 AND stability == solid``, and the second
    half is what stops "top of the ranking" from reading as "actionable".
    """
    db, membership = _two_concepts(stable_move=0.05, thin_move=0.50)
    out = rps.build_concept_rps(db, membership, end_ms=_end_of(LAST), lookback=20)

    by_name = {c["concept"]: c for c in out["concepts"]}
    assert by_name["薄概念"]["rps"] == pytest.approx(100.0, abs=0.1)
    assert by_name["薄概念"]["stability"] == "thin"
    assert by_name["薄概念"]["is_strong"] is False
    # And the solid concept, despite the lower return, does not qualify either
    # (RPS 50 < 90) — being solid is necessary, not sufficient.
    assert by_name["稳概念"]["stability"] == "solid"
    assert by_name["稳概念"]["is_strong"] is False


def test_is_strong_threshold_is_inclusive_and_requires_solid():
    """``>=`` not ``>``, and the stability half is not optional."""
    assert rps._is_strong(90.0, "solid") is True
    assert rps._is_strong(89.9, "solid") is False
    assert rps._is_strong(99.0, "thin") is False
    assert rps._is_strong(99.0, "fair") is False


# -- lookback clamp and empty paths ----------------------------------------


def test_lookback_is_clamped_to_the_documented_range():
    assert rps._clamp_lookback(1) == rps.MIN_LOOKBACK
    assert rps._clamp_lookback(10_000) == rps.MAX_LOOKBACK
    assert rps._clamp_lookback(120) == 120

    symbols = _members_for(3)
    db = StubDB(_bars(_flat_concept(symbols, ret=0.10)), SESSIONS)
    rps.build_concept_rps(
        db,
        _membership([(symbol, "主题") for symbol in symbols]),
        end_ms=_end_of(LAST),
        lookback=10_000,
    )
    # The clamp must reach the reader, not just the helper: the request is
    # lookback + 1 sessions.
    assert db.session_requests[-1] == rps.MAX_LOOKBACK + 1


def test_base_shorter_than_lookback_returns_a_reason():
    rows = [("A.SZ", day, 10.0) for day in SESSIONS[:5]]
    db = StubDB(_bars(rows), SESSIONS[:5])
    out = rps.build_concept_rps(
        db,
        _membership([(symbol, "主题") for symbol in _members_for(3)]),
        end_ms=_end_of(SESSIONS[4]),
        lookback=20,
    )
    assert out["concepts"] == []
    assert out["reason"] == "insufficient_sessions(5)"


def test_empty_membership_returns_reason_not_exception():
    db = StubDB(_bars([]), SESSIONS)
    out = rps.build_concept_rps(
        db, pd.DataFrame(columns=["symbol", "concept"]), end_ms=_end_of(LAST), lookback=20
    )
    assert out["reason"] == "membership_empty"
    assert out["concepts"] == []
    assert out["as_of"] is None


def test_membership_missing_columns_is_named():
    db = StubDB(_bars([]), SESSIONS)
    out = rps.build_concept_rps(
        db, pd.DataFrame({"symbol": ["A.SZ"]}), end_ms=_end_of(LAST), lookback=20
    )
    assert out["reason"] == "membership_missing_columns"


def test_empty_daily_base_returns_reason():
    db = StubDB(_bars([]), SESSIONS)
    out = rps.build_concept_rps(
        db,
        _membership([(symbol, "主题") for symbol in _members_for(3)]),
        end_ms=_end_of(LAST),
        lookback=20,
    )
    assert out["reason"] == "daily_base_empty"
    assert out["as_of"] is None


def test_membership_that_matches_no_symbol_is_named():
    rows = _flat_concept(["A.SZ"], ret=0.10)
    db = StubDB(_bars(rows), SESSIONS)
    out = rps.build_concept_rps(
        db,
        _membership([("X.SZ", "孤儿"), ("Y.SZ", "孤儿"), ("Z.SZ", "孤儿")]),
        end_ms=_end_of(LAST),
        lookback=20,
    )
    assert out["reason"] == "membership_daily_symbol_mismatch"


def test_explicit_end_ms_is_honoured():
    """``end_ms`` must pick the window, not merely be accepted.

    An implementation that always resolves "the newest session" would pass
    every other test here (they all end at the newest one) and silently score
    a different date than the caller asked for — which for a panel showing a
    historical date is the worst failure mode available.
    """
    old_end = SESSIONS[-5]  # 21 sessions remain, the minimum legal window
    rows = _flat_concept(["A.SZ", "B.SZ", "C.SZ"], ret=0.10)
    db = StubDB(_bars(rows), SESSIONS)

    out = rps.build_concept_rps(
        db,
        _membership([("A.SZ", "主题"), ("B.SZ", "主题"), ("C.SZ", "主题")]),
        end_ms=_end_of(old_end),
        lookback=20,
    )
    assert out["reason"] is None, out["reason"]
    assert out["as_of"] == old_end


def test_missing_end_ms_resolves_to_the_newest_session():
    rows = _flat_concept(["A.SZ", "B.SZ", "C.SZ"], ret=0.10)
    db = StubDB(_bars(rows), SESSIONS)

    out = rps.build_concept_rps(
        db,
        _membership([("A.SZ", "主题"), ("B.SZ", "主题"), ("C.SZ", "主题")]),
        lookback=20,
    )
    assert out["reason"] is None, out["reason"]
    assert out["as_of"] == LAST


def test_is_strong_agrees_with_the_rounded_rps_the_reader_sees():
    """The highlight must match the printed number, not a hidden float.

    209 concepts place a solid concept at rank 188: raw percentile
    ``188/209*100 = 89.952``, which the response rounds to ``90.0``. Comparing
    the raw value would print 90.0 and still refuse the highlight — the reader
    sees a rule (>= 90) violated by the number on screen. The call site must
    round first, compare second.

    Filler concepts carry one member each (``min_members=1``) so the ranking
    can be 209 deep without building 600 symbols.
    """
    assert round(188 / 209 * 100, 1) == 90.0  # the arithmetic this test rests on

    solid_symbols = _members_for(21)
    rows = _flat_concept(solid_symbols, ret=0.01875)  # 1.875% → ranks 188th of 209
    pairs = [(symbol, "边界") for symbol in solid_symbols]
    # 187 concepts below it and 21 above, so the ascending rank is exactly 188.
    filler_moves = [0.0001 * index for index in range(187)]
    filler_moves += [0.0200 + 0.0001 * index for index in range(21)]
    for index, move in enumerate(filler_moves):
        symbol = f"F{index:03d}.SZ"
        rows += _flat_concept([symbol], ret=move)
        pairs.append((symbol, f"填充{index:03d}"))

    db = StubDB(_bars(rows), SESSIONS)
    out = rps.build_concept_rps(
        db, _membership(pairs), end_ms=_end_of(LAST), lookback=20, min_members=1
    )
    assert out["concept_count"] == 209, out["concept_count"]
    concept = next(c for c in out["concepts"] if c["concept"] == "边界")
    assert concept["stability"] == "solid"
    assert concept["rps"] == 90.0
    assert concept["is_strong"] is True


def test_no_symbol_with_an_asof_bar_is_reported_as_no_returns():
    """A window with no complete pair is "no returns", not "every concept is too small".

    This distinguishes the edge filter from a filter that merely tolerates
    NaNs downstream: pandas' ``mean``/``count`` skip NaN, so a NaN as-of price
    is invisible in the aggregate — until *every* member has one, at which
    point the naive path reports ``all_concepts_below_min_members`` and sends
    whoever debugs it looking at the membership snapshot instead of the base.
    """
    rows = [
        (symbol, day, 100.0)
        for symbol in ("A.SZ", "B.SZ", "C.SZ")
        for day in SESSIONS
        if day != LAST and day >= BASE_20
    ]
    db = StubDB(_bars(rows), SESSIONS)

    out = rps.build_concept_rps(
        db,
        _membership([("A.SZ", "主题"), ("B.SZ", "主题"), ("C.SZ", "主题")]),
        end_ms=_end_of(LAST),
        lookback=20,
    )
    assert out["concepts"] == []
    assert out["reason"] == "no_returns_in_window"


def test_response_always_publishes_both_notes():
    """Every payload carries the snapshot and drift caveats.

    A page that renders a ranking without the caveat invites reading a
    three-name concept's RPS as a market fact.
    """
    db, membership = _two_concepts(stable_move=0.05, thin_move=0.50)
    out = rps.build_concept_rps(db, membership, end_ms=_end_of(LAST), lookback=20)
    assert out["membership_note"]
    assert out["drift_note"]
    assert "thin" in out["drift_note"]


# -- real base --------------------------------------------------------------


@pytest.mark.skipif(not DB_PATH.is_file(), reason=f"no {DB_PATH}")
def test_real_base_produces_a_plausible_ranking():
    """Contract run against the real partition — shape, range, and hygiene.

    No golden values: the base gains a session every trading day, so a pinned
    concept name or RPS would go red for the right data for the wrong reason.
    What must hold: the call completes without network access, every concept
    clears the default gate, all three stability bands are populated (a band
    with zero members means the binning is written backwards), and no
    blacklisted bucket appears.
    """
    from selene_space_ashare import concept_map
    from selene_space_market.kline_db import KlineDB

    cache = concept_map.CACHE_DIR / concept_map.CACHE_NAME
    if not cache.is_file():
        pytest.skip(f"concept membership cache not present ({cache})")
    membership = pd.read_parquet(cache)

    handle = KlineDB(db_path=str(DB_PATH))
    handle._conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        out = rps.build_concept_rps(handle, membership)
        stamps = handle.get_session_stamps(
            "astock", "daily_all", "1d", end_ms=2**62, limit=1
        )
    finally:
        handle._conn.close()
        handle._conn = None

    assert out["reason"] is None, out["reason"]
    # Empirical band, not a derived exact value: 1184 concepts clear the member
    # gate in the deduped snapshot, and a handful lose it here because their
    # members do not all hold a bar on both window edges (measured 1179).
    assert 1_000 <= out["concept_count"] <= 1_250, out["concept_count"]
    assert out["concept_count"] == len(out["concepts"])
    assert out["lookback"] == rps.CONCEPT_RPS_LOOKBACK

    # as_of is the real newest session, not a hard-coded date.
    assert stamps, "the real base reported no sessions"
    expected = pd.Timestamp(stamps[-1], unit="ms", tz="UTC").tz_convert("Asia/Shanghai")
    assert out["as_of"] == expected.strftime("%Y-%m-%d")

    bands = {c["stability"] for c in out["concepts"]}
    assert bands == {"thin", "fair", "solid"}, bands
    assert all(c["member_count"] >= rps.MIN_RPS_MEMBERS for c in out["concepts"])
    assert all(0.0 <= c["rps"] <= 100.0 for c in out["concepts"])
    assert all(
        c["is_strong"] is False
        for c in out["concepts"]
        if c["stability"] != "solid"
    ), "a thin/fair concept was marked strong"
    assert not {c["concept"] for c in out["concepts"]} & set(
        rps.DEFAULT_EXCLUDE_CONCEPTS
    )
    # At least one concept must survive the full pipeline with a real return —
    # an all-zero list would pass the range checks above vacuously.
    assert any(abs(c["ret"]) > 0.01 for c in out["concepts"][:50])
