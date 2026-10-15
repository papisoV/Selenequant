"""US-003: GET /alpha/formulas/{id}/ic-decay — IC 衰减图数据通路.

Locks:
- happy path with a real-shaped partition: full decay surface served
  (ic_by_lag dict / half_life / peak_ic / peak_lag / turnover /
  turnover_adjusted_ic / is_decayed)
- unknown formula → 404
- no kline data (KlineDB empty / raises) → 422 语义显形, never fake zeros
- degenerate signal (<10 finite values) → 422

(reward_std engine-side coverage lives in
packages/alpha-core/tests/test_engine_rank_step.py.)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException

from app.api.v1 import alpha


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(alpha, "_results_dir", tmp_path)
    yield


class _FakeKlineDB:
    """Yields a deterministic partition where a correlated signal can decay."""

    def __init__(self, df: pd.DataFrame | None):
        self._df = df

    def get_klines(self, exchange, market_type, symbol, interval):
        if self._df is None:
            return pd.DataFrame()
        return self._df.copy()


def _mk_partition(n: int = 300, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    return pd.DataFrame(
        {
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": np.abs(rng.normal(0, 1, n)) * 1e6,
        }
    )


def _write_formula(tmp_path, name: str = "f_ic") -> str:
    """Persist in the canonical on-disk shape: ``{results}/{id}/
    best_meme_strategy.json`` holding the bare tokens list (same as the
    engine's save + the formulas endpoint read)."""
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    import json as _json

    (d / "best_meme_strategy.json").write_text(
        _json.dumps([7]),  # MOMENTUM_REV — single feature, real-valued per bar
        encoding="utf-8",
    )
    return name


def _patch_klines(monkeypatch, db):
    import selene_space_market.kline_db as kline_db_mod

    monkeypatch.setattr(kline_db_mod, "KlineDB", lambda db_path: db)


@pytest.mark.asyncio
async def test_ic_decay_full_surface(tmp_path, monkeypatch) -> None:
    fid = _write_formula(tmp_path, "f_ic")
    _patch_klines(monkeypatch, _FakeKlineDB(_mk_partition()))

    resp = await alpha.get_ic_decay(
        fid,
        symbol="BTC/USDT",
        exchange="binance",
        market_type="futures",
        interval="1h",
        max_lag=20,
    )
    data = resp.data

    assert data["formula_id"] == fid
    assert data["source"] == "real"
    assert data["max_lag"] == 20
    surface = {
        "ic_by_lag",
        "half_life",
        "peak_ic",
        "peak_lag",
        "turnover",
        "turnover_adjusted_ic",
        "is_decayed",
    }
    assert surface <= set(data)
    # ic_by_lag: lag-int-keyed, covers 1..max_lag
    lags = {int(k) for k in data["ic_by_lag"]}
    assert lags == set(range(1, 21))
    assert isinstance(data["is_decayed"], bool)
    assert 0.0 <= data["turnover"] <= 1.0


@pytest.mark.asyncio
async def test_ic_decay_unknown_formula_404(tmp_path, monkeypatch) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await alpha.get_ic_decay("nope")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_ic_decay_no_data_422(tmp_path, monkeypatch) -> None:
    fid = _write_formula(tmp_path, "f_ic")
    _patch_klines(monkeypatch, _FakeKlineDB(None))  # empty partition

    with pytest.raises(HTTPException) as exc_info:
        await alpha.get_ic_decay(fid)
    assert exc_info.value.status_code == 422
    assert "synthetic" in exc_info.value.detail  # states why, not a dead end


@pytest.mark.asyncio
async def test_ic_decay_short_partition_422(tmp_path, monkeypatch) -> None:
    fid = _write_formula(tmp_path, "f_ic")
    _patch_klines(monkeypatch, _FakeKlineDB(_mk_partition(n=30)))  # < 50 bars

    with pytest.raises(HTTPException) as exc_info:
        await alpha.get_ic_decay(fid)
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_ic_decay_max_lag_param_respected(tmp_path, monkeypatch) -> None:
    fid = _write_formula(tmp_path, "f_ic")
    _patch_klines(monkeypatch, _FakeKlineDB(_mk_partition()))

    resp = await alpha.get_ic_decay(
        fid,
        symbol="BTC/USDT",
        exchange="binance",
        market_type="futures",
        interval="1h",
        max_lag=5,
    )
    assert resp.data["max_lag"] == 5
    assert {int(k) for k in resp.data["ic_by_lag"]} == set(range(1, 6))
