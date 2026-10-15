"""OOS gate / DSR field passthrough for /research/tuning/results.

The tuner (scripts/llm_param_tuner.py) writes oos_sharpe / oos_rejected /
all_rejected / deflated_sharpe_ratio into tuning_*.json. The summary endpoint
must forward them (frontend shows the OOS-decay badge) and old JSON files
without those keys must still parse with None/False defaults.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import research as research_module
from app.main import app


def _result(idx: int, **extra) -> dict:
    base = {
        "strategy_id": f"bb_v{idx}",
        "strategy_name": f"布林带{idx}",
        "interval": "4h",
        "exchange": "binance",
        "market_type": "futures",
        "symbol": "BTC/USDT",
        "best_sharpe": 2.0,
        "best_params": {"bb_len": 20},
        "n_trials": 10,
        "optimization_time": 1.0,
        "backtest_result": {"total_return": 0.1, "win_rate": 0.6, "total_trades": 50},
    }
    base.update(extra)
    return base


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(research_module, "TUNING_RESULTS_DIR", tmp_path)
    monkeypatch.setattr(research_module, "_tuning_cache", [])
    monkeypatch.setattr(research_module, "_tuning_cache_mtime", 0.0)
    return TestClient(app)


def test_oos_fields_passthrough(client: TestClient, tmp_path) -> None:
    payload = {
        "results": [
            _result(
                1,
                oos_sharpe=0.42,
                oos_rejected=True,
                all_rejected=False,
                deflated_sharpe_ratio=1.1,
            ),
        ],
    }
    (tmp_path / "tuning_20260923_000001.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    r = client.get("/api/v1/research/tuning/results")
    assert r.status_code == 200
    row = r.json()["data"][0]
    assert row["oos_sharpe"] == pytest.approx(0.42)
    assert row["oos_rejected"] is True
    assert row["all_rejected"] is False
    assert row["deflated_sharpe_ratio"] == pytest.approx(1.1)


def test_legacy_json_without_oos_keys_defaults(client: TestClient, tmp_path) -> None:
    payload = {"results": [_result(2)]}
    (tmp_path / "tuning_20260923_000002.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    r = client.get("/api/v1/research/tuning/results")
    assert r.status_code == 200
    row = r.json()["data"][0]
    assert row["oos_sharpe"] is None
    assert row["oos_rejected"] is False
    assert row["all_rejected"] is False
    assert row["deflated_sharpe_ratio"] is None


# === P0-3 pre_noop_fix + P1-4 annualization_factor (US-003/US-004) =========


def test_pre_noop_fix_true_for_old_files(client: TestClient, tmp_path) -> None:
    """AC3-1: 文件名日期 < 20261001 → pre_noop_fix True."""
    payload = {"results": [_result(3)]}
    (tmp_path / "tuning_20260825_141110.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    r = client.get("/api/v1/research/tuning/results")
    assert r.status_code == 200
    assert r.json()["data"][0]["pre_noop_fix"] is True


def test_pre_noop_fix_false_for_post_fix_files(client: TestClient, tmp_path) -> None:
    """AC3-2: 文件名日期 >= 20261001 → pre_noop_fix False."""
    payload = {"results": [_result(4)]}
    (tmp_path / "tuning_20261001_144452.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    r = client.get("/api/v1/research/tuning/results")
    assert r.status_code == 200
    assert r.json()["data"][0]["pre_noop_fix"] is False


def test_empty_dir_returns_empty_list(client: TestClient, tmp_path) -> None:
    """AC3-3: 空目录 → 200 + data=[](现有行为回归)."""
    r = client.get("/api/v1/research/tuning/results")
    assert r.status_code == 200
    assert r.json()["data"] == []


def test_endpoint_is_read_only_on_files(client: TestClient, tmp_path) -> None:
    """AC3-4: 端点纯读 — 调用前后 tmp_path 文件字节不变."""
    payload = {"results": [_result(5)]}
    p = tmp_path / "tuning_20260825_141110.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    before = p.read_bytes()
    r = client.get("/api/v1/research/tuning/results")
    assert r.status_code == 200
    assert p.read_bytes() == before


def test_annualization_us_stock_daily(client: TestClient, tmp_path) -> None:
    """AC4-1: yahoo/us_stock/AAPL/1d → 252."""
    payload = {
        "results": [
            _result(
                6,
                exchange="yahoo",
                market_type="us_stock",
                symbol="AAPL",
                interval="1d",
            )
        ]
    }
    (tmp_path / "tuning_20261002_000001.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    r = client.get("/api/v1/research/tuning/results")
    row = r.json()["data"][0]
    assert row["annualization_factor"] == 252
    assert row["pre_noop_fix"] is False


def test_annualization_crypto_daily_and_hourly(client: TestClient, tmp_path) -> None:
    """AC4-2: binance BTC/USDT 1d → 365;1h → 8760."""
    payload = {"results": [_result(7, interval="1d"), _result(8, interval="1h")]}
    (tmp_path / "tuning_20261002_000002.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    r = client.get("/api/v1/research/tuning/results")
    factors = {
        row["strategy_id"]: row["annualization_factor"] for row in r.json()["data"]
    }
    assert factors["bb_v7"] == 365
    assert factors["bb_v8"] == 8760


def test_annualization_equity_unknown_interval_none_vs_crypto_fallback(
    client: TestClient, tmp_path
) -> None:
    """AC4-3(拆分断言): equity+未知interval → None(raise);
    crypto+未知interval → 365(兜底不 raise)."""
    payload = {
        "results": [
            _result(
                9,
                exchange="yahoo",
                market_type="us_stock",
                symbol="AAPL",
                interval="2d",
            ),
            _result(10, interval="13x"),
        ]
    }
    (tmp_path / "tuning_20261002_000003.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    r = client.get("/api/v1/research/tuning/results")
    factors = {
        row["strategy_id"]: row["annualization_factor"] for row in r.json()["data"]
    }
    assert factors["bb_v9"] is None
    assert factors["bb_v10"] == 365


def test_annualization_legacy_json_defaults_binance(
    client: TestClient, tmp_path
) -> None:
    """AC4-4: 旧 JSON 无 exchange/market_type 键 → 默认 binance/futures 走 365 系."""
    payload = {
        "results": [
            {
                "strategy_id": "legacy1",
                "strategy_name": "旧结果",
                "interval": "1d",
                "best_sharpe": 1.0,
                "best_params": {},
                "n_trials": 5,
                "optimization_time": 0.5,
                "backtest_result": {"total_return": 0.1, "win_rate": 0.5},
            }
        ]
    }
    (tmp_path / "tuning_20260801_000001.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    r = client.get("/api/v1/research/tuning/results")
    row = r.json()["data"][0]
    assert row["annualization_factor"] == 365
    assert row["pre_noop_fix"] is True
