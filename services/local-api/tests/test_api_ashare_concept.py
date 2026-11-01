# Absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 tick-stock-panel contributors
# Source: https://github.com/shy3130/tick-stock-panel
"""Tests for /api/v1/ashare/concept-members — one concept's constituents, priced.

The contracts that break visibly if they regress, in order:

* **Duplicate ``(symbol, concept)`` pairs are collapsed.** The snapshot holds 669
  of them (measured 2026-10-25). A ``len()`` or a naive join double-counts real
  constituents, and the doubled row is indistinguishable from a genuine one.
* **Truncation is reported, not implied.** The largest concept has 3,888
  members. Returning 1000 rows without ``total`` presents a slice as the whole
  concept — the panel would then be wrong in a way that looks right.
* **Unpriced members are kept and sorted last.** After the close the daily
  fallback may miss a name; dropping it would silently shrink the concept.
* **The price source is stated.** ``quote_source`` distinguishes live snapshot
  from yesterday's daily bar, so the UI never implies live prices at 16:00.
"""

from __future__ import annotations

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.error_handler import install_error_handlers

pytest.importorskip("selene_space_ashare")

import app.api.v1.ashare_concept as ac  # noqa: E402
from app.api.v1.ashare_concept import router as concept_router  # noqa: E402

URL = "/api/v1/ashare/concept-members"


def _make_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(concept_router, prefix="/api/v1")
    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_make_app())


def _membership(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["symbol", "name", "concept"])


@pytest.fixture()
def membership(monkeypatch):
    """Install a membership frame; returns the setter so tests can re-stub."""

    def install(rows: list[tuple[str, str, str]]) -> None:
        monkeypatch.setattr(
            "selene_space_ashare.concept_map.fetch_concept_membership",
            lambda force=False: _membership(rows),
        )

    install(
        [
            ("600000.SH", "浦发银行", "银行"),
            ("600036.SH", "招商银行", "银行"),
            ("000001.SZ", "平安银行", "银行"),
            ("600519.SH", "贵州茅台", "白酒"),
        ]
    )
    return install


@pytest.fixture()
def quotes(monkeypatch):
    """Snapshot with two priced banks; the third is deliberately absent."""

    rows = {
        "600000.SH": {"symbol": "600000.SH", "last": 10.0, "prev_close": 9.0},
        "600036.SH": {"symbol": "600036.SH", "last": 40.0, "prev_close": 42.0},
    }
    monkeypatch.setattr(ac, "_quote_index", lambda: (
        {
            "600000.SH": {"symbol": "600000.SH", "name": "浦发银行", "last": 10.0, "change_pct": 11.11, "amount": 1.0, "volume": 2.0},
            "600036.SH": {"symbol": "600036.SH", "name": "招商银行", "last": 40.0, "change_pct": -4.76, "amount": 3.0, "volume": 4.0},
        },
        "snapshot",
        "2026-10-25",
        None,
    ))


def test_members_are_listed_for_the_requested_concept(client, membership, quotes):
    resp = client.get(URL, params={"concept": "银行"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "ok"
    assert {r["symbol"] for r in data["rows"]} == {"600000.SH", "600036.SH", "000001.SZ"}
    assert data["total"] == 3


def test_duplicate_pairs_are_collapsed(client, membership, quotes):
    """The snapshot's 669 duplicate pairs must not double-count members."""
    membership(
        [
            ("600000.SH", "浦发银行", "银行"),
            ("600000.SH", "浦发银行", "银行"),
            ("600036.SH", "招商银行", "银行"),
        ]
    )
    data = client.get(URL, params={"concept": "银行"}).json()["data"]
    assert data["total"] == 2
    assert [r["symbol"] for r in data["rows"]].count("600000.SH") == 1


def test_rows_sorted_by_change_desc_with_unpriced_last(client, membership, quotes):
    rows = client.get(URL, params={"concept": "银行"}).json()["data"]["rows"]
    assert [r["symbol"] for r in rows] == ["600000.SH", "600036.SH", "000001.SZ"]
    assert rows[-1]["change_pct"] is None
    assert rows[-1]["priced"] is False


def test_unpriced_member_keeps_its_name_from_membership(client, membership, quotes):
    """A member with no quote must still render its name, not a bare code."""
    rows = client.get(URL, params={"concept": "银行"}).json()["data"]["rows"]
    unpriced = [r for r in rows if r["symbol"] == "000001.SZ"][0]
    assert unpriced["name"] == "平安银行"


def test_truncation_reports_total_and_flag(client, membership, quotes):
    membership(
        [(f"60{i:04d}.SH", f"票{i}", "大概念") for i in range(30)]
    )
    data = client.get(URL, params={"concept": "大概念", "limit": 10}).json()["data"]
    assert data["total"] == 30           # the whole concept
    assert data["returned"] == 10        # what the wire carried
    assert data["truncated"] is True
    assert len(data["rows"]) == 10


def test_truncation_keeps_the_strongest_not_the_first_rows(
    client, membership, monkeypatch
):
    """Truncation must rank, not slice frame order — otherwise 'top 1000' lies."""
    membership([(f"60{i:04d}.SH", f"票{i}", "大概念") for i in range(5)])
    quotes = {
        f"60{i:04d}.SH": {
            "symbol": f"60{i:04d}.SH",
            "change_pct": float(i),
            "last": 1.0,
            "name": f"票{i}",
            "amount": None,
            "volume": None,
        }
        for i in range(5)
    }
    monkeypatch.setattr(ac, "_quote_index", lambda: (quotes, "snapshot", "2026-10-25", None))
    data = client.get(URL, params={"concept": "大概念", "limit": 2}).json()["data"]
    assert [r["symbol"] for r in data["rows"]] == ["600004.SH", "600003.SH"]


def test_unknown_concept_is_empty_not_degraded(client, membership, quotes):
    data = client.get(URL, params={"concept": "不存在的概念"}).json()["data"]
    assert data["status"] == "empty"
    assert data["reason"] == "unknown_concept"
    assert data["total"] == 0


def test_membership_unavailable_degrades(client, monkeypatch):
    def boom(force=False):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(
        "selene_space_ashare.concept_map.fetch_concept_membership", boom
    )
    resp = client.get(URL, params={"concept": "银行"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "degraded"
    assert data["reason"] == "concept_membership_unavailable"


def test_no_quotes_still_returns_members(client, membership, monkeypatch):
    """After the close with no daily base, members must still be listed."""
    monkeypatch.setattr(
        ac, "_quote_index", lambda: ({}, "none", None, "kline_manager_unavailable")
    )
    data = client.get(URL, params={"concept": "银行"}).json()["data"]
    assert data["status"] == "ok"
    assert data["total"] == 3
    assert data["priced_count"] == 0
    assert data["quote_source"] == "none"
    assert all(r["change_pct"] is None for r in data["rows"])


def test_quote_source_is_reported(client, membership, quotes):
    data = client.get(URL, params={"concept": "银行"}).json()["data"]
    assert data["quote_source"] == "snapshot"
    assert data["as_of"] == "2026-10-25"


def test_daily_fallback_when_snapshot_absent(monkeypatch, membership):
    """Snapshot absent outside trading hours → daily base, source says so."""
    monkeypatch.setattr(ac, "_snapshot_rows", lambda: (None, "snapshot_empty"))
    monkeypatch.setattr(ac, "_daily_db", lambda: object())
    monkeypatch.setattr(ac, "_latest_session", lambda db: "2026-10-24")

    import selene_space_ashare.market_daily as md
    import selene_space_ashare.market_overview as mo

    monkeypatch.setattr(
        md, "market_daily_universe", lambda db, end_date=None: pd.DataFrame()
    )
    # Faithful to the real helper: quotes_from_daily emits last/prev_close but
    # **no** change_pct. Stubbing a change_pct here would let the endpoint pass
    # while shipping "—" for every row (the shape bug this test now guards).
    monkeypatch.setattr(
        mo,
        "quotes_from_daily",
        lambda frame, today: [
            {"symbol": "600000.SH", "name": "浦发银行", "last": 11.0, "prev_close": 10.0, "amount": None, "volume": None}
        ],
    )
    index, source, as_of, reason = ac._quote_index()
    assert source == "daily"
    assert as_of == "2026-10-24"
    assert reason is None
    assert index["600000.SH"]["change_pct"] == pytest.approx(10.0)


def test_snapshot_change_pct_is_derived_from_prev_close(monkeypatch):
    """The snapshot helper has no change_pct either — it must be computed."""
    monkeypatch.setattr(ac, "_snapshot_rows", lambda: ({"600000.SH": {}}, None))
    monkeypatch.setattr(ac, "_snapshot_session", lambda rows: "2026-10-25")

    import selene_space_ashare.market_overview as mo

    monkeypatch.setattr(
        mo,
        "quotes_from_snapshot",
        lambda rows: [
            {"symbol": "600000.SH", "name": "浦发银行", "last": 9.0, "prev_close": 10.0},
            # Suspended / bad prev_close: no change, and never a fake 0.
            {"symbol": "600036.SH", "name": "招商银行", "last": 40.0, "prev_close": None},
        ],
    )
    index, source, _, _ = ac._quote_index()
    assert source == "snapshot"
    assert index["600000.SH"]["change_pct"] == pytest.approx(-10.0)
    assert index["600036.SH"]["change_pct"] is None


def test_limit_bounds_are_validated(client, membership):
    """A bad limit is a 422 — a programming error, not an upstream condition."""
    assert client.get(URL, params={"concept": "银行", "limit": 0}).status_code == 422
    assert (
        client.get(URL, params={"concept": "银行", "limit": 999_999}).status_code == 422
    )


def test_concept_param_is_required(client, membership):
    assert client.get(URL).status_code == 422
