"""US-103: POST /ashare/portfolio-backtest — the batch10 portfolio engine
wired to the product surface.

Covers the four contracts that make the endpoint safe to expose:
  200 + three metric blocks (portfolio / benchmark / excess),
  pool=csi500 universe resolution,
  zero settleable windows -> 404 with an explanatory message (never an
  all-NaN 200, which is what backtest_periodic_portfolio +
  calc_performance_metrics would otherwise produce),
  empty panel -> 404.
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
import selene_space_ashare.data_fetcher as df_mod  # noqa: E402
from selene_space_ashare import portfolio_rebal as pr_mod  # noqa: E402

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

N_DAYS = 90
CODES = ["600519", "000858", "000001", "601318"]


def _klines(symbol: str, start_date: str = "20240101") -> pd.DataFrame:
    np.random.seed(200 + int(symbol))
    n = N_DAYS
    close = 10 + np.cumsum(np.random.randn(n) * 0.1)
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="B").strftime("%Y%m%d"),
            "open": close,
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close,
            "volume": np.full(n, 1_000_000.0),
        }
    )


def _index_daily() -> pd.DataFrame:
    np.random.seed(11)
    n = N_DAYS
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="B").strftime("%Y%m%d"),
            "close": 3000 + np.cumsum(np.random.randn(n) * 10),
        }
    )


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(df_mod, "get_daily_klines", _klines)
    monkeypatch.setattr(df_mod, "get_index_daily", lambda *a, **k: _index_daily())

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(ashare_router, prefix="/api/v1")
    return TestClient(app)


def test_returns_three_metric_blocks(client: TestClient) -> None:
    resp = client.post("/api/v1/ashare/portfolio-backtest", params={"n_long": 2})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert set(body) == {
        "portfolio_metrics",
        "benchmark_metrics",
        "excess_metrics",
        "config",
    }
    for block in ("portfolio_metrics", "benchmark_metrics", "excess_metrics"):
        assert isinstance(body[block], dict) and body[block], block
    # bare dict, no envelope (envelope is error-only on this router)
    assert "data" not in body and "meta" not in body


def test_windows_table_is_not_returned(client: TestClient) -> None:
    """The raw window table stays server-side; only the metric blocks ship."""
    resp = client.post("/api/v1/ashare/portfolio-backtest", params={"n_long": 2})
    assert resp.status_code == 200, resp.text
    assert "windows" not in resp.json()


def test_csi500_pool_resolves_universe(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    constituents = pd.DataFrame({"code": CODES})
    monkeypatch.setattr(
        df_mod, "get_csi500_constituents", lambda: constituents, raising=False
    )
    resp = client.post(
        "/api/v1/ashare/portfolio-backtest", params={"pool": "csi500", "n_long": 2}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["config"]["n_long"] == 2


def test_zero_windows_returns_404_not_all_nan_200(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """n_long larger than any scoreable cross-section yields zero windows.
    Silently returning an all-NaN 200 would render an empty page."""
    resp = client.post("/api/v1/ashare/portfolio-backtest", params={"n_long": 9999})
    assert resp.status_code == 404, resp.text
    assert "零个可结算窗口" in resp.text


def test_empty_panel_returns_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(df_mod, "get_daily_klines", lambda *a, **k: pd.DataFrame())
    resp = client.post("/api/v1/ashare/portfolio-backtest", params={"n_long": 2})
    assert resp.status_code == 404, resp.text


def test_rebalance_days_must_match_forward_horizon(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/ashare/portfolio-backtest",
        params={"n_long": 2, "rebalance_days": 7, "forward_col": "fwd_5d"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unparseable_forward_col_is_rejected(client: TestClient) -> None:
    """_forward_horizon returns 0 (never raises) for a name it cannot parse,
    so the endpoint guards explicitly rather than letting a bogus column
    reach the engine as a zero-step."""
    resp = client.post(
        "/api/v1/ashare/portfolio-backtest",
        params={"n_long": 2, "forward_col": "fwd_zzd"},
    )
    assert resp.status_code == 400, resp.text
    assert "forward_col" in resp.text


def test_engine_validation_error_maps_to_400(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ValueError from the engine (e.g. no detectable factor columns)
    must surface as a validation error, not a 502 upstream error."""

    def _boom(*_a, **_k):
        raise ValueError("no ALL_FACTORS columns found in panel")

    monkeypatch.setattr(pr_mod, "run_portfolio_backtest", _boom)
    resp = client.post("/api/v1/ashare/portfolio-backtest", params={"n_long": 2})
    assert resp.status_code == 400, resp.text
    assert "no ALL_FACTORS columns" in resp.text


# ── US-105: stock_weight dispatch axis ───────────────────────────────


@pytest.mark.parametrize("sw", ["equal", "slsqp", "inv_vol"])
def test_stock_weight_paths_do_not_explode(client: TestClient, sw: str) -> None:
    """All three stock-weight schemes run end to end and report themselves
    back in config."""
    resp = client.post(
        "/api/v1/ashare/portfolio-backtest",
        params={"n_long": 2, "stock_weight": sw},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["config"]["stock_weight"] == sw


def test_invalid_stock_weight_is_rejected(client: TestClient) -> None:
    """A typo must 400, not silently degrade to equal weighting under a
    label the caller reads as risk parity."""
    resp = client.post(
        "/api/v1/ashare/portfolio-backtest",
        params={"n_long": 2, "stock_weight": "invol"},
    )
    assert resp.status_code == 400, resp.text
    assert "stock_weight" in resp.text


def test_equal_stock_weight_matches_default_output(client: TestClient) -> None:
    """The new axis must not perturb the default path: explicit equal ==
    the pre-US-105 default, numerically."""
    base = client.post("/api/v1/ashare/portfolio-backtest", params={"n_long": 2})
    explicit = client.post(
        "/api/v1/ashare/portfolio-backtest",
        params={"n_long": 2, "stock_weight": "equal"},
    )
    assert base.status_code == explicit.status_code == 200
    for block in ("portfolio_metrics", "benchmark_metrics", "excess_metrics"):
        a, b = base.json()[block], explicit.json()[block]
        for key in a:
            if isinstance(a[key], float) and a[key] != a[key]:
                assert b[key] != b[key], (block, key)
            else:
                assert a[key] == pytest.approx(b[key], rel=1e-12, abs=1e-15), (
                    block,
                    key,
                )


def test_slsqp_and_inv_vol_require_close_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a close column the risk history is unavailable; the engine
    must raise rather than fall back to equal silently."""
    from selene_space_ashare.portfolio_rebal import run_portfolio_backtest as _run

    panel = pd.DataFrame(
        {
            "date": ["20240102"],
            "symbol": ["600519"],
            "composite_score": [1.0],
            "fwd_5d": [0.01],
        }
    )
    with pytest.raises(ValueError, match="close"):
        _run(panel, factor_names=["composite_score"], stock_weight="slsqp")


def test_slsqp_infeasible_n_long_logs_warning(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """max_w (default 0.06) makes slsqp infeasible below n_long=17, where the
    optimizer silently returns equal weights. The run still succeeds (engine
    math is unchanged), so the only defence is the warning."""
    import logging

    with caplog.at_level(logging.WARNING):
        resp = client.post(
            "/api/v1/ashare/portfolio-backtest",
            params={"n_long": 2, "stock_weight": "slsqp"},
        )
    assert resp.status_code == 200, resp.text
    assert "infeasible" in caplog.text


def test_slsqp_feasible_n_long_does_not_warn(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    with caplog.at_level(logging.WARNING):
        client.post(
            "/api/v1/ashare/portfolio-backtest",
            params={"n_long": 20, "stock_weight": "slsqp"},
        )
    assert "infeasible" not in caplog.text
