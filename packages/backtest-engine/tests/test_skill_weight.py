"""per-skill Beta 先验权重(📋3):公式数值、四道准入闸、fail-neutral、边界钳制。

纯函数,零 IO。所有"该不该给某个 Skill 挪权重"的判断都在这层锁死 ——
装配层只负责喂 bucket,不参与公式。
"""

from __future__ import annotations

import math

import pytest

from selene_space_backtest.skill_weight import (
    BETA_PRIOR_SIZE,
    MAX_SKILL_WEIGHT_FACTOR,
    MIN_SKILL_OUTCOME_SAMPLE_SIZE,
    MIN_SKILL_WEIGHT_FACTOR,
    NEUTRAL_SKILL_WEIGHT_FACTOR,
    compute_skill_weight_factors,
    score_bucket,
)

ENGINE = "decision-signal-v1"


def _bucket(**overrides) -> dict:
    """一个"样本充足、纯命中"的 5d bucket —— 各用例按需覆盖。"""
    base = {
        "skill_id": "alpha",
        "horizon": "5d",
        "engine_version": ENGINE,
        "total": 30,
        "evaluated": 30,
        "hit": 30,
        "miss": 0,
        "observational": 0,
        "unable": 0,
        "sample_sufficient": True,
    }
    base.update(overrides)
    return base


# -- fail-neutral:没证据一律 1.0 ---------------------------------------------------


def test_no_buckets_means_all_neutral():
    out = compute_skill_weight_factors(
        skill_ids=["alpha", "beta"], buckets=[], engine_version=ENGINE
    )
    assert out == {"alpha": 1.0, "beta": 1.0}


def test_every_requested_skill_appears_even_when_unscored():
    out = compute_skill_weight_factors(
        skill_ids=["alpha", "beta"],
        buckets=[_bucket(skill_id="alpha")],
        engine_version=ENGINE,
    )
    assert set(out) == {"alpha", "beta"}
    assert out["beta"] == NEUTRAL_SKILL_WEIGHT_FACTOR


def test_empty_skill_ids_or_version_is_neutral():
    assert (
        compute_skill_weight_factors(
            skill_ids=[], buckets=[_bucket()], engine_version=ENGINE
        )
        == {}
    )
    assert compute_skill_weight_factors(
        skill_ids=["alpha"], buckets=[_bucket()], engine_version=""
    ) == {"alpha": 1.0}


def test_duplicate_skill_ids_are_deduped():
    out = compute_skill_weight_factors(
        skill_ids=["alpha", "alpha"], buckets=[], engine_version=ENGINE
    )
    assert out == {"alpha": 1.0}


# -- 四道准入闸:任一不过即跳过(不是钳制) --------------------------------------------


def test_insufficient_sample_keeps_neutral():
    # evaluated < 30 → 样本不足,权重纹丝不动(哪怕全中)。
    bucket = _bucket(evaluated=29, hit=29, miss=0)
    out = compute_skill_weight_factors(
        skill_ids=["alpha"], buckets=[bucket], engine_version=ENGINE
    )
    assert out["alpha"] == 1.0


def test_sample_sufficient_false_is_skipped():
    # 调用方**声明**样本不足 → 跳过,即使计数看起来够。
    out = compute_skill_weight_factors(
        skill_ids=["alpha"],
        buckets=[_bucket(sample_sufficient=False)],
        engine_version=ENGINE,
    )
    assert out["alpha"] == 1.0


def test_sample_sufficient_must_be_literal_true_not_truthy():
    out = compute_skill_weight_factors(
        skill_ids=["alpha"],
        buckets=[_bucket(sample_sufficient=1)],
        engine_version=ENGINE,
    )
    assert out["alpha"] == 1.0


def test_stale_engine_version_is_skipped():
    out = compute_skill_weight_factors(
        skill_ids=["alpha"],
        buckets=[_bucket(engine_version="decision-signal-v2")],
        engine_version=ENGINE,
    )
    assert out["alpha"] == 1.0


def test_unsupported_horizon_is_skipped():
    out = compute_skill_weight_factors(
        skill_ids=["alpha"], buckets=[_bucket(horizon="99d")], engine_version=ENGINE
    )
    assert out["alpha"] == 1.0


def test_counts_that_do_not_add_up_are_rejected():
    # hit + miss != evaluated → 混了不同population的行,该 Skill 的证据不可用。
    bucket = _bucket(evaluated=30, hit=20, miss=5)
    out = compute_skill_weight_factors(
        skill_ids=["alpha"], buckets=[bucket], engine_version=ENGINE
    )
    assert out["alpha"] == 1.0


def test_duplicate_bucket_for_same_skill_horizon_invalidates_skill():
    # 同一个 (skill, horizon) 出现两次 → 身份有歧义,该 Skill 整体作废。
    dup = _bucket()
    out = compute_skill_weight_factors(
        skill_ids=["alpha"], buckets=[dup, dict(dup)], engine_version=ENGINE
    )
    assert out["alpha"] == 1.0


@pytest.mark.parametrize("bad", [-1, 1.5, True, "30", None])
def test_malformed_counts_are_rejected(bad):
    out = compute_skill_weight_factors(
        skill_ids=["alpha"], buckets=[_bucket(evaluated=bad)], engine_version=ENGINE
    )
    assert out["alpha"] == 1.0


def test_unknown_skill_ids_in_buckets_are_ignored():
    out = compute_skill_weight_factors(
        skill_ids=["alpha"], buckets=[_bucket(skill_id="ghost")], engine_version=ENGINE
    )
    assert out == {"alpha": 1.0}


# -- 公式数值:与 DSA 逐项对齐 -------------------------------------------------------


def test_perfect_record_raises_weight_above_one():
    out = compute_skill_weight_factors(
        skill_ids=["alpha"], buckets=[_bucket()], engine_version=ENGINE
    )
    assert out["alpha"] > 1.0
    assert out["alpha"] <= MAX_SKILL_WEIGHT_FACTOR


def test_perfect_miss_record_lowers_weight_below_one():
    out = compute_skill_weight_factors(
        skill_ids=["alpha"],
        buckets=[_bucket(hit=0, miss=30)],
        engine_version=ENGINE,
    )
    assert out["alpha"] < 1.0
    assert out["alpha"] >= MIN_SKILL_WEIGHT_FACTOR


def test_even_record_is_neutral():
    # 半对半错 → direction_score = 0 → factor = 1.0。
    out = compute_skill_weight_factors(
        skill_ids=["alpha"],
        buckets=[_bucket(hit=15, miss=15)],
        engine_version=ENGINE,
    )
    assert out["alpha"] == pytest.approx(1.0, abs=1e-9)


def test_bucket_score_matches_hand_computed_formula():
    # 手算:hit=24, evaluated=30 → posterior=(24+15)/(30+30)=0.65
    #      direction=0.30; unable_rate=0 → bucket=0.30
    #      evidence=30/(30+30)=0.5;  combined=0.30
    #      factor=1.2**0.30≈1.05622
    bucket = _bucket(hit=24, miss=6, evaluated=30)
    score = score_bucket(bucket)
    assert score is not None
    bucket_score, evidence = score
    assert bucket_score == pytest.approx(0.30, abs=1e-9)
    assert evidence == pytest.approx(0.5, abs=1e-9)
    expected = MAX_SKILL_WEIGHT_FACTOR**0.30
    out = compute_skill_weight_factors(
        skill_ids=["alpha"], buckets=[bucket], engine_version=ENGINE
    )
    assert out["alpha"] == pytest.approx(expected, abs=1e-9)


def test_unable_penalty_shaves_the_score():
    # unable 冲淡方向分:bucket = direction - 0.25 * unable_rate。
    with_unable = _bucket(hit=20, miss=10, evaluated=30, unable=30, total=60)
    clean = _bucket(hit=20, miss=10, evaluated=30)
    assert score_bucket(with_unable)[0] < score_bucket(clean)[0]


def test_observational_dilutes_unable_rate():
    # observational(中性)也进 terminal 分母 → 拉低 unable_rate → 少扣一点。
    more_unable_rate = _bucket(hit=20, miss=10, unable=30, observational=0)
    less_unable_rate = _bucket(hit=20, miss=10, unable=30, observational=30)
    assert score_bucket(less_unable_rate)[0] > score_bucket(more_unable_rate)[0]


def test_zero_directional_evidence_bucket_is_unusable():
    # evaluated=0 且 terminal>0:能不能出分?公式给 direction 由先验决定,
    # 但 evaluated < 30 → 按闸门拒绝,a/b 都不该把 0 样本读成信号。
    assert score_bucket(_bucket(evaluated=0, hit=0, miss=0, unable=5)) is None


# -- 多 horizon 加权合并 ------------------------------------------------------------


def test_multi_horizon_combines_by_evidence_weight():
    # 两个 horizon:一个全中(高分),一个全错(低分),证据强度相同 → 合并回中性附近。
    buckets = [
        _bucket(horizon="1d", hit=30, miss=0),
        _bucket(horizon="5d", hit=0, miss=30),
    ]
    out = compute_skill_weight_factors(
        skill_ids=["alpha"], buckets=buckets, engine_version=ENGINE
    )
    assert out["alpha"] == pytest.approx(1.0, abs=1e-6)


def test_longer_sample_dominates_shorter():
    # 强证据的 horizon 应压过弱证据的。
    buckets = [
        _bucket(horizon="1d", hit=30, miss=0, evaluated=30),
        _bucket(horizon="5d", hit=0, miss=90, evaluated=90),
    ]
    out = compute_skill_weight_factors(
        skill_ids=["alpha"], buckets=buckets, engine_version=ENGINE
    )
    assert out["alpha"] < 1.0  # 90 样本的失败压过 30 样本的成功


# -- 钳制边界 ----------------------------------------------------------------------


def test_factor_never_exceeds_max():
    # 有限样本下 posterior 恒 < 1(先验把它从 1 拽回来),故 factor **趋近**
    # 上界但永不达到 —— 钳制是护栏,不是饱和目标。断言"不越界 + 足够接近"。
    out = compute_skill_weight_factors(
        skill_ids=["alpha"],
        buckets=[_bucket(hit=100000, miss=0, evaluated=100000)],
        engine_version=ENGINE,
    )
    assert out["alpha"] < MAX_SKILL_WEIGHT_FACTOR
    assert out["alpha"] == pytest.approx(MAX_SKILL_WEIGHT_FACTOR, abs=1e-3)


def test_factor_never_below_min():
    out = compute_skill_weight_factors(
        skill_ids=["alpha"],
        buckets=[_bucket(hit=0, miss=100000, evaluated=100000)],
        engine_version=ENGINE,
    )
    assert out["alpha"] > MIN_SKILL_WEIGHT_FACTOR
    assert out["alpha"] == pytest.approx(MIN_SKILL_WEIGHT_FACTOR, abs=1e-3)


def test_clamp_bounds_hold_for_contrived_extreme_buckets():
    # 钳制仍是硬护栏:喂一个 bucket_score 被算成越界的输入,结果必须夹在带内。
    # (正常 bucket 到不了边界,故这里用一个"完美"的合成 bucket 逼近它。)
    out = compute_skill_weight_factors(
        skill_ids=["alpha"],
        buckets=[_bucket(hit=10**9, miss=0, evaluated=10**9)],
        engine_version=ENGINE,
    )
    assert MIN_SKILL_WEIGHT_FACTOR <= out["alpha"] <= MAX_SKILL_WEIGHT_FACTOR


def test_all_returned_factors_are_finite_and_in_range():
    buckets = [
        _bucket(horizon="1d", hit=40, miss=0),
        _bucket(horizon="3d", hit=0, miss=40),
        _bucket(horizon="5d", hit=20, miss=20),
    ]
    out = compute_skill_weight_factors(
        skill_ids=["alpha"], buckets=buckets, engine_version=ENGINE
    )
    for factor in out.values():
        assert math.isfinite(factor)
        assert MIN_SKILL_WEIGHT_FACTOR <= factor <= MAX_SKILL_WEIGHT_FACTOR


def test_beta_prior_size_is_thirty():
    # 先验是 15/15 —— 常量本身是被依赖的事实(决定了 30 样本的权重量级)。
    assert BETA_PRIOR_SIZE == 30
    assert MIN_SKILL_OUTCOME_SAMPLE_SIZE == 30
