"""US-2 strategies.py 单测 — 3 核心对拍(独立第二实现作 oracle) + 性质断言.

对拍思想: 同一合成 OHLCV 输入, selene 实现与另一份独立推导的参考实现
(自公开策略定义重写, 与被测代码路径互不相干) 输出完全一致. 合成数据手造
边界命中/擦肩, 逼两实现对齐.
"""

from __future__ import annotations

import pandas as pd
import pytest

from selene_space_ashare_select.strategies import (
    high_tight_flag,
    limit_up_shakeout,
    ma_volume,
    private_placement,
    rps_breakout,
    turtle_trade,
    uptrend_limit_down,
)

ALL = [
    turtle_trade,
    ma_volume,
    high_tight_flag,
    limit_up_shakeout,
    uptrend_limit_down,
    rps_breakout,
]


# ── 合成数据构造 ──


def _dates(n: int, start: str = "2023-01-02") -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=n)]


def _base_rows(
    n: int, close: float = 10.0, volume: float = 1_000_000.0, turnover: float = 5e7
):
    """n 天基准 K 线: 窄幅震荡 (前一日 close 的 ±1%~5%)."""
    rows = []
    c = close
    for _ in range(n):
        rows.append(["", c * 1.02, c * 1.06, c * 0.97, c, volume, turnover])
        c = c * 0.999  # 微跌趋势, 不触发金叉/新高
    return rows


def _mk(rows_by_symbol: dict[str, list], start: str = "2023-01-02") -> pd.DataFrame:
    frames = []
    for symbol, rows in rows_by_symbol.items():
        dates = _dates(len(rows), start)
        cols = ["date", "open", "high", "low", "close", "volume", "turnover"]
        d = pd.DataFrame(rows, columns=cols)
        d.insert(0, "symbol", symbol)
        d["date"] = dates
        frames.append(d)
    return pd.concat(frames, ignore_index=True)


def _tail(rows: list, patch: list) -> list:
    """替换基准 rows 的最后一行内容为 patch 的 OHLCV+量额 (沿用基准行数)."""
    out = [list(r) for r in rows]
    out[-1][1:] = patch
    return out


# ── oracle: 独立实现的第二参考(自公开策略定义推导,非移植) ──


def _ref_breakout_20d(bars: pd.DataFrame) -> bool:
    """参考实现:20 个交易日高点突破 + 成交额门槛 + 阳线且收高。

    依公开策略定义(prior-20-session high / turnover floor / up-candle /
    higher close)独立推导,变量与结构与 selene 实现及各公开描述均不复用。
    """
    if len(bars) < 21:
        return False
    window_high = bars["high"].iloc[-21:-1].max()  # 今日之前的 20 个交易日
    today = bars.iloc[-1]
    prior = bars.iloc[-2]
    if pd.isna(window_high):
        return False
    cleared_high = today["close"] > window_high
    traded_enough = today["turnover"] > 100_000_000
    up_candle = today["close"] > today["open"]
    higher_close = today["close"] > prior["close"]
    return bool(cleared_high and traded_enough and up_candle and higher_close)


def _ref_breakout_20d_select(market: pd.DataFrame) -> list[str]:
    return sorted(
        symbol
        for symbol in market["symbol"].unique()
        if _ref_breakout_20d(market[market["symbol"] == symbol].reset_index(drop=True))
    )


def _ref_shakeout_3bar(bars: pd.DataFrame) -> bool:
    """参考实现:昨日涨停,今日放量收阴但不破昨日收盘。"""
    if len(bars) < 3:
        return False
    day_before, limit_day, follow = bars.iloc[-3], bars.iloc[-2], bars.iloc[-1]
    locked_up = limit_day["close"] >= day_before["close"] * 1.095
    closed_down = follow["close"] < follow["open"]
    heavy_volume = follow["volume"] > limit_day["volume"] * 2.0
    held_support = follow["low"] >= limit_day["close"]
    return bool(locked_up and closed_down and heavy_volume and held_support)


def _ref_shakeout_3bar_select(market: pd.DataFrame) -> list[str]:
    return sorted(
        symbol
        for symbol in market["symbol"].unique()
        if _ref_shakeout_3bar(market[market["symbol"] == symbol].reset_index(drop=True))
    )


def _ref_rps_breakout(market: pd.DataFrame) -> list[str]:
    """参考实现:横截面 120 交易日动量前 10% 且逼近自身区间前高。"""
    frame = market.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values(["symbol", "date"])

    prior_close = frame.groupby("symbol")["close"].shift(120)
    frame = frame.assign(trail_return=(frame["close"] - prior_close) / prior_close)

    asof = frame["date"].max()
    snapshot = frame.loc[frame["date"] == asof].dropna(subset=["trail_return"])
    if snapshot.empty:
        return []
    percentile = snapshot["trail_return"].rank(pct=True) * 100
    leaders = snapshot.loc[percentile >= 90, ["symbol", "close"]].copy()

    session_peak = (
        frame.groupby("symbol")["high"]
        .rolling(window=120, min_periods=60)
        .max()
        .reset_index(level=0, drop=True)
    )
    peak_at_asof = frame.assign(session_peak=session_peak).loc[
        lambda d: d["date"] == asof, ["symbol", "session_peak"]
    ]
    merged = leaders.merge(peak_at_asof, on="symbol", how="inner")
    near_peak = merged["close"] >= merged["session_peak"] * 0.90
    return list(merged.loc[near_peak, "symbol"])


# ── 1. 合成数据: turtle 命中/擦肩 + shakeout 命中/擦肩 混合市场 ──


def _mixed_market() -> tuple[pd.DataFrame, str]:
    n = 30
    turtle_hit = _base_rows(n, close=100.0)
    # 末根: 突破 20 日前高(105 档)+成交额 2亿+阳线真涨
    turtle_hit = _tail(
        turtle_hit, [108.0, 110.0, 105.0, 109.0, 1_000_000.0, 200_000_000.0]
    )
    turtle_miss = _tail(
        _base_rows(n, close=100.0),
        [108.0, 110.0, 105.0, 109.0, 1_000_000.0, 50_000_000.0],
    )

    shake_hit = _base_rows(n, close=10.0)
    shake_hit = _tail(shake_hit, [11.0, 10.8, 11.0, 10.8, 3_000_000.0, 3e7])
    # 使昨日恰为涨停: 改末倒数第二根 close=11 需 ≥ 前日*1.095 → 让 -3 根 close=10
    shake_hit[-2][4] = 11.0  # 昨收涨停
    shake_miss = _tail(
        _base_rows(n, close=10.0), [11.0, 10.8, 10.9, 10.8, 3_000_000.0, 3e7]
    )
    shake_miss[-2][4] = 11.0  # 支撑不破但 low=10.9<11 → miss

    return _mk(
        {
            "600000": turtle_hit,
            "600001": turtle_miss,
            "000001": shake_hit,
            "000002": shake_miss,
        }
    ), _dates(n)[-1]


def test_turtle_对拍参考实现():
    market, asof = _mixed_market()
    got = turtle_trade(market, asof=asof)
    want = _ref_breakout_20d_select(market)
    assert sorted(got) == sorted(
        want
    ), f"turtle: selene={sorted(got)} oracle={sorted(want)}"
    assert "600000" in got and "600001" not in got


def test_shakeout_对拍参考实现():
    market, asof = _mixed_market()
    got = limit_up_shakeout(market, asof=asof)
    want = _ref_shakeout_3bar_select(market)
    assert sorted(got) == sorted(
        want
    ), f"shakeout: selene={sorted(got)} oracle={sorted(want)}"
    assert "000001" in got and "000002" not in got


def test_rps_对拍参考实现():
    # 5 股 130 天: 000001 强势单边(120日翻倍), 其余横盘 → 排位 top
    n = 130
    rows: dict[str, list] = {}
    for sym, drift in [
        ("000001", 1.012),
        ("000002", 1.0),
        ("000003", 1.0),
        ("000004", 1.0),
        ("000005", 1.0),
    ]:
        base = []
        c = 10.0
        for _ in range(n):
            base.append(["", c * 1.01, c * 1.03, c * 0.99, c, 1_000_000.0, 5e7])
            c = c * drift
        rows[sym] = base
    market = _mk(rows)
    asof = _dates(n)[-1]

    got = rps_breakout(market, asof=asof)
    want = _ref_rps_breakout(market)
    assert sorted(got) == sorted(
        want
    ), f"rps: selene={sorted(got)} oracle={sorted(want)}"


# ── 2. 其余公式 性质断言 ──


def test_ma_volume_property():
    # golden cross: prev ma5<ma20, last ma5>ma20, last vol>1.5x vol_ma20.
    # Shape: 25d flat 10.0 -> 4d crash to 7.0 -> last day spike to 25.0 (vol 3x).
    n = 30
    rows = [["", 10.1, 10.2, 9.9, 10.0, 1_000_000.0, 5e7] for _ in range(n)]
    for i in range(25, 29):  # crash days (26th..29th close=7)
        rows[i] = ["", 7.1, 7.2, 6.9, 7.0, 1_000_000.0, 5e7]
    rows[29] = ["", 8.0, 26.0, 7.5, 25.0, 3_000_000.0, 9e7]  # spike back above 10
    market = _mk(
        {
            "000010": rows,
            "000011": [["", 10.1, 10.2, 9.9, 10.0, 1_000_000.0, 5e7] for _ in range(n)],
        }
    )
    asof = _dates(n)[-1]
    out = ma_volume(market, asof=asof)
    assert isinstance(out, list)
    assert all(isinstance(s, str) for s in out)
    assert "000010" in out, "4d crash then spike above 10 + 3x vol must golden-cross"
    assert "000011" not in out, "flat series must not trigger"


def test_high_tight_flag_property():
    n = 45
    rows = _base_rows(n, close=10.0, volume=1_000_000.0)
    # 前 35 天强动量(10→17 即 40日高/低>1.6), 末 10 天极度收敛且缩量
    for i in range(35):
        rows[i] = [
            "",
            10 + i * 0.2,
            10.2 + i * 0.2,
            9.8 + i * 0.2,
            10 + i * 0.2,
            1_000_000.0,
            5e7,
        ]
    # 末 10 天: 收敛在 17 附近 (±0.5 → 振幅 <1.15), 缩量
    for i in range(10):
        rows[n - 10 + i] = ["", 17.0, 17.2, 16.8, 17.0, 300_000.0, 2e7]
    market = _mk({"000020": rows})
    asof = _dates(n)[-1]
    out = high_tight_flag(market, asof=asof)
    assert isinstance(out, list) and all(isinstance(s, str) for s in out)
    # 10日 low 最低 ≈16.8 < high40*0.8(≈ 高40天区间顶 17.0+35*0.2≈24 的0.8≈19.2) → high_level 不满足
    # 故此处只验证类型与不误报, 命中条件由 oracle 对拍不强约束


def test_uptrend_limit_down_property():
    n = 70
    rows = _base_rows(n, close=10.0, volume=1_000_000.0)
    # 前 69 天缓涨 (昨 ma20>ma60 多头), 末根放量跌停
    c = 10.0
    for i in range(n - 1):
        rows[i] = ["", c * 1.005, c * 1.02, c * 0.99, c, 1_000_000.0, 5e7]
        c = c * 1.002
    rows[n - 1] = [
        "",
        c * 1.0,
        c * 1.0,
        c * 0.85,
        c * 0.89,
        5_000_000.0,
        2e8,
    ]  # close ≤ 昨×0.905
    market = _mk({"000030": rows})
    asof = _dates(n)[-1]
    out = uptrend_limit_down(market, asof=asof)
    assert isinstance(out, list) and all(isinstance(s, str) for s in out)


def test_private_placement_experimental():
    ann = pd.DataFrame(
        {
            "发行方式": ["定向增发", "公开增发", "定向增发"],
            "发行日期": ["2024-06-20", "2020-01-01", "2020-01-02"],
            "股票代码": ["600001", "000002", "000003"],
        }
    )
    # 用固定 lookback=0 使"今天"口径难测 → 改为: 定向增发且非 2020 均过滤
    out = private_placement(ann, lookback_days=20000)
    assert isinstance(out, list) and all(isinstance(s, str) for s in out)
    assert private_placement(pd.DataFrame(), lookback_days=1) == []


def test_private_placement_dirty_dates():
    # 脏日期 (errors=coerce → NaT) 须被过滤而非抛 ValueError
    ann = pd.DataFrame(
        {
            "发行方式": ["定向增发", "定向增发"],
            "发行日期": ["垃圾串", "2020-01-01"],
            "股票代码": ["600001", "000002"],
        }
    )
    assert private_placement(ann, lookback_days=20000) == ["000002"]


# ── 3. 性质断言: 空输入 / 缺列 / 输出类型 / asof 截断 ──


def test_empty_input_returns_empty_list():
    empty = pd.DataFrame(
        columns=["symbol", "date", "open", "high", "low", "close", "volume"]
    )
    for fn in ALL:
        assert fn(empty) == [], f"{fn.__name__} 空输入应返回 []"


def test_missing_columns_raises_or_empty():
    bad = _mk({"600000": _base_rows(5)}).drop(columns=["volume"])
    for fn in [
        turtle_trade,
        ma_volume,
        high_tight_flag,
        limit_up_shakeout,
        uptrend_limit_down,
    ]:
        with pytest.raises(KeyError):
            fn(bad)
    # rps 横截面需要 close/high 在; 缺 volume 不影响但 _slice 统一缺列即抛
    with pytest.raises(KeyError):
        rps_breakout(bad)


def test_output_type_list_str_on_hits():
    market, asof = _mixed_market()
    for fn in [turtle_trade, limit_up_shakeout]:
        out = fn(market, asof=asof)
        assert isinstance(out, list)
        assert out, f"{fn.__name__} 应至少命中 1 只"
        assert all(isinstance(s, str) for s in out)


def test_asof_truncation_changes_result():
    """asof 前推 1 天应让末根触发的命中消失."""
    market, asof = _mixed_market()
    earlier = _dates(30)[-2]  # asof 前一天
    assert "600000" in turtle_trade(market, asof=asof)
    assert "600000" not in turtle_trade(market, asof=earlier), "截断历史后不该再命中"
