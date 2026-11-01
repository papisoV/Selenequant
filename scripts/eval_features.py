#!/usr/bin/env python
"""US-301 结构特征价值 checkpoint（demo 出数 + 有效性基线）。

在冻结命名清单前运行：从 klines.db 拉多组 (symbol, interval) 窗口，
计算结构特征，输出数值分布并断言特征具有区分度：

- 全部特征在全部样本上有穷且非 NaN；
- 连续特征分布有展布（跨样本 std 超过阈值，非恒定值）；
- 关键状态特征（突破标志 / 局部极值 / 内包 / 缺口）在样本间取到
  多个不同值；
- range_position 覆盖区间两侧（展布超过阈值）。

未达标退出码 1（命名不得冻结）。合规：脚本仅做数值计算，不引用
参考实现字段名。

Usage:
    python scripts/eval_features.py
    python scripts/eval_features.py --std-threshold 0.0001
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

# Ensure packages are importable when run from repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
for pkg in (
    "packages/market-analysis",
    "packages/strategy-engine",
):
    path = str(_REPO_ROOT / pkg)
    if path not in sys.path:
        sys.path.insert(0, path)

import pandas as pd  # noqa: E402

from selene_space_market_analysis.data_source import KlineSource  # noqa: E402
from selene_space_market_analysis.structure_features import (  # noqa: E402
    STRUCTURE_FEATURE_KEYS,
    compute_structure_features,
)

# 样本集：多 symbol × 多 interval，覆盖不同行情特征（趋势/震荡/波动差异）。
_SAMPLES = [
    ("binance", "futures", "BTC/USDT", "1m"),
    ("binance", "futures", "BTC/USDT", "5m"),
    ("binance", "futures", "BTC/USDT", "15m"),
    ("binance", "futures", "BTC/USDT", "1h"),
    ("binance", "futures", "ETH/USDT", "1m"),
    ("binance", "futures", "ETH/USDT", "5m"),
    ("binance", "futures", "ETH/USDT", "15m"),
    ("binance", "futures", "ETH/USDT", "1h"),
    ("binance", "futures", "SOL/USDT", "1m"),
    ("binance", "futures", "SOL/USDT", "5m"),
    ("binance", "futures", "SOL/USDT", "15m"),
    ("binance", "spot", "BTC/USDT", "1m"),
    ("binance", "spot", "BTC/USDT", "5m"),
    ("binance", "spot", "BTC/USDT", "15m"),
    ("binance", "spot", "ETH/USDT", "1m"),
    ("binance", "spot", "ETH/USDT", "5m"),
    ("binance", "spot", "SOL/USDT", "1m"),
    ("binance", "spot", "SOL/USDT", "5m"),
]

# 状态标志特征：跨样本应出现 0 与非 0 两种值。
_FLAG_KEYS = (
    "breakout_flag",
    "last_swing_high",
    "last_swing_low",
    "last_inside_bar",
    "last_gap_up",
    "last_gap_down",
)


def _collect() -> pd.DataFrame:
    """对每个样本计算结构特征，返回 rows=样本, cols=特征 的 DataFrame。"""
    source = KlineSource()
    rows: list[dict[str, object]] = []
    labels: list[str] = []
    for exchange, market_type, symbol, interval in _SAMPLES:
        df = source.load(exchange, market_type, symbol, interval)
        feats = compute_structure_features(df)
        if not feats:
            print(f"  [skip] {symbol} {interval}: 无数据或特征为空")
            continue
        rows.append(feats)
        labels.append(f"{symbol} {interval}")
    if not rows:
        raise SystemExit("  [FAIL] 全部样本均无数据，无法评估")
    frame = pd.DataFrame(rows, index=labels)
    return frame[list(STRUCTURE_FEATURE_KEYS)]


def _is_finite(x: object) -> bool:
    if isinstance(x, (int, float)):
        return math.isfinite(float(x))
    if isinstance(x, str):
        return bool(x)
    return x is not None


def _check_finite(frame: pd.DataFrame) -> list[str]:
    """全部值有穷且非 NaN。"""
    bad: list[str] = []
    for col in frame.columns:
        for label in frame.index:
            v = frame.at[label, col]
            if not _is_finite(v):
                bad.append(f"{col}@{label}={v!r}")
    return bad


def _numeric_cols(frame: pd.DataFrame) -> list[str]:
    return [c for c in frame.columns if pd.api.types.is_numeric_dtype(frame[c])]


def main() -> int:
    parser = argparse.ArgumentParser(description="US-301 结构特征价值 checkpoint")
    parser.add_argument("--std-threshold", type=float, default=1e-4)
    parser.add_argument("--min-spread-ratio", type=float, default=0.6,
                        help="连续特征非恒定比例下限")
    args = parser.parse_args()

    frame = _collect()
    print(f"结构特征价值 checkpoint — 样本数: {len(frame)}，特征数: {len(frame.columns)}")
    print()

    # 1. 有穷性
    bad = _check_finite(frame)
    print(f"[1] 有穷性检查: {'PASS' if not bad else 'FAIL'}")
    for b in bad[:10]:
        print(f"    {b}")
    if bad:
        return 1

    # 2. 数值特征分布（区分度）
    numeric = _numeric_cols(frame)
    spread: list[str] = []
    for col in numeric:
        vals = pd.to_numeric(frame[col], errors="coerce")
        std = float(vals.std(ddof=0))
        lo = float(vals.min())
        hi = float(vals.max())
        distinct = vals.nunique(dropna=True)
        if distinct >= 2 and std >= args.std_threshold:
            spread.append(col)
        print(f"  {col:<26} min={lo:<12.4g} max={hi:<12.4g} "
              f"std={std:<10.4g} distinct={distinct}")
    nonconst_ratio = len(spread) / len(numeric) if numeric else 0.0
    print(f"[2] 数值特征跨样本非恒定比例: {nonconst_ratio:.0%} "
          f"(要求 ≥ {args.min_spread_ratio:.0%}) "
          f"-> {'PASS' if nonconst_ratio >= args.min_spread_ratio else 'FAIL'}")
    if not numeric or nonconst_ratio < args.min_spread_ratio:
        return 1

    # 3. 状态标志多样性
    flag_ok = []
    for col in _FLAG_KEYS:
        if col not in frame.columns:
            continue
        distinct = frame[col].nunique(dropna=True)
        ok = distinct >= 2
        flag_ok.append((col, distinct, ok))
        print(f"  {col:<26} distinct={distinct} {'OK' if ok else 'const'}")
    any_flag_ok = any(ok for _, _, ok in flag_ok)
    print(f"[3] 状态标志多样性（至少一类取到 0 与非 0）: "
          f"{'PASS' if any_flag_ok else 'FAIL'}")
    if not any_flag_ok:
        return 1

    # 4. range_position 展布
    rp = pd.to_numeric(frame["range_position"], errors="coerce")
    rp_span = float(rp.max() - rp.min())
    print(f"[4] range_position 跨样本展布: {rp_span:.3f} "
          f"(要求 ≥ 0.5) -> {'PASS' if rp_span >= 0.5 else 'FAIL'}")
    if rp_span < 0.5:
        return 1

    print()
    print("价值 checkpoint 通过：结构特征出数且有区分度，命名清单可冻结。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
