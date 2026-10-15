"""Tests for /api/v1/fund-ingest/* endpoints (fund-ingest L3 吸收)。

路由读 SQLite 快照表,monkeypatch ``selene_space_fund_ingest.store.connect``
指向 tmp 种子库;DB 打不开 → 500(通用文案)。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.error_handler import install_error_handlers

pytest.importorskip("selene_space_fund_ingest")

from app.api.v1.fund_ingest import router as fund_ingest_router  # noqa: E402
from selene_space_fund_ingest import store as fi_store  # noqa: E402


def _make_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(fund_ingest_router, prefix="/api/v1")
    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_make_app())


def _seed(
    monkeypatch: pytest.MonkeyPatch, table: str, rows: list[dict], tmp_path
) -> None:
    db_path = tmp_path / "t.db"
    conn = fi_store.connect(db_path)
    fi_store.upsert_rows(conn, table, rows)
    conn.close()
    _orig_connect = fi_store.connect  # patch 前捕获原函数,避免 lambda 自递归
    monkeypatch.setattr(fi_store, "connect", lambda *a, **k: _orig_connect(db_path))


def test_cb_populated_returns_200(client, monkeypatch, tmp_path) -> None:
    _seed(
        monkeypatch,
        "cb_bonds",
        [
            {
                "code": "110047",
                "name": "山鹰转债",
                "price": 116.02,
                "premium_pct": 12.7,
            },
            {
                "code": "127003",
                "name": "海印转债",
                "price": 108.055,
                "premium_pct": 33.6,
            },
        ],
        tmp_path,
    )
    resp = client.get("/api/v1/fund-ingest/cb")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert {r["code"] for r in body} == {"110047", "127003"}


def test_cb_double_low_sort_ascending(client, monkeypatch, tmp_path) -> None:
    _seed(
        monkeypatch,
        "cb_bonds",
        [
            {"code": "A", "price": 116.0, "premium_pct": 12.0},  # 双低 128
            {"code": "B", "price": 100.0, "premium_pct": 5.0},  # 双低 105
            {"code": "C", "price": 110.0, "premium_pct": None},  # 缺值排除
        ],
        tmp_path,
    )
    resp = client.get("/api/v1/fund-ingest/cb", params={"sort": "double_low", "top": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert [r["code"] for r in body] == ["B", "A"]
    assert body[0]["double_low"] == 105.0


def test_lof_premium_excludes_null_and_sorts_desc(
    client, monkeypatch, tmp_path
) -> None:
    _seed(
        monkeypatch,
        "lof_etf_spot",
        [
            {
                "code": "X1",
                "category": "LOF",
                "price": 1.25,
                "nav": 1.20,
                "premium_pct": 4.17,
            },
            {
                "code": "X2",
                "category": "LOF",
                "price": 0.95,
                "nav": 1.00,
                "premium_pct": -5.0,
            },
            {
                "code": "X3",
                "category": "LOF",
                "price": 1.10,
                "nav": None,
                "premium_pct": None,
            },
        ],
        tmp_path,
    )
    resp = client.get("/api/v1/fund-ingest/lof-premium")
    assert resp.status_code == 200
    body = resp.json()
    assert [r["code"] for r in body] == ["X2", "X1"]  # |5.0| > |4.17|,X3 被排除


def test_etf_spot_filters_category(client, monkeypatch, tmp_path) -> None:
    _seed(
        monkeypatch,
        "lof_etf_spot",
        [
            {"code": "E1", "category": "ETF", "price": 3.21},
            {"code": "L1", "category": "LOF", "price": 1.25},
        ],
        tmp_path,
    )
    resp = client.get("/api/v1/fund-ingest/etf-spot")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["code"] == "E1"


def test_reits_sorted_by_change_pct_desc(client, monkeypatch, tmp_path) -> None:
    _seed(
        monkeypatch,
        "reits_spot",
        [
            {"code": "R1", "name": "REIT-A", "price": 2.5, "change_pct": -1.2},
            {"code": "R2", "name": "REIT-B", "price": 3.1, "change_pct": 0.8},
        ],
        tmp_path,
    )
    resp = client.get("/api/v1/fund-ingest/reits")
    assert resp.status_code == 200
    body = resp.json()
    assert [r["code"] for r in body] == ["R2", "R1"]


def test_db_failure_returns_500(client, monkeypatch) -> None:
    def _boom(*a, **k):
        raise RuntimeError("db locked")

    monkeypatch.setattr(fi_store, "connect", _boom)
    resp = client.get("/api/v1/fund-ingest/reits")
    assert resp.status_code == 500
    assert "REITs" in resp.json()["detail"]
