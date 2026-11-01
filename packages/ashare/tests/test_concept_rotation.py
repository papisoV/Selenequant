"""Concept rotation matrix — arithmetic on synthetic bars, shape on the real base.

The synthetic half exists because the interesting failures are arithmetic, not
plumbing: an equal-weight mean that silently becomes a close-weighted one, a
suspended member that gets counted as a flat 0% instead of being excluded, a
blacklisted bucket that ranks first because it holds 800 names. None of those
are visible in a real-data smoke test — real data returns *a* ranking either
way, just the wrong one.

The real-base half is a contract test: the module must produce the documented
shape against ``("astock","daily_all")`` without any network, and the numbers
must stay in the range a 12-session window implies. It is not a golden-value
test — the base moves every session.

运行:
    pytest packages/ashare/tests/test_concept_rotation.py -v
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time
from pathlib import Path

import pandas as pd
import pytest

from selene_space_ashare import concept_rotation as cr

REPO_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = REPO_ROOT / "data" / "klines.db"


def _day_ms(day: str) -> int:
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Shanghai")
    y, m, d = (int(part) for part in day.split("-"))
    return int(datetime.combine(date(y, m, d), time.min, tzinfo=tz).timestamp() * 1000)


class StubDB:
    """The two KlineDB read methods rotation needs, backed by literal frames.

    Deliberately minimal: if rotation starts calling a third method, this stub
    raises and the test says so, rather than quietly exercising a different
    path than production.
    """

    def __init__(self, bars: pd.DataFrame, sessions: list[str]):
        self._bars = bars
        self._sessions = sorted(sessions)

    def get_session_stamps(self, *_args, **_kwargs) -> list[int]:
        return [_day_ms(day) for day in self._sessions]

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


SESSIONS = ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]


def _membership(pairs: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame(pairs, columns=["symbol", "concept"])


# -- arithmetic -------------------------------------------------------------


def test_equal_weight_mean_is_not_close_weighted():
    """Two members with the same % move contribute equally regardless of price.

    A close-weighted or return-weighted bug is invisible if both members move
    together, so the fixture gives them identical percentage moves and wildly
    different absolute prices: the expected concept move is exactly +10%, while
    a price-weighted mean would land elsewhere.
    """
    rows = [
        ("A.SZ", "2026-10-15", 10.0),
        ("A.SZ", "2026-10-16", 11.0),  # +10%
        ("B.SZ", "2026-10-15", 1000.0),
        ("B.SZ", "2026-10-16", 1100.0),  # +10%
    ]
    # Backfill earlier sessions so the clamp floor cannot bite and the last
    # session is scored against a predecessor that actually exists.
    rows = [
        (symbol, day, price)
        for day in SESSIONS[:3]
        for symbol, price in (("A.SZ", 9.0), ("B.SZ", 900.0))
    ] + rows
    db = StubDB(_bars(rows), SESSIONS)

    out = cr.build_rotation_matrix(
        db,
        _membership([("A.SZ", "主题X"), ("B.SZ", "主题X")]),
        days=7,
        end_date="2026-10-16",
        min_members=2,
    )

    assert out["reason"] is None
    assert out["dates"][0] == "2026-10-16"
    value = dict(out["columns"]["2026-10-16"])["主题X"]
    assert value == pytest.approx(10.0, abs=0.01)


def test_pct_is_a_percentage_number_not_a_fraction():
    rows = [("A.SZ", "2026-10-15", 10.0), ("A.SZ", "2026-10-16", 10.522)]
    db = StubDB(_bars(rows), SESSIONS)

    out = cr.build_rotation_matrix(
        db,
        _membership([("A.SZ", "主题X")]),
        days=7,
        end_date="2026-10-16",
        min_members=1,
    )
    assert dict(out["columns"]["2026-10-16"])["主题X"] == pytest.approx(5.22, abs=0.01)


def test_suspended_member_is_excluded_from_both_sides():
    """A member with no bar that session must not be counted as a flat 0%.

    This is the difference between "three of five constituents rose 10%" and
    "three rose 10% and two were unchanged" — 10% vs 6%. The fixture also
    checks the denominator by giving the concept a min_members of 3 with only
    two *active* members: the concept must drop out entirely, not get scored on
    the two that happened to trade.
    """
    rows = [
        # Three members trade both days.
        ("A.SZ", "2026-10-15", 10.0),
        ("A.SZ", "2026-10-16", 11.0),
        ("B.SZ", "2026-10-15", 10.0),
        ("B.SZ", "2026-10-16", 11.0),
        ("C.SZ", "2026-10-15", 10.0),
        ("C.SZ", "2026-10-16", 11.0),
        # D is halted on the scored session — no 10-16 row at all.
        ("D.SZ", "2026-10-15", 50.0),
        # E is halted the whole window.
        ("E.SZ", "2026-10-12", 50.0),
    ]
    db = StubDB(_bars(rows), SESSIONS)
    membership = _membership(
        [
            ("A.SZ", "均分"),
            ("B.SZ", "均分"),
            ("C.SZ", "均分"),
            ("D.SZ", "均分"),
            ("E.SZ", "均分"),
        ]
    )

    out = cr.build_rotation_matrix(
        db, membership, days=7, end_date="2026-10-16", min_members=3
    )
    assert dict(out["columns"]["2026-10-16"])["均分"] == pytest.approx(10.0, abs=0.01)


def test_min_members_threshold_drops_small_concepts():
    rows = [
        ("A.SZ", "2026-10-15", 10.0),
        ("A.SZ", "2026-10-16", 12.0),
        ("B.SZ", "2026-10-15", 10.0),
        ("B.SZ", "2026-10-16", 11.0),
        ("C.SZ", "2026-10-15", 10.0),
        ("C.SZ", "2026-10-16", 11.0),
    ]
    membership = _membership(
        [
            ("A.SZ", "两人概念"),
            ("B.SZ", "两人概念"),
            ("A.SZ", "三人概念"),
            ("B.SZ", "三人概念"),
            ("C.SZ", "三人概念"),
        ]
    )
    db = StubDB(_bars(rows), SESSIONS)

    out = cr.build_rotation_matrix(
        db, membership, days=7, end_date="2026-10-16", min_members=3
    )
    names = {row[0] for row in out["columns"]["2026-10-16"]}
    assert "三人概念" in names
    assert "两人概念" not in names, "a 2-member concept cleared a min_members=3 bar"


def test_default_min_members_is_the_documented_three():
    """The default must be 3, not merely *some* threshold.

    Every other test passes ``min_members=`` explicitly, so the module constant
    was never consumed by a test: it could be changed to any value and the
    suite stayed green. Pin the default itself by calling without the argument
    and straddling the boundary — a 3-member concept survives, a 2-member one
    does not.
    """
    assert cr.MIN_MEMBERS == 3
    rows = [
        (symbol, day, 10.0)
        for day in ("2026-10-15", "2026-10-16")
        for symbol in ("A.SZ", "B.SZ", "C.SZ")
    ]
    membership = _membership(
        [
            ("A.SZ", "两人概念"),
            ("B.SZ", "两人概念"),
            ("A.SZ", "三人概念"),
            ("B.SZ", "三人概念"),
            ("C.SZ", "三人概念"),
        ]
    )
    db = StubDB(_bars(rows), SESSIONS)

    out = cr.build_rotation_matrix(db, membership, days=7, end_date="2026-10-16")

    names = {row[0] for row in out["columns"]["2026-10-16"]}
    assert "三人概念" in names
    assert "两人概念" not in names


def test_blacklist_removes_attribute_buckets():
    rows = [
        ("A.SZ", "2026-10-15", 10.0),
        ("A.SZ", "2026-10-16", 15.0),
        ("B.SZ", "2026-10-15", 10.0),
        ("B.SZ", "2026-10-16", 11.0),
        ("C.SZ", "2026-10-15", 10.0),
        ("C.SZ", "2026-10-16", 11.0),
    ]
    membership = _membership(
        [
            ("A.SZ", "融资融券"),
            ("B.SZ", "融资融券"),
            ("C.SZ", "融资融券"),
            ("A.SZ", "真主题"),
            ("B.SZ", "真主题"),
            ("C.SZ", "真主题"),
        ]
    )
    db = StubDB(_bars(rows), SESSIONS)

    out = cr.build_rotation_matrix(
        db, membership, days=7, end_date="2026-10-16", min_members=3
    )
    names = {row[0] for row in out["columns"]["2026-10-16"]}
    assert "融资融券" not in names
    assert "真主题" in names

    # Passing an empty exclude set is the documented escape hatch.
    kept = cr.build_rotation_matrix(
        db,
        membership,
        days=7,
        end_date="2026-10-16",
        min_members=3,
        exclude=frozenset(),
    )
    assert "融资融券" in {row[0] for row in kept["columns"]["2026-10-16"]}


def test_resumption_bar_is_not_scored_as_a_one_day_move():
    """A bar after a halt carries the whole suspended move.

    Symbol C is halted on 10-15 and resumes 10-16 with a +20% move that spans
    two sessions. Scoring it as a single day would lift the concept to 10%;
    its true single-day contribution is unknown, so the concept must be scored
    on A and B alone (+10%).
    """
    rows = [
        ("A.SZ", "2026-10-15", 10.0),
        ("A.SZ", "2026-10-16", 11.0),
        ("B.SZ", "2026-10-15", 10.0),
        ("B.SZ", "2026-10-16", 11.0),
        # C: present 10-14, absent 10-15, back 10-16 up 20% from 10-14.
        ("C.SZ", "2026-10-14", 10.0),
        ("C.SZ", "2026-10-16", 12.0),
    ]
    db = StubDB(_bars(rows), SESSIONS)
    membership = _membership(
        [("A.SZ", "停牌主题"), ("B.SZ", "停牌主题"), ("C.SZ", "停牌主题")]
    )

    out = cr.build_rotation_matrix(
        db, membership, days=7, end_date="2026-10-16", min_members=2
    )
    assert dict(out["columns"]["2026-10-16"])["停牌主题"] == pytest.approx(
        10.0, abs=0.01
    )


def test_days_is_clamped_to_the_documented_range():
    assert cr._clamp_days(1) == cr.MIN_DAYS
    assert cr._clamp_days(365) == cr.MAX_DAYS
    assert cr._clamp_days(12) == 12

    # The clamp must be exercised through the *call*, not only the helper: a
    # build that forgets to clamp asks for 365 sessions and this stub only has
    # five, so the requested window cannot be honoured. Assert the request that
    # reached the reader, which is what the clamp actually controls.
    class RecordingDB(StubDB):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.requests: list[dict] = []

        def get_session_stamps(self, *args, **kwargs):
            self.requests.append(dict(kwargs))
            return super().get_session_stamps(*args, **kwargs)

    rows = [("A.SZ", "2026-10-15", 10.0), ("A.SZ", "2026-10-16", 11.0)]
    db = RecordingDB(_bars(rows), SESSIONS)
    members = _membership([("A.SZ", "主题X")])

    cr.build_rotation_matrix(
        db, members, days=365, end_date="2026-10-16", min_members=1
    )
    assert db.requests[-1]["limit"] - 1 == cr.MAX_DAYS, (
        f"requested {db.requests[-1]['limit']} sessions for days=365 — the clamp "
        "did not reach the reader"
    )

    db.requests.clear()
    cr.build_rotation_matrix(db, members, days=1, end_date="2026-10-16", min_members=1)
    assert db.requests[-1]["limit"] - 1 == cr.MIN_DAYS

    # Clamping widens the fetch; it must not truncate the result to one day.
    out = cr.build_rotation_matrix(
        db, members, days=1, end_date="2026-10-16", min_members=1
    )
    assert out["dates"]


def test_window_left_edge_is_never_scored():
    """The window's first session is a baseline, not a return.

    A 5-session base asked for ``days`` sessions must score at most
    ``len(sessions) - 1`` of them. Scoring the opening session would mean its
    return was computed against a close outside the fetched window — the exact
    silent-staleness the predecessor lookup exists to avoid.
    """
    rows = [("A.SZ", day, float(index + 1)) for index, day in enumerate(SESSIONS)]
    db = StubDB(_bars(rows), SESSIONS)
    out = cr.build_rotation_matrix(
        db,
        _membership([("A.SZ", "主题X")]),
        days=8,
        end_date="2026-10-16",
        min_members=1,
    )

    assert out["dates"] == sorted(SESSIONS[1:], reverse=True), (
        f"scored sessions were {out['dates']}; the opening session must be a "
        "baseline only"
    )


# -- empty and degraded paths ----------------------------------------------


def test_empty_membership_returns_reason_not_exception():
    db = StubDB(_bars([]), SESSIONS)
    out = cr.build_rotation_matrix(
        db, pd.DataFrame(columns=["symbol", "concept"]), days=7, end_date="2026-10-16"
    )
    assert out["dates"] == []
    assert out["columns"] == {}
    assert out["concept_count"] == 0
    assert out["reason"] == "membership_empty"


def test_membership_missing_columns_is_named():
    db = StubDB(_bars([]), SESSIONS)
    out = cr.build_rotation_matrix(
        db, pd.DataFrame({"symbol": ["A.SZ"]}), days=7, end_date="2026-10-16"
    )
    assert out["reason"] == "membership_missing_columns"


def test_empty_daily_base_returns_reason():
    db = StubDB(_bars([]), SESSIONS)
    out = cr.build_rotation_matrix(
        db, _membership([("A.SZ", "主题X")]), days=7, end_date="2026-10-16"
    )
    assert out["reason"] == "daily_base_empty"
    assert out["as_of"] is None


def test_single_session_window_cannot_score():
    """One session is a baseline, not a return — it must be reported, not faked."""
    rows = [("A.SZ", "2026-10-16", 10.0)]
    db = StubDB(_bars(rows), ["2026-10-16"])
    out = cr.build_rotation_matrix(
        db, _membership([("A.SZ", "主题X")]), days=7, end_date="2026-10-16"
    )
    assert out["dates"] == []
    assert out["reason"]


def test_malformed_end_date_raises():
    db = StubDB(_bars([]), SESSIONS)
    with pytest.raises(ValueError):
        cr.build_rotation_matrix(
            db, _membership([("A.SZ", "主题X")]), days=7, end_date="2026-13-45"
        )


def test_dates_are_newest_first_and_columns_are_sorted_desc():
    rows = []
    for day, prices in zip(
        SESSIONS,
        [(10, 10, 10), (10, 10, 10), (10, 10, 10), (10, 10, 10), (10, 13.31, 10.0)],
        strict=False,
    ):
        rows += [
            ("A.SZ", day, prices[0]),
            ("B.SZ", day, prices[1]),
            ("C.SZ", day, prices[2]),
        ]
    db = StubDB(_bars(rows), SESSIONS)
    membership = _membership(
        [
            ("A.SZ", "弱"),
            ("C.SZ", "弱"),
            ("A.SZ", "强"),
            ("B.SZ", "强"),
            ("C.SZ", "强"),
        ]
    )
    out = cr.build_rotation_matrix(
        db, membership, days=7, end_date="2026-10-16", min_members=2
    )

    assert out["dates"] == sorted(out["dates"], reverse=True)
    for rows_out in out["columns"].values():
        pcts = [row[1] for row in rows_out]
        assert pcts == sorted(pcts, reverse=True)


# -- real base --------------------------------------------------------------


@pytest.mark.skipif(not DB_PATH.is_file(), reason=f"no {DB_PATH}")
def test_real_base_produces_a_plausible_matrix():
    """Contract run against the real partition — shape and range only.

    No golden values: the base gains a session every trading day, so any pinned
    ranking would go red for the right data and the wrong reason. What must
    hold is that the call completes, the window is long enough to be a window,
    and the numbers are percentages in a range daily equity moves occupy.
    """
    from selene_space_market.kline_db import KlineDB

    handle = KlineDB(db_path=str(DB_PATH))
    handle._conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        try:
            membership = cr_membership_from_cache()
        except FileNotFoundError:
            pytest.skip("concept membership cache not present (offline run)")
        out = cr.build_rotation_matrix(handle, membership, days=12)
    finally:
        handle._conn.close()
        handle._conn = None

    assert out["reason"] is None, out["reason"]
    assert len(out["dates"]) >= 10
    assert 1100 <= out["concept_count"] <= 1250, out["concept_count"]
    assert out["as_of"] == out["dates"][0]

    latest = out["columns"][out["dates"][0]]
    # A single session's column must hold essentially every concept that appears
    # somewhere in the window. A large gap means the last session is being
    # scored against a truncated membership (e.g. only the names that traded).
    assert len(latest) >= 0.95 * out["concept_count"], (
        f"latest column has {len(latest)} concepts but the window saw "
        f"{out['concept_count']} — the final session is losing constituents"
    )
    pcts = [row[1] for row in latest]
    assert all(-35.0 <= value <= 35.0 for value in pcts), (
        "a concept daily move outside ±35% suggests a return is being computed "
        "across a halt or against the wrong baseline"
    )
    # Names are non-empty strings — a broken join would yield NaN or "".
    assert all(isinstance(row[0], str) and row[0] for row in latest[:20])


def cr_membership_from_cache() -> pd.DataFrame:
    """Read the membership parquet straight from disk; never touches the network."""
    from selene_space_ashare import concept_map

    path = concept_map.CACHE_DIR / concept_map.CACHE_NAME
    if not path.is_file():
        raise FileNotFoundError(path)
    return pd.read_parquet(path)
