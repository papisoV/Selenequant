# Absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 tick-stock-panel contributors
# Source: https://github.com/shy3130/tick-stock-panel
"""AI 复盘 prompt tests — **the redline suite**.

The recap's product value is that it is an objective description, not advice.
That is enforced in two places and both are pinned here:

1. ``SYSTEM_PROMPT`` names every term in ``FORBIDDEN_PHRASES``. The list is a
   constant precisely so a prompt edit that silently drops a term fails here.
2. The service appends ``DISCLAIMER`` when the model omits it, idempotently.

Everything else in this file is about the prompt not lying: unavailable data
must read as unavailable, never as a zero.
"""

from __future__ import annotations


from selene_space_ashare import market_recap_prompt as prompt


# ---------------------------------------------------------------------------
# Redline
# ---------------------------------------------------------------------------


def test_every_forbidden_phrase_is_named_in_the_system_prompt():
    missing = [p for p in prompt.FORBIDDEN_PHRASES if p not in prompt.SYSTEM_PROMPT]
    assert missing == [], f"prompt stopped forbidding: {missing}"


def test_forbidden_list_is_not_empty():
    # A vacuous guard: an empty tuple passes any "all(...)" loop above.
    assert len(prompt.FORBIDDEN_PHRASES) >= 10


def test_system_prompt_carries_the_disclaimer_verbatim():
    assert prompt.DISCLAIMER in prompt.SYSTEM_PROMPT


def test_system_prompt_forbids_position_and_direction_language():
    for term in ("仓位", "买卖方向", "操作指令"):
        assert term in prompt.SYSTEM_PROMPT


def test_ensure_disclaimer_is_idempotent():
    once = prompt.ensure_disclaimer("## 1. 定调\n今天的市场……")
    assert prompt.DISCLAIMER in once
    assert prompt.ensure_disclaimer(once) == once


def test_ensure_disclaimer_leaves_an_already_disclaimed_report_alone():
    body = f"报告正文\n\n{prompt.DISCLAIMER}"
    assert prompt.ensure_disclaimer(body) == body


def test_ensure_disclaimer_on_empty_text_returns_the_disclaimer_alone():
    assert prompt.ensure_disclaimer("") == prompt.DISCLAIMER


# ---------------------------------------------------------------------------
# Formatting — percent numbers, not fractions
# ---------------------------------------------------------------------------


def test_fmt_pct_treats_input_as_a_percent_number():
    assert prompt.fmt_pct(1.23) == "+1.23%"
    assert prompt.fmt_pct(-0.45) == "-0.45%"
    assert prompt.fmt_pct(0) == "+0.00%"
    assert prompt.fmt_pct(None) == "—"
    assert prompt.fmt_pct(float("nan")) == "—"


def test_fmt_yi_converts_yuan_to_yi():
    assert prompt.fmt_yi(1.234e12) == "12340 亿"
    assert prompt.fmt_yi(None) == "—"


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------


def _overview(**overrides):
    base = {
        "as_of": "2026-10-20",
        "status": "ok",
        "indices": [
            {
                "symbol": "000001.SH",
                "name": "上证指数",
                "last_price": 3333.0,
                "change_pct": 1.0,
            },
        ],
        "breadth": {"up": 3285, "down": 2141, "flat": 138, "up_pct": 59.0},
        "amount": {"total": None, "available": False},
        "limit": {
            "limit_up": 71,
            "broken": 68,
            "limit_down": 22,
            "max_boards": 11,
            "seal_rate": 51.0,
            "tiers": [],
        },
        "trend": {
            "above_ma5_pct": 61.0,
            "above_ma20_pct": 44.0,
            "above_ma60_pct": 30.0,
        },
        "activity": {"avg_vol_ratio": 1.1, "high_vol_pct": 8.0},
        "coverage": {"quoted": 5564, "universe": 5574, "ratio": 0.998},
        "radar": [],
        "emotion": {"score": 54, "label": "震荡"},
        "concept_rank": None,
        "industry_rank": None,
    }
    base.update(overrides)
    return base


def test_missing_amount_reads_as_unavailable_never_zero():
    text = prompt.build_user_prompt(_overview())
    assert "不含成交额" in text
    assert "成交额: 0 亿" not in text


def test_available_amount_is_rendered():
    text = prompt.build_user_prompt(
        _overview(amount={"total": 1.03e12, "available": True})
    )
    assert "两市成交额: 10300 亿" in text


def test_missing_indices_state_the_reason():
    text = prompt.build_user_prompt(
        _overview(indices=[], indices_reason="fetch_failed: timeout")
    )
    assert "指数行情不可用" in text and "timeout" in text


def test_empty_emotion_renders_as_unavailable():
    text = prompt.build_user_prompt(
        _overview(emotion={"score": None, "label": "无数据"}, radar=[])
    )
    assert "情绪温度: 不可用" in text
    # A null must not be formatted as a real temperature.
    assert "情绪温度: None" not in text


def test_lhb_section_is_absent_without_rows():
    text = prompt.build_user_prompt(_overview(), lhb_context="")
    assert "龙虎榜资金动向" not in text


def test_lhb_section_appears_with_rows():
    text = prompt.build_user_prompt(_overview(), lhb_context="- 上榜家数: 66")
    assert "## 龙虎榜资金动向" in text and "上榜家数: 66" in text


def test_focus_is_appended_with_the_redline_restated():
    text = prompt.build_user_prompt(_overview(), focus="重点看半导体")
    assert "## 用户关注点" in text and "重点看半导体" in text
    assert "红线不变" in text


def test_no_news_says_so_without_inviting_invention():
    text = prompt.build_user_prompt(_overview(), news=None)
    assert "暂无新闻数据" in text
    assert "不要编造" in text


def test_news_items_render_with_source_and_date():
    text = prompt.build_user_prompt(
        _overview(),
        news=[
            {
                "title": "国常会部署稳增长",
                "source": "新华社",
                "published_date": "2026-10-20",
                "snippet": "会议指出……",
            }
        ],
    )
    assert "国常会部署稳增长" in text
    assert "新华社 / 2026-10-20" in text


def test_no_premarket_feed_section_is_present():
    """B4-② 盘前风向标 is a paid fuyao feed; the section is absent, not empty."""
    text = prompt.build_user_prompt(_overview())
    assert "盘前风向标" not in text


# ---------------------------------------------------------------------------
# Summary line
# ---------------------------------------------------------------------------


def test_summary_uses_short_index_names():
    summary = prompt.recap_summary(_overview())
    assert summary.startswith("上+1.00%")
    assert "情绪54(震荡)" in summary
    assert "涨停71" in summary
    assert "成交额不可用" in summary


def test_summary_says_when_emotion_is_unavailable():
    summary = prompt.recap_summary(
        _overview(emotion={"score": None, "label": "无数据"})
    )
    assert "情绪不可用" in summary
    assert "None" not in summary
