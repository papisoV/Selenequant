"""为回归测试生成确定性的 BTC/USDT 1h 样本数据。

来源: 最初移植自作者本人的既有项目 binance-quant(自有版权, 无第三方许可问题)。
2026-10-05 cleanroom 重写: 表达层另写(累乘抽成 helper、开盘价用切片拼接、
列用逐列赋值而非一次成 dict),**数值逐位不变** —— 4 个测试硬编码了这里的
收盘价, 改一个比特就会红。

跑法: 直接执行本文件会把结果写到同目录的 btc_usdt_1h_sample.parquet。
"""

from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd

#: 默认产出 200 根 1h K 线; 起点 2024-01-01 00:00 UTC。
DEFAULT_BARS = 200
DEFAULT_SEED = 42
DEFAULT_PRICE = 40_000.0
DEFAULT_START_MS = 1_704_067_200_000
HOUR_MS = 3_600_000

#: 对数收益的分布参数(轻微上行漂移 + 日线级波动)。
_DRIFT = 0.0002
_VOL = 0.015
#: 上下影线的宽度区间。
_WICK_RANGE = (0.002, 0.010)
#: 成交量区间。
_VOLUME_RANGE = (50.0, 500.0)
#: 价格保留几位小数(测试按这个精度比对)。
_PRICE_DP = 2
#: 首根开盘相对首价的下浮比例。
_FIRST_OPEN_RATIO = 0.999

_COLUMNS = (
    "timestamp",
    "exchange",
    "symbol",
    "interval",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "closed",
)


def _cumulative_closes(
    rng: np.random.RandomState, n_bars: int, first: float
) -> np.ndarray:
    """对数收益的累积路径。

    必须逐根相乘: ``np.cumsum`` 的浮点累加次序与逐根连乘不同, 结果在第 2 位
    小数上就会漂, 而本模块的 4 个调用方硬编码了这些收盘价。首根不带收益。
    """
    steps = rng.normal(loc=_DRIFT, scale=_VOL, size=n_bars)
    closes = np.empty(n_bars, dtype=np.float64)
    closes[0] = first
    for idx in range(1, n_bars):
        closes[idx] = closes[idx - 1] * np.exp(steps[idx])
    return closes


def _spread_deviations(rng: np.random.RandomState, n_bars: int) -> np.ndarray:
    """影线宽度(两组独立抽样)。"""
    lo, hi = _WICK_RANGE
    return rng.uniform(lo, hi, size=n_bars)


def generate_deterministic_btc_data(
    n_bars: int = DEFAULT_BARS,
    seed: int = DEFAULT_SEED,
    initial_price: float = DEFAULT_PRICE,
    start_ts_ms: int = DEFAULT_START_MS,
    interval_ms: int = HOUR_MS,
) -> pd.DataFrame:
    """用带种子的随机游走生成确定性 OHLCV 数据。

    同样的 (n_bars, seed, ...) 永远给出同一张表 —— 回归测试依赖这一点。

    Args:
        n_bars: K 线根数。
        seed: 随机种子。
        initial_price: 首根收盘价。
        start_ts_ms: 起始时间戳(毫秒)。
        interval_ms: 相邻两根的间隔(毫秒)。

    Returns:
        DataFrame, 列见模块级 ``_COLUMNS``。
    """
    rng = np.random.RandomState(seed)  # noqa: NPY002 — 显式种子, 求可复现

    # 抽样次序必须与历史一致(normal → 上影 → 下影 → 量), 否则种子错位、数值全变。
    closes = _cumulative_closes(rng, n_bars, initial_price)
    opens = np.concatenate(([initial_price * _FIRST_OPEN_RATIO], closes[:-1]))

    up_wick = _spread_deviations(rng, n_bars)
    down_wick = _spread_deviations(rng, n_bars)
    highs = np.maximum(opens, closes) * (1.0 + up_wick)
    lows = np.minimum(opens, closes) * (1.0 - down_wick)

    volumes = rng.uniform(*_VOLUME_RANGE, size=n_bars)
    timestamps = start_ts_ms + np.arange(n_bars) * interval_ms

    frame = pd.DataFrame(index=pd.RangeIndex(n_bars))
    frame["timestamp"] = timestamps.astype(np.int64)
    frame["exchange"] = "backtest"
    frame["symbol"] = "BTC/USDT"
    frame["interval"] = "1h"
    frame["open"] = np.round(opens, _PRICE_DP)
    frame["high"] = np.round(highs, _PRICE_DP)
    frame["low"] = np.round(lows, _PRICE_DP)
    frame["close"] = np.round(closes, _PRICE_DP)
    frame["volume"] = np.round(volumes, _PRICE_DP)
    frame["closed"] = True
    return frame.loc[:, list(_COLUMNS)]


if __name__ == "__main__":
    target = pathlib.Path(__file__).parent / "btc_usdt_1h_sample.parquet"
    sample = generate_deterministic_btc_data()
    sample.to_parquet(target, engine="pyarrow", index=False)
    print(f"Written {len(sample)} rows to {target}")
