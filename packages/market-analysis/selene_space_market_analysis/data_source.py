"""K 线数据源 + 指标预计算（US-007）。

主源是仓库根的 data/klines.db（只读，路径由 selene_space_core.kline_paths
决定），指标复用 strategy-engine 的 ADX/ATR/EMA/RSI/SMA 预计算，供编排器
Stage1 输入组装（降采样窗口）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from selene_space_core.kline_paths import default_klines_db_path
from selene_space_strategy.indicators.adx import ADX
from selene_space_strategy.indicators.atr import ATR
from selene_space_strategy.indicators.ema import EMA
from selene_space_strategy.indicators.rsi import RSI
from selene_space_strategy.indicators.sma import SMA

from .structure_features import compute_structure_features

DEFAULT_KLINES_DB = default_klines_db_path()

# 预计算窗口：足够覆盖 SMA50 / ADX14 的 lookback。
WINDOW_BARS = 500
# 下发给 LLM 的 K 线窗口（降采样后条数）。
DOWNSAMPLE_MAX = 60

_OHLCV_COLS = ["open", "high", "low", "close", "volume"]


def _last_value(series: pd.Series) -> float | None:
    """取序列最后一个值；NaN 归一为 None，float 四舍五入保证回放稳定。"""
    if series.empty or pd.isna(series.iloc[-1]):
        return None
    return round(float(series.iloc[-1]), 6)


def _downsample(df: pd.DataFrame, max_bars: int) -> pd.DataFrame:
    """均匀降采样，始终保留最新一根（尾点在内）。"""
    n = len(df)
    if n <= max_bars:
        return df
    idx = sorted({(n - 1) * i // (max_bars - 1) for i in range(max_bars)})
    return df.iloc[idx]


class KlineSource:
    """从 klines.db 只读加载 K 线并预计算指标特征。"""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path else DEFAULT_KLINES_DB

    @property
    def db_path(self) -> Path:
        return self._db_path

    def load(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        *,
        limit: int = WINDOW_BARS,
    ) -> pd.DataFrame:
        """加载最近 ``limit`` 根 K 线，时间升序。无数据返回空表。"""
        conn = sqlite3.connect(str(self._db_path))
        try:
            df = pd.read_sql_query(
                "SELECT timestamp, open, high, low, close, volume FROM ("
                "  SELECT timestamp, open, high, low, close, volume FROM klines"
                "  WHERE exchange = ? AND market_type = ? AND symbol = ? AND interval = ?"
                "  ORDER BY timestamp DESC LIMIT ?"
                ") ORDER BY timestamp ASC",
                conn,
                params=(exchange, market_type, symbol, interval, limit),
            )
        finally:
            conn.close()
        if df.empty:
            return df
        for col in _OHLCV_COLS:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    def compute_bar_features(self, df: pd.DataFrame) -> dict[str, Any]:
        """对完整窗口预计算指标，返回最新值（确定性基线，供 LLM 原样回显）。

        US-301：在指标基础上并入结构特征（几何 + 结构，见 structure_features.py），
        全部为确定性预计算，LLM 无权修改；round-6 保证回放稳定。
        """
        if df.empty or "close" not in df.columns:
            return {}
        features: dict[str, Any] = {
            "close_last": _last_value(df["close"]),
            "sma_50": _last_value(SMA(period=50).calculate(df)),
            "ema_20": _last_value(EMA(period=20).calculate(df)),
            "rsi_14": _last_value(RSI(period=14).calculate(df)),
            "atr_14": _last_value(ATR(period=14).calculate(df)),
            "adx_14": _last_value(ADX(period=14).calculate(df)),
        }
        features.update(compute_structure_features(df))
        return features

    def build_input_data(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        *,
        max_klines: int = DOWNSAMPLE_MAX,
    ) -> dict[str, Any]:
        """组装 Stage1 输入：符号元信息 + 降采样 K 线 + 指标特征。"""
        df = self.load(exchange, market_type, symbol, interval)
        klines = _downsample(df, max_klines)
        return {
            "exchange": exchange,
            "market_type": market_type,
            "symbol": symbol,
            "interval": interval,
            "klines": [
                {
                    "timestamp": int(row["timestamp"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                }
                for _, row in klines.iterrows()
            ],
            "bar_features": self.compute_bar_features(df),
        }


__all__ = [
    "DEFAULT_KLINES_DB",
    "DOWNSAMPLE_MAX",
    "KlineSource",
    "WINDOW_BARS",
]
