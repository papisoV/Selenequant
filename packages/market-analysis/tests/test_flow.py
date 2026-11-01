"""US-005 — MarketAnalysisFlow 两阶段编排单测（gate 短路 + 全流程 + partial 落盘）。

覆盖验收标准：
- A2 gate 短路：entry_gate=wait/unknown 时全程仅 1 次 LLM call，合成 wait 记录
  可重放过市场校验器（validate_stage2）
- 全流程：proceed → Stage1 + Stage2 共 2 次 call + playbook 路由落盘
- A8 网络错误：模拟超时 → partial record 落盘含 exception 字段可查（调用不丢）
- US-201 JSON 修复：strict→lenient 归一化、repaired 语义降级、meta.json_repaired 标记
- US-202 经验库：stage1 prompt 注入历史案例 / 不足 3 条退化不注入
"""
from __future__ import annotations

import json

import pytest
from typing import Any

from selene_space_llm.base import LLMConfig
from selene_space_llm.openai_compat import OpenAICompatProvider

from selene_space_market_analysis.exceptions import ProviderError, SemanticError
from selene_space_market_analysis.experience_library import ExperienceLibrary
from selene_space_market_analysis.flow import MarketAnalysisFlow
from selene_space_market_analysis.models import Stage1Diagnosis, Stage2Decision
from selene_space_market_analysis.persistence import InMemoryPersistence
from selene_space_market_analysis.validator import validate_stage2
from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.analysis_records import (
    AnalysisRecordRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager

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

# entry_gate=proceed → 全流程场景的 Stage1 合法输出。
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


def _config() -> LLMConfig:
    return LLMConfig(
        model="test-model",
        temperature=0.2,
        max_tokens=256,
        api_key=None,
        base_url=None,
    )


def _flow_with(fake, *, playbooks=None):
    """构造注入假 provider 的 flow + 内存持久化，返回 (flow, persistence, calls)。"""
    from selene_space_market_analysis.llm_provider import HttpLLMProvider

    calls: list[str] = []

    async def counting_fake(messages):
        calls.append("call")
        return fake()

    provider = HttpLLMProvider(config=_config(), provider=counting_fake)
    persistence = InMemoryPersistence()
    flow = MarketAnalysisFlow(
        llm=provider,
        persistence=persistence,
        playbook_router=playbooks,
    )
    return flow, persistence, calls


# ---------------------------------------------------------------------------
# A2 — gate 短路
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_short_circuit_single_llm_call() -> None:
    """entry_gate=wait → 全程仅 1 次 LLM call，落盘合成 wait 记录（A2）。"""
    flow, persistence, calls = _flow_with(lambda: json.dumps(_STAGE1_WAIT))

    record = await flow.analyze(_INPUT, input_id="BTCUSDT-1d", session_id="s1")

    assert calls == ["call"]  # 只发生 1 次 LLM call，stage2 跳过
    assert record["stage2"]["decision"] == "no_trade"
    assert record["stage2"]["decision_summary"]["short_circuited"] is True
    assert record["stage2"]["decision_summary"]["entry_gate"] == "wait"
    assert record["stage1"]["market_regime"] == "range"
    assert len(persistence.records) == 1


@pytest.mark.asyncio
async def test_gate_short_circuit_wait_record_replays() -> None:
    """合成 wait 记录可重放过市场校验器（validator 对 no_trade 显式宽容）。"""
    flow, persistence, _calls = _flow_with(lambda: json.dumps(_STAGE1_WAIT))

    record = await flow.analyze(_INPUT, input_id="BTCUSDT-1d", session_id="s1")

    stage1 = Stage1Diagnosis.model_validate(record["stage1"])
    stage2 = Stage2Decision.model_validate(record["stage2"])
    report = validate_stage2(stage2, stage1=stage1)
    assert report.is_valid, [e.message for e in report.errors]


@pytest.mark.asyncio
async def test_gate_short_circuit_unknown_synthesizes() -> None:
    """entry_gate=unknown 同样短路，全程 1 次 LLM call。"""
    stage1_unknown = dict(_STAGE1_WAIT, entry_gate="unknown")
    flow, persistence, calls = _flow_with(lambda: json.dumps(stage1_unknown))

    record = await flow.analyze(_INPUT, input_id="id", session_id="s")

    assert calls == ["call"]
    assert record["stage2"]["decision"] == "no_trade"
    assert record["stage2"]["decision_summary"]["entry_gate"] == "unknown"


# ---------------------------------------------------------------------------
# 全流程 — proceed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_flow_proceed_two_calls_with_playbook_routing() -> None:
    """proceed → Stage1 + Stage2 共 2 次 call，playbook 路由结果落盘。"""
    responses = iter([json.dumps(_STAGE1_PROCEED), json.dumps(_STAGE2_PROCEED)])
    flow, persistence, calls = _flow_with(
        lambda: next(responses),
        playbooks=lambda stage1: ["al-brooks-channels", "al-brooks-trend"] if stage1.entry_gate == "proceed" else [],
    )

    record = await flow.analyze(_INPUT, input_id="BTCUSDT-1d", session_id="s1")

    assert len(calls) == 2
    assert record["stage2"]["decision"] == "limit"
    assert record["dispatch_playbooks"] == ["al-brooks-channels", "al-brooks-trend"]
    assert record["stage2"]["decision_summary"]["diagnosis_summary"] == _DIAGNOSIS_SUMMARY_PROCEED
    assert len(persistence.records) == 1


# ---------------------------------------------------------------------------
# A8 — 网络错误 → partial 落盘
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_network_error_persists_partial_record() -> None:
    """模拟超时/连接失败 → partial record 落盘含 exception 字段（A8）。"""
    from selene_space_market_analysis.llm_provider import HttpLLMProvider

    async def failing_fake(messages):
        raise TimeoutError("connect timeout")

    provider = HttpLLMProvider(config=_config(), provider=failing_fake)
    persistence = InMemoryPersistence()
    flow = MarketAnalysisFlow(llm=provider, persistence=persistence)

    with pytest.raises(ProviderError):
        await flow.analyze(_INPUT, input_id="BTCUSDT-1d", session_id="s1")

    assert len(persistence.records) == 1
    partial = persistence.records[0]
    assert "exception" in partial
    assert partial["exception"]["type"] == "ProviderError"
    assert "connect timeout" in partial["exception"]["message"]
    # stage1 未完成 → stage1 为空 dict，但 meta/input 保留（调用不丢）
    assert partial["stage1"] == {}
    assert partial["meta"]["input_id"] == "BTCUSDT-1d"
    assert partial["input_data"]["symbol"] == "BTCUSDT"


@pytest.mark.asyncio
async def test_stage2_failure_persists_stage1_partial() -> None:
    """stage2 失败 → partial 保留已成功的 stage1 + exception 字段。"""
    from selene_space_market_analysis.llm_provider import HttpLLMProvider

    responses = iter([json.dumps(_STAGE1_PROCEED)])

    async def fake(messages):
        try:
            return next(responses)
        except StopIteration:
            raise TimeoutError("read timeout") from None

    provider = HttpLLMProvider(config=_config(), provider=fake)
    persistence = InMemoryPersistence()
    flow = MarketAnalysisFlow(llm=provider, persistence=persistence)

    with pytest.raises(ProviderError):
        await flow.analyze(_INPUT, input_id="BTCUSDT-1d", session_id="s1")

    partial = persistence.records[0]
    assert partial["stage1"]["entry_gate"] == "proceed"  # stage1 已成功保留
    assert partial["exception"]["type"] == "ProviderError"
    assert partial["stage2"] is None


# ---------------------------------------------------------------------------
# US-201 — 两 stage JSON 先 strict 后 lenient 归一化
# ---------------------------------------------------------------------------


def _flow_capturing(fake, *, playbooks=None, experience_library=None):
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
        experience_library=experience_library,
    )
    return flow, persistence, captured


@pytest.mark.asyncio
async def test_stage1_truncated_json_repaired_and_marked() -> None:
    """stage1 截断 JSON → lenient 修复成功 → meta.json_repaired=true + raw 原样保留。"""
    truncated = json.dumps(_STAGE1_WAIT)[:-1]  # 去掉末尾 } → 可修复截断
    flow, persistence, _captured = _flow_with(lambda: truncated)

    record = await flow.analyze(_INPUT, input_id="BTCUSDT-1d", session_id="s1")

    assert record["stage1"]["market_regime"] == "range"
    assert record["meta"]["json_repaired"] is True
    assert record["raw"]["stage1"] == truncated  # 原始 raw 原样保留


@pytest.mark.asyncio
async def test_repaired_semantic_error_is_retryable_feedback() -> None:
    """lenient 修复后语义校验失败 → 降级为可重试反馈（不命中终态 SemanticError）。"""
    bad_stage1 = dict(
        _STAGE1_WAIT,
        evidence=[
            {"kind": "pattern", "key": "x", "statement": "是", "kline_range": "K1-K10"}
        ],
    )
    # 两次响应都截断（都可被 lenient 修复）：第一次语义失败→重试，第二次成功
    responses = iter([json.dumps(bad_stage1)[:-1], json.dumps(_STAGE1_WAIT)[:-1]])
    flow, persistence, calls = _flow_with(lambda: next(responses))

    record = await flow.analyze(_INPUT, input_id="BTCUSDT-1d", session_id="s1")

    assert len(calls) == 2  # 语义错误被转可重试反馈，未抛 SemanticError
    assert record["stage1"]["market_regime"] == "range"
    assert record["meta"]["json_repaired"] is True


@pytest.mark.asyncio
async def test_non_repaired_semantic_error_is_terminal() -> None:
    """strict 直接成功（未修复）且语义校验失败 → 终态 SemanticError（不重试）。"""
    bad_stage1 = dict(
        _STAGE1_WAIT,
        evidence=[
            {"kind": "pattern", "key": "x", "statement": "是", "kline_range": "K1-K10"}
        ],
    )
    flow, persistence, calls = _flow_with(lambda: json.dumps(bad_stage1))

    with pytest.raises(SemanticError):
        await flow.analyze(_INPUT, input_id="BTCUSDT-1d", session_id="s1")

    assert len(calls) == 1  # 终态语义错误不重试
    assert "json_repaired" not in persistence.records[0]["meta"]


# ---------------------------------------------------------------------------
# US-202 — stage1 prompt 经验库案例注入
# ---------------------------------------------------------------------------


def _reference_record(symbol: str, interval: str, regime: str) -> dict:
    return {
        "meta": {"input_id": f"{symbol}-{interval}", "session_id": "s1", "timestamp": "t"},
        "input_data": {
            "symbol": symbol,
            "interval": interval,
            "klines": [{"timestamp": 1, "close": 50000.0}],
            "bar_features": _BAR_FEATURES,
        },
        "stage1": {
            "market_regime": regime,
            "entry_gate": "proceed",
            "signal_patterns": [
                {"name": "bull_flag", "bias": "bullish", "confidence": 0.75, "kline_range": "K1-K10"}
            ],
            "trend_context": {"direction": "up"},
            "bar_features": _BAR_FEATURES,
            "evidence": [
                {"kind": "pattern", "key": "breakout", "statement": "回踩确认后放量", "kline_range": "K1-K10"}
            ],
            "diagnosis_summary": {"regime": regime, "reason": "均线多头排列"},
        },
        "stage2": {
            "decision": "limit",
            "dispatch_playbooks": [],
            "decision_summary": {
                "diagnosis_summary": {"regime": regime, "reason": "均线多头排列"},
                "reason": "顺势回调进场",
            },
            "evidence": [
                {"kind": "decision", "key": "entry", "statement": "回踩支撑位轻仓", "kline_range": "K1-K10"}
            ],
            "next_bar_forecast": None,
        },
        "dispatch_playbooks": [],
        "usage_total": {"prompt_tokens": 10, "completion_tokens": 5},
    }


@pytest.mark.asyncio
async def test_stage1_injects_experience_cases_when_available() -> None:
    """经验库命中 ≥3 条可参考案例 → stage1 prompt 注入历史案例块。"""
    async with AsyncSQLiteManager(
        ":memory:", migration_runner=MigrationRunner(ALL_MIGRATIONS)
    ) as db:
        repo = AnalysisRecordRepository(db)
        for _ in range(3):
            await repo.save(_reference_record("BTCUSDT", "1d", "range"))
        await repo.save(_reference_record("BTCUSDT", "4h", "range"))  # 不同 interval 不命中

        lib = ExperienceLibrary(repo)
        flow, _persistence, captured = _flow_capturing(
            lambda: json.dumps(_STAGE1_WAIT), experience_library=lib
        )
        await flow.analyze(_INPUT, input_id="BTCUSDT-1d", session_id="s1")

        prompt = captured[0][-1]["content"]
        assert "【历史同类案例（仅参考）】" in prompt
        assert "【历史案例 1】" in prompt
        assert "【历史案例 3】" in prompt


@pytest.mark.asyncio
async def test_stage1_degrades_without_enough_cases() -> None:
    """可参考案例不足 3 条 → 不注入（prompt 退化为现状）。"""
    async with AsyncSQLiteManager(
        ":memory:", migration_runner=MigrationRunner(ALL_MIGRATIONS)
    ) as db:
        repo = AnalysisRecordRepository(db)
        await repo.save(_reference_record("BTCUSDT", "1d", "range"))  # 仅 1 条

        lib = ExperienceLibrary(repo)
        flow, _persistence, captured = _flow_capturing(
            lambda: json.dumps(_STAGE1_WAIT), experience_library=lib
        )
        await flow.analyze(_INPUT, input_id="BTCUSDT-1d", session_id="s1")

        prompt = captured[0][-1]["content"]
        assert "【历史同类案例（仅参考）】" not in prompt


# ---------------------------------------------------------------------------
# US-301 — 结构特征 bar_features 锚定（A-301）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stage1_tampered_structure_feature_triggers_cheat() -> None:
    """LLM 篡改 bar_features 内结构特征字段 → CheatError（锚定拦截）。"""
    from selene_space_market_analysis.exceptions import CheatError

    tampered = dict(_STAGE1_WAIT)
    tampered["bar_features"] = dict(_BAR_FEATURES, range_position=0.9)
    flow, persistence, calls = _flow_with(lambda: json.dumps(tampered))

    with pytest.raises(CheatError):
        await flow.analyze(_INPUT, input_id="BTCUSDT-1d", session_id="s1")

    assert len(calls) == 1  # 终态 cheat 不重试


# ---------------------------------------------------------------------------
# P0-① — 跨重试作弊检测（锚 market_regime，尝试 N vs N-1）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stage1_regime_flip_between_retries_triggers_cheat() -> None:
    """模型在重试间翻转 market_regime（改口诊断）→ CheatError（终态）。"""
    from selene_space_market_analysis.exceptions import CheatError

    bad1 = dict(_STAGE1_PROCEED)
    bad1["signal_patterns"] = []  # entry_gate=proceed 无信号 → consistency 失败触发重试
    flip = dict(_STAGE1_WAIT)
    flip["market_regime"] = "trend_down"  # 重试时翻转诊断方向
    responses = iter([json.dumps(bad1), json.dumps(flip)])
    flow, persistence, calls = _flow_with(lambda: next(responses))

    with pytest.raises(CheatError):
        await flow.analyze(_INPUT, input_id="BTCUSDT-1d", session_id="s1")

    assert len(calls) == 2  # 第二次命中翻转即终态，不再重试


@pytest.mark.asyncio
async def test_stage1_stable_regime_across_retries_succeeds() -> None:
    """重试间 market_regime 保持稳定 → 不触发跨重试 cheat，第二次通过。"""
    bad1 = dict(
        _STAGE1_WAIT,
        evidence=[
            {"kind": "pattern", "key": "x", "statement": "是", "kline_range": "K1-K10"}
        ],
    )
    responses = iter([json.dumps(bad1)[:-1], json.dumps(_STAGE1_WAIT)[:-1]])
    flow, persistence, calls = _flow_with(lambda: next(responses))

    record = await flow.analyze(_INPUT, input_id="BTCUSDT-1d", session_id="s1")

    assert len(calls) == 2
    assert record["stage1"]["market_regime"] == "range"


# ---------------------------------------------------------------------------
# P0-③ — strict+lenient 双失败 → 可操作诊断注入重试反馈
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stage1_truncated_json_retry_feedback_carries_diagnosis() -> None:
    """双失败且内容被截断 → 重试反馈携带 truncated 诊断提示。"""
    responses = iter(
        [
            '{"market_regime": "trend_up", "entry_gate": "wait", "signal_',
            json.dumps(_STAGE1_WAIT),
        ]
    )
    flow, _persistence, captured = _flow_capturing(lambda: next(responses))

    await flow.analyze(_INPUT, input_id="BTCUSDT-1d", session_id="s1")

    retry_prompt = captured[1][-1]["content"]
    assert "非合法 JSON（truncated）" in retry_prompt
    assert "截断" in retry_prompt


@pytest.mark.asyncio
async def test_stage1_syntax_json_retry_feedback_carries_diagnosis() -> None:
    """双失败且结构完整 → 重试反馈携带 syntax 诊断提示。"""
    responses = iter(['{"a": 1,,}', json.dumps(_STAGE1_WAIT)])
    flow, _persistence, captured = _flow_capturing(lambda: next(responses))

    await flow.analyze(_INPUT, input_id="BTCUSDT-1d", session_id="s1")

    retry_prompt = captured[1][-1]["content"]
    assert "非合法 JSON（syntax）" in retry_prompt
    assert "语法" in retry_prompt


# ---------------------------------------------------------------------------
# US-303 — next_cycle_forecast 契约（A-303）
# ---------------------------------------------------------------------------

_STAGE2_PROCEED_WITH_CYCLE = {
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
    "next_cycle_forecast": {
        "direction": "up",
        "probability": 0.66,
        "cycle_stage": "early",
        "phase_position": 0.4,
    },
}


@pytest.mark.asyncio
async def test_stage2_parses_next_cycle_forecast() -> None:
    """LLM 输出 next_cycle_forecast → Stage2 契约解析落盘。"""
    responses = iter([json.dumps(_STAGE1_PROCEED), json.dumps(_STAGE2_PROCEED_WITH_CYCLE)])
    flow, persistence, calls = _flow_with(lambda: next(responses))

    record = await flow.analyze(_INPUT, input_id="BTCUSDT-1d", session_id="s1")

    cycle = record["stage2"]["next_cycle_forecast"]
    assert cycle["direction"] == "up"
    assert cycle["probability"] == 0.66
    assert cycle["cycle_stage"] == "early"
    assert cycle["phase_position"] == 0.4
    assert "next_cycle_forecast" not in cycle  # 位置子字段用 phase_position


@pytest.mark.asyncio
async def test_stage2_prompt_instructs_next_cycle_forecast() -> None:
    """Stage2 提示词要求 LLM 输出 next_cycle_forecast。"""
    responses = iter([json.dumps(_STAGE1_PROCEED), json.dumps(_STAGE2_PROCEED)])
    flow, persistence, captured = _flow_capturing(lambda: next(responses))

    await flow.analyze(_INPUT, input_id="BTCUSDT-1d", session_id="s1")

    stage2_prompt = captured[1][-1]["content"]
    assert "next_cycle_forecast" in stage2_prompt


@pytest.mark.asyncio
async def test_synth_wait_record_carries_next_cycle_none() -> None:
    """gate 短路的合成 wait 记录 next_cycle_forecast 为 None（可重放校验器）。"""
    flow, persistence, calls = _flow_with(lambda: json.dumps(_STAGE1_WAIT))

    record = await flow.analyze(_INPUT, input_id="BTCUSDT-1d", session_id="s1")

    assert record["stage2"]["next_cycle_forecast"] is None
    from selene_space_market_analysis.validator import validate_stage2

    stage1 = Stage1Diagnosis.model_validate(record["stage1"])
    stage2 = Stage2Decision.model_validate(record["stage2"])
    report = validate_stage2(stage2, stage1=stage1)
    assert report.is_valid, [e.message for e in report.errors]


# ---------------------------------------------------------------------------
# US-001 — 流式 provider：on_event 事件序列 + 结果等价
# ---------------------------------------------------------------------------


class _StreamingStub(OpenAICompatProvider):
    """不联网的 OpenAICompatProvider 子类：chat_stream 产出自定义 chunk 序列。

    构造不联网（config.base_url=None → 仅归一化 URL，不发起请求）；
    通过 HttpLLMProvider(provider=stub) 注入后 supports_stream=True，
    从而触发 flow 的流式路径。
    """

    def __init__(
        self,
        stage_chunks: list[list[str]],
        *,
        stage_fail: list[Exception | None] | None = None,
    ) -> None:
        super().__init__(_config())
        self._stage_chunks = stage_chunks
        self._stage_fail = list(stage_fail or [None] * len(stage_chunks))
        self.stream_calls = 0

    async def chat_stream(self, messages, **kwargs) -> Any:
        idx = self.stream_calls
        self.stream_calls += 1
        fail = self._stage_fail[min(idx, len(self._stage_fail) - 1)]
        if fail is not None:
            raise fail
        for chunk in self._stage_chunks[min(idx, len(self._stage_chunks) - 1)]:
            yield chunk


def _flow_streaming(stub: _StreamingStub, *, playbooks=None):
    """构造注入流式 stub 的 flow，返回 (flow, persistence, events, on_event, stub)。"""
    from selene_space_market_analysis.llm_provider import HttpLLMProvider

    provider = HttpLLMProvider(config=_config(), provider=stub)
    assert provider.supports_stream is True
    events: list[dict] = []

    async def on_event(ev: dict) -> None:
        events.append(ev)

    persistence = InMemoryPersistence()
    flow = MarketAnalysisFlow(
        llm=provider,
        persistence=persistence,
        playbook_router=playbooks,
    )
    return flow, persistence, events, on_event, stub


@pytest.mark.asyncio
async def test_streaming_provider_emits_text_events_and_equivalent_result() -> None:
    """流式 provider：事件序列含 stage1_text/stage2_text chunk，结果与一次性等价。"""
    stub = _StreamingStub(
        [
            list("{" + json.dumps(_STAGE1_PROCEED)[1:]),  # 拆成单字符 chunk
            list("{" + json.dumps(_STAGE2_PROCEED)[1:]),
        ]
    )
    flow, persistence, events, on_event, stub = _flow_streaming(stub)

    record = await flow.analyze(
        _INPUT,
        input_id="BTCUSDT-1d",
        session_id="s1",
        on_event=on_event,
    )

    types = [e["type"] for e in events]
    assert types[0] == "stage1_start"
    assert "stage2_start" in types
    assert "stage1_done" in types
    assert "stage2_done" in types
    # 每个 stage 至少 emit 过 text chunk
    assert any(e["type"] == "stage1_text" and e["delta"] for e in events)
    assert any(e["type"] == "stage2_text" and e["delta"] for e in events)
    # 结果与一次性调用等价
    assert record["stage1"]["market_regime"] == "trend_up"
    assert record["stage2"]["decision"] == "limit"
    assert stub.stream_calls == 2


@pytest.mark.asyncio
async def test_streaming_gate_short_circuit_emits_stage2_skipped() -> None:
    """流式 + gate 短路：发 stage2_skipped，无 stage2_start，仅 1 次流式调用。"""
    stub = _StreamingStub([list("{" + json.dumps(_STAGE1_WAIT)[1:])])
    flow, persistence, events, on_event, stub = _flow_streaming(stub)

    record = await flow.analyze(
        _INPUT,
        input_id="BTCUSDT-1d",
        session_id="s1",
        on_event=on_event,
    )

    types = [e["type"] for e in events]
    assert "stage2_skipped" in types
    assert "stage2_start" not in types
    assert record["stage2"]["decision"] == "no_trade"
    assert stub.stream_calls == 1


@pytest.mark.asyncio
async def test_streaming_retry_emits_retry_event_then_succeeds() -> None:
    """流式首次输出非法 JSON → stage1_retry 事件 → 第二次流式成功。"""
    stub = _StreamingStub(
        [["not-json"], list("{" + json.dumps(_STAGE1_WAIT)[1:])],
        stage_fail=[None, None],
    )
    flow, persistence, events, on_event, stub = _flow_streaming(stub)

    record = await flow.analyze(
        _INPUT,
        input_id="BTCUSDT-1d",
        session_id="s1",
        on_event=on_event,
    )

    types = [e["type"] for e in events]
    assert "stage1_retry" in types
    assert record["stage1"]["market_regime"] == "range"
    assert stub.stream_calls == 2


@pytest.mark.asyncio
async def test_fake_provider_without_stream_degrades_to_call_async() -> None:
    """注入假 provider（无流式）→ supports_stream=False，走 call_async 老路径。"""
    from selene_space_market_analysis.llm_provider import HttpLLMProvider

    provider = HttpLLMProvider(
        config=_config(),
        provider=lambda messages: json.dumps(_STAGE1_WAIT),
    )
    assert provider.supports_stream is False

    events: list[dict] = []

    async def on_event(ev: dict) -> None:
        events.append(ev)

    persistence = InMemoryPersistence()
    flow = MarketAnalysisFlow(llm=provider, persistence=persistence)
    record = await flow.analyze(
        _INPUT, input_id="BTCUSDT-1d", session_id="s1", on_event=on_event
    )

    types = [e["type"] for e in events]
    assert "stage1_start" in types
    assert "stage1_done" in types
    # 无 text 事件（非流式）
    assert not any(e["type"] == "stage1_text" for e in events)
    assert record["stage1"]["market_regime"] == "range"


# ---------------------------------------------------------------------------
# US-004 — trade_temperament 注入 Stage2 prompt（Stage1 不注入；None 不注入）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trade_temperament_injects_stage2_prompt_only() -> None:
    """带 trade_temperament → Stage2 prompt 含【交易倾向】段，Stage1 prompt 不含。"""
    responses = iter([json.dumps(_STAGE1_PROCEED), json.dumps(_STAGE2_PROCEED)])
    flow, _persistence, captured = _flow_capturing(lambda: next(responses))

    await flow.analyze(
        _INPUT,
        input_id="BTCUSDT-1d",
        session_id="s1",
        trade_temperament="aggressive",
    )

    assert len(captured) == 2  # Stage1 + Stage2
    stage1_prompt = captured[0][-1]["content"]
    stage2_prompt = captured[1][-1]["content"]
    assert "【交易倾向】" in stage2_prompt
    assert "激进型" in stage2_prompt
    assert "【交易倾向】" not in stage1_prompt


@pytest.mark.asyncio
async def test_trade_temperament_none_omits_prompt_section() -> None:
    """不带 trade_temperament → Stage2 prompt 不含【交易倾向】段。"""
    responses = iter([json.dumps(_STAGE1_PROCEED), json.dumps(_STAGE2_PROCEED)])
    flow, _persistence, captured = _flow_capturing(lambda: next(responses))

    await flow.analyze(_INPUT, input_id="BTCUSDT-1d", session_id="s1")

    assert len(captured) == 2
    stage2_prompt = captured[1][-1]["content"]
    assert "【交易倾向】" not in stage2_prompt
