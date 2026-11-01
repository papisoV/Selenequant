"""US-012 C2 — MarketAnalysisFlow.analyze_incremental 增量分析单测。

覆盖验收标准：
- 增量：新 K 线复用上次诊断摘要（prior 上下文）+ 上次记录 id（meta.prev_record_id）
- 完整重推（analyze）不注入 prior 段落；prev_record=None 退化为完整重推
- 增量在 gate 短路与全流程两种路径下均正确注入 prior
"""
from __future__ import annotations

import json

import pytest

from selene_space_llm.base import LLMConfig

from selene_space_market_analysis.flow import MarketAnalysisFlow
from selene_space_market_analysis.persistence import InMemoryPersistence

_BAR_FEATURES = {"adx": 28.5, "ema20": 50000.0}

_INPUT = {
    "symbol": "BTCUSDT",
    "interval": "1d",
    "bar_features": _BAR_FEATURES,
    "klines": [{"open": 49000, "high": 51000, "low": 48000, "close": 50000}],
}

_DIAGNOSIS_SUMMARY_PROCEED = {"regime": "trend_up", "reason": "均线多头排列"}

# entry_gate=wait → 短路场景的 Stage1 合法输出。
_STAGE1_WAIT = {
    "market_regime": "range",
    "entry_gate": "wait",
    "signal_patterns": [
        {"name": "compression", "bias": "neutral", "confidence": 0.6, "kline_range": "K1-K10"}
    ],
    "trend_context": {"direction": "flat"},
    "bar_features": _BAR_FEATURES,
    "evidence": [
        {"kind": "pattern", "key": "range_boundary", "statement": "价格在区间内震荡", "kline_range": "K1-K10"}
    ],
    "diagnosis_summary": {"regime": "range", "reason": "无明确趋势"},
}

# entry_gate=proceed → 全流程场景的 Stage1/Stage2 合法输出。
_STAGE1_PROCEED = {
    "market_regime": "trend_up",
    "entry_gate": "proceed",
    "signal_patterns": [
        {"name": "bull_flag", "bias": "bullish", "confidence": 0.75, "kline_range": "K1-K10"}
    ],
    "trend_context": {"direction": "up"},
    "bar_features": _BAR_FEATURES,
    "evidence": [
        {"kind": "pattern", "key": "breakout", "statement": "回踩确认后放量", "kline_range": "K1-K10"}
    ],
    "diagnosis_summary": _DIAGNOSIS_SUMMARY_PROCEED,
}

_STAGE2_PROCEED = {
    "decision": "limit",
    "dispatch_playbooks": [],
    "decision_summary": {
        "diagnosis_summary": _DIAGNOSIS_SUMMARY_PROCEED,
        "reason": "顺势回调进场",
    },
    "evidence": [
        {"kind": "decision", "key": "entry", "statement": "回踩支撑位轻仓", "kline_range": "K1-K10"}
    ],
    "next_bar_forecast": None,
    "trade_setup": {
        "direction": "long",
        "entry_price": 100.0,
        "stop_loss_price": 95.0,
        "take_profit_price": 110.0,
        "risk_reward_ratio": 2.0,
        "estimated_win_rate": 0.5,
        "confidence": 0.7,
        "trader_equation_passed": True,
        "decision_path": ["gate:proceed", "趋势确认", "入场", "止损设置"],
    },
}

_PRIOR_BLOCK = "【上次结论（复用，仅参考）】"


def _config() -> LLMConfig:
    return LLMConfig(
        model="test-model",
        temperature=0.2,
        max_tokens=256,
        api_key=None,
        base_url=None,
    )


def _flow_capturing(fake, *, playbooks=None):
    """构造注入假 provider 的 flow，捕获每次调用的完整消息列表。"""
    from selene_space_market_analysis.llm_provider import HttpLLMProvider

    captured: list[list[dict]] = []

    async def recording_fake(messages):
        captured.append([{"role": m.role, "content": m.content} for m in messages])
        return fake()

    provider = HttpLLMProvider(config=_config(), provider=recording_fake)
    persistence = InMemoryPersistence()
    flow = MarketAnalysisFlow(
        llm=provider,
        persistence=persistence,
        playbook_router=playbooks,
    )
    return flow, persistence, captured


def _stage1_user_prompt(captured: list[list[dict]], index: int) -> str:
    return captured[index][-1]["content"]


# ---------------------------------------------------------------------------
# C2 — 增量分析 prior 注入 + prev_record_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_incremental_threads_prior_and_prev_record_id() -> None:
    """增量：复用上次诊断摘要（prior 注入 stage1 prompt）+ meta.prev_record_id。"""
    flow, persistence, captured = _flow_capturing(lambda: json.dumps(_STAGE1_WAIT))

    first = await flow.analyze(_INPUT, input_id="BTCUSDT-1d", session_id="s1")
    # 完整重推不注入 prior 段落
    assert _PRIOR_BLOCK not in _stage1_user_prompt(captured, 0)

    second = await flow.analyze_incremental(
        _INPUT,
        prev_record=first,
        input_id="BTCUSDT-1d",
        session_id="s1",
    )

    assert second["meta"]["prev_record_id"] == first["meta"]["record_id"]
    prompt = _stage1_user_prompt(captured, 1)
    assert _PRIOR_BLOCK in prompt
    indented = json.dumps(_STAGE1_WAIT["diagnosis_summary"], ensure_ascii=False, indent=2)
    assert indented in prompt
    assert len(persistence.records) == 2


@pytest.mark.asyncio
async def test_incremental_without_prev_record_falls_back() -> None:
    """prev_record=None → 退化为完整重推（无 prior 段落、无 prev_record_id）。"""
    flow, persistence, captured = _flow_capturing(lambda: json.dumps(_STAGE1_WAIT))

    record = await flow.analyze_incremental(
        _INPUT,
        prev_record=None,
        input_id="BTCUSDT-1d",
        session_id="s1",
    )

    assert _PRIOR_BLOCK not in _stage1_user_prompt(captured, 0)
    assert "prev_record_id" not in record["meta"]
    assert len(persistence.records) == 1


@pytest.mark.asyncio
async def test_incremental_proceed_full_flow_threads_prior() -> None:
    """proceed 全流程（2 次 call/次）下，增量仍正确注入 prior。"""
    responses = iter(
        [
            json.dumps(_STAGE1_PROCEED),
            json.dumps(_STAGE2_PROCEED),
            json.dumps(_STAGE1_PROCEED),
            json.dumps(_STAGE2_PROCEED),
        ]
    )
    flow, persistence, captured = _flow_capturing(lambda: next(responses))

    first = await flow.analyze(_INPUT, input_id="BTCUSDT-1d", session_id="s1")
    assert _PRIOR_BLOCK not in _stage1_user_prompt(captured, 0)

    second = await flow.analyze_incremental(
        _INPUT,
        prev_record=first,
        input_id="BTCUSDT-1d",
        session_id="s1",
    )

    # 完整重推 2 次 call（index 0/1），增量第 1 次 call 在 index 2
    assert _PRIOR_BLOCK in _stage1_user_prompt(captured, 2)
    assert second["meta"]["prev_record_id"] == first["meta"]["record_id"]
    assert second["stage2"]["decision"] == "limit"
    assert len(persistence.records) == 2
