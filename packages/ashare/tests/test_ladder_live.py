"""Tests for the live ladder history (``ladder_live``).

Zero network: the 10jqka fetchers are monkeypatched with frames shaped like the
real endpoint output. The two properties worth guarding are (a) the history is
*dense* (one row per symbol per session, 0 on a break) so the successor-row
consumers see real adjacency, and (b) the memo cache never hands back a frame a
caller could mutate into the cache.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from selene_space_ashare import ladder_live

_CST = timezone(timedelta(hours=8))


def _pool(codes: list[str], date: str) -> pd.DataFrame:
    return pd.DataFrame(
        {"code": codes, "name": [f"n{c}" for c in codes], "date": [date] * len(codes)}
    )


def _cont(pairs: list[tuple[str, int]], date: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "code": [c for c, _ in pairs],
            "consecutive_boards": [b for _, b in pairs],
            "date": [date] * len(pairs),
        }
    )


def _unavailable(date="", force=False, strict=False):
    """Stand in for a fetcher whose endpoint did not answer.

    Mirrors the real contract exactly: strict callers get the raise, lenient
    callers get the empty frame that a transport failure produces. A stub that
    always raised would let the strict wiring be removed without any test
    noticing — it has to answer the same way production does.
    """
    if strict:
        from selene_space_ashare.ths_data import LimitUpDataUnavailable

        raise LimitUpDataUnavailable("endpoint down (test stub)")
    return pd.DataFrame()


@pytest.fixture(autouse=True)
def _clear_cache():
    ladder_live.clear_cache()
    yield
    ladder_live.clear_cache()


class TestClosedSessions:
    def test_returns_ascending_trading_days_before_today(self) -> None:
        # 2024-10-08 Tue; the National Day break means 09-30 is the prior session.
        days = ladder_live._closed_sessions(3, datetime(2024, 10, 8).date())
        assert days == sorted(days)
        assert all(d < "20241008" for d in days)

    def test_zero_days_is_empty(self) -> None:
        assert ladder_live._closed_sessions(0, datetime(2024, 10, 8).date()) == []


class TestDensify:
    def test_absent_symbol_session_is_zero_not_missing(self) -> None:
        sparse = pd.DataFrame(
            {
                "symbol": ["600001", "600002"],
                "date": ["20241001", "20241002"],
                "consecutive_boards": [3, 1],
            }
        )
        out = ladder_live._densify(
            sparse, ["600001", "600002"], ["20241001", "20241002"]
        )
        assert len(out) == 4  # 2 symbols x 2 sessions
        got = {(r.symbol, r.date): r.consecutive_boards for r in out.itertuples()}
        assert got[("600001", "20241001")] == 3
        assert got[("600001", "20241002")] == 0  # broke the run, not absent
        assert got[("600002", "20241001")] == 0
        assert got[("600002", "20241002")] == 1

    def test_zero_row_breaks_the_successor_row_consumers(self) -> None:
        # The whole point: with a 0 row in between, `attach_ladder_promo`'s
        # shift(1) sees a break instead of a stale multi-day-old run.
        from selene_space_ashare.ladder import attach_ladder_promo

        sparse = pd.DataFrame(
            {
                "symbol": ["600001", "600001"],
                "date": ["20241001", "20241003"],
                "consecutive_boards": [2, 3],
            }
        )
        dense = ladder_live._densify(
            sparse, ["600001"], ["20241001", "20241002", "20241003"]
        )
        promo = attach_ladder_promo(dense)
        row = promo.loc[promo["date"] == "20241003"].iloc[0]
        # Sparse would treat 20241001 (2) as the predecessor of 20241003 (3) and
        # call it a promotion; dense sees 20241002 == 0, so it is a fresh board.
        assert row["promo_pool"] == 0
        assert row["promo_ok"] == 0

    def test_duplicate_symbol_date_keeps_last(self) -> None:
        sparse = pd.DataFrame(
            {
                "symbol": ["600001", "600001"],
                "date": ["20241001", "20241001"],
                "consecutive_boards": [1, 4],
            }
        )
        out = ladder_live._densify(sparse, ["600001"], ["20241001"])
        assert out.iloc[0]["consecutive_boards"] == 4


class TestHistoryFrames:
    def _patch(self, monkeypatch, pools: dict, conts: dict) -> None:
        def _pool_full(date="", force=False, strict=False):
            return pools.get(date, pd.DataFrame())

        def _cont_full(date="", force=False, strict=False):
            return conts.get(date, pd.DataFrame())

        monkeypatch.setattr(ladder_live, "fetch_limit_up_pool_full", _pool_full)
        monkeypatch.setattr(ladder_live, "fetch_continuous_limit_up", _cont_full)
        monkeypatch.setattr(
            ladder_live,
            "_seal_rate_frame",
            lambda days: pd.DataFrame(
                {
                    "date": days,
                    "seal_rate": [0.8] * len(days),
                    "open_count": [1] * len(days),
                }
            ),
        )

    def test_panel_is_dense_and_has_required_columns(self, monkeypatch) -> None:
        self._patch(
            monkeypatch,
            pools={
                "20241001": _pool(["600001", "600002"], "20241001"),
                "20241002": _pool(["600001"], "20241002"),
            },
            conts={
                "20241001": _cont([("600001", 2)], "20241001"),
                "20241002": _cont([("600001", 3)], "20241002"),
            },
        )
        monkeypatch.setattr(
            ladder_live,
            "_closed_sessions",
            lambda n, today: ["20241001", "20241002"],
        )
        panel = ladder_live.live_ladder_history(days_back=2)
        assert list(panel["date"]) == ["20241001", "20241002"]
        assert panel.loc[panel["date"] == "20241001", "first_board"].iloc[0] == 1
        assert panel.loc[panel["date"] == "20241001", "ge2_count"].iloc[0] == 1

    def test_per_stock_is_dense_over_answered_sessions(self, monkeypatch) -> None:
        self._patch(
            monkeypatch,
            pools={
                "20241001": _pool(["600001"], "20241001"),
                "20241002": _pool(["600002"], "20241002"),
            },
            conts={"20241001": pd.DataFrame(), "20241002": pd.DataFrame()},
        )
        monkeypatch.setattr(
            ladder_live, "_closed_sessions", lambda n, today: ["20241001", "20241002"]
        )
        per_stock = ladder_live.live_ladder_per_stock(days_back=2)
        assert len(per_stock) == 4  # 2 symbols x 2 sessions
        assert set(per_stock.columns) == {"symbol", "date", "consecutive_boards"}

    def test_quiet_window_returns_empty_with_columns(self, monkeypatch) -> None:
        # Every endpoint answered, but none had any limit-up name: an empty
        # tape, not an outage. Must not raise.
        self._patch(monkeypatch, pools={}, conts={})
        monkeypatch.setattr(
            ladder_live, "_closed_sessions", lambda n, today: ["20241001"]
        )
        panel = ladder_live.live_ladder_history(days_back=1)
        assert panel.empty
        assert "seal_rate" in panel.columns

    def test_total_outage_raises_rather_than_returning_an_empty_panel(
        self, monkeypatch
    ) -> None:
        """An unreachable endpoint must be distinguishable from a quiet tape.

        The regression this pins: both used to return an empty frame, so the
        background warm-up read a dead endpoint as a successful warm and never
        retried — and the endpoint layer read it as an authoritative empty
        panel. Strict mode makes the outage raise.
        """
        from selene_space_ashare.ths_data import LimitUpDataUnavailable

        monkeypatch.setattr(ladder_live, "fetch_limit_up_pool_full", _unavailable)
        monkeypatch.setattr(ladder_live, "fetch_continuous_limit_up", _unavailable)
        monkeypatch.setattr(
            ladder_live, "_closed_sessions", lambda n, today: ["20241001"]
        )
        with pytest.raises(LimitUpDataUnavailable):
            ladder_live.live_ladder_history(days_back=1)

    def test_total_outage_is_negative_cached(self, monkeypatch) -> None:
        # A sustained outage must not re-walk all 20 sessions on every request.
        from selene_space_ashare.ths_data import LimitUpDataUnavailable

        calls = {"n": 0}

        def _counted(*args, **kwargs):
            calls["n"] += 1
            raise LimitUpDataUnavailable("down")

        monkeypatch.setattr(ladder_live, "fetch_limit_up_pool_full", _counted)
        monkeypatch.setattr(
            ladder_live, "_closed_sessions", lambda n, today: ["20241001"]
        )

        for _ in range(3):
            with pytest.raises(LimitUpDataUnavailable):
                ladder_live.live_ladder_history(days_back=1)
        assert calls["n"] == 1  # walked once, then served from the failure memo

    def test_continuous_outage_skips_the_session_never_fabricates_first_boards(
        self, monkeypatch
    ) -> None:
        """The core regression: one timed-out continuous fetch used to relabel
        the entire pool as 首板.

        The pool answered, so the session looked real — but the continuous list
        came back empty *from an outage*, and `ladder_history_from_pool` reads
        an empty continuous frame as "no 连板 that day". Every pool code then
        became a 1-board, fabricating a whole session of 首板 out of one
        timeout. Under strict mode the session is skipped instead.
        """
        self._patch(
            monkeypatch,
            pools={
                "20241001": _pool(["600001", "600002"], "20241001"),
                "20241002": _pool(["600001"], "20241002"),
            },
            conts={"20241002": _cont([("600001", 2)], "20241002")},
        )
        # Session 1's continuous endpoint is down; session 2's is fine.
        monkeypatch.setattr(
            ladder_live,
            "fetch_continuous_limit_up",
            lambda date="", force=False, strict=False: (
                _unavailable(date, force, strict)
                if date == "20241001"
                else _cont([("600001", 2)], date)
            ),
        )
        monkeypatch.setattr(
            ladder_live, "_closed_sessions", lambda n, today: ["20241001", "20241002"]
        )
        panel = ladder_live.live_ladder_history(days_back=2)
        # The outage session is absent — the surviving rows are what the walk
        # actually answered for, not a fabricated all-首板 day.
        assert list(panel["date"]) == ["20241002"]

    def test_memo_hit_is_returned_and_not_aliased(self, monkeypatch) -> None:
        calls = {"n": 0}

        def _pool_full(date="", force=False, strict=False):
            calls["n"] += 1
            return _pool(["600001"], "20241001")

        monkeypatch.setattr(ladder_live, "fetch_limit_up_pool_full", _pool_full)
        monkeypatch.setattr(
            ladder_live, "fetch_continuous_limit_up", lambda *a, **k: pd.DataFrame()
        )
        monkeypatch.setattr(
            ladder_live, "_closed_sessions", lambda n, today: ["20241001"]
        )
        monkeypatch.setattr(
            ladder_live,
            "_seal_rate_frame",
            lambda days: pd.DataFrame({"date": days, "seal_rate": [0.8] * len(days)}),
        )
        panel = ladder_live.live_ladder_history(days_back=1)
        panel.loc[0, "date"] = "MUTATED"
        panel2 = ladder_live.live_ladder_history(days_back=1)
        assert "MUTATED" not in set(panel2["date"])
        assert calls["n"] == 1  # second call was a memo hit

    def test_live_panel_carries_the_backfill_turnover_columns(
        self, monkeypatch
    ) -> None:
        """`/ladder` asks for `limit_up_count`; the backfill panel provides it.

        The live panel is the *primary* source, so it must expose the same
        column set or the same endpoint renders different row keys depending on
        `history_source`. `limit_up_count` is the sealed count (the pool) and
        `touched_count` adds the 炸板 count from the stats endpoint.
        """
        self._patch(
            monkeypatch,
            pools={
                "20241001": _pool(["600001", "600002", "600003"], "20241001"),
                "20241002": _pool(["600001"], "20241002"),
            },
            conts={
                "20241001": _cont([("600001", 2)], "20241001"),
                "20241002": pd.DataFrame(),
            },
        )
        monkeypatch.setattr(
            ladder_live, "_closed_sessions", lambda n, today: ["20241001", "20241002"]
        )
        panel = ladder_live.live_ladder_history(days_back=2)
        for col in ("limit_up_count", "touched_count"):
            assert col in panel.columns
        day1 = panel.loc[panel["date"] == "20241001"].iloc[0]
        assert day1["limit_up_count"] == 3  # every pool code sealed
        assert day1["touched_count"] == 4  # + open_count (stubbed 1)

    def test_seal_rate_refetch_is_forced_even_on_the_default_path(
        self, monkeypatch
    ) -> None:
        """The stats cache has no TTL, so a pre-publish request poisons a date.

        `live_phase_series` is called with the default ``force=False``, so a
        caller-forwarded force would serve the poisoned entry. The seal-rate
        frame must refetch regardless.
        """
        forces: list[bool] = []

        def _stats(date="", force=False):
            forces.append(force)
            return {"seal_rate": 0.5}

        monkeypatch.setattr(
            ladder_live,
            "fetch_limit_up_pool_full",
            lambda date="", force=False, strict=False: _pool(["600001"], "20241001"),
        )
        monkeypatch.setattr(
            ladder_live,
            "fetch_continuous_limit_up",
            lambda date="", force=False, strict=False: pd.DataFrame(),
        )
        monkeypatch.setattr(
            ladder_live, "_closed_sessions", lambda n, today: ["20241001"]
        )
        monkeypatch.setattr(ladder_live, "fetch_limit_up_stats", _stats)
        ladder_live.live_ladder_history(days_back=1)
        assert forces == [True]


class TestLivePhaseSeries:
    def test_empty_snapshot_returns_settled_series_scope_none(
        self, monkeypatch
    ) -> None:
        sentinel = pd.DataFrame(
            {
                "date": ["20241001"],
                "max_consecutive": [3],
                "first_board": [5],
                "ge2_count": [2],
                "promo_rate": [0.3],
                "seal_rate": [0.7],
            }
        )
        monkeypatch.setattr(
            ladder_live, "live_ladder_history", lambda *a, **k: sentinel
        )
        labeled, scope = ladder_live.live_phase_series({})
        assert scope is None
        assert "phase" in labeled.columns  # classifier ran

    def test_snapshot_path_passes_per_stock_baseline(self, monkeypatch) -> None:
        # The regression this exists for: passing the aggregated panel to
        # live_ladder_row raised "daily_history 缺少列". Spy on the frame type.
        seen: dict[str, object] = {}

        monkeypatch.setattr(
            ladder_live,
            "live_ladder_history",
            lambda *a, **k: pd.DataFrame(columns=["date", "first_board"]),
        )
        per_stock = pd.DataFrame(
            {"symbol": ["600001"], "date": ["20241001"], "consecutive_boards": [2]}
        )
        monkeypatch.setattr(
            ladder_live, "live_ladder_per_stock", lambda *a, **k: per_stock
        )

        from selene_space_ashare import ladder as ladder_mod

        def _spy(rows, daily_history, **k):
            seen["cols"] = list(daily_history.columns)
            return {
                "date": "20241002",
                "max_consecutive": 3,
                "first_board": 1,
                "ge2_count": 1,
                "promo_rate": None,
                "seal_rate": 1.0,
            }

        monkeypatch.setattr(ladder_mod, "live_ladder_row", _spy)
        rows = {
            "600001.SH": {
                "last": 11.0,
                "prev_close": 10.0,
                "high": 11.0,
                "name": "甲",
                "timestamp": 1728959735000,
            }
        }
        labeled, scope = ladder_live.live_phase_series(rows)
        assert seen["cols"] == ["symbol", "date", "consecutive_boards"]
        assert scope == "intraday_provisional"
        assert "20241002" in set(labeled["date"].astype(str))

    def test_frozen_off_session_snapshot_does_not_shadow_the_settled_row(
        self, monkeypatch
    ) -> None:
        """Pre-open / weekend: the snapshot's date is already settled.

        Off-session the source serves a frozen cross-section whose timestamp is
        the last trade time, so `_snapshot_date` resolves to a session already
        in the settled history. Folding it would replace the authoritative EOD
        row with a row derived from a frozen quote — the regression this pins.
        """
        settled = pd.DataFrame(
            {
                "date": ["20241001", "20241002"],
                "max_consecutive": [4, 9],
                "first_board": [20, 30],
                "ge2_count": [3, 6],
                "promo_rate": [0.2, 0.17],
                "seal_rate": [0.7, 0.6],
            }
        )
        monkeypatch.setattr(
            ladder_live, "live_ladder_history", lambda *a, **k: settled.copy()
        )
        monkeypatch.setattr(
            ladder_live,
            "live_ladder_per_stock",
            lambda *a, **k: pd.DataFrame(
                {"symbol": ["600001"], "date": ["20241002"], "consecutive_boards": [9]}
            ),
        )

        from selene_space_ashare import ladder as ladder_mod

        def _stale_snapshot_row(rows, daily_history, **k):
            # Same date as the last settled session, different (stale) values.
            return {
                "date": "20241002",
                "max_consecutive": 1,
                "first_board": 1,
                "ge2_count": 0,
                "promo_rate": None,
                "seal_rate": 1.0,
            }

        monkeypatch.setattr(ladder_mod, "live_ladder_row", _stale_snapshot_row)
        rows = {
            "600001.SH": {
                "last": 11.0,
                "prev_close": 10.0,
                "high": 11.0,
                "name": "甲",
                "timestamp": 1728959735000,
            }
        }
        labeled, scope = ladder_live.live_phase_series(rows)

        assert scope is None  # nothing in progress
        # Exactly one row per date — the stale snapshot row was discarded.
        assert list(labeled["date"].astype(str)) == ["20241001", "20241002"]
        last = labeled.iloc[-1]
        assert int(last["max_consecutive"]) == 9  # settled value kept, not the 1
        assert int(last["first_board"]) == 30

    def test_newer_snapshot_session_is_folded(self, monkeypatch) -> None:
        # The complement of the case above: a strictly newer snapshot session is
        # appended and labelled provisional.
        settled = pd.DataFrame(
            {
                "date": ["20241001"],
                "max_consecutive": [4],
                "first_board": [20],
                "ge2_count": [3],
                "promo_rate": [0.2],
                "seal_rate": [0.7],
            }
        )
        monkeypatch.setattr(
            ladder_live, "live_ladder_history", lambda *a, **k: settled.copy()
        )
        monkeypatch.setattr(
            ladder_live,
            "live_ladder_per_stock",
            lambda *a, **k: pd.DataFrame(
                {"symbol": ["600001"], "date": ["20241001"], "consecutive_boards": [4]}
            ),
        )

        from selene_space_ashare import ladder as ladder_mod

        def _newer_row(rows, daily_history, **k):
            return {
                "date": "20241002",
                "max_consecutive": 5,
                "first_board": 3,
                "ge2_count": 2,
                "promo_rate": 0.5,
                "seal_rate": 0.8,
            }

        monkeypatch.setattr(ladder_mod, "live_ladder_row", _newer_row)
        rows = {
            "600001.SH": {
                "last": 11.0,
                "prev_close": 10.0,
                "high": 11.0,
                "name": "甲",
                "timestamp": 1728959735000,
            }
        }
        labeled, scope = ladder_live.live_phase_series(rows)
        assert scope == "intraday_provisional"
        assert list(labeled["date"].astype(str))[-1] == "20241002"
