"""📋5 行情热点/题材层单测 — 相位/强度边界、聚合、诚实降级、个股角色。"""

from __future__ import annotations

import pytest

from selene_space_market_analysis.theme_layer import (
    MarketThemeContext,
    PrimaryTheme,
    RankedThemeItem,
    StockBoardPosition,
    build_active_themes,
    build_theme_context,
    infer_stock_role,
    normalize_ranking,
    phase_from_change,
    strength_from_change,
)


# ---------------------------------------------------------------------------
# 相位边界
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (3.0, "accelerating"),  # 恰好 3 也算加速（>=）
        (10.0, "accelerating"),
        (0.0001, "warming"),
        (0.0, "cooling"),  # > 0 才算转暖，0 归退潮
        (-1.5, "cooling"),
        (None, "unknown"),
    ],
)
def test_phase_from_change_boundaries(value: float | None, expected: str) -> None:
    assert phase_from_change(value) == expected


# ---------------------------------------------------------------------------
# 强度 clamp
# ---------------------------------------------------------------------------


def test_strength_clamp_upper_and_lower() -> None:
    assert strength_from_change(1000.0) == 100  # 超大值封顶
    assert strength_from_change(-1000.0) == 0  # 超深负归零
    assert strength_from_change(None) is None


def test_strength_zero_axis() -> None:
    assert strength_from_change(0.0) == 50


# ---------------------------------------------------------------------------
# 活跃题材
# ---------------------------------------------------------------------------


def _items(*changes: float | None) -> list[RankedThemeItem]:
    return [
        RankedThemeItem(name=f"theme{i}", change_pct=value, rank=i + 1, source="industry")
        for i, value in enumerate(changes)
    ]


def test_active_themes_keep_positive_only_and_sort_desc() -> None:
    active = build_active_themes(_items(1.0, None, -2.0, 5.0, 0.0), limit=10)
    assert [item.name for item in active] == ["theme3", "theme0"]
    assert [item.change_pct for item in active] == [5.0, 1.0]


def test_active_themes_limit_honored() -> None:
    active = build_active_themes(_items(1.0, 2.0, 3.0, 4.0), limit=2)
    assert [item.change_pct for item in active] == [4.0, 3.0]


def test_active_themes_phase_strength_reason_attached() -> None:
    active = build_active_themes(_items(4.0), limit=5)
    item = active[0]
    assert item.phase == "accelerating"
    assert item.strength_score == 82  # 50 + 4*8
    assert item.reason == "industry/concept ranking gain"


# ---------------------------------------------------------------------------
# 排名归一化（ashare 行形态）
# ---------------------------------------------------------------------------


def test_normalize_ranking_accepts_ashare_shape() -> None:
    rows = [
        {"rank": 1, "industry": "半导体", "change_pct": 3.2},
        {"rank": 2, "industry": "银行", "change_pct": "-0.5"},
        {"industry": "  "},  # 空名 → 跳过
    ]
    items = normalize_ranking(rows, source="industry")
    assert [item.name for item in items] == ["半导体", "银行"]
    assert items[0].change_pct == pytest.approx(3.2)
    assert items[1].change_pct == pytest.approx(-0.5)
    assert items[0].source == "industry"
    assert items[0].rank == 1


# ---------------------------------------------------------------------------
# 上下文聚合 + 诚实降级
# ---------------------------------------------------------------------------


def _industry_rows() -> list[dict]:
    return [
        {"rank": 1, "industry": "半导体", "change_pct": 5.0},
        {"rank": 2, "industry": "证券", "change_pct": 2.0},
        {"rank": 3, "industry": "银行", "change_pct": -1.0},
    ]


def _concept_rows() -> list[dict]:
    return [{"rank": 1, "industry": "算力租赁", "change_pct": 4.0}]


def test_context_ok_when_both_sources_present() -> None:
    ctx = build_theme_context({"industry": _industry_rows(), "concept": _concept_rows()})
    assert ctx.status == "ok"
    assert ctx.data_quality.status == "ok"
    assert ctx.data_quality.missing_fields == []
    assert [item.name for item in ctx.active_themes] == ["半导体", "算力租赁", "证券"]
    assert [item.name for item in ctx.leading_industries] == ["半导体", "证券"]
    assert [item.name for item in ctx.leading_concepts] == ["算力租赁"]
    assert [item.name for item in ctx.lagging_themes] == ["银行"]
    assert ctx.theme_breadth.active_count == 3
    assert ctx.theme_breadth.leading_industry_count == 2
    assert ctx.theme_breadth.leading_concept_count == 1
    assert ctx.theme_breadth.lagging_count == 1


def test_context_partial_when_concept_missing() -> None:
    ctx = build_theme_context({"industry": _industry_rows()})
    assert ctx.status == "partial"
    assert ctx.data_quality.missing_fields == ["concept_ranking"]
    # 行业侧数据仍然如实聚合，不因缺概念而清空
    assert [item.name for item in ctx.active_themes] == ["半导体", "证券"]


def test_context_partial_when_both_missing_is_honest() -> None:
    ctx = build_theme_context({})
    assert ctx.status == "partial"
    assert ctx.data_quality.missing_fields == ["industry_ranking", "concept_ranking"]
    assert ctx.active_themes == []
    assert ctx.theme_breadth.active_count == 0


def test_context_partial_when_industry_missing_only_concept() -> None:
    ctx = build_theme_context({"concept": _concept_rows()})
    assert ctx.status == "partial"
    assert ctx.data_quality.missing_fields == ["industry_ranking"]
    assert [item.name for item in ctx.active_themes] == ["算力租赁"]


def test_context_empty_rankings_ok_with_zero_breadth() -> None:
    # 来源都给了但为空列表 → 不是缺失，是当天没有数据
    ctx = build_theme_context({"industry": [], "concept": []})
    assert ctx.status == "ok"
    assert ctx.data_quality.missing_fields == []
    assert ctx.theme_breadth.active_count == 0


def test_context_unusable_rows_recorded_in_errors() -> None:
    # 行都缺 name → 归一化产出 0 条，但输入非空 → 记 errors
    ctx = build_theme_context({"industry": [{"rank": 1}], "concept": []})
    assert ctx.data_quality.errors == ["industry_ranking_unusable"]
    assert ctx.status == "partial"


def test_context_defaults() -> None:
    ctx = build_theme_context({"industry": _industry_rows(), "concept": _concept_rows()})
    assert isinstance(ctx, MarketThemeContext)
    assert ctx.market == "cn"
    assert ctx.schema_version == "market-theme-v1"
    assert ctx.trade_date is None


def test_context_limit_respected() -> None:
    rows = [{"rank": i + 1, "industry": f"板块{i}", "change_pct": i + 1.0} for i in range(6)]
    ctx = build_theme_context({"industry": rows, "concept": []}, limit=3)
    assert len(ctx.active_themes) == 3
    assert len(ctx.leading_industries) == 3
    assert ctx.theme_breadth.active_count == 3


# ---------------------------------------------------------------------------
# 个股角色
# ---------------------------------------------------------------------------


def _primary() -> PrimaryTheme:
    return PrimaryTheme(name="半导体", source="industry", phase="warming", rank=1, change_pct=5.0)


def _boards() -> list[StockBoardPosition]:
    return [StockBoardPosition(name="半导体", source="industry", rank=1, change_pct=5.0)]


def test_role_leader() -> None:
    role = infer_stock_role(
        "600000",
        _primary(),
        _boards(),
        has_market_match=True,
        hotspot_constituents=[{"code": "600000", "theme": "半导体"}],
        leader_stocks=[{"code": "600000", "theme_name": "半导体"}],
    )
    assert role == "leader"


def test_role_follower() -> None:
    role = infer_stock_role(
        "600000",
        _primary(),
        _boards(),
        has_market_match=True,
        hotspot_constituents=[{"code": "600000", "theme": "半导体"}],
        leader_stocks=[],
    )
    assert role == "follower"


def test_role_edge_when_no_role_evidence() -> None:
    # 有主题材但热点/领涨名单都查不到该股 → 有关联板块记 edge
    role = infer_stock_role(
        "600000",
        _primary(),
        _boards(),
        has_market_match=True,
        hotspot_constituents=[],
        leader_stocks=[],
    )
    assert role == "edge"


def test_role_unknown_when_no_boards_no_theme() -> None:
    assert (
        infer_stock_role(
            "600000",
            None,
            [],
            has_market_match=False,
            hotspot_constituents=[],
            leader_stocks=[],
        )
        == "unknown"
    )


def test_role_edge_fallback_with_boards_but_no_theme() -> None:
    # 无主题材但有关联板块 → edge（证据薄），不是 unknown
    role = infer_stock_role(
        "600000",
        None,
        _boards(),
        has_market_match=False,
        hotspot_constituents=[],
        leader_stocks=[],
    )
    assert role == "edge"


def test_role_market_mismatch_is_edge_with_boards() -> None:
    role = infer_stock_role(
        "600000",
        _primary(),
        _boards(),
        has_market_match=False,
        hotspot_constituents=[{"code": "600000", "theme": "半导体"}],
        leader_stocks=[],
    )
    assert role == "edge"


def test_role_code_normalization_case_insensitive() -> None:
    role = infer_stock_role(
        "sz000001",
        _primary(),
        _boards(),
        has_market_match=True,
        hotspot_constituents=[{"code": "SZ000001", "concept": "半导体"}],
        leader_stocks=[],
    )
    assert role == "follower"
