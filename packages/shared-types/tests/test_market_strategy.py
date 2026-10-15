"""Behavior lock for the market strategy blueprints.

Absorbed from daily_stock_analysis ``tests/test_market_strategy.py`` (MIT,
Copyright (c) 2026 ZhuLinsen) at commit ``ce364e45``.  Upstream keeps the
Chinese blueprint data in ``src/core/market_strategy.py`` and the alternate
language variants as inline strings in ``src/market_analyzer.py``; the tests
below assert the *content* of both, plus Selene's additions:

- A blueprint carries positioning / principles / dimensions (with checkpoints)
  / action framework, and renders a prompt block with language-appropriate
  headings.
- Every market in Selene's universe resolves to a blueprint (TW and CRYPTO are
  Selene-original extensions upstream lacks).
- Rendering is fail-open: an unknown market yields "" rather than a guess.
"""

from __future__ import annotations

import pytest

from selene_space_types.market_strategy import (
    MarketStrategyBlueprint,
    StrategyDimension,
    render_strategy_blueprint,
    strategy_blueprint,
)


# --- CN --------------------------------------------------------------------------


def test_cn_blueprint_prompt_block_contains_action_framework_zh():
    block = strategy_blueprint("cn", "zh").to_prompt_block()
    assert "A股市场三段式复盘策略" in block
    assert "### 行动框架" in block
    assert "进攻" in block


def test_cn_blueprint_english_uses_three_phase_title():
    block = strategy_blueprint("cn", "en").to_prompt_block()
    assert "A-share Three-Phase Recap Strategy" in block
    assert "### Action Framework" in block
    assert "Offensive" in block
    assert "A股市场三段式复盘策略" not in block


def test_cn_markdown_block_is_dimension_names_only():
    md = strategy_blueprint("cn", "zh").to_markdown_block()
    assert md.startswith("### 六、策略框架")
    assert "**趋势结构**" in md

    md_en = strategy_blueprint("cn", "en").to_markdown_block()
    assert md_en.startswith("### 6. Strategy Framework")
    assert "**Trend Structure**" in md_en


# --- US / HK / JP / KR -----------------------------------------------------------


def test_us_blueprint_contains_regime_strategy():
    block = strategy_blueprint("us", "en").to_prompt_block()
    assert "US Market Regime Strategy" in block
    assert "Risk-on" in block
    assert "Macro & Flows" in block


def test_us_blueprint_chinese_variant():
    block = strategy_blueprint("us", "zh").to_prompt_block()
    assert "美股市场三段式复盘策略" in block
    assert "US Market Regime Strategy" not in block


@pytest.mark.parametrize(
    ("market", "title", "dimension"),
    [
        ("hk", "Hong Kong Market Regime Strategy", "Capital Flows"),
        ("jp", "Japan Market Regime Strategy", "Macro & FX"),
        ("kr", "Korea Market Regime Strategy", "Technology Cycle"),
    ],
)
def test_regional_english_blueprints(market, title, dimension):
    block = strategy_blueprint(market, "en").to_prompt_block()
    assert title in block
    assert dimension in block


@pytest.mark.parametrize(
    ("market", "title"),
    [
        ("hk", "港股市场三段式复盘策略"),
        ("jp", "日本市场三段式复盘策略"),
        ("kr", "韩国市场三段式复盘策略"),
    ],
)
def test_regional_chinese_blueprints(market, title):
    block = strategy_blueprint(market, "zh").to_prompt_block()
    assert title in block


def test_jp_kr_english_blueprints_carry_localized_principles():
    jp = strategy_blueprint("jp", "en").to_prompt_block()
    assert "without inventing breadth" in jp
    assert "只基于可得指数" not in jp


# --- Selene-original extensions --------------------------------------------------


def test_tw_blueprint_mentions_taiwan_institutional_flows():
    block = strategy_blueprint("tw", "en").to_prompt_block()
    assert "Taiwan Market Regime Strategy" in block
    assert "Institutional Flows" in block
    assert "TAIEX" in block

    zh = strategy_blueprint("tw", "zh").to_prompt_block()
    assert "台股市场三段式复盘策略" in zh
    assert "法人资金" in zh


def test_crypto_blueprint_is_derivatives_first():
    block = strategy_blueprint("crypto", "en").to_prompt_block()
    assert "Crypto Market Regime Strategy" in block
    assert "Derivatives & Flows" in block
    assert "funding rate" in block


@pytest.mark.parametrize("market", ["cn", "hk", "us", "jp", "kr", "tw", "crypto"])
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_every_market_has_a_blueprint_in_both_languages(market, lang):
    blueprint = strategy_blueprint(market, lang)
    assert blueprint is not None
    assert blueprint.lang == lang
    assert blueprint.title
    assert blueprint.principles
    assert blueprint.dimensions
    assert blueprint.action_framework


def test_aliases_resolve_to_the_same_blueprint():
    assert (
        strategy_blueprint("ashare", "zh").region
        == strategy_blueprint("cn", "zh").region
        == "CN"
    )
    assert strategy_blueprint("kospi", "en").region == "KR"
    assert strategy_blueprint("USA", "en").region == "US"  # case-insensitive
    assert strategy_blueprint("perpetual", "en").region == "CRYPTO"


def test_lang_defaults_to_english():
    assert strategy_blueprint("cn").lang == "en"
    assert strategy_blueprint("cn", "zh-CN").lang == "zh"  # zh-prefixed dialects


# --- fail-open -------------------------------------------------------------------


def test_unknown_market_returns_none_and_empty_render():
    assert strategy_blueprint("mars", "en") is None
    assert render_strategy_blueprint("mars", "en") == ""
    assert render_strategy_blueprint("", "en") == ""
    assert render_strategy_blueprint(None, "en") == ""


def test_render_helpers_dispatch_prompt_vs_markdown():
    prompt = render_strategy_blueprint("us", "en")
    assert prompt.startswith("## Strategy Blueprint: US Market Regime Strategy")
    markdown = render_strategy_blueprint("us", "en", markdown=True)
    assert markdown.startswith("### 6. Strategy Framework")


# --- structure -------------------------------------------------------------------


def test_blueprint_and_dimension_are_frozen():
    blueprint = strategy_blueprint("cn", "zh")
    with pytest.raises(Exception):
        blueprint.title = "x"  # type: ignore[misc]
    assert isinstance(blueprint.dimensions[0], StrategyDimension)
    assert isinstance(blueprint, MarketStrategyBlueprint)


def test_all_dimensions_carry_checkpoints_or_objective():
    for blueprint in (
        strategy_blueprint(market, lang)
        for market in ("cn", "hk", "us", "jp", "kr", "tw", "crypto")
        for lang in ("zh", "en")
    ):
        for dim in blueprint.dimensions:
            assert dim.name
            assert dim.objective
