"""Tests for KlineSource + indicator precompute (US-007).

依赖仓库根 data/klines.db（selene_space_core.kline_paths 单一来源）；
缺失时整文件跳过。
"""

from __future__ import annotations

import pytest

from selene_space_core.kline_paths import repo_root
from selene_space_market_analysis.data_source import (
    DEFAULT_KLINES_DB,
    DOWNSAMPLE_MAX,
    KlineSource,
    WINDOW_BARS,
)

pytestmark = pytest.mark.skipif(
    not DEFAULT_KLINES_DB.exists(),
    reason="klines.db not present at repo-root data/",
)

_PROBE = ("binance", "futures", "BTC/USDT", "1h")


def test_default_db_is_the_canonical_repo_root_path():
    assert DEFAULT_KLINES_DB.name == "klines.db"
    assert DEFAULT_KLINES_DB.parent == repo_root() / "data"


def test_load_returns_recent_window_ascending():
    source = KlineSource()
    df = source.load(*_PROBE)
    assert not df.empty
    assert len(df) <= WINDOW_BARS
    for col in ("open", "high", "low", "close", "volume"):
        assert col in df.columns
    assert list(df["timestamp"]) == sorted(df["timestamp"])


def test_load_unknown_symbol_returns_empty():
    source = KlineSource()
    df = source.load("binance", "futures", "NOPE/FAKE", "1h")
    assert df.empty


def test_compute_bar_features_has_all_keys():
    source = KlineSource()
    df = source.load(*_PROBE)
    features = source.compute_bar_features(df)
    for key in ("close_last", "sma_50", "ema_20", "rsi_14", "atr_14", "adx_14"):
        assert key in features
    assert features["close_last"] is not None
    assert isinstance(features["close_last"], float)


def test_compute_bar_features_empty_df_returns_empty():
    source = KlineSource()
    assert (
        source.compute_bar_features(
            source.load("binance", "futures", "NOPE/FAKE", "1h")
        )
        == {}
    )


def test_build_input_data_assembles_prompt_shape():
    source = KlineSource()
    payload = source.build_input_data(*_PROBE)
    assert payload["symbol"] == "BTC/USDT"
    assert payload["interval"] == "1h"
    assert payload["exchange"] == "binance"
    assert payload["market_type"] == "futures"
    assert len(payload["klines"]) <= DOWNSAMPLE_MAX
    assert payload["klines"]
    first = payload["klines"][0]
    for key in ("timestamp", "open", "high", "low", "close", "volume"):
        assert key in first
    assert payload["bar_features"]["close_last"] is not None


def test_build_input_data_downsamples_large_window():
    source = KlineSource()
    payload = source.build_input_data(*_PROBE)
    # 真实窗口远大于 DOWNSAMPLE_MAX → 必然降采样
    assert len(payload["klines"]) == DOWNSAMPLE_MAX
    # 保留最新一根
    df = source.load(*_PROBE)
    assert payload["klines"][-1]["timestamp"] == int(df["timestamp"].iloc[-1])
