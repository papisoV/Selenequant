"""B1 — 历史分析纵向对比上下文（render_history_context + flow 注入）。

设计意图为最近 N 次分析纵向对比（仅设计意图，独立实现，见包内
PROVENANCE.md 的参考实现登记）。覆盖：

- 摘录字段：含 diagnosis_summary / decision，不含 prompt / raw
- 跳过异常记录（不分成败但失败记录不可作对照）
- token 硬顶 1500：超限丢弃靠后块
- limit 5 配额
- flow 级：history_n=0 时 prompt 逐字节不变；history_n>0 注入同 interval
  最近记录；无 list_recent 的桩静默退化
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from selene_space_llm.base import LLMConfig

from selene_space_market_analysis.experience_library import (
    render_history_context,
)
from selene_space_market_analysis.flow import MarketAnalysisFlow
from selene_space_market_analysis.persistence import InMemoryPersistence

_BAR_FEATURES = {"adx": 28.5, "ema20": 50000.0}

_INPUT = {
    "symbol": "BTC/USDT",
    "interval": "1h",
    "bar_features": _BAR_FEATURES,
    "klines": [{"open": 49000, "high": 51000, "low": 48000, "close": 50000}],
}

_STAGE1_PROCEED = {
    "market_regime": "trend_up",
    "entry_gate": "proceed",
    "signal_patterns": [],
    "trend_context": {"direction": "up"},
    "bar_features": _BAR_FEATURES,
    "evidence": [],
    "diagnosis_summary": {"regime": "trend_up", "reason": "均线多头排列"},
}

_STAGE1_WAIT = {
    "market_regime": "range",
    "entry_gate": "wait",
    "signal_patterns": [],
    "trend_context": {"direction": "flat"},
    "bar_features": _BAR_FEATURES,
    "evidence": [],
    "diagnosis_summary": {"regime": "range", "reason": "无明确趋势"},
}

_STAGE2: dict[str, Any] = {
    "decision": "limit",
    "dispatch_playbooks": [],
    "decision_summary": {"reason": "顺势回调进场"},
    "evidence": [],
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
        "decision_path": ["gate:proceed", "趋势确认"],
    },
}

_HISTORY_BLOCK = "【历史记录对比（仅参考）】"


def _record(
    i: int,
    *,
    exception: bool = False,
    interval: str = "1h",
    decision: str = "limit",
    reason: str = "均线多头排列",
) -> dict[str, Any]:
    stage1 = {
        **_STAGE1_PROCEED,
        "diagnosis_summary": {"regime": "trend_up", "reason": reason},
    }
    rec = {
        "meta": {"record_id": f"ar-{i}"},
        "input_data": {**_INPUT, "interval": interval},
        "stage1": stage1,
        "stage2": {**_STAGE2, "decision": decision},
        "prompt": {"stage1": [{"role": "user", "content": "SECRET-PROMPT"}]},
        "raw": {"stage1": "SECRET-RAW"},
    }
    if exception:
        rec["exception"] = {"type": "RuntimeError", "message": "boom"}
        rec["stage1"] = _STAGE1_WAIT
    return rec


# --------------------------------------------------------------------------
# render_history_context 纯函数
# --------------------------------------------------------------------------
def test_excerpt_fields_include_summary_and_decision_exclude_prompt_raw() -> None:
    out = render_history_context([_record(1, decision="market")])
    assert "均线多头排列" in out  # diagnosis_summary 摘录
    assert '"decision": "market"' in out
    assert "SECRET-PROMPT" not in out
    assert "SECRET-RAW" not in out
    assert "ar-1" not in out  # meta.record_id 不摘录


def test_exception_records_are_skipped() -> None:
    out = render_history_context([_record(1, exception=True), _record(2)])
    assert "【历史记录对比 1】" in out
    assert "boom" not in out
    # 异常记录被跳过 → 编号连续，只有 1 块。
    assert "【历史记录对比 2】" not in out


def test_token_cap_drops_tail_blocks() -> None:
    # 每块摘录超 _CASE_MAX_CHARS(800) 被截断；逐块累计 token 预算，
    # 超限后靠后块被丢弃（cap 下块数 < 无 cap 下块数）。
    big = "市" * 2000  # 远超截断阈值，保证每块顶满 800 字符
    records = [_record(i, reason=big) for i in range(3)]
    capped = render_history_context(records, token_cap=800)
    uncapped = render_history_context(records, token_cap=10_000)
    assert "【历史记录对比 1】" in capped
    assert capped.count("【历史记录对比") < uncapped.count("【历史记录对比")
    assert uncapped.count("【历史记录对比") == 3


def test_limit_caps_block_count() -> None:
    out = render_history_context([_record(i) for i in range(9)], limit=5)
    assert out.count("【历史记录对比") == 5


def test_render_history_context_empty_records() -> None:
    assert render_history_context([]) == ""
    assert render_history_context([_record(1, exception=True)]) == ""


# --------------------------------------------------------------------------
# flow 级：history_n 注入
# --------------------------------------------------------------------------
def _config() -> LLMConfig:
    return LLMConfig(
        model="test-model",
        temperature=0.2,
        max_tokens=256,
        api_key=None,
        base_url=None,
    )


class _ListRecentPersistence(InMemoryPersistence):
    """InMemoryPersistence + list_recent（模拟真实 SQLite 持久化的查询面）。"""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        super().__init__()
        self._history = records

    async def list_recent(
        self, *, symbol: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        return self._history[:limit]


def _capturing_flow(persistence):
    """构造注入假 provider 的 flow，捕获每次调用的完整消息列表。"""
    from selene_space_market_analysis.llm_provider import HttpLLMProvider

    captured: list[list[dict]] = []

    async def recording_fake(messages):
        captured.append([{"role": m.role, "content": m.content} for m in messages])
        return json.dumps(_STAGE1_WAIT)

    provider = HttpLLMProvider(config=_config(), provider=recording_fake)
    flow = MarketAnalysisFlow(llm=provider, persistence=persistence)
    return flow, captured


def _stage1_user_prompt(captured: list[list[dict]], index: int) -> str:
    return str(captured[index][-1]["content"])


@pytest.mark.asyncio
async def test_history_n_zero_prompt_unchanged() -> None:
    records = [_record(1), _record(2), _record(3)]
    persistence = _ListRecentPersistence(records)
    flow, captured = _capturing_flow(persistence)

    await flow.analyze(_INPUT, input_id="i", session_id="s", history_n=0)
    no_history = _stage1_user_prompt(captured, 0)

    # 与完全不注入的基线逐字节一致（不依赖记录存在与否）。
    persistence2 = _ListRecentPersistence(records)
    flow2, captured2 = _capturing_flow(persistence2)
    await flow2.analyze(_INPUT, input_id="i", session_id="s")
    assert no_history == _stage1_user_prompt(captured2, 0)
    assert _HISTORY_BLOCK not in no_history


@pytest.mark.asyncio
async def test_history_n_injects_recent_same_interval() -> None:
    records = [
        _record(1, interval="1h"),
        _record(2, interval="1h"),
        _record(3, interval="4h"),  # 不同 interval 被过滤
        _record(4, interval="1h", exception=True),  # 异常被过滤
    ]
    persistence = _ListRecentPersistence(records)
    flow, captured = _capturing_flow(persistence)

    await flow.analyze(_INPUT, input_id="i", session_id="s", history_n=3)
    prompt = _stage1_user_prompt(captured, 0)
    assert _HISTORY_BLOCK in prompt
    assert "【历史记录对比 1】" in prompt
    assert "【历史记录对比 2】" in prompt
    # 只注入同 interval 的非异常记录（2 条），编号不补位。
    assert "【历史记录对比 3】" not in prompt
    assert "boom" not in prompt


@pytest.mark.asyncio
async def test_no_list_recent_silently_degrades() -> None:
    # InMemoryPersistence 桩无 list_recent → history_n>0 静默退化（不报错）。
    persistence = InMemoryPersistence()
    flow, captured = _capturing_flow(persistence)

    await flow.analyze(_INPUT, input_id="i", session_id="s", history_n=3)
    prompt = _stage1_user_prompt(captured, 0)
    assert _HISTORY_BLOCK not in prompt
