"""结构特征预计算（US-301）。

几何特征（实体/影线/收盘位/重叠/内包/缺口/跟随）与结构特征
（swing/区间位置/突破/计数），全部采用通用市场术语自研命名
（如 high_low_count / range_position / breakout_flag），中文注释释义。

这些特征是确定性预计算，供 KlineSource.compute_bar_features 扩展
bar_features 基线；LLM 必须原样回显，cheat_anchor 据此拦截篡改。
浮点值一律 round 6 位（继承 data_source._last_value 的 round-6 纪律），
保证回放稳定、杜绝精确值误报。

合规：命名按包内 PROVENANCE.md 隔离表，不引入参考实现字段名。
"""
from __future__ import annotations

from typing import Any

import pandas as pd

# 区间位置/突破判定的默认回看根数（确定性，冻结后不可变）。
RANGE_LOOKBACK = 20
# swing（分形）判定的左右各回看根数。
SWING_K = 2

# 全部结构特征键（供测试与锚定基线文档化使用，冻结后不可变）。
STRUCTURE_FEATURE_KEYS: tuple[str, ...] = (
    # 几何 — 实体
    "body_ratio_last",
    "avg_body_ratio",
    "bullish_count",
    # 几何 — 影线
    "upper_wick_ratio_last",
    "lower_wick_ratio_last",
    "long_upper_wick_count",
    "long_lower_wick_count",
    # 几何 — 收盘位
    "close_position_last",
    "avg_close_position",
    "close_above_mid_count",
    # 几何 — 重叠
    "overlap_ratio_last",
    "avg_overlap_ratio",
    # 几何 — 内包
    "inside_bar_count",
    "inside_bar_ratio",
    "last_inside_bar",
    # 几何 — 缺口
    "gap_up_count",
    "gap_down_count",
    "gap_count",
    "last_gap_up",
    "last_gap_down",
    # 几何 — 跟随
    "follow_up_count",
    "follow_down_count",
    "up_close_count",
    # 结构 — swing
    "swing_high_count",
    "swing_low_count",
    "last_swing_high",
    "last_swing_low",
    # 结构 — 区间位置
    "range_high",
    "range_low",
    "range_position",
    "range_width_ratio",
    # 结构 — 突破
    "breakout_flag",
    "breakout_direction",
    "recent_breakout_count",
    # 结构 — 计数
    "bar_count",
    "new_high_count",
    "new_low_count",
    "high_low_count",
)


# ---------------------------------------------------------------------------
# 基础量（逐 K 线）
# ---------------------------------------------------------------------------


def _bar_range(df: pd.DataFrame) -> pd.Series:
    """单根全距 high - low。"""
    return df["high"] - df["low"]


def _bar_body(df: pd.DataFrame) -> pd.Series:
    """单根实体长度 |close - open|。"""
    return (df["close"] - df["open"]).abs()


def _upper_wick(df: pd.DataFrame) -> pd.Series:
    """单根上影线长度 high - max(open, close)。"""
    return df["high"] - df[["open", "close"]].max(axis=1)


def _lower_wick(df: pd.DataFrame) -> pd.Series:
    """单根下影线长度 min(open, close) - low。"""
    return df[["open", "close"]].min(axis=1) - df["low"]


def _ratio(num: pd.Series, den: pd.Series) -> pd.Series:
    """安全比例：分母为 0 → 0.0；结果截断到 [0,1]。"""
    out = num / den
    out = out.fillna(0.0)
    return out.clip(lower=0.0, upper=1.0)


def _scalar(series: pd.Series) -> float:
    """取序列最后一个值；NaN 归一为 0.0。"""
    v = series.iloc[-1]
    if pd.isna(v):
        return 0.0
    return float(v)


def _round6(x: float) -> float:
    """round 6 位（与 data_source._last_value 同纪律，防锚定精确值误报）。"""
    return round(float(x), 6)


# ---------------------------------------------------------------------------
# 几何特征
# ---------------------------------------------------------------------------


def body_geometry(df: pd.DataFrame) -> dict[str, Any]:
    """实体特征：最后一根实体占全距比例 / 窗口平均实体比例 / 阳线计数。"""
    ratio = _ratio(_bar_body(df), _bar_range(df))
    return {
        "body_ratio_last": _round6(_scalar(ratio)),
        "avg_body_ratio": _round6(float(ratio.mean())),
        "bullish_count": int((df["close"] > df["open"]).sum()),
    }


def wick_geometry(df: pd.DataFrame) -> dict[str, Any]:
    """影线特征：上下影线比例与"影线显著（长于实体）"计数。

    上影线显著 = 上方抛压；下影线显著 = 下方承接。
    """
    rng = _bar_range(df)
    body = _bar_body(df)
    upper = _upper_wick(df)
    lower = _lower_wick(df)
    return {
        "upper_wick_ratio_last": _round6(_scalar(_ratio(upper, rng))),
        "lower_wick_ratio_last": _round6(_scalar(_ratio(lower, rng))),
        "long_upper_wick_count": int((upper > body).sum()),
        "long_lower_wick_count": int((lower > body).sum()),
    }


def close_position_geometry(df: pd.DataFrame) -> dict[str, Any]:
    """收盘位特征：收盘在全距中的位置（0=最低，1=最高）。

    平均收盘位 >0.5 表示收盘偏强（买方主导）。
    """
    pos = _ratio(df["close"] - df["low"], _bar_range(df))
    return {
        "close_position_last": _round6(_scalar(pos)),
        "avg_close_position": _round6(float(pos.mean())),
        "close_above_mid_count": int((pos > 0.5).sum()),
    }


def overlap_geometry(df: pd.DataFrame) -> dict[str, Any]:
    """重叠特征：相邻两根 K 线区间重叠量占较小全距的比例。

    重叠高 = 连续推进（价格在前一根范围内延续）；重叠低 = 大阳/大阴扩张。
    """
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    n = len(highs)
    ratios: list[float] = []
    for i in range(1, n):
        ov = min(highs[i], highs[i - 1]) - max(lows[i], lows[i - 1])
        if ov <= 0:
            ratios.append(0.0)
            continue
        den = min(highs[i] - lows[i], highs[i - 1] - lows[i - 1])
        ratios.append(ov / den if den > 0 else 0.0)
    last = ratios[-1] if ratios else 0.0
    avg = sum(ratios) / len(ratios) if ratios else 0.0
    return {
        "overlap_ratio_last": _round6(last),
        "avg_overlap_ratio": _round6(avg),
    }


def inside_bar_geometry(df: pd.DataFrame) -> dict[str, Any]:
    """内包特征：当前根全距完全落在上一根全距内的 K 线数（含端点）。

    内包 = 收敛/蓄势，常出现在突破前的压缩区。
    """
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    n = len(highs)
    cnt = 0
    for i in range(1, n):
        if lows[i] >= lows[i - 1] and highs[i] <= highs[i - 1]:
            cnt += 1
    last = int(n >= 2 and lows[-1] >= lows[-2] and highs[-1] <= highs[-2])
    ratio = cnt / (n - 1) if n > 1 else 0.0
    return {
        "inside_bar_count": int(cnt),
        "inside_bar_ratio": _round6(ratio),
        "last_inside_bar": last,
    }


def gap_geometry(df: pd.DataFrame) -> dict[str, Any]:
    """缺口特征：跳空高开/低开的 K 线数（缺口 = 无重叠的价格断层）。"""
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    n = len(highs)
    up = 0
    down = 0
    for i in range(1, n):
        if lows[i] > highs[i - 1]:
            up += 1
        elif highs[i] < lows[i - 1]:
            down += 1
    return {
        "gap_up_count": int(up),
        "gap_down_count": int(down),
        "gap_count": int(up + down),
        "last_gap_up": int(n >= 2 and lows[-1] > highs[-2]),
        "last_gap_down": int(n >= 2 and highs[-1] < lows[-2]),
    }


def follow_geometry(df: pd.DataFrame) -> dict[str, Any]:
    """跟随特征：窗口末端连续收阳/收阴根数 + 窗口内阳线计数。

    连续同向收盘 = 趋势跟随；末端连续收阳越长趋势越强。
    """
    closes = df["close"].to_numpy(dtype=float)
    n = len(closes)
    up = 0
    for i in range(n - 1, 0, -1):
        if closes[i] > closes[i - 1]:
            up += 1
        else:
            break
    down = 0
    for i in range(n - 1, 0, -1):
        if closes[i] < closes[i - 1]:
            down += 1
        else:
            break
    return {
        "follow_up_count": int(up),
        "follow_down_count": int(down),
        "up_close_count": int((df["close"] > df["open"]).sum()),
    }


# ---------------------------------------------------------------------------
# 结构特征
# ---------------------------------------------------------------------------


def swing_structure(df: pd.DataFrame, *, k: int = SWING_K) -> dict[str, Any]:
    """swing 特征：分形 swing 高点/低点计数 + 末根局部极值标志。

    swing 高 = 一根 K 线高点严格高于左右各 k 根；swing 低同理（方向相反）。
    末端两根无法确认完整分形，用"末根是否创局部新高/新低"近似。
    """
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    n = len(highs)
    swh = 0
    swl = 0
    for i in range(k, n - k):
        if all(highs[i] > highs[i + j] for j in range(-k, k + 1) if j):
            swh += 1
        if all(lows[i] < lows[i + j] for j in range(-k, k + 1) if j):
            swl += 1
    if n >= k + 1 and n >= 2:
        last_swh = int(
            highs[-1] > highs[-2]
            and highs[-1] >= float(highs[-(k + 1):-1].max())
        )
        last_swl = int(
            lows[-1] < lows[-2]
            and lows[-1] <= float(lows[-(k + 1):-1].min())
        )
    else:
        last_swh = 0
        last_swl = 0
    return {
        "swing_high_count": int(swh),
        "swing_low_count": int(swl),
        "last_swing_high": int(last_swh),
        "last_swing_low": int(last_swl),
    }


def range_structure(df: pd.DataFrame, *, lookback: int = RANGE_LOOKBACK) -> dict[str, Any]:
    """区间位置特征：最近 N 根最高/最低 + 最新收盘在区间内的相对位置。

    range_position=0 贴近区间底（超卖边缘），=1 贴近区间顶（超买边缘）；
    range_width_ratio 越大区间越宽（波动扩张）。
    """
    tail = df.tail(lookback)
    r_high = float(tail["high"].max())
    r_low = float(tail["low"].min())
    close_last = float(tail["close"].iloc[-1])
    span = r_high - r_low
    if span > 0:
        pos = (close_last - r_low) / span
    else:
        pos = 0.5
    pos = max(0.0, min(1.0, pos))
    width = span / close_last if close_last else 0.0
    return {
        "range_high": _round6(r_high),
        "range_low": _round6(r_low),
        "range_position": _round6(pos),
        "range_width_ratio": _round6(width),
    }


def breakout_structure(
    df: pd.DataFrame, *, lookback: int = RANGE_LOOKBACK
) -> dict[str, Any]:
    """突破特征：末根收盘相对前 N 根极值的突破标志 + 窗口内突破次数。

    breakout_flag: 0=无突破，1=向上突破（收盘 > 前 N 根最高），
    2=向下突破（收盘 < 前 N 根最低）；recent_breakout_count 统计窗口内
    每根收盘突破此前 lookback 根极值的次数。
    """
    n = len(df)
    closes = df["close"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    prior = df.iloc[max(0, n - 1 - lookback):n - 1]
    if prior.empty:
        flag = 0
        direction = "none"
    else:
        prior_high = float(prior["high"].max())
        prior_low = float(prior["low"].min())
        if closes[-1] > prior_high:
            flag = 1
            direction = "up"
        elif closes[-1] < prior_low:
            flag = 2
            direction = "down"
        else:
            flag = 0
            direction = "none"
    cnt = 0
    for i in range(1, n):
        lo = max(0, i - lookback)
        if closes[i] > highs[lo:i].max():
            cnt += 1
        elif closes[i] < lows[lo:i].min():
            cnt += 1
    return {
        "breakout_flag": int(flag),
        "breakout_direction": direction,
        "recent_breakout_count": int(cnt),
    }


def count_structure(df: pd.DataFrame) -> dict[str, Any]:
    """计数特征：窗口 K 线数 + 创新高/创新低次数（high_low_count 为合计）。

    新高 = 高点严格高于此前所有高点；新低 = 低点严格低于此前所有低点。
    """
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    n = len(highs)
    new_high = 0
    new_low = 0
    max_high = highs[0]
    min_low = lows[0]
    for i in range(1, n):
        if highs[i] > max_high:
            new_high += 1
            max_high = highs[i]
        if lows[i] < min_low:
            new_low += 1
            min_low = lows[i]
    return {
        "bar_count": int(n),
        "new_high_count": int(new_high),
        "new_low_count": int(new_low),
        "high_low_count": int(new_high + new_low),
    }


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def compute_structure_features(
    df: pd.DataFrame,
    *,
    range_lookback: int = RANGE_LOOKBACK,
    swing_k: int = SWING_K,
) -> dict[str, Any]:
    """对 K 线窗口计算全部结构特征（确定性，供 LLM 原样回显锚定）。

    空表或缺少 OHLC 列返回 {}。返回值各键见 ``STRUCTURE_FEATURE_KEYS``。
    """
    if df is None or df.empty:
        return {}
    if not {"open", "high", "low", "close"}.issubset(df.columns):
        return {}
    out: dict[str, Any] = {}
    out.update(body_geometry(df))
    out.update(wick_geometry(df))
    out.update(close_position_geometry(df))
    out.update(overlap_geometry(df))
    out.update(inside_bar_geometry(df))
    out.update(gap_geometry(df))
    out.update(follow_geometry(df))
    out.update(swing_structure(df, k=swing_k))
    out.update(range_structure(df, lookback=range_lookback))
    out.update(breakout_structure(df, lookback=range_lookback))
    out.update(count_structure(df))
    return out


__all__ = [
    "RANGE_LOOKBACK",
    "STRUCTURE_FEATURE_KEYS",
    "SWING_K",
    "compute_structure_features",
]
