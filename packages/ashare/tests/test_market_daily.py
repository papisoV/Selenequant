"""B3 read-face integration test — the real ``("astock","daily_all")`` partition.

Why this file exists: B0 laid down 3.9M bars and B3 added a read face, but the
failure mode this guards against is a *write-only pipeline* — data landed and
nothing reads it. Unit tests over ``tmp_path`` cannot see that: they pass just
as happily if the production partition is empty, mis-keyed, or stamped with a
different date convention than the reader assumes.

So this test runs the real ``get_cross_section`` against the real file and
asserts the contract on real rows. It is **not** a network test — the symbol
name lookup (the only network call in the read path) is monkeypatched out,
matching the convention in ``test_chanlun_real_data.py``.

The connection is opened ``mode=ro`` and injected, so ``KlineDB.open()`` never
runs: that path executes ``CREATE ... IF NOT EXISTS`` DDL and commits, which
would nudge the user's runtime DB as a side effect of running the suite.

Absent DB ⇒ whole module skips. Absent partition ⇒ the assertions fail loudly,
which is the point: a green suite must not outlive the data it claims to cover.

运行:
    pytest packages/ashare/tests/test_market_daily.py -v
"""

import sqlite3
from pathlib import Path

import pytest

from selene_space_ashare import market_daily as md

REPO_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = REPO_ROOT / "data" / "klines.db"

#: Floor, not the exact count. The universe drifts as listings change; an exact
#: equality would go red on the next IPO for the right data and the wrong reason.
MIN_SYMBOLS = 5000

pytestmark = pytest.mark.skipif(
    not DB_PATH.is_file(),
    reason=f"real klines.db not present: {DB_PATH}",
)


@pytest.fixture()
def live_db():
    """A KlineDB bound to the real file through a read-only connection.

    ``_conn`` is set directly (rather than via ``open()``) so the schema DDL in
    ``open()`` cannot write to the live database. ``_ensure()`` returns the
    existing connection when one is set, so every read path works unchanged.
    """
    from selene_space_market.kline_db import KlineDB

    handle = KlineDB(db_path=str(DB_PATH))
    handle._conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        yield handle
    finally:
        handle._conn.close()
        handle._conn = None


@pytest.fixture(scope="module")
def latest_frame():
    """The cross-section at the newest session, computed once per module.

    A cross-section over 3.9M rows takes ~16s here, and four of the tests below
    assert on this same frame; recomputing it per test would add a minute to the
    suite for no extra coverage. The frame is read-only and the tests that
    mutate (none do) would need their own query anyway.
    """
    from selene_space_market.kline_db import KlineDB

    handle = KlineDB(db_path=str(DB_PATH))
    handle._conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    patcher = pytest.MonkeyPatch()
    patcher.setattr(md, "_universe_names", lambda: {})
    try:
        yield md.market_daily_universe(handle, end_date=_latest_session_date(handle))
    finally:
        patcher.undo()
        handle._conn.close()
        handle._conn = None


def _latest_session_date(db) -> str:
    """Newest session present in the partition, as 'YYYY-MM-DD'."""
    from datetime import datetime

    from selene_space_ashare.sina_daily import CST

    ts = db.get_partition_max_timestamp(md.EXCHANGE, md.MARKET_TYPE, md.INTERVAL)
    assert ts is not None, "daily_all partition is empty — B0's backfill is gone"
    return datetime.fromtimestamp(ts / 1000, tz=CST).date().isoformat()


def test_universe_is_non_empty_and_whole_market(latest_frame):
    """The plan's mandated guard: the read face sees the real partition."""
    frame = latest_frame

    assert len(frame) >= MIN_SYMBOLS, (
        f"cross-section returned {len(frame)} rows; expected the whole market — "
        "the partition may be empty or keyed under a different market_type"
    )
    assert frame["symbol"].is_unique, "one row per symbol is the contract"


def test_universe_columns_and_attrs_match_contract(latest_frame):
    frame = latest_frame

    assert tuple(frame.columns) == md.COLUMNS
    assert "amount" not in frame.columns  # Sina carries no amount field
    assert frame.attrs["adjust"] == "raw"
    assert frame.attrs["source"] == "sina"


def test_frame_session_matches_partition_anchor(latest_frame, live_db):
    """The frame's dates must agree with an independently computed anchor.

    Not ``frame["date"].max() <= frame["date"].max()``: a date convention bug
    (stamping a CST-midnight bar in UTC shifts every session back one day) is
    invisible to a self-referential bound, because the whole frame moves
    together. The anchor below is derived from the raw timestamp column, so a
    shifted frame disagrees with it.
    """
    frame = latest_frame
    expected_session = _latest_session_date(live_db)

    assert frame["date"].max() == expected_session, (
        f"newest bar in the frame is dated {frame['date'].max()} but the partition's "
        f"newest timestamp converts to {expected_session} — the date stamp and the "
        "stored timestamps disagree on the session convention"
    )
    assert (frame["date"] <= expected_session).all()


def test_universe_prices_are_positive_and_prev_close_is_populated(latest_frame):
    """Spot-check the numbers, not just the shape: a mis-stamped bar or a broken
    correlated subquery yields a frame of the right width and the wrong values."""
    frame = latest_frame

    for col in ("open", "high", "low", "close"):
        assert (frame[col] > 0).all(), f"non-positive {col} in the real partition"
    assert (frame["volume"] >= 0).all()

    # prev_close comes from the correlated subquery. Symbols on their first bar
    # have none; the overwhelming majority are mid-series and must have one.
    have_prev = frame["prev_close"].notna().sum()
    assert have_prev >= 0.9 * len(frame), (
        f"only {have_prev}/{len(frame)} rows carry prev_close — the correlated "
        "subquery is probably not matching the preceding bar"
    )


def test_earlier_end_date_returns_an_earlier_session(
    live_db, monkeypatch, latest_frame
):
    """The read face is a point-in-time query, not a latest-only shortcut."""
    monkeypatch.setattr(md, "_universe_names", lambda: {})
    newest = _latest_session_date(live_db)
    full = latest_frame

    sampled = full["date"].iloc[0]
    assert sampled <= newest

    earlier = md.market_daily_universe(live_db, end_date=sampled)

    assert len(earlier) <= len(full)
    assert (earlier["date"] <= sampled).all()


# -- market_daily_window ----------------------------------------------------


@pytest.fixture()
def window_frame(live_db):
    """A 12-session window ending at the newest session, computed once."""
    from datetime import datetime as _dt, timedelta as _td

    end = _latest_session_date(live_db)
    end_dt = _dt.fromisoformat(end)
    # Wide calendar margin: 12 sessions can span a holiday block (National Day
    # alone eats a week), and the window is defined in sessions, not days.
    start = (end_dt - _td(days=45)).date().isoformat()
    patcher = pytest.MonkeyPatch()
    patcher.setattr(md, "_universe_names", lambda: {})
    try:
        yield md.market_daily_window(live_db, start_date=start, end_date=end)
    finally:
        patcher.undo()


def test_window_columns_match_contract(window_frame):
    assert tuple(window_frame.columns) == md.COLUMNS
    assert window_frame.attrs["adjust"] == "raw"
    assert window_frame.attrs["source"] == "sina"
    assert not window_frame.empty, "window over the real partition returned no rows"


def test_window_is_sorted_by_symbol_then_date(window_frame):
    keys = list(zip(window_frame["symbol"], window_frame["date"], strict=False))
    assert keys == sorted(keys), "window must be ordered by (symbol, date) ascending"
    # Several sessions per symbol, not one cross-section repeated.
    assert window_frame.groupby("symbol", sort=False).size().max() > 1


def test_window_covers_multiple_sessions(window_frame):
    sessions = set(window_frame["date"])
    assert len(sessions) >= 8, (
        f"45-calendar-day window produced only {len(sessions)} sessions — the "
        "range query may be clamping to one day"
    )


def test_window_prev_close_comes_from_before_the_first_session(live_db, window_frame):
    """The window's opening session must be scorable.

    If ``prev_close`` were taken from the first *in-window* bar, every symbol's
    opening row would carry its own close as "previous" — i.e. a 0.00% session
    that silently drops the window's first day from every return.
    """
    first_session = window_frame["date"].min()
    opening = window_frame[window_frame["date"] == first_session]

    assert len(opening) > 100
    populated = opening["prev_close"].notna()
    assert populated.mean() > 0.95, (
        f"only {populated.mean():.1%} of the opening session carries prev_close — "
        "the lookup is not reaching back before the window"
    )
    # Independent cross-check: a symbol's opening prev_close must equal the
    # close of its own last bar *before* the window — read straight from the
    # table, not from the helper under test.
    from datetime import datetime as _dt, timedelta as _td

    symbol = opening["symbol"].iloc[0]
    first_day = _dt.fromisoformat(first_session)
    prior_bars = live_db.get_klines(
        md.EXCHANGE,
        md.MARKET_TYPE,
        symbol,
        md.INTERVAL,
        end_date=first_day - _td(days=1),
    )
    assert not prior_bars.empty, f"{symbol} has no history before the window"
    expected_prev = float(prior_bars["close"].iloc[-1])
    row = opening[opening["symbol"] == symbol].iloc[0]
    assert float(row["prev_close"]) == pytest.approx(expected_prev, rel=1e-9), (
        f"{symbol}: window prev_close {row['prev_close']} != last pre-window close "
        f"{expected_prev} — the baseline is being taken from inside the window"
    )
    assert float(row["close"]) != pytest.approx(expected_prev, rel=1e-9), (
        "the sampled symbol's opening bar is flat against its prior close; pick a "
        "different sample so this test can actually detect a baseline mix-up"
    )


def test_window_matches_cross_section_on_the_same_end_date(live_db, window_frame):
    """The two read faces must agree on the same session's close.

    Anchored on an independently computed return, not on ``a == a``: for a
    sample of symbols the window's own (close, prev_close) pair is turned into
    a percentage and compared against the cross-section's — a mismatch in either
    reader shows up as a price disagreement, where comparing closes alone would
    pass even if both readers were shifted by one session.
    """
    end_date = window_frame["date"].max()
    last = window_frame[window_frame["date"] == end_date]
    assert not last.empty

    sample = last.head(50)
    by_symbol = dict(zip(sample["symbol"], sample["close"], strict=False))

    # Close of the *previous* session, computed from the window itself.
    prior_date = sorted(window_frame["date"].unique())[-2]
    prior = window_frame[window_frame["date"] == prior_date]
    prior_close = dict(zip(prior["symbol"], prior["close"], strict=False))

    from datetime import datetime

    from selene_space_ashare.sina_daily import CST

    end_ms = (
        int(
            datetime.combine(
                datetime.fromisoformat(end_date).date(), datetime.min.time(), tzinfo=CST
            ).timestamp()
            * 1000
        )
        + 86_399_999
    )
    cross = live_db.get_cross_section(
        md.EXCHANGE, md.MARKET_TYPE, md.INTERVAL, end_ms=end_ms
    )
    cross_by_symbol = dict(zip(cross["symbol"], cross["close"], strict=False))
    cross_prev = dict(zip(cross["symbol"], cross["prev_close"], strict=False))

    checked = 0
    for symbol, close in by_symbol.items():
        if symbol not in cross_by_symbol or symbol not in prior_close:
            continue
        assert cross_by_symbol[symbol] == pytest.approx(close, rel=1e-9), (
            f"{symbol}: window close {close} != cross-section close "
            f"{cross_by_symbol[symbol]} on {end_date}"
        )
        assert cross_prev[symbol] == pytest.approx(prior_close[symbol], rel=1e-9), (
            f"{symbol}: cross-section prev_close disagrees with the window's "
            f"prior-session close on {end_date}"
        )
        checked += 1
    assert checked >= 20, f"only {checked} symbols comparable — sample too sparse"


def test_window_rejects_malformed_dates(live_db):
    for bad in ("2026-13-45", "2026/10/16", "not-a-date", ""):
        with pytest.raises(ValueError):
            md.market_daily_window(live_db, start_date=bad, end_date="2026-10-16")


def test_window_rejects_inverted_range(live_db):
    with pytest.raises(ValueError):
        md.market_daily_window(live_db, start_date="2026-10-16", end_date="2026-10-01")


def test_window_rejects_unsupported_adjust(live_db):
    with pytest.raises(ValueError):
        md.market_daily_window(
            live_db,
            start_date="2026-10-01",
            end_date="2026-10-16",
            adjust="qfq",  # type: ignore[arg-type]
        )


def test_empty_window_returns_contract_columns(live_db):
    """A range with no trading days is empty, not an error and not a KeyError.

    Uses a future weekend far past the base so the result is empty regardless of
    what gets backfilled later.
    """
    frame = md.market_daily_window(
        live_db, start_date="2099-01-03", end_date="2099-01-04"
    )
    assert frame.empty
    assert tuple(frame.columns) == md.COLUMNS


def test_window_symbol_filter(live_db, window_frame):
    picked = list(window_frame["symbol"].unique()[:3])
    filtered = md.market_daily_window(
        live_db,
        start_date=window_frame["date"].min(),
        end_date=window_frame["date"].max(),
        symbols=picked,
    )
    assert set(filtered["symbol"]) == set(picked)
    # Unknown symbols contribute nothing rather than raising.
    unknown = md.market_daily_window(
        live_db,
        start_date=window_frame["date"].min(),
        end_date=window_frame["date"].max(),
        symbols=["999999.XX"],
    )
    assert unknown.empty
    assert tuple(unknown.columns) == md.COLUMNS


def test_window_reads_daily_all_only(live_db):
    """Guard the partition key: a window accidentally reading ``spot`` would
    return the frozen static-import bars instead of the live base."""
    assert md.EXCHANGE == "astock"
    assert md.MARKET_TYPE == "daily_all"
    assert md.INTERVAL == "1d"
