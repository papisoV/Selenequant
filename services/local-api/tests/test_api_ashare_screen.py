# Absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 tick-stock-panel contributors
# Source: https://github.com/shy3130/tick-stock-panel
"""Tests for /api/v1/ashare/screen — the interactive screener endpoint.

The endpoint's load-bearing contracts, in the order they break things:

* **Fail-open (200 + ``status``) for data problems; 422 only for a malformed
  condition.** A screener that 500s on a missing baseline reads to the user as
  "the page is broken", not "the market data is unavailable".
* **The newest session is never silently scored when it is thin.** Measured
  2026-10-20: 67 of 5574 names. Serving that cross-section without a
  ``partial_coverage`` verdict would silently hide 98% of the market.
* **Parameters reach the spec unchanged**, including the percent-unit
  ``change_pct`` and the yuan-unit market caps.
"""

from __future__ import annotations

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.error_handler import install_error_handlers

pytest.importorskip("selene_space_ashare")

import app.api.v1.ashare_screen as sc  # noqa: E402
from app.api.v1.ashare_screen import router as screen_router  # noqa: E402


def _make_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(screen_router, prefix="/api/v1")
    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_make_app())


def _payload(**overrides):
    payload = {
        "status": "ok",
        "reason": None,
        "as_of": "2026-10-19",
        "adjust": "raw",
        "quote_source": "daily",
        "coverage": {
            "quoted": 3973,
            "universe": 5574,
            "ratio": 0.713,
            "baseline_symbols": 5564,
            "baseline_as_of": "2026-10-16",
            "baseline_sessions": 120,
            "amount_available": False,
            "market_cap_available": False,
        },
        "screen": {},
        "matched": 1,
        "scanned": 3973,
        "truncated": False,
        "notes": [],
        "rows": [
            {
                "symbol": "600000.SH",
                "name": "浦发银行",
                "last": 10.0,
                "change_pct": 1.01,
                "boards": 0,
                "volume_ratio": 1.2,
                "turnover": None,
                "turnover_est": False,
                "float_mv": None,
                "total_mv": None,
            }
        ],
    }
    payload.update(overrides)
    return payload


class TestPost:
    def test_happy_path_wraps_in_envelope(self, client, monkeypatch) -> None:
        seen: dict = {}

        def fake_build(date="", **fields):
            seen["date"] = date
            seen["fields"] = fields
            return _payload()

        monkeypatch.setattr(sc, "build_screen", fake_build)

        response = client.post("/api/v1/ashare/screen", json={"change_pct_min": 1.0})

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["status"] == "ok"
        assert body["data"]["rows"][0]["symbol"] == "600000.SH"
        assert seen["fields"] == {"change_pct_min": 1.0}

    def test_date_is_popped_from_the_body(self, client, monkeypatch) -> None:
        seen: dict = {}

        def fake_build(date="", **fields):
            seen["date"] = date
            seen["fields"] = fields
            return _payload()

        monkeypatch.setattr(sc, "build_screen", fake_build)

        client.post("/api/v1/ashare/screen", json={"date": "2026-10-16", "limit": 20})

        assert seen["date"] == "2026-10-16"
        assert "date" not in seen["fields"]
        assert seen["fields"]["limit"] == 20

    def test_malformed_json_is_422(self, client) -> None:
        response = client.post(
            "/api/v1/ashare/screen",
            content="not json",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422

    def test_non_object_body_is_422(self, client) -> None:
        response = client.post("/api/v1/ashare/screen", json=[1, 2, 3])
        assert response.status_code == 422

    def test_invalid_condition_is_422_not_500(self, client) -> None:
        """A malformed condition is the caller's error — it must not read as data loss."""
        response = client.post(
            "/api/v1/ashare/screen", json={"change_pct_min": 5.0, "change_pct_max": 1.0}
        )
        assert response.status_code == 422
        assert "倒挂" in response.text

    def test_data_failure_is_200_degraded(self, client, monkeypatch) -> None:
        def boom(date="", **fields):
            raise RuntimeError("disk gone")

        monkeypatch.setattr(sc, "build_screen", boom)

        response = client.post("/api/v1/ashare/screen", json={})

        assert response.status_code == 200
        body = response.json()["data"]
        assert body["status"] == "degraded"
        assert "screen_failed" in body["reason"]


class TestGet:
    def test_query_params_reach_the_body(self, client, monkeypatch) -> None:
        seen: dict = {}

        def fake_build(date="", **fields):
            seen["date"] = date
            seen["fields"] = fields
            return _payload()

        monkeypatch.setattr(sc, "build_screen", fake_build)

        client.get(
            "/api/v1/ashare/screen"
            "?date=2026-10-16&change_pct_min=1.5&board_types=main,cyb"
            "&exclude_st=true&limit=20&sort_by=change_pct&sort_desc=false"
        )

        assert seen["date"] == "2026-10-16"
        fields = seen["fields"]
        assert fields["change_pct_min"] == "1.5"
        assert fields["board_types"] == "main,cyb"
        assert fields["limit"] == "20"
        assert fields["sort_desc"] == "false"

    def test_defaults_are_always_sent(self, client, monkeypatch) -> None:
        seen: dict = {}

        def fake_build(date="", **fields):
            seen["fields"] = fields
            return _payload()

        monkeypatch.setattr(sc, "build_screen", fake_build)

        client.get("/api/v1/ashare/screen")

        assert seen["fields"]["sort_by"] == "change_pct"
        assert seen["fields"]["sort_desc"] == "true"
        assert seen["fields"]["limit"] == "50"


# ---------------------------------------------------------------------------
# build_screen — the real assembly, with every data source stubbed
# ---------------------------------------------------------------------------


def _quote_rows(n: int, *, start: int = 0) -> list[dict]:
    rows = []
    for i in range(start, start + n):
        code = f"{600000 + i:06d}"
        rows.append(
            {
                "symbol": f"{code}.SH",
                "name": f"票{i:03d}",
                "last": 10.0 + i,
                "prev_close": 10.0,
                "high": 11.0 + i,
                "low": 9.0,
                "volume": 1.0e6,
                "amount": None,
                "change_pct": float(i % 10),
                "baseline_close": 10.0,
                "ma5": 10.0,
                "ma20": 10.0,
                "ma60": 10.0,
                "high60": 12.0,
                "low60": 8.0,
                "avg_vol5": 1.0e6,
                "prev_boards": 0,
                "volume_ratio": 1.0,
                "limit_up": False,
                "limit_down": False,
                "broken": False,
                "boards": 0,
            }
        )
    return rows


class _FakeBaseline:
    def __init__(self) -> None:
        self.symbols = 0
        self.as_of = "2026-10-16"
        self.sessions = 120
        self.frame = pd.DataFrame()


@pytest.fixture()
def stubbed_screen(monkeypatch):
    """Every external boundary stubbed; ``state`` drives each test."""
    state: dict = {
        "db": object(),
        "universe": 5574,
        "sessions": ["2026-10-20", "2026-10-19", "2026-10-16"],
        "per_session": {"2026-10-20": 67, "2026-10-19": 3973, "2026-10-16": 5564},
        "snapshot": None,
        "snap_session": None,
        "baseline_raises": False,
        "market_cap": None,
        "universe_raises": None,  # session date -> Exception
    }

    # Module-level caches outlive a test and are keyed by db identity; a fresh
    # dict per test keeps one test's quotes out of the next one's.
    _quotes_cache, _session_cache = sc._quotes_cache, sc._session_cache
    sc._quotes_cache, sc._session_cache = {}, {}
    monkeypatch.setattr(sc, "_quotes_cache", sc._quotes_cache)
    monkeypatch.setattr(sc, "_session_cache", sc._session_cache)
    monkeypatch.setattr(sc, "_universe_size", lambda: state["universe"])
    monkeypatch.setattr(sc, "_daily_db", lambda: state["db"])
    monkeypatch.setattr(
        sc, "_recent_sessions", lambda db, limit=6: list(state["sessions"])
    )
    # ``get_session_counts`` fast path: derived from ``per_session`` unless a
    # test turns it off to exercise the per-session trial read.
    monkeypatch.setattr(
        sc,
        "_session_counts",
        lambda db, days=30: (
            []
            if state.get("no_counts")
            else [(d, state["per_session"].get(d, 0)) for d in state["sessions"]]
        ),
    )
    monkeypatch.setattr(sc, "_snapshot_rows", lambda: (state["snapshot"], None))
    monkeypatch.setattr(sc, "_snapshot_session", lambda rows: state["snap_session"])
    monkeypatch.setattr(sc, "_market_cap_frame", lambda: state["market_cap"])

    def fake_baseline(db, as_of):
        if state["baseline_raises"]:
            raise RuntimeError("baseline exploded")
        return _FakeBaseline()

    monkeypatch.setattr(sc, "_baseline_for", fake_baseline)

    from selene_space_ashare import market_daily, market_overview

    def fake_universe(db, *, end_date, adjust="raw"):
        err = state["universe_raises"] and state["universe_raises"].get(end_date)
        if err is not None:
            raise err
        n = state["per_session"].get(end_date, 0)
        return pd.DataFrame(
            {"symbol": [f"S{i}" for i in range(n)], "date": [end_date] * n}
        )

    monkeypatch.setattr(market_daily, "market_daily_universe", fake_universe)
    monkeypatch.setattr(
        market_overview,
        "quotes_from_daily",
        lambda frame, *, today: _quote_rows(len(frame)) if len(frame) else [],
    )
    monkeypatch.setattr(
        market_overview,
        "quotes_from_snapshot",
        lambda rows: _quote_rows(len(rows)),
    )
    monkeypatch.setattr(
        market_overview,
        "build_quote_rows",
        lambda *, quotes, baseline, trade_date=None: list(quotes),
    )
    yield state
    sc._quotes_cache, sc._session_cache = _quotes_cache, _session_cache


class TestCaching:
    def test_second_screen_does_not_reread_the_market(
        self, stubbed_screen, monkeypatch
    ) -> None:
        """The whole point of the cache: a re-filter costs mask time, not 10 s."""
        reads = {"n": 0}

        from selene_space_ashare import market_daily

        real_universe = market_daily.market_daily_universe

        def counting(db, *, end_date, adjust="raw"):
            reads["n"] += 1
            return real_universe(db, end_date=end_date, adjust=adjust)

        monkeypatch.setattr(market_daily, "market_daily_universe", counting)

        sc.build_screen(change_pct_min=1.0)
        after_first = reads["n"]
        sc.build_screen(change_pct_min=2.0)

        # Exactly one full-market read per scored session, not one per request.
        assert after_first == 1
        assert reads["n"] == after_first

    def test_second_screen_does_not_rebuild_rows(
        self, stubbed_screen, monkeypatch
    ) -> None:
        """Enriching 4k names costs ~200 ms; a re-filter must not pay it again."""
        builds = {"n": 0}

        from selene_space_ashare import market_overview

        real = market_overview.build_quote_rows

        def counting(*, quotes, baseline, trade_date=None):
            builds["n"] += 1
            return real(quotes=quotes, baseline=baseline, trade_date=trade_date)

        monkeypatch.setattr(market_overview, "build_quote_rows", counting)

        sc.build_screen(change_pct_min=1.0)
        sc.build_screen(change_pct_min=2.0)

        assert builds["n"] == 1

    def test_session_selection_runs_once_per_ttl(
        self, stubbed_screen, monkeypatch
    ) -> None:
        calls = {"n": 0}

        def counting(db, days=30):
            calls["n"] += 1
            return [
                (d, stubbed_screen["per_session"].get(d, 0))
                for d in stubbed_screen["sessions"]
            ]

        monkeypatch.setattr(sc, "_session_counts", counting)

        sc.build_screen()
        sc.build_screen()

        assert calls["n"] == 1

    def test_explicit_past_date_is_cached_separately(self, stubbed_screen) -> None:
        a = sc.build_screen(date="2026-10-16")
        b = sc.build_screen(date="2026-10-16")

        assert a["scanned"] == b["scanned"] == 5564
        assert "2026-10-16" in {key[1] for key in sc._quotes_cache}


class TestBuildScreen:
    def test_thin_newest_session_is_skipped_for_a_covered_one(
        self, stubbed_screen
    ) -> None:
        """67/5574 = 1.2% newest → score 10-19 (71.3%), not the thin tape."""
        data = sc.build_screen()

        assert data["status"] == "ok"
        assert data["as_of"] == "2026-10-19"
        assert data["scanned"] == 3973

    def test_all_thin_sessions_degrade(self, stubbed_screen) -> None:
        stubbed_screen["per_session"] = {"2026-10-20": 67, "2026-10-19": 100}
        stubbed_screen["sessions"] = ["2026-10-20", "2026-10-19"]

        data = sc.build_screen()

        assert data["status"] == "degraded"
        assert data["reason"] == "partial_coverage"
        assert data["as_of"] is None

    def test_falls_back_to_trial_reads_without_counts(self, stubbed_screen) -> None:
        """No ``get_session_counts`` (old db) ⇒ the slow but correct path still works."""
        stubbed_screen["no_counts"] = True

        data = sc.build_screen()

        assert data["as_of"] == "2026-10-19"
        assert data["scanned"] == 3973

    def test_unknown_universe_does_not_block_a_session(self, stubbed_screen) -> None:
        """Coverage is advisory when the universe count itself is unavailable."""
        stubbed_screen["universe"] = None

        data = sc.build_screen()

        assert data["as_of"] == "2026-10-20"
        assert data["coverage"]["ratio"] is None

    def test_no_db_is_degraded(self, monkeypatch) -> None:
        monkeypatch.setattr(sc, "_daily_db", lambda: None)

        data = sc.build_screen()

        assert data["status"] == "degraded"
        assert data["reason"] == "kline_manager_unavailable"

    def test_baseline_failure_is_degraded_not_raised(self, stubbed_screen) -> None:
        stubbed_screen["baseline_raises"] = True

        data = sc.build_screen()

        assert data["status"] == "degraded"
        assert "baseline_failed" in data["reason"]

    def test_market_cap_is_only_fetched_when_asked(
        self, stubbed_screen, monkeypatch
    ) -> None:
        calls = []

        def counting_frame():
            calls.append(1)
            return None

        monkeypatch.setattr(sc, "_market_cap_frame", counting_frame)

        sc.build_screen()
        assert calls == []

        sc.build_screen(float_mv_min=1e10)
        assert len(calls) == 1

    def test_market_cap_flag_is_null_when_not_consulted(self, stubbed_screen) -> None:
        """没设市值条件 ⇒ 根本没去取市值,``null`` 才是如实回答。

        渲染实证(2026-10-20,真实后端):普通筛选下这个字段是 ``false``,页脚
        因此对每一次正常筛选都写「市值表不可用,市值条件会排掉全部行」——把
        「没问」说成了「问了但没有」。三态才能把两件事分开。
        """
        data = sc.build_screen(change_pct_min=1.0)

        assert data["coverage"]["market_cap_available"] is None

    def test_market_cap_flag_is_false_when_the_fetch_failed(
        self, stubbed_screen
    ) -> None:
        """问了但拿不到 —— 这才是 ``false`` 的含义,页脚据此报警。"""
        stubbed_screen["market_cap"] = None

        data = sc.build_screen(float_mv_min=1e10)

        assert data["coverage"]["market_cap_available"] is False
        assert data["matched"] == 0

    def test_market_cap_flag_is_true_when_the_frame_arrived(
        self, stubbed_screen
    ) -> None:
        stubbed_screen["market_cap"] = pd.DataFrame(
            [
                {
                    "symbol": "600000",
                    "date": "2026-10-19",
                    "float_mv": 3e11,
                    "total_mv": 4e11,
                }
            ]
        )

        data = sc.build_screen(float_mv_min=1e10)

        assert data["coverage"]["market_cap_available"] is True

    def test_market_cap_join_filters_by_cap(self, stubbed_screen) -> None:
        stubbed_screen["market_cap"] = pd.DataFrame(
            [
                {
                    "symbol": "600000",
                    "date": "2026-10-19",
                    "float_mv": 3e11,
                    "total_mv": 4e11,
                }
            ]
        )

        data = sc.build_screen(float_mv_min=1e10)

        assert [r["symbol"] for r in data["rows"]] == ["600000.SH"]

    def test_snapshot_is_used_for_its_own_session(self, stubbed_screen) -> None:
        stubbed_screen["snapshot"] = {"a": object(), "b": object()}
        stubbed_screen["snap_session"] = "2026-10-20"

        data = sc.build_screen()

        assert data["as_of"] == "2026-10-20"
        assert data["quote_source"] == "snapshot"
        assert data["scanned"] == 2
        assert data["coverage"]["amount_available"] is True

    def test_explicit_date_uses_daily_when_snapshot_is_another_day(
        self, stubbed_screen
    ) -> None:
        stubbed_screen["snapshot"] = {"a": object()}
        stubbed_screen["snap_session"] = "2026-10-20"

        data = sc.build_screen(date="2026-10-16")

        assert data["as_of"] == "2026-10-16"
        assert data["quote_source"] == "daily"
        assert data["scanned"] == 5564

    def test_empty_matched_is_ok_not_empty(self, stubbed_screen) -> None:
        """No stock passes the filter ≠ no data. ``empty`` is reserved for no rows."""
        data = sc.build_screen(price_min=1e6)

        assert data["status"] == "ok"
        assert data["matched"] == 0

    def test_no_rows_is_empty(self, stubbed_screen) -> None:
        stubbed_screen["per_session"] = {}
        stubbed_screen["universe"] = None

        data = sc.build_screen()

        assert data["status"] == "empty"
        assert data["reason"] == "no_quotes"

    def test_invalid_condition_raises_value_error(self, stubbed_screen) -> None:
        with pytest.raises(ValueError, match="倒挂"):
            sc.build_screen(change_pct_min=5.0, change_pct_max=1.0)

    def test_response_carries_the_normalized_spec(self, stubbed_screen) -> None:
        data = sc.build_screen(
            change_pct_min=1.0, board_types="main,cyb", exclude_st=True
        )

        assert data["screen"]["change_pct"] == [1.0, None]
        assert data["screen"]["board_type"] == ["main", "cyb"]
        assert data["screen"]["exclude_st"] is True


class TestSpecMapping:
    def test_units_are_percent_and_yuan(self) -> None:
        spec = sc._spec_from_mapping(
            {"change_pct_min": "1.5", "float_mv_min": "3000000000"}
        )
        assert spec.change_pct_min == 1.5
        assert spec.float_mv_min == 3.0e9

    def test_empty_strings_are_unset(self) -> None:
        spec = sc._spec_from_mapping({"price_min": "", "boards_min": None})
        assert spec.price_min is None
        assert spec.boards_min is None

    def test_booleans_from_query_strings(self) -> None:
        assert sc._spec_from_mapping({"exclude_st": "true"}).exclude_st is True
        assert sc._spec_from_mapping({"exclude_st": "false"}).exclude_st is False
        assert sc._spec_from_mapping({"limit_up": "false"}).limit_up is False
        assert sc._spec_from_mapping({"limit_up": ""}).limit_up is None

    def test_bad_boolean_is_a_value_error(self) -> None:
        with pytest.raises(ValueError, match="布尔"):
            sc._spec_from_mapping({"exclude_st": "maybe"})

    def test_comma_and_list_boards_equivalent(self) -> None:
        assert sc._spec_from_mapping({"board_types": "main,cyb"}).board_types == (
            "main",
            "cyb",
        )
        assert sc._spec_from_mapping({"board_types": ["main", "cyb"]}).board_types == (
            "main",
            "cyb",
        )

    def test_unknown_fields_are_ignored(self) -> None:
        spec = sc._spec_from_mapping({"rsi_min": 30, "price_min": "1"})
        assert spec.price_min == 1.0
