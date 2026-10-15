"""QuantSkills 验证清单尾巴(US-302):多重校正 / IC 衰减 / 分市场一致性。

三项各自独立、纯函数、全部自研实现 —— 只吸收 QuantSkills 报告的
"缺 9 项验证" 清单思想,零 GPL 代码搬运(QuantSkills GPL-3.0)。

spearman 复用 validation_protocol._spearman_pair 的语义(有限交集 +
长度/恒方差闸),不 import 私有件,自带同语义实现保持模块独立。
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy import stats


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman rho over the finite intersection (语义对齐 _spearman_pair)。"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    if len(x) < 2 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return np.nan
    return float(stats.spearmanr(x, y)[0])


def multiple_testing_correction(
    n_significant: int,
    n_factors: int,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Bonferroni 校正 + 二项上尾检验:显著因子数是否超随机基线。

    H0:每个因子独立以概率 alpha 偶然显著。观察到的 n_significant 越过
    随机期望(n_factors * alpha)足够多才判"不是运气"。216 个纯随机 IC
    在 alpha=0.05 下期望约 10.8 个显著 —— p 应 > 0.05(不误报)。

    返回:bonferroni_alpha(个体校正阈值)、expected_by_chance、
    binom_p(上尾 P(X >= n_significant | B(n_factors, alpha)))、
    exceeds_random_baseline(0.05 水平的布尔判定)。
    """
    if n_factors <= 0:
        raise ValueError(f"n_factors must be positive: {n_factors!r}")
    if not 0 < alpha < 1:
        raise ValueError(f"alpha must be in (0, 1): {alpha!r}")
    if n_significant < 0 or n_significant > n_factors:
        raise ValueError(
            f"n_significant out of range [0, {n_factors}]: {n_significant!r}"
        )
    p = float(stats.binom.sf(n_significant - 1, n_factors, alpha))  # P(X >= k)
    return {
        "n_significant": int(n_significant),
        "n_factors": int(n_factors),
        "alpha": float(alpha),
        "bonferroni_alpha": float(alpha / n_factors),
        "expected_by_chance": float(n_factors * alpha),
        "binom_p": float(p),
        "exceeds_random_baseline": bool(p < alpha),
    }


def ic_decay(ic_series: list[float], horizons: list[int]) -> dict[str, Any]:
    """IC 自相关衰减:逐 horizon Spearman(IC_t, IC_{t+h}),报半衰期。

    半衰期 = |Spearman| 首次跌破初值一半的最小 horizon;全程未跌破 →
    None(不衰减)。ic_series 过短或 horizons 越界按能算的算,算不了的
    horizon 输出 NaN。
    """
    arr = np.asarray([x for x in ic_series if np.isfinite(x)], dtype=float)
    n = len(arr)
    result: dict[str, Any] = {
        "n_obs": int(n),
        "autocorr": {},
        "half_life": None,
    }
    if n < 3:
        return result
    for h in sorted({int(x) for x in horizons if x > 0}):
        if h >= n:
            result["autocorr"][str(h)] = float("nan")
            continue
        rho = _spearman(arr[:-h], arr[h:])
        result["autocorr"][str(h)] = rho
    half_val = abs(result["autocorr"].get("1", float("nan")))
    baseline = half_val if np.isfinite(half_val) and half_val > 0 else None
    if baseline is not None:
        for h in sorted(int(k) for k in result["autocorr"]):
            v = result["autocorr"][str(h)]
            if np.isfinite(v) and abs(v) < baseline / 2:
                result["half_life"] = h
                break
    return result


def market_split_consistency(validations: list[Any]) -> dict[str, Any]:
    """按 dataset 分组的方向一致性:各组 OOS IC + 多数方向占比。

    ``validations`` 是 DatasetValidation 容器(取 dataset_key 与
    oos_rank_ic);同一 key 重复出现时取首值(DatasetValidation 每 key
    一行的契约下即该组 IC)。
    """
    medians: dict[str, float] = {}
    for v in validations:
        key = str(getattr(v, "dataset_key", v))
        ic = getattr(v, "oos_rank_ic", None)
        if ic is not None and np.isfinite(ic):
            medians.setdefault(key, float(ic))
    if not medians:
        return {"n_datasets": 0, "group_medians": {}, "direction_agreement": None}
    signs = [1 if v > 0 else -1 for v in medians.values()]
    majority = 1 if sum(signs) >= 0 else -1
    n_same = sum(1 for s in signs if s == majority)
    return {
        "n_datasets": len(medians),
        "group_medians": medians,
        "direction_agreement": float(n_same / len(medians)),
    }


def subperiod_sign_consistency(
    daily_ic: Any, n_subperiods: int = 4
) -> dict[str, Any]:
    """把逐日 IC 序列切 N 段,各段均值符号与整体一致的占比。

    为什么需要(吸收 external-research-project 的输出同质化审计思想):探针现在的
    口径是**全窗口聚合**(overall/recent90/trend),一个只在某段行情
    成立的信号也能在整体上过线。分段一致率能把"全程有效"和"只在
    某段有效"分开 —— 这是判别力,不是防伪。

    **诊断字段,非门禁**:不参与 ``multiple_testing_correction`` 的
    阈值判定,也不否决候选。同数据二次用作门禁会虚增置信度。

    语义对齐 ``fitness._subperiod_consistency``(同号占比),但此处
    自带实现保持模块独立、且公开可复用 —— 不 import 私有件(与该文件
    顶部 ``_spearman`` 的做法一致)。

    返回 ``{"n_subperiods", "n_nonempty", "consistent", "sign_ratio"}``:
    段均值为零的段不计入分母(与 fitness 同语义:恒零不算认同);
    整体 IC 为零或有效段不足时 ``sign_ratio`` 为 None(无方向可言)。
    """
    arr = np.asarray(getattr(daily_ic, "to_numpy", lambda: daily_ic)(), dtype=float)
    arr = arr[np.isfinite(arr)]
    k = max(1, int(n_subperiods))
    if len(arr) < 2:
        return {
            "n_subperiods": k,
            "n_nonempty": 0,
            "consistent": 0,
            "sign_ratio": None,
        }
    overall = float(np.mean(arr))
    if overall == 0.0:
        return {
            "n_subperiods": k,
            "n_nonempty": 0,
            "consistent": 0,
            "sign_ratio": None,
        }
    chunks = np.array_split(arr, k)
    means = [float(np.mean(c)) for c in chunks if len(c)]
    if not means:
        return {
            "n_subperiods": k,
            "n_nonempty": 0,
            "consistent": 0,
            "sign_ratio": None,
        }
    want = 1.0 if overall > 0 else -1.0
    consistent = sum(1 for m in means if (1.0 if m > 0 else -1.0) == want and m != 0.0)
    return {
        "n_subperiods": k,
        "n_nonempty": len(means),
        "consistent": consistent,
        "sign_ratio": float(consistent / len(means)),
    }
