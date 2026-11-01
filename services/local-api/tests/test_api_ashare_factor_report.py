"""US-106: POST /ashare/factor-report — IC report on the product surface.

The endpoint reuses build_factor_panel + generate_factor_report behind the
F8 cache pattern (bare dict + in-process cache + _EMPTY negative sentinel +
sha1 key, no TTL). These tests pin the three contracts that matter:
the report blocks are returned, neutralize toggles the *_neutral columns,
and a second identical call is served from cache rather than rebuilt.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.error_handler import install_error_handlers

pytest.importorskip("selene_space_ashare")

from app.api.v1.ashare import router as ashare_router  # noqa: E402
from app.services import ashare_factor_report as svc  # noqa: E402

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

CODES = ["600519", "000858", "000001", "601318", "600036", "000333"]


def _klines(symbol: str) -> pd.DataFrame:
    np.random.seed(300 + int(symbol))
    n = 120
    close = 10 + np.cumsum(np.random.randn(n) * 0.1)
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="B").strftime("%Y%m%d"),
            "open": close,
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close,
            "volume": np.abs(np.random.randn(n)) * 1e6 + 1e5,
        }
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    svc._cache.clear()
    yield
    svc._cache.clear()


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(
        "selene_space_ashare.data_fetcher.get_daily_klines",
        lambda sym, **kw: _klines(sym),
    )
    # no industry map -> neutralize is a no-op merge; keeps the fixture
    # hermetic without a network call
    monkeypatch.setattr(
        "selene_space_ashare.data_fetcher.fetch_industry_map",
        lambda: pd.DataFrame(columns=["symbol", "industry"]),
    )
    monkeypatch.setattr(
        "selene_space_ashare.fundamentals_pit.load_financials", lambda *a, **k: []
    )

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(ashare_router, prefix="/api/v1")
    return TestClient(app)


def test_report_returns_ic_blocks(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/ashare/factor-report", params={"symbols": ",".join(CODES)}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in (
        "ic_report",
        "quantile_returns",
        "factor_correlation",
        "n_days",
        "n_symbols",
    ):
        assert key in body, key
    assert body["ic_report"], "ic_report must not be empty"
    assert body["n_symbols"] == len(CODES)


def test_second_call_hits_cache(client: TestClient, monkeypatch) -> None:
    """The panel build is the expensive part; a repeat call must not rebuild."""
    calls = {"n": 0}
    real = svc.build_factor_panel

    def _counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(svc, "build_factor_panel", _counting)

    params = {"symbols": ",".join(CODES)}
    assert client.post("/api/v1/ashare/factor-report", params=params).status_code == 200
    assert client.post("/api/v1/ashare/factor-report", params=params).status_code == 200
    assert calls["n"] == 1, f"expected 1 build, got {calls['n']}"


def test_negative_cache_on_empty_data(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unusable klines -> 404, and the doomed build is remembered so the
    next call does not pay for it again."""
    calls = {"n": 0}

    def _boom(*_a, **_k):
        calls["n"] += 1
        raise ValueError("no usable kline data for any symbol")

    monkeypatch.setattr(svc, "build_factor_panel", _boom)

    params = {"symbols": ",".join(CODES)}
    r1 = client.post("/api/v1/ashare/factor-report", params=params)
    r2 = client.post("/api/v1/ashare/factor-report", params=params)
    assert r1.status_code == 404, r1.text
    assert r2.status_code == 404, r2.text
    assert calls["n"] == 1, f"negative cache should absorb the retry, got {calls['n']}"


def test_symbols_or_pool_required(client: TestClient) -> None:
    resp = client.post("/api/v1/ashare/factor-report")
    assert resp.status_code == 400, resp.text


def test_invalid_pool_rejected(client: TestClient) -> None:
    resp = client.post("/api/v1/ashare/factor-report", params={"pool": "nope"})
    assert resp.status_code == 400, resp.text


def test_explicit_factors_subset(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/ashare/factor-report",
        params={"symbols": ",".join(CODES), "factors": "RSI_14D,MOMENTUM_1M"},
    )
    assert resp.status_code == 200, resp.text
    names = {row["factor"] for row in resp.json()["ic_report"]}
    assert names <= {"RSI_14D", "MOMENTUM_1M"}
    assert names, "expected the requested factors in the report"


def test_neutralize_switch_changes_columns(client: TestClient) -> None:
    """With an industry column present, neutralize=True adds *_neutral
    columns; neutralize=False must not."""
    industry = pd.DataFrame(
        {"symbol": CODES, "industry": ["a", "a", "b", "b", "c", "c"]}
    )
    import selene_space_ashare.data_fetcher as dfm

    orig = dfm.fetch_industry_map
    dfm.fetch_industry_map = lambda: industry
    try:
        on = client.post(
            "/api/v1/ashare/factor-report",
            params={"symbols": ",".join(CODES), "neutralize": "true"},
        )
        off = client.post(
            "/api/v1/ashare/factor-report",
            params={"symbols": ",".join(CODES), "neutralize": "false"},
        )
    finally:
        dfm.fetch_industry_map = orig

    assert on.status_code == off.status_code == 200, (on.text, off.text)
    on_cols = set(on.json()["ic_report"][0])
    off_cols = set(off.json()["ic_report"][0])
    assert any(c.endswith("_neutral") for c in on_cols), sorted(on_cols)
    assert not any(c.endswith("_neutral") for c in off_cols), sorted(off_cols)


def test_small_universe_flags_all_nan_ic(client: TestClient) -> None:
    """calc_ic needs >=10 usable names per cross-section (factor_eval.py:33).
    A 6-symbol universe therefore yields an all-NaN report that looks exactly
    like "these factors have no predictive power". The panel built fine, so it
    is not an error — but the response must say why the numbers are blank."""
    resp = client.post(
        "/api/v1/ashare/factor-report", params={"symbols": ",".join(CODES)}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ic_all_nan"] is True
    assert "10" in body["ic_all_nan_reason"]


def test_wide_universe_does_not_flag_all_nan(client: TestClient) -> None:
    wide = [f"6000{i:02d}" for i in range(20)]
    resp = client.post(
        "/api/v1/ashare/factor-report", params={"symbols": ",".join(wide)}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ic_all_nan"] is False
    assert any(
        row.get("IC_mean") is not None and row["IC_mean"] == row["IC_mean"]
        for row in body["ic_report"]
    ), "expected at least one finite IC on a wide universe"


def test_ic_series_shape_and_alignment(client: TestClient) -> None:
    """The frontend plots a per-date IC line, so the response must carry a
    date axis plus one value array per factor, each aligned to that axis."""
    wide = [f"6000{i:02d}" for i in range(20)]
    resp = client.post(
        "/api/v1/ashare/factor-report", params={"symbols": ",".join(wide)}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ic_series = body["ic_series"]
    assert isinstance(ic_series, dict), type(ic_series)
    assert ic_series["series"], "expected at least one factor IC series"
    n_dates = len(ic_series["dates"])
    assert n_dates > 1, "need a real time axis to draw a line"
    for entry in ic_series["series"]:
        assert len(entry["values"]) == n_dates, entry["name"]
        assert any(v is not None for v in entry["values"]), entry["name"]
    # every series must be one of the factors the report actually evaluated
    evaluated = {row["factor"] for row in body["ic_report"]}
    assert {e["name"] for e in ic_series["series"]} <= evaluated


def test_ic_series_empty_on_tiny_universe(client: TestClient) -> None:
    """Below the calc_ic 10-name threshold every daily IC is NaN. The series
    must degrade to empty rather than shipping a grid of nulls the chart
    would render as a blank panel with no explanation."""
    resp = client.post(
        "/api/v1/ashare/factor-report", params={"symbols": ",".join(CODES)}
    )
    assert resp.status_code == 200, resp.text
    ic_series = resp.json()["ic_series"]
    assert ic_series["series"] == []
    assert ic_series["dates"] == []


def test_ic_series_axis_is_order_independent() -> None:
    """The shared date axis must be the union of every factor's dates, not the
    first factor's. A factor with a longer warm-up (more leading NaN) would
    otherwise truncate the others' earlier points, so the chart's content would
    depend on the order the factors happen to be listed in."""
    import pandas as pd

    rng = __import__("numpy").random.default_rng(1)
    dates = pd.date_range("2024-01-01", periods=40, freq="D")
    rows = []
    for i, d in enumerate(dates):
        for s in range(12):
            f = rng.normal()
            rows.append(
                {
                    "date": d,
                    "symbol": f"S{s:02d}",
                    "LATE": float("nan") if i < 15 else f,  # 15-day warm-up
                    "EARLY": f,
                    "fwd_5d": f * 0.1 + rng.normal(scale=0.4),
                }
            )
    panel = pd.DataFrame(rows)

    late_first = svc._build_ic_series(panel, ["LATE", "EARLY"], "fwd_5d")
    early_first = svc._build_ic_series(panel, ["EARLY", "LATE"], "fwd_5d")

    assert late_first["dates"] == early_first["dates"]
    by_name_a = {s["name"]: s["values"] for s in late_first["series"]}
    by_name_b = {s["name"]: s["values"] for s in early_first["series"]}
    assert by_name_a == by_name_b, "series must not depend on factor order"
    # the long-coverage factor keeps every point regardless of ordering
    assert sum(v is not None for v in by_name_a["EARLY"]) == len(dates)
    # the short-coverage factor keeps its own points, blank only in its warm-up
    assert sum(v is not None for v in by_name_a["LATE"]) == len(dates) - 15


def test_refresh_endpoint_rebuilds(client: TestClient, monkeypatch) -> None:
    """POST /factor-report/refresh must drop the cached entry and rebuild:
    a warm cache should NOT serve the refresh call."""
    calls = {"n": 0}
    real = svc.build_factor_panel

    def _counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(svc, "build_factor_panel", _counting)
    params = {"symbols": ",".join(CODES)}

    assert client.post("/api/v1/ashare/factor-report", params=params).status_code == 200
    assert calls["n"] == 1, "first call builds once"
    # warm hit: no rebuild
    assert client.post("/api/v1/ashare/factor-report", params=params).status_code == 200
    assert calls["n"] == 1, "cached call must not rebuild"

    r = client.post("/api/v1/ashare/factor-report/refresh", params=params)
    assert r.status_code == 200, r.text
    assert calls["n"] == 2, "refresh must bypass the cache and rebuild"
    body = r.json()
    assert body["rebuilt"] is True
    assert body["params"]["n_symbols"] == len(CODES)
    assert body["report"], "refresh must return the rebuilt report"


def test_refresh_endpoint_requires_universe(client: TestClient) -> None:
    r = client.post("/api/v1/ashare/factor-report/refresh")
    assert r.status_code == 400, r.text


def test_refresh_cache_key_matches_get(client: TestClient, monkeypatch) -> None:
    """refresh must evict the SAME key GET populated — differing params would
    evict nothing and leave the stale entry, so a following GET would still
    serve the old result."""
    real = svc.build_factor_panel
    calls = {"n": 0}

    def _counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(svc, "build_factor_panel", _counting)
    params = {"symbols": ",".join(CODES), "neutralize": "true"}

    client.post("/api/v1/ashare/factor-report", params=params)
    assert calls["n"] == 1
    client.post("/api/v1/ashare/factor-report/refresh", params=params)
    assert calls["n"] == 2, "refresh rebuilt"
    # the entry is repopulated, so the next GET is a cache hit again
    client.post("/api/v1/ashare/factor-report", params=params)
    assert calls["n"] == 2, "refresh must leave a warm entry for the same key"
