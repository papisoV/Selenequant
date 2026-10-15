"""Tests for /api/v1/ashare/* 4xx error contract.

Verifies the 3 endpoints no longer return 200 + misleading error dict —
empty upstream data -> 404 NOT_FOUND, data-source failure -> 502
EXCHANGE_ERROR. Functions are monkeypatched, no network access.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.error_handler import install_error_handlers

# Module-level import guard: selene_space_ashare is an optional package. The
# router import must succeed in environments where it is installed.
pytest.importorskip("selene_space_ashare")

from app.api.v1.ashare import router as ashare_router  # noqa: E402
from selene_space_ashare import data_fetcher as df_mod  # noqa: E402


def _make_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(ashare_router, prefix="/api/v1")
    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_make_app())


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame()


def test_factors_empty_data_returns_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(df_mod, "get_daily_klines", lambda *a, **k: _empty_df())

    resp = client.get("/api/v1/ashare/factors/600519")
    assert resp.status_code == 404
    body = resp.json()
    assert body["data"] is None
    assert body["error"]["code"] == "NOT_FOUND"


def test_factors_upstream_error_returns_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*a, **k) -> pd.DataFrame:
        raise RuntimeError("akshare network failure")

    monkeypatch.setattr(df_mod, "get_daily_klines", _boom)

    resp = client.get("/api/v1/ashare/factors/600519")
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "EXCHANGE_ERROR"


def test_regime_empty_data_returns_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(df_mod, "get_index_daily", lambda *a, **k: _empty_df())

    resp = client.get("/api/v1/ashare/regime")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_backtest_no_factor_data_returns_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(df_mod, "get_daily_klines", lambda *a, **k: _empty_df())

    resp = client.post("/api/v1/ashare/backtest")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_backtest_response_contains_universe_note(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """US-A2: backtest response flags survivorship bias via universe_note."""

    np.random.seed(3)
    n = 80
    dates = pd.date_range("2024-01-01", periods=n, freq="B")

    def _fake_klines(symbol: str, start_date: str = "20240101") -> pd.DataFrame:
        close = 10 + np.cumsum(np.random.randn(n) * 0.1)
        return pd.DataFrame(
            {
                "date": dates,
                "open": close,
                "high": close * 1.02,
                "low": close * 0.98,
                "close": close,
                "volume": np.full(n, 1_000_000.0),
            }
        )

    index_close = 3000 + np.cumsum(np.random.randn(n) * 10)
    index_daily = pd.DataFrame({"date": dates, "close": index_close})

    monkeypatch.setattr(df_mod, "get_daily_klines", _fake_klines)
    monkeypatch.setattr(df_mod, "get_index_daily", lambda *a, **k: index_daily)

    resp = client.post(
        "/api/v1/ashare/backtest?symbols=600519,000858&weight_method=equal"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "universe_note" in body
    assert "生存偏差" in body["universe_note"]


def test_backtest_invalid_factor_set_returns_400(client: TestClient) -> None:
    """US-B3: unknown factor_set -> VALIDATION_ERROR, not silent fallback."""

    resp = client.post("/api/v1/ashare/backtest?factor_set=bogus")
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "factor_set" in body["error"]["message"]


def test_backtest_factor_set_technical_fundamental_degrades_without_data(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """US-B3: technical_fundamental with empty financials cache still runs
    (falls back to technical factors only) instead of erroring out."""

    np.random.seed(3)
    n = 80
    dates = pd.date_range("2024-01-01", periods=n, freq="B")

    def _fake_klines(symbol: str, start_date: str = "20240101") -> pd.DataFrame:
        close = 10 + np.cumsum(np.random.randn(n) * 0.1)
        return pd.DataFrame(
            {
                "date": dates,
                "open": close,
                "high": close * 1.02,
                "low": close * 0.98,
                "close": close,
                "volume": np.full(n, 1_000_000.0),
            }
        )

    index_daily = pd.DataFrame(
        {
            "date": dates,
            "close": 3000 + np.cumsum(np.random.randn(n) * 10),
        }
    )

    monkeypatch.setattr(df_mod, "get_daily_klines", _fake_klines)
    monkeypatch.setattr(df_mod, "get_index_daily", lambda *a, **k: index_daily)

    import selene_space_ashare.fundamentals_pit as fp_mod

    monkeypatch.setattr(fp_mod, "load_financials", lambda *a, **k: [])

    resp = client.post(
        "/api/v1/ashare/backtest?symbols=600519,000858&weight_method=equal"
        "&factor_set=technical_fundamental"
    )
    assert resp.status_code == 200, resp.text
    assert "universe_note" in resp.json()


def test_financials_timeline_sorted_and_filtered(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import selene_space_ashare.fundamentals_pit as fp_mod

    records = [
        {"symbol": "000008", "year": 2026, "quarter": 1, "BPS": 3.1},
        {"symbol": "600519", "year": 2026, "quarter": 1, "BPS": 170.0},
        {"symbol": "000008", "year": 2025, "quarter": 4, "BPS": 2.9},
        {"symbol": "000008", "year": 2026, "quarter": 2, "BPS": 3.2},
    ]
    monkeypatch.setattr(fp_mod, "load_financials", lambda *a, **k: records)

    resp = client.get("/api/v1/ashare/financials/000008")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["n_records"] == 3
    assert [(r["year"], r["quarter"]) for r in body["records"]] == [
        (2025, 4),
        (2026, 1),
        (2026, 2),
    ]


def test_financials_timeline_unknown_symbol_returns_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import selene_space_ashare.fundamentals_pit as fp_mod

    monkeypatch.setattr(fp_mod, "load_financials", lambda *a, **k: [])

    resp = client.get("/api/v1/ashare/financials/000008")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def _snapshot_records() -> list[dict]:
    return [
        {"symbol": "AAA", "year": 2026, "quarter": 1, "BPS": 1.0},
        {"symbol": "BBB", "year": 2026, "quarter": 1, "BPS": 3.0},
        {"symbol": "CCC", "year": 2026, "quarter": 1, "BPS": 2.0},
        {"symbol": "AAA", "year": 2026, "quarter": 2, "BPS": 10.0},
        {"symbol": "AAA", "year": 2026, "quarter": 2, "YOYNI": 5.0},
    ]


def test_financials_snapshot_latest_quarter_desc_and_stats(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import selene_space_ashare.fundamentals_pit as fp_mod

    monkeypatch.setattr(fp_mod, "load_financials", _snapshot_records)

    resp = client.get("/api/v1/ashare/financials-snapshot?field=BPS")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # latest quarter in cache is 2026Q2; AAA only (BBB/CCC lack Q2 rows)
    assert (body["year"], body["quarter"]) == (2026, 2)
    assert body["n_universe"] == 1
    assert body["max"] == {"symbol": "AAA", "value": 10.0}

    resp = client.get(
        "/api/v1/ashare/financials-snapshot?field=BPS&year=2026&quarter=1"
    )
    body = resp.json()
    assert body["n_universe"] == 3
    assert [t["symbol"] for t in body["top"]] == ["BBB", "CCC", "AAA"]
    assert body["median"] == 2.0
    assert body["min"] == {"symbol": "AAA", "value": 1.0}


def test_financials_snapshot_empty_field_or_quarter_returns_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import selene_space_ashare.fundamentals_pit as fp_mod

    monkeypatch.setattr(fp_mod, "load_financials", _snapshot_records)

    resp = client.get("/api/v1/ashare/financials-snapshot?field=nonexistent")
    assert resp.status_code == 404

    resp = client.get(
        "/api/v1/ashare/financials-snapshot?field=BPS&year=2020&quarter=1"
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_fundamentals_backfill_validation_and_pass_through(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """US-B1: /fundamentals-backfill parses symbols/quarters and returns
    counts; empty symbols -> 404; baostock error -> 502."""

    captured: dict = {}

    def _fake_fetch(codes, q_list):
        captured["codes"] = codes
        captured["quarters"] = q_list
        return [{"symbol": s, "epsTTM": 1.0} for s in codes]

    import selene_space_ashare.fundamentals_pit as fp_mod

    monkeypatch.setattr(fp_mod, "fetch_financials_pit", _fake_fetch)

    resp = client.post(
        "/api/v1/ashare/fundamentals-backfill?symbols=600519,000858"
        "&quarters=2026-2,2026-1"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"n_records": 2, "n_symbols_with_data": 2, "requested": 2}
    assert captured["codes"] == ["600519", "000858"]
    assert captured["quarters"] == [(2026, 2), (2026, 1)]

    resp = client.post("/api/v1/ashare/fundamentals-backfill")
    assert resp.status_code == 404

    def _boom(codes, q_list):
        raise RuntimeError("baostock down")

    monkeypatch.setattr(fp_mod, "fetch_financials_pit", _boom)
    resp = client.post("/api/v1/ashare/fundamentals-backfill?symbols=600519")
    assert resp.status_code == 502


# ─── batch5: /style-report ─────────────────────────────────────────


def _style_constituents() -> pd.DataFrame:
    return pd.DataFrame(
        {"code": ["600519", "000002", "000001"], "name": ["a", "b", "c"]}
    )


def _style_build_result() -> dict:
    return {
        "forward_col": "fwd_5d",
        "lookback": 20,
        "n_symbols": 3,
        "n_days": 10,
        "styles": {
            "SIZE": {
                "regime": "leading",
                "cum_spread": 0.1,
                "n_days": 10,
                "spread_series": [{"date": "2026-01-01", "spread": 0.01}],
            },
            "BP": {"regime": "factor_absent"},
            "YOY_NI": {"regime": "factor_absent"},
        },
        "orthogonal": {
            "kept": [
                {
                    "factor": "SIZE",
                    "IC_mean": 0.0312,
                    "IC_std": 0.12,
                    "IR": 0.26,
                    "IC_pos_rate": 0.65,
                    "t_stat": 2.6,
                }
            ],
            "dropped": [{"factor": "BP", "reason": "orthogonal t=1.2 < 2.0"}],
            "order": ["SIZE", "BP", "YOY_NI"],
            "params": {"min_abs_t": 2.0, "max_factors": None},
        },
    }


@pytest.fixture()
def _clear_style_cache():
    from app.services import ashare_style_report as svc

    svc._cache.clear()
    yield
    svc._cache.clear()


def test_style_report_cache_hit_builds_once(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    _clear_style_cache: None,
) -> None:
    import app.services.ashare_style_report as svc

    calls = {"n": 0}

    def _fake_build(codes, start, end, fwd, lookback):
        calls["n"] += 1
        return _style_build_result()

    monkeypatch.setattr(svc, "_build", _fake_build)
    monkeypatch.setattr(df_mod, "get_hs300_constituents", _style_constituents)

    resp1 = client.get("/api/v1/ashare/style-report")
    assert resp1.status_code == 200, resp1.text
    body1 = resp1.json()
    assert body1["styles"]["SIZE"]["regime"] == "leading"
    assert body1["styles"]["BP"] == {"regime": "factor_absent"}
    assert body1["orthogonal"]["kept"][0]["factor"] == "SIZE"
    assert body1["orthogonal"]["dropped"][0]["factor"] == "BP"
    assert calls["n"] == 1

    resp2 = client.get("/api/v1/ashare/style-report")
    assert resp2.status_code == 200
    assert resp2.json() == body1
    assert calls["n"] == 1  # cache hit, no rebuild


def test_orthogonal_section_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import ashare_style_report as svc
    from app.services.ashare_style_report import _orthogonal_section

    # <3 candidate factors → None (orthogonalize needs >= 3)
    small = pd.DataFrame(
        {
            "date": ["2026-01-01"] * 2,
            "symbol": ["600519", "000001"],
            "fwd_5d": [0.01, 0.02],
            "SIZE": [1.0, 2.0],
        }
    )
    assert _orthogonal_section(small) is None

    # orthogonalize raising → None (never kills the whole build)
    bad = pd.DataFrame(
        {
            "date": ["2026-01-01"] * 6,
            "symbol": ["a", "b", "c"] * 2,
            "F1": [1.0, 2.0, 3.0, 1.0, 2.0, 3.0],
            "F2": [3.0, 2.0, 1.0, 3.0, 2.0, 1.0],
            "F3": [1.0, 3.0, 2.0, 1.0, 3.0, 2.0],
            "fwd_5d": [0.1, 0.2, 0.3, 0.1, 0.2, 0.3],
        }
    )
    monkeypatch.setattr(
        svc,
        "orthogonalize_greedy",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert _orthogonal_section(bad) is None


def test_style_report_refresh_forces_rebuild(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    _clear_style_cache: None,
) -> None:
    import app.services.ashare_style_report as svc

    calls = {"n": 0}

    def _fake_build(codes, start, end, fwd, lookback):
        calls["n"] += 1
        return _style_build_result()

    monkeypatch.setattr(svc, "_build", _fake_build)
    monkeypatch.setattr(df_mod, "get_hs300_constituents", _style_constituents)

    client.get("/api/v1/ashare/style-report")
    assert calls["n"] == 1

    resp = client.post("/api/v1/ashare/style-report/refresh")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rebuilt"] is True
    assert body["params"]["n_symbols"] == 3
    assert body["report"]["styles"]["SIZE"]["regime"] == "leading"
    assert calls["n"] == 2


def test_style_report_empty_data_404_negative_cache(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    _clear_style_cache: None,
) -> None:
    import app.services.ashare_style_report as svc

    calls = {"n": 0}

    def _fake_build(codes, start, end, fwd, lookback):
        calls["n"] += 1
        # all styles factor_absent -> negative-cache sentinel
        return {
            "forward_col": "fwd_5d",
            "lookback": 20,
            "n_symbols": 3,
            "n_days": 0,
            "styles": {
                "SIZE": {"regime": "factor_absent"},
                "BP": {"regime": "factor_absent"},
                "YOY_NI": {"regime": "factor_absent"},
            },
        }

    monkeypatch.setattr(svc, "_build", _fake_build)
    monkeypatch.setattr(df_mod, "get_hs300_constituents", _style_constituents)

    resp1 = client.get("/api/v1/ashare/style-report")
    assert resp1.status_code == 404
    assert resp1.json()["error"]["code"] == "NOT_FOUND"
    assert calls["n"] == 1

    resp2 = client.get("/api/v1/ashare/style-report")
    assert resp2.status_code == 404
    assert calls["n"] == 1  # negative cache: no second build


def test_style_report_constituents_unavailable_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, _clear_style_cache: None
) -> None:
    monkeypatch.setattr(
        df_mod, "get_hs300_constituents", lambda *a, **k: pd.DataFrame()
    )

    resp = client.get("/api/v1/ashare/style-report")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_style_report_upstream_error_502_not_cached(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    _clear_style_cache: None,
) -> None:
    import app.services.ashare_style_report as svc

    def _boom(codes, start, end, fwd, lookback):
        raise RuntimeError("kline cache corrupted")

    monkeypatch.setattr(svc, "_build", _boom)
    monkeypatch.setattr(df_mod, "get_hs300_constituents", _style_constituents)

    resp = client.get("/api/v1/ashare/style-report")
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "EXCHANGE_ERROR"
    assert not svc._cache  # errors are not cached


def test_backtest_invalid_pool_returns_400(client: TestClient) -> None:
    resp = client.post("/api/v1/ashare/backtest?pool=bogus")
    assert resp.status_code == 400
    assert "pool" in resp.json()["error"]["message"]


def test_backtest_csi500_unavailable_returns_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        df_mod, "get_csi500_constituents", lambda *a, **k: pd.DataFrame()
    )
    resp = client.post("/api/v1/ashare/backtest?pool=csi500")
    assert resp.status_code == 404
    assert "csi500" in resp.json()["error"]["message"]
