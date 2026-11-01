"""A股选股公式 — 7 模板纯函数化 (策略条件吸收自 public-reference 公开策略定义).

抄思路不抄实现:
- 输入: 多股市场 DataFrame (列 symbol/date/open/high/low/close/volume/turnover),
  turnover = 成交额(元). 输出: 命中的股票代码 list[str].
- asof=None 取 df 内最新交易日 (末根截面选股口径);
  asof=<交易日> 截断历史 (供 US-3 历史回测逐调仓日重放).
- 无 IO 副作用. 各公式按公开策略条件实现, NaN / 窗口约定在本仓内自洽.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

_REQUIRED = ["symbol", "date", "open", "high", "low", "close", "volume"]
_MIN_LEN = {"turtle_trade": 21, "high_tight_flag": 40, "uptrend_limit_down": 60}


def _slice(df: pd.DataFrame, asof: str | None) -> pd.DataFrame:
    """asof 截断; 缺关键列直接抛 KeyError (调用方决定是否容错)."""
    if df.empty:
        return df
    missing = [c for c in _REQUIRED if c not in df.columns]
    if missing:
        raise KeyError(f"缺列: {missing}")
    if asof is not None:
        df = df[df["date"] <= asof]
    return df.sort_values(["symbol", "date"])


def _scan(df: pd.DataFrame, asof: str | None, is_hit) -> list[str]:
    df = _slice(df, asof)
    if df.empty:
        return []
    out: list[str] = []
    for symbol, sd in df.groupby("symbol"):
        if is_hit(sd.reset_index(drop=True)):
            out.append(symbol)
    return out


# ── turtle_trade: 20日新高突破 + 成交额过亿 + 实体阳线且真涨 ──


def _turtle_hit(sd: pd.DataFrame) -> bool:
    if len(sd) < _MIN_LEN["turtle_trade"] or "turnover" not in sd.columns:
        return False
    prior_hi = sd["high"].shift(1).rolling(20).max()
    last = sd.iloc[-1]
    prev = sd.iloc[-2]
    if pd.isna(prior_hi.iloc[-1]):
        return False
    cleared = last["close"] > prior_hi.iloc[-1]
    traded_enough = last["turnover"] > 100_000_000
    return bool(
        cleared
        and traded_enough
        and last["close"] > last["open"]
        and last["close"] > prev["close"]
    )


def turtle_trade(df: pd.DataFrame, asof: str | None = None) -> list[str]:
    """海龟突破: 今收 > 前20日最高; 成交额>1亿; 阳线且今收>昨收."""
    return _scan(df, asof, _turtle_hit)


# ── ma_volume: 5/20 金叉 + 放量 1.5x ──


def _ma_volume_hit(sd: pd.DataFrame) -> bool:
    if len(sd) < 21:
        return False
    sd = sd.copy()
    sd["ma5"] = sd["close"].rolling(5).mean()
    sd["ma20"] = sd["close"].rolling(20).mean()
    sd["vol_ma20"] = sd["volume"].rolling(20).mean()
    last, prev = sd.iloc[-1], sd.iloc[-2]
    if any(
        pd.isna(x)
        for x in (
            prev["ma5"],
            prev["ma20"],
            last["ma5"],
            last["ma20"],
            last["vol_ma20"],
        )
    ):
        return False
    golden = prev["ma5"] < prev["ma20"] and last["ma5"] > last["ma20"]
    surge = last["volume"] > last["vol_ma20"] * 1.5
    return bool(golden and surge)


def ma_volume(df: pd.DataFrame, asof: str | None = None) -> list[str]:
    """均量金叉放量: 昨ma5<ma20 且 今ma5>ma20; 今量>20日均量1.5x."""
    return _scan(df, asof, _ma_volume_hit)


# ── high_tight_flag: 强动量后极度收敛缩量 ──


def _htf_hit(sd: pd.DataFrame) -> bool:
    if len(sd) < _MIN_LEN["high_tight_flag"]:
        return False
    t40, t10 = sd.tail(40), sd.tail(10)
    high40, low40 = t40["high"].max(), t40["low"].min()
    high10, low10 = t10["high"].max(), t10["low"].min()
    if low40 == 0 or low10 == 0:
        return False
    momentum = high40 / low40 > 1.6
    consolidation = high10 / low10 < 1.15
    high_level = low10 >= high40 * 0.8
    vol_ma20 = sd["volume"].iloc[-21:-1].mean()
    shrink = sd["volume"].iloc[-1] < vol_ma20 * 0.6
    return bool(momentum and consolidation and high_level and shrink)


def high_tight_flag(df: pd.DataFrame, asof: str | None = None) -> list[str]:
    """高旗形: 40日振幅>1.6; 10日振幅<1.15; 10日低≥40日高0.8; 缩量<0.6x."""
    return _scan(df, asof, _htf_hit)


# ── limit_up_shakeout: 昨涨停今放量收阴不破昨收 ──


def _shakeout_hit(sd: pd.DataFrame) -> bool:
    if len(sd) < 3:
        return False
    day_before, limit_day, follow = sd.iloc[-3], sd.iloc[-2], sd.iloc[-1]
    locked_up = limit_day["close"] >= day_before["close"] * 1.095
    closed_down = follow["close"] < follow["open"]
    heavy_volume = follow["volume"] > limit_day["volume"] * 2.0
    held_support = follow["low"] >= limit_day["close"]
    return bool(locked_up and closed_down and heavy_volume and held_support)


def limit_up_shakeout(df: pd.DataFrame, asof: str | None = None) -> list[str]:
    """涨停洗盘: 昨涨≥9.5%; 今收阴; 今量>昨量2x; 今低≥昨收."""
    return _scan(df, asof, _shakeout_hit)


# ── uptrend_limit_down: 上升趋势放量跌停 ──


def _limit_down_hit(sd: pd.DataFrame) -> bool:
    if len(sd) < _MIN_LEN["uptrend_limit_down"]:
        return False
    sd = sd.copy()
    sd["ma20"] = sd["close"].rolling(20).mean()
    sd["ma60"] = sd["close"].rolling(60).mean()
    sd["vol_ma20"] = sd["volume"].rolling(20).mean()
    prev, today = sd.iloc[-2], sd.iloc[-1]
    if any(pd.isna(x) for x in (prev["ma20"], prev["ma60"], today["vol_ma20"])):
        return False
    uptrend = prev["ma20"] > prev["ma60"]
    limit_down = today["close"] <= prev["close"] * 0.905
    surge = today["volume"] > today["vol_ma20"] * 2.0
    return bool(uptrend and limit_down and surge)


def uptrend_limit_down(df: pd.DataFrame, asof: str | None = None) -> list[str]:
    """上升趋势跌停: 昨ma20>ma60; 今收≤昨收×0.905; 量>20日均2x."""
    return _scan(df, asof, _limit_down_hit)


# ── rps_breakout: 横截面 120日动量 RPS≥90 且逼近前高 ──


def rps_breakout(
    df: pd.DataFrame,
    asof: str | None = None,
    rps_period: int = 120,
    rps_threshold: int = 90,
) -> list[str]:
    """极强动量突破: 120日涨幅横截面排位 pct≥90; 今收≥120日最高×0.90."""
    df = _slice(df, asof)
    if df.empty:
        return []
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"])
    d = d.sort_values(["symbol", "date"])
    d["base_close"] = d.groupby("symbol")["close"].shift(rps_period)
    d["trail_return"] = (d["close"] - d["base_close"]) / d["base_close"]

    latest = d["date"].max()
    latest_df = d[d["date"] == latest].copy().dropna(subset=["trail_return"])
    if latest_df.empty:
        return []
    latest_df["rank_pct"] = latest_df["trail_return"].rank(pct=True) * 100
    strong = latest_df[latest_df["rank_pct"] >= rps_threshold].copy()

    roll = (
        d.groupby("symbol")["high"]
        .rolling(window=rps_period, min_periods=rps_period // 2)
        .max()
        .reset_index(level=0, drop=True)
    )
    d["session_peak"] = roll
    latest_high = d[d["date"] == latest][["symbol", "session_peak"]]
    merged = strong.merge(latest_high, on="symbol")
    merged = merged.dropna(subset=["session_peak"])
    hit = merged[merged["close"] >= merged["session_peak"] * 0.90]
    return list(hit["symbol"])


# ── private_placement: 定增公告 (experimental, akshare 外部数据) ──


def private_placement(announcements: pd.DataFrame, lookback_days: int = 7) -> list[str]:
    """从增发公告表筛近期定向增发 (experimental: 数据源非 OHLCV, 需自备 akshare 快照).

    列约定: 发行方式 / 发行日期 / 股票代码. 依赖调用方提供数据, 本函数纯过滤无 IO.
    """
    if announcements is None or announcements.empty:
        return []
    if not {"发行方式", "发行日期", "股票代码"}.issubset(announcements.columns):
        return []
    df = announcements[announcements["发行方式"] == "定向增发"].copy()
    if df.empty:
        return []
    cutoff = date.today().toordinal() - lookback_days
    dates = pd.to_datetime(df["发行日期"], errors="coerce")
    ords = dates.map(lambda x: x.toordinal() if pd.notna(x) else None)
    df = df.assign(_ord=ords)
    df = df[df["_ord"].notna() & (df["_ord"] >= cutoff)]
    if df.empty:
        return []
    codes = df["股票代码"].astype(str).str.extract(r"(\d{6})")[0].dropna()
    return list(dict.fromkeys(codes))  # 去重保序
