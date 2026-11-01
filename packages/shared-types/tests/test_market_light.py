"""Behaviour locks for the Market Light composite module.

Covers: the three dimension scorers (including DSA's truncation semantics and
the fail-neutral 50 for missing data); the three-band status and four-band
temperature; the composite weighting and rounding; the ``data_quality`` ladder
where an inapplicable dimension (crypto/HK/US/JP/KR limits) is excluded rather
than counted as missing; reason rendering in both languages; and the input
guards (unknown region, bad weights, out-of-range dimension score).
"""

from __future__ import annotations

import pytest

from selene_space_types.market_light import (
    LIMIT_APPLICABLE_MARKETS,
    NEUTRAL_SCORE,
    MarketLightDimension,
    MarketLightWeights,
    breadth_dimension,
    compose_market_light,
    index_dimension,
    limit_dimension,
    market_light_status,
    market_light_temperature,
)


class TestBreadthDimension:
    def test_advancers_share(self) -> None:
        assert breadth_dimension(6000, 4000).score == 60
        assert breadth_dimension(100, 0).score == 100
        assert breadth_dimension(0, 100).score == 0

    def test_no_participants_is_neutral_unavailable(self) -> None:
        dim = breadth_dimension(0, 0)
        assert dim.score == NEUTRAL_SCORE
        assert dim.available is False
        assert dim.applicable is True

    def test_truncates_like_dsa(self) -> None:
        # 1/3 * 100 = 33.33 -> int() truncates to 33.
        assert breadth_dimension(1, 2).score == 33


class TestIndexDimension:
    def test_flat_is_neutral(self) -> None:
        assert index_dimension([0.0]).score == 50

    def test_gain_and_loss_scale(self) -> None:
        assert index_dimension([1.0]).score == 62  # 50 + 12
        assert index_dimension([-1.0]).score == 38  # 50 - 12

    def test_average_across_indices(self) -> None:
        assert index_dimension([1.0, 0.5]).score == 59  # 50 + 0.75*12

    def test_clamped_at_bounds(self) -> None:
        assert index_dimension([10.0]).score == 100
        assert index_dimension([-10.0]).score == 0

    def test_truncates_fractional(self) -> None:
        # 50 + 0.8*12 = 59.6 -> int() truncates to 59.
        assert index_dimension([0.8]).score == 59

    def test_none_entries_ignored(self) -> None:
        assert index_dimension([None, 1.0, None]).score == 62

    def test_no_index_data_is_neutral_unavailable(self) -> None:
        dim = index_dimension([])
        assert dim.score == NEUTRAL_SCORE
        assert dim.available is False


class TestLimitDimension:
    def test_ups_share(self) -> None:
        assert limit_dimension(80, 20, applicable=True).score == 80

    def test_zero_moves_unavailable_but_applicable(self) -> None:
        dim = limit_dimension(0, 0, applicable=True)
        assert dim.score == NEUTRAL_SCORE
        assert dim.available is False
        assert dim.applicable is True

    def test_inapplicable_market_is_neutral_and_flagged(self) -> None:
        dim = limit_dimension(0, 0, applicable=False)
        assert dim.score == NEUTRAL_SCORE
        assert dim.available is False
        assert dim.applicable is False


class TestStatusAndTemperature:
    @pytest.mark.parametrize(
        "score,expected",
        [
            (100, "green"),
            (60, "green"),
            (59, "yellow"),
            (40, "yellow"),
            (39, "red"),
            (0, "red"),
        ],
    )
    def test_status_bands(self, score: int, expected: str) -> None:
        assert market_light_status(score) == expected

    @pytest.mark.parametrize(
        "score,zh,en",
        [
            (70, "强势", "risk-on"),
            (69, "偏暖", "constructive"),
            (55, "偏暖", "constructive"),
            (54, "震荡", "mixed"),
            (40, "震荡", "mixed"),
            (39, "偏弱", "defensive"),
        ],
    )
    def test_temperature_bands(self, score: int, zh: str, en: str) -> None:
        assert market_light_temperature(score, "zh") == zh
        assert market_light_temperature(score, "en") == en


class TestComposeMarketLight:
    def test_deterministic_weighted_score(self) -> None:
        light = compose_market_light(
            region="cn",
            up_count=6000,
            down_count=4000,  # breadth 60
            index_changes=[1.0, 0.5],  # index 59
            limit_up_count=80,
            limit_down_count=20,  # limit 80
        )
        # 0.45*60 + 0.35*59 + 0.20*80 = 63.65 -> round 64
        assert light.region == "CN"
        assert light.breadth.score == 60
        assert light.index.score == 59
        assert light.limit.score == 80
        assert light.score == 64
        assert light.status == "green"
        assert light.label == "可进攻"
        assert light.temperature_label == "偏暖"
        assert light.data_quality == "ok"

    def test_region_aliases_normalize(self) -> None:
        assert compose_market_light(region="us").region == "US"
        assert compose_market_light(region="crypto").region == "CRYPTO"

    def test_unknown_region_raises(self) -> None:
        with pytest.raises(ValueError):
            compose_market_light(region="atlantis")

    def test_missing_index_is_unavailable(self) -> None:
        light = compose_market_light(region="cn", up_count=6000, down_count=4000)
        assert light.index.available is False
        assert light.data_quality == "unavailable"

    def test_partial_when_applicable_dimension_lacks_data(self) -> None:
        # Index present, breadth absent, limit applicable but no moves -> partial.
        light = compose_market_light(region="cn", index_changes=[0.0])
        assert light.breadth.available is False
        assert light.limit.applicable is True
        assert light.data_quality == "partial"

    def test_crypto_limit_inapplicable_keeps_quality_ok(self) -> None:
        light = compose_market_light(
            region="crypto",
            up_count=60,
            down_count=40,
            index_changes=[0.5],
        )
        assert light.limit.applicable is False
        assert light.limit.score == NEUTRAL_SCORE
        assert light.data_quality == "ok"

    def test_limit_applicability_matches_market_capability(self) -> None:
        assert LIMIT_APPLICABLE_MARKETS == frozenset({"CN", "TW"})
        cn = compose_market_light(region="cn", index_changes=[0.0])
        hk = compose_market_light(region="hk", index_changes=[0.0])
        assert cn.limit.applicable is True
        assert hk.limit.applicable is False

    def test_limit_applicable_override(self) -> None:
        light = compose_market_light(
            region="us", limit_up_count=10, limit_down_count=5, limit_applicable=True
        )
        assert light.limit.applicable is True
        assert light.limit.score == 66  # int(10/15*100) truncates 66.67 -> 66

    def test_bad_weights_rejected(self) -> None:
        with pytest.raises(ValueError):
            MarketLightWeights(breadth=0.5, index=0.5, limit=0.5)

    def test_dimension_score_bounds(self) -> None:
        with pytest.raises(ValueError):
            MarketLightDimension(score=101, available=True)
        with pytest.raises(ValueError):
            MarketLightDimension(score=-1, available=True)


class TestReasons:
    def test_zh_reasons_expanding(self) -> None:
        light = compose_market_light(
            region="cn",
            up_count=7000,
            down_count=3000,
            index_changes=[1.5],
            limit_up_count=90,
        )
        assert any("赚钱效应扩散" in r for r in light.reasons)
        assert any("主要指数平均涨跌幅" in r for r in light.reasons)
        assert any("涨跌停差" in r for r in light.reasons)

    def test_en_reasons(self) -> None:
        light = compose_market_light(
            region="us", up_count=3000, down_count=7000, index_changes=[-1.0], lang="en"
        )
        assert any("downside pressure dominates" in r for r in light.reasons)
        assert any("average major-index change" in r for r in light.reasons)

    def test_turnover_fallback_when_no_breadth(self) -> None:
        light = compose_market_light(region="cn", total_amount=12345.0)
        assert any("成交额" in r for r in light.reasons)

    def test_last_resort_reason_when_nothing_available(self) -> None:
        light = compose_market_light(region="cn")
        assert light.reasons == ("结构化涨跌数据有限，按可用行情综合判断",)

    def test_reasons_cover_all_three_categories_within_cap(self) -> None:
        light = compose_market_light(
            region="cn",
            up_count=7000,
            down_count=3000,
            index_changes=[1.5],
            limit_up_count=90,
            limit_down_count=5,
        )
        # Three reason categories exist; the module caps at four as a defensive
        # bound mirroring DSA, so the cap is never reached in practice.
        assert len(light.reasons) == 3
        assert len(light.reasons) <= 4
