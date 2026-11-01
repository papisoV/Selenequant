"""US-301 — 结构特征预计算单测（A-301 验收）。

每类几何/结构特征为纯函数且各 ≥1 单测断言；bar_features 扩展后
cheat_anchor 拦截 LLM 篡改新字段；round-6 纪律保证锚定精确等不误报。
"""
from __future__ import annotations

import pandas as pd
import pytest

from selene_space_market_analysis.core import cheat_anchor
from selene_space_market_analysis.data_source import KlineSource
from selene_space_market_analysis.exceptions import CheatError
from selene_space_market_analysis.structure_features import (
    STRUCTURE_FEATURE_KEYS,
    body_geometry,
    breakout_structure,
    close_position_geometry,
    compute_structure_features,
    count_structure,
    follow_geometry,
    gap_geometry,
    inside_bar_geometry,
    overlap_geometry,
    range_structure,
    swing_structure,
    wick_geometry,
)


def _df(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """构造 OHLC DataFrame（open, high, low, close）。"""
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


# ---------------------------------------------------------------------------
# 几何 — 实体 / 影线 / 收盘位
# ---------------------------------------------------------------------------


def test_body_geometry_last_and_average() -> None:
    df = _df([(1.0, 2.0, 0.0, 2.0), (2.0, 2.0, 1.0, 1.5)])
    out = body_geometry(df)
    assert out["body_ratio_last"] == pytest.approx(0.5)  # 实体 0.5 / 全距 1
    assert out["avg_body_ratio"] == pytest.approx(0.5)
    assert out["bullish_count"] == 1  # 仅第一根收阳


def test_wick_geometry_long_wicks_detected() -> None:
    df = _df([(1.0, 3.0, 0.0, 1.0)])  # 十字星：上影 2，下影 1，实体 0
    out = wick_geometry(df)
    assert out["upper_wick_ratio_last"] == pytest.approx(2 / 3)
    assert out["lower_wick_ratio_last"] == pytest.approx(1 / 3)
    assert out["long_upper_wick_count"] == 1
    assert out["long_lower_wick_count"] == 1


def test_close_position_geometry_span() -> None:
    df = _df([(1.0, 2.0, 0.0, 2.0), (2.0, 2.0, 0.0, 0.0)])
    out = close_position_geometry(df)
    assert out["close_position_last"] == pytest.approx(0.0)  # 收在全距最低
    assert out["avg_close_position"] == pytest.approx(0.5)
    assert out["close_above_mid_count"] == 1


# ---------------------------------------------------------------------------
# 几何 — 重叠 / 内包 / 缺口 / 跟随
# ---------------------------------------------------------------------------


def test_overlap_geometry_full_and_none() -> None:
    full = _df([(1.0, 3.0, 0.0, 2.0), (1.0, 3.0, 0.0, 2.0)])
    assert overlap_geometry(full)["overlap_ratio_last"] == pytest.approx(1.0)
    none = _df([(1.0, 2.0, 1.0, 2.0), (3.0, 4.0, 3.0, 4.0)])  # 完全不重叠
    assert overlap_geometry(none)["overlap_ratio_last"] == pytest.approx(0.0)


def test_inside_bar_geometry() -> None:
    inside = _df([(1.0, 4.0, 0.0, 2.0), (1.5, 3.0, 1.0, 2.0)])  # 第二根被完全包含
    out = inside_bar_geometry(inside)
    assert out["inside_bar_count"] == 1
    assert out["last_inside_bar"] == 1
    assert out["inside_bar_ratio"] == pytest.approx(1.0)
    outside = _df([(1.0, 3.0, 1.0, 2.0), (0.0, 4.0, 0.0, 3.0)])  # 第二根吞没第一根
    assert inside_bar_geometry(outside)["inside_bar_count"] == 0


def test_gap_geometry_up_and_down() -> None:
    up = _df([(1.0, 2.0, 1.0, 2.0), (3.0, 4.0, 2.5, 4.0)])  # 跳空高开
    out_up = gap_geometry(up)
    assert out_up["gap_up_count"] == 1
    assert out_up["last_gap_up"] == 1
    assert out_up["gap_count"] == 1
    down = _df([(1.0, 2.0, 1.0, 2.0), (0.0, 0.5, 0.0, 0.5)])  # 跳空低开
    out_down = gap_geometry(down)
    assert out_down["gap_down_count"] == 1
    assert out_down["last_gap_down"] == 1


def test_follow_geometry_streaks() -> None:
    up = _df([(0.5, 1.0, 0.5, 1.0), (1.5, 2.0, 1.5, 2.0),
              (2.5, 3.0, 2.5, 3.0), (3.5, 4.0, 3.5, 4.0)])
    out = follow_geometry(up)
    assert out["follow_up_count"] == 3  # 末端连续 3 根收阳
    assert out["follow_down_count"] == 0
    assert out["up_close_count"] == 4
    down = _df([(3.5, 4.0, 3.5, 4.0), (2.5, 3.0, 2.5, 3.0),
                (1.5, 2.0, 1.5, 2.0), (0.5, 1.0, 0.5, 1.0)])
    assert follow_geometry(down)["follow_down_count"] == 3


# ---------------------------------------------------------------------------
# 结构 — swing / 区间位置 / 突破 / 计数
# ---------------------------------------------------------------------------


def test_swing_structure_counts_and_last_flag() -> None:
    df = _df([(1.0, 1.0, 3.0, 1.0), (2.0, 2.0, 2.0, 2.0), (3.0, 3.0, 1.0, 3.0),
              (2.0, 2.0, 2.0, 2.0), (1.0, 1.0, 3.0, 1.0)])
    out = swing_structure(df, k=2)
    assert out["swing_high_count"] == 1  # 中间高点 3 严格高于两侧
    assert out["swing_low_count"] == 1  # 中间低点 1 严格低于两侧
    assert out["last_swing_high"] == 0
    assert out["last_swing_low"] == 0
    df2 = _df([(1.0, 1.0, 3.0, 1.0), (2.0, 2.0, 2.0, 2.0), (3.0, 3.0, 1.0, 3.0),
               (2.0, 2.0, 2.0, 2.0), (3.0, 3.0, 1.0, 3.0)])
    out2 = swing_structure(df2, k=2)
    assert out2["last_swing_high"] == 1  # 末根创局部新高


def test_range_structure_position_and_width() -> None:
    df = _df([(1.0, 1.0, 0.0, 1.0), (2.0, 2.0, 1.0, 2.0), (3.0, 4.0, 3.0, 3.5)])
    out = range_structure(df, lookback=2)
    assert out["range_high"] == pytest.approx(4.0)
    assert out["range_low"] == pytest.approx(1.0)
    assert out["range_position"] == pytest.approx((3.5 - 1.0) / 3.0)
    assert out["range_width_ratio"] == pytest.approx(3.0 / 3.5)


def test_breakout_structure_up_flag_and_count() -> None:
    df = _df([(1.0, 1.0, 0.0, 1.0), (2.0, 2.0, 1.0, 2.0), (3.0, 3.0, 2.0, 3.0)])
    out = breakout_structure(df, lookback=2)
    assert out["breakout_flag"] == 1  # 末根收盘 3 高于前 2 根最高 2
    assert out["breakout_direction"] == "up"
    assert out["recent_breakout_count"] == 2  # 每根都较前序窗口新高
    df_down = _df([(3.0, 4.0, 3.0, 4.0), (2.0, 3.0, 2.0, 3.0), (1.0, 2.0, 1.0, 1.0)])
    out_down = breakout_structure(df_down, lookback=2)
    assert out_down["breakout_flag"] == 2
    assert out_down["breakout_direction"] == "down"


def test_count_structure_new_high_low() -> None:
    df = _df([(1.0, 1.0, 2.0, 1.0), (2.0, 2.0, 1.0, 2.0), (1.0, 1.0, 2.0, 1.0),
              (3.0, 3.0, 0.0, 3.0), (2.0, 2.0, 1.0, 2.0)])
    out = count_structure(df)
    assert out["bar_count"] == 5
    assert out["new_high_count"] == 2  # bar2(新高2)、bar4(新高3)
    assert out["new_low_count"] == 2  # bar2(新低1)、bar4(新低0)
    assert out["high_low_count"] == 4


# ---------------------------------------------------------------------------
# 主入口 + round-6 纪律 + 锚定集成
# ---------------------------------------------------------------------------


def test_compute_structure_features_has_all_keys_and_finite() -> None:
    df = _df([(1.0, 1.0, 3.0, 1.0), (2.0, 2.0, 2.0, 2.0), (3.0, 4.0, 1.0, 3.5)])
    out = compute_structure_features(df)
    assert set(out.keys()) == set(STRUCTURE_FEATURE_KEYS)
    for v in out.values():
        assert v is not None
        assert not (isinstance(v, float) and pd.isna(v))


def test_compute_structure_features_empty_and_missing_cols() -> None:
    assert compute_structure_features(pd.DataFrame()) == {}
    assert compute_structure_features(_df([])) == {}
    assert compute_structure_features(pd.DataFrame({"open": [1.0]})) == {}


def test_compute_structure_features_deterministic() -> None:
    df = _df([(1.0, 1.0, 3.0, 1.0), (2.0, 2.0, 2.0, 2.0), (3.0, 4.0, 1.0, 3.5)])
    assert compute_structure_features(df) == compute_structure_features(df.copy())


def test_structure_feature_floats_are_round6() -> None:
    df = _df([(1.0, 1.0, 3.0, 1.0), (2.0, 2.0, 2.0, 2.0), (3.0, 4.0, 1.0, 3.5)])
    for key, value in compute_structure_features(df).items():
        if isinstance(value, float):
            assert round(value, 6) == value, f"{key} 未继承 round-6 纪律"


def test_compute_bar_features_includes_structure_features() -> None:
    """data_source 集成：bar_features 扩展后含结构特征字段（无需 DB）。"""
    df = _df([(1.0, 1.0, 3.0, 1.0), (2.0, 2.0, 2.0, 2.0), (3.0, 4.0, 1.0, 3.5)])
    features = KlineSource().compute_bar_features(df)
    for key in ("range_position", "breakout_flag", "high_low_count",
                "close_position_last", "gap_count", "swing_high_count"):
        assert key in features


def test_cheat_anchor_detects_tampered_structure_feature() -> None:
    """LLM 篡改 bar_features 内的结构特征 → CheatError（A-301 锚定）。"""
    baseline = {"bar_features": {"range_position": 0.8, "breakout_flag": 1}}
    tampered = {"bar_features": {"range_position": 0.3, "breakout_flag": 1}}
    with pytest.raises(CheatError):
        cheat_anchor(baseline, tampered, ("bar_features",))
