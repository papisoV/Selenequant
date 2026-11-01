"""US-302:多重校正 / IC 衰减 / 分市场一致性 —— 行为锁。"""

from __future__ import annotations

import numpy as np
import pytest

from selene_space_factor_research.mining.validation_extensions import (
    ic_decay,
    market_split_consistency,
    multiple_testing_correction,
    subperiod_sign_consistency,
)


def _random_ics(seed: int = 7, n: int = 216) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.standard_normal(n) * 0.003  # 纯噪声量级的 IC 分布


# --- multiple_testing_correction -------------------------------------------


def test_random_ics_do_not_flag():
    """216 个随机 IC,~alpha 比例偶然显著 → p 应 > 0.05,不误报。"""
    ics = _random_ics()
    n_sig = int(np.sum(np.abs(ics) > 0.02))  # 极宽松阈值下也只会命中零星
    res = multiple_testing_correction(n_sig, len(ics))
    assert res["binom_p"] > 0.05
    assert res["exceeds_random_baseline"] is False


def test_real_signal_flags():
    """30/216 显著(期望 10.8)→ 二项上尾 p < 0.05,应报。"""
    res = multiple_testing_correction(30, 216)
    assert res["binom_p"] < 0.05
    assert res["exceeds_random_baseline"] is True
    assert res["bonferroni_alpha"] == pytest.approx(0.05 / 216)
    assert res["expected_by_chance"] == pytest.approx(216 * 0.05)


def test_mtc_input_validation():
    with pytest.raises(ValueError, match="n_factors must be positive"):
        multiple_testing_correction(0, 0)
    with pytest.raises(ValueError, match="out of range"):
        multiple_testing_correction(217, 216)


# --- ic_decay ---------------------------------------------------------------


def test_constant_ic_no_decay():
    res = ic_decay([0.05] * 60, [1, 2, 5, 10])
    assert res["half_life"] is None


def test_exponential_decay_half_life():
    """AR(1) 过程 x_{t+1}=0.7x_t+ε:理论自相关 0.7^h,半衰期 ≈4。"""
    rng = np.random.default_rng(11)
    x = [0.0] * 200
    for t in range(1, 200):
        x[t] = 0.7 * x[t - 1] + rng.standard_normal()
    res = ic_decay(x, [1, 2, 3, 4, 5, 6, 8, 10])
    assert res["half_life"] is not None
    assert 2 <= res["half_life"] <= 8


def test_ic_decay_short_series():
    res = ic_decay([0.1, np.nan], [1])
    assert res["n_obs"] == 1
    assert res["autocorr"] == {}
    assert res["half_life"] is None


# --- market_split_consistency -----------------------------------------------


class _DS:
    def __init__(self, key: str, ic: float | None):
        self.dataset_key = key
        self.oos_rank_ic = ic


def test_same_direction_full_agreement():
    vals = [_DS("sh_000", 0.04), _DS("sz_399", 0.06), _DS("cy_300", 0.02)]
    res = market_split_consistency(vals)
    assert res["n_datasets"] == 3
    assert res["direction_agreement"] == 1.0


def test_split_direction_partial_agreement():
    vals = [_DS("sh_000", 0.04), _DS("sz_399", 0.06), _DS("cy_300", -0.05)]
    res = market_split_consistency(vals)
    assert res["direction_agreement"] == pytest.approx(2 / 3)


def test_none_ic_excluded():
    vals = [_DS("sh_000", 0.04), _DS("sz_399", None)]
    res = market_split_consistency(vals)
    assert res["n_datasets"] == 1


# --- subperiod_sign_consistency(F2:探针分段稳定性)-------------------------


def test_all_same_sign_is_fully_consistent():
    """全程同号 → 4/4 一致。"""
    res = subperiod_sign_consistency(np.full(40, -0.01))
    assert res["sign_ratio"] == pytest.approx(1.0)
    assert res["consistent"] == res["n_nonempty"] == 4


def test_last_subperiod_flip_lowers_ratio():
    """末段翻转 → 3/4,正是"只在某段成立"要暴露的情形。"""
    arr = np.concatenate([np.full(30, -0.01), np.full(10, 0.02)])
    res = subperiod_sign_consistency(arr)
    assert res["sign_ratio"] == pytest.approx(0.75)
    assert res["consistent"] == 3


def test_flat_or_empty_has_no_direction():
    """全零 / 空 / 单点 → sign_ratio 为 None(无方向可言,不硬编 0.0 冒充)。"""
    assert subperiod_sign_consistency(np.zeros(20))["sign_ratio"] is None
    assert subperiod_sign_consistency(np.array([]))["sign_ratio"] is None
    assert subperiod_sign_consistency(np.array([0.01]))["sign_ratio"] is None


def test_nan_filtered_before_split():
    """NaN 不参与(与探针 ic_daily.dropna() 口径一致)。"""
    arr = np.array([-0.01, np.nan, -0.01, np.nan, -0.01, -0.01])
    res = subperiod_sign_consistency(arr, n_subperiods=2)
    assert res["sign_ratio"] == pytest.approx(1.0)
    assert res["n_nonempty"] == 2


def test_custom_subperiod_count():
    """段数可配(默认 4);单段退化为"整体符号自比",恒 1.0。"""
    arr = np.array([-0.01, 0.02, -0.03, 0.04])
    assert subperiod_sign_consistency(arr, n_subperiods=1)["sign_ratio"] == pytest.approx(1.0)
    assert subperiod_sign_consistency(arr, n_subperiods=4)["n_subperiods"] == 4
