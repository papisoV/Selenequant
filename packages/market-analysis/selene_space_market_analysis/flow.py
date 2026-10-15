"""MarketAnalysisFlow — 两阶段行情分析编排器（Stage1 诊断 → gate 短路 → Stage2 决策）。

编排逻辑参考 selene 自有 MIT decision-workflow 的概念（orchestrator.py），市场侧独立撰写：
- Stage1：调用 LLM 产出诊断 JSON → 3 层校验 → 重试（含 bar_features 不可变锚定）
- Gate 短路：entry_gate=wait/unknown 时跳过 Stage2（全程 1 次 LLM call），合成 wait 记录
- Stage2：仅 proceed 时运行，跨阶段 diagnosis_summary 锚定防篡改
- Partial 落盘：任何异常 → 持久化带 ``exception`` 字段的 partial record，调用不丢
- 支持 cancel/背景执行语义：``analyze`` 为 async，端点层可挂后台任务

合规口径见包内 PROVENANCE.md：无复制声明。
"""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .core import (
    cheat_anchor,
    cheat_anchor_between_attempts,
    diagnose_json_failure,
    make_meta,
    normalize_llm_json,
    record_assembly,
)
from .exceptions import (
    CheatError,
    SemanticError,
    ValidationError,
)
from .experience_library import ExperienceLibrary, render_history_context
from .llm_provider import HttpLLMProvider, LLMMessage
from .models import Stage1Diagnosis, Stage2Decision, TradeSetup
from .persistence import RecordPersistence
from .routing import MARKET_PLAYBOOK_TABLE, route_playbooks
from .validator import ValidatorConfig, validate_stage1, validate_stage2

logger = logging.getLogger(__name__)

# Stage1 输出中必须与确定性输入（预计算指标）保持一致、不可被 LLM 篡改的字段。
# 仅 bar_features 有确定性基线（预计算指标 + US-301 结构特征），market_regime
# 是 LLM 诊断输出，不参与锚定（否则 baseline 缺失恒报 CheatError）。
# cheat_anchor 对 bar_features 整体做 dict 精确比对，新结构特征字段自动纳入锚定。
_STAGE1_IMMUTABLE: tuple[str, ...] = ("bar_features",)

# 跨重试作弊检测锚定字段（PA detect_cheat 的 direction 对位）。校验器从不要求
# 模型修改 market_regime（consistency 只约束 gate×信号形态、spike×proceed），
# 故重试间翻转 = 模型改口诊断而非修正格式，可安全锚定。
_STAGE1_RETRY_ANCHOR: tuple[str, ...] = ("market_regime",)

_DEFAULT_SYSTEM_PROMPT = (
    "你是一个严谨的行情分析 AI。请基于给定的 K 线与预计算指标，"
    "输出严格符合要求的 JSON 结构，不得修改指标数值。"
)

# 交易倾向 → Stage2 prompt 注入段（自研命名 trade_temperament，cleanroom 词表零命中）。
_TEMPERAMENT_LABELS: dict[str, str] = {
    "conservative": "保守型：优先风险控制，仅高置信度信号才进场",
    "balanced": "均衡型：风险与收益并重，按信号置信度正常建仓",
    "aggressive": "激进型：偏好更多交易机会与更大仓位，容忍更高回撤",
    "very_aggressive": "极度激进型：愿意承担高风险以追求高收益，交易频率显著提高",
}


class MarketAnalysisFlow:
    """两阶段行情分析编排器。"""

    def __init__(
        self,
        *,
        llm: HttpLLMProvider,
        persistence: RecordPersistence,
        validator_config: ValidatorConfig | None = None,
        playbook_router: Callable[[Stage1Diagnosis], list[str]] | None = None,
        max_retry: int = 3,
        system_prompt: str = _DEFAULT_SYSTEM_PROMPT,
        experience_library: ExperienceLibrary | None = None,
    ) -> None:
        self._llm = llm
        self._persistence = persistence
        self._validator_config = validator_config or ValidatorConfig()
        self._playbook_router = playbook_router or (
            lambda stage1: route_playbooks(stage1.model_dump(), MARKET_PLAYBOOK_TABLE)
        )
        self._max_retry = max_retry
        self._system_prompt = system_prompt
        self._experience_library = experience_library

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyze(
        self,
        input_data: dict[str, Any],
        *,
        input_id: str,
        session_id: str,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        trade_temperament: str | None = None,
        history_n: int = 0,
    ) -> dict[str, Any]:
        """两阶段分析并落盘审计记录（无先验上下文的完整重推）。

        ``on_event`` 为可选异步事件回调（SSE 进度推送）：stage1_start /
        stage1_text(delta) / stage1_done / stage1_retry / stage2_start /
        stage2_text(delta) / stage2_done / stage2_retry / stage2_skipped。
        流式仅当底层 provider 支持时逐 chunk 发 text；否则只发 start/done。

        ``trade_temperament`` 为交易倾向入参（仅注入 Stage2 prompt，影响决策
        倾向；None 时不注入，提示词保持默认均衡语义）。

        ``history_n`` 为历史分析纵向对比条数（0=不启用；>0 时注入最近
        同 symbol+interval 的 N 条记录供 LLM 前后对照，仅参考）。
        """
        return await self._analyze(
            input_data,
            prior=None,
            prev_record_id=None,
            input_id=input_id,
            session_id=session_id,
            on_event=on_event,
            trade_temperament=trade_temperament,
            history_n=history_n,
        )

    async def analyze_incremental(
        self,
        input_data: dict[str, Any],
        *,
        prev_record: dict[str, Any] | None,
        input_id: str,
        session_id: str,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        trade_temperament: str | None = None,
        history_n: int = 0,
    ) -> dict[str, Any]:
        """增量分析：复用上次诊断摘要（prior 上下文）与上次记录 id，避免从零重推。

        ``prev_record`` 为上次落盘的完整记录（含 stage1.diagnosis_summary 与
        meta.record_id）；为空时退化为完整重推。``history_n`` 语义同
        :meth:`analyze`。
        """
        prior = ((prev_record or {}).get("stage1") or {}).get("diagnosis_summary") or None
        prev_record_id = (prev_record.get("meta") or {}).get("record_id") if prev_record else None
        return await self._analyze(
            input_data,
            prior=prior,
            prev_record_id=prev_record_id,
            input_id=input_id,
            session_id=session_id,
            on_event=on_event,
            trade_temperament=trade_temperament,
            history_n=history_n,
        )

    async def _analyze(
        self,
        input_data: dict[str, Any],
        *,
        prior: dict[str, Any] | None,
        prev_record_id: str | None,
        input_id: str,
        session_id: str,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        trade_temperament: str | None = None,
        history_n: int = 0,
    ) -> dict[str, Any]:
        """两阶段分析并落盘审计记录。

        - 成功：返回完整 record（增量时 meta 含 prev_record_id）。
        - 异常：持久化带 ``exception`` 字段的 partial record 后重新抛出
          （端点层可据此返回失败响应而不丢调用）。
        """
        meta = make_meta(input_id=input_id, session_id=session_id)
        if prev_record_id:
            meta["prev_record_id"] = prev_record_id
        stage1_dict: dict[str, Any] | None = None
        stage2_dict: dict[str, Any] | None = None
        stage1_prompt: list[dict] | None = None
        stage1_raw: str | None = None
        stage2_prompt: list[dict] | None = None
        stage2_raw: str | None = None
        playbooks: list[str] = []
        usage_total: dict[str, int] = {}

        try:
            stage1, usage1, stage1_prompt, stage1_raw, stage1_repaired = await self._run_stage1(
                input_data, prior, on_event=on_event, history_n=history_n
            )
            if stage1_repaired:
                meta["json_repaired"] = True
            stage1_dict = stage1.model_dump()

            if stage1.entry_gate != "proceed":
                if on_event is not None:
                    await on_event(
                        {"type": "stage2_skipped", "gate": stage1.entry_gate}
                    )
                stage2 = self._synth_wait_record(stage1)
                stage2_dict = stage2.model_dump()
                usage_total = usage1
            else:
                stage2, usage2, stage2_prompt, stage2_raw, stage2_repaired = (
                    await self._run_stage2(
                        input_data,
                        stage1,
                        on_event=on_event,
                        trade_temperament=trade_temperament,
                    )
                )
                if stage2_repaired:
                    meta["json_repaired"] = True
                stage2_dict = stage2.model_dump()
                playbooks = self._playbook_router(stage1)
                usage_total = _merge_usage(usage1, usage2)

            record = record_assembly(
                meta=meta,
                input_data=input_data,
                stage1=stage1_dict,
                stage2=stage2_dict,
                dispatch_playbooks=playbooks,
                usage_total=usage_total,
            )
            record["prompt"] = {"stage1": stage1_prompt, "stage2": stage2_prompt}
            record["raw"] = {"stage1": stage1_raw, "stage2": stage2_raw}
            await self._save(record)
            return record

        except Exception as exc:
            # Partial 落盘：调用不丢。stage1/stage2 保留已成功的部分。
            partial = record_assembly(
                meta=meta,
                input_data=input_data,
                stage1=stage1_dict or {},
                stage2=stage2_dict,
                dispatch_playbooks=playbooks,
                usage_total=usage_total,
            )
            partial["prompt"] = {"stage1": stage1_prompt, "stage2": stage2_prompt}
            partial["raw"] = {"stage1": stage1_raw, "stage2": stage2_raw}
            partial["exception"] = {
                "type": type(exc).__name__,
                "message": str(exc),
            }
            await self._save(partial)
            logger.warning(
                "MarketAnalysisFlow partial record persisted (input_id=%s): %s",
                input_id,
                type(exc).__name__,
            )
            raise

    async def _save(self, record: dict[str, Any]) -> None:
        """持久化并把存储返回的 id 记入 meta.record_id。"""
        record_id = await self._persistence.save(record)
        record["meta"]["record_id"] = record_id

    # ------------------------------------------------------------------
    # Stage 1
    # ------------------------------------------------------------------

    async def _call_llm(
        self,
        messages: list[LLMMessage],
        *,
        stage: str,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> tuple[str, dict[str, int]]:
        """调用 LLM：底层支持流式则逐 chunk 发 text 事件并累积全文，否则一次性调用。

        返回 (content, usage)。流式路径 usage 为空 dict（chat_stream 不返回用量）。
        """
        if self._llm.supports_stream:
            parts: list[str] = []
            async for chunk in self._llm.stream(messages):
                parts.append(chunk)
                if on_event is not None:
                    await on_event({"type": f"{stage}_text", "delta": chunk})
            return "".join(parts), {}
        response = await self._llm.call_async(messages)
        return response.content, response.usage

    async def _run_stage1(
        self,
        input_data: dict[str, Any],
        prior: dict[str, Any] | None = None,
        *,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        history_n: int = 0,
    ) -> tuple[Stage1Diagnosis, dict[str, int], list[dict], str, bool]:
        """Stage1 诊断重试循环（含 bar_features 不可变锚定）。

        返回 (result, usage, prompt, raw, repaired)：repaired 表示本次成功输出
        经 lenient JSON 修复（供 meta.json_repaired 审计标记）。
        """
        baseline = self._extract_indicator_baseline(input_data)
        injection = ""
        if self._experience_library is not None:
            injection = await self._experience_library.build_stage1_context(
                symbol=input_data.get("symbol") or "",
                interval=input_data.get("interval") or "",
                market_regime=input_data.get("market_regime"),
            )
        history_context = ""
        if history_n > 0:
            list_recent = getattr(self._persistence, "list_recent", None)
            if list_recent is not None:
                symbol = input_data.get("symbol") or ""
                interval = input_data.get("interval") or ""
                records = await list_recent(
                    symbol=symbol, limit=max(50, history_n * 5)
                )
                same_interval: list[dict[str, Any]] = []
                for rec in records:
                    if "exception" in rec:
                        continue
                    if (rec.get("input_data") or {}).get("interval") != interval:
                        continue
                    same_interval.append(rec)
                    if len(same_interval) >= history_n:
                        break
                history_context = render_history_context(same_interval)
        base_user = self._build_stage1_user_prompt(
            input_data, prior, injection=injection, history_context=history_context
        )
        feedback: str | None = None
        prev_result: Stage1Diagnosis | None = None

        for _attempt in range(1, self._max_retry + 1):
            if on_event is not None:
                await on_event({"type": "stage1_start", "attempt": _attempt})
            user_content = base_user if feedback is None else base_user + feedback
            messages = [
                LLMMessage(role="system", content=self._system_prompt),
                LLMMessage(role="user", content=user_content),
            ]
            prompt = [{"role": m.role, "content": m.content} for m in messages]
            content, usage = await self._call_llm(
                messages, stage="stage1", on_event=on_event
            )
            try:
                parsed, repaired = normalize_llm_json(content, "strict")
            except json.JSONDecodeError as strict_exc:
                try:
                    parsed, repaired = normalize_llm_json(content, "lenient")
                except json.JSONDecodeError:
                    diag = diagnose_json_failure(content)
                    feedback = (
                        f"\n\n【上次错误】stage1 输出非合法 JSON（{diag['kind']}）: "
                        f"{strict_exc.msg}。{diag['hint']}"
                    )
                    if on_event is not None:
                        await on_event(
                            {
                                "type": "stage1_retry",
                                "attempt": _attempt,
                                "message": f"非合法 JSON（{diag['kind']}）",
                            }
                        )
                    continue

            try:
                result = Stage1Diagnosis.model_validate(parsed)
            except Exception as exc:
                feedback = f"\n\n【上次错误】stage1 契约校验失败: {exc}"
                if on_event is not None:
                    await on_event(
                        {"type": "stage1_retry", "attempt": _attempt, "message": "契约校验失败"}
                    )
                continue

            # 跨重试作弊检测（PA detect_cheat 对位）：模型不得在重试间翻转诊断方向。
            if prev_result is not None:
                cheat_anchor_between_attempts(
                    prev_result.model_dump(),
                    result.model_dump(),
                    _STAGE1_RETRY_ANCHOR,
                )
            prev_result = result

            report = validate_stage1(result, config=self._validator_config)
            if report.is_valid:
                cheat_anchor(
                    baseline,
                    result.model_dump(),
                    _STAGE1_IMMUTABLE,
                )
                if on_event is not None:
                    await on_event(
                        {"type": "stage1_done", "attempt": _attempt, "usage": usage}
                    )
                return result, usage, prompt, content, repaired

            _raise_terminal(report.errors, repaired=repaired)
            feedback = "\n\n【上次错误】" + "; ".join(e.message for e in report.errors)
            if on_event is not None:
                await on_event(
                    {
                        "type": "stage1_retry",
                        "attempt": _attempt,
                        "message": "; ".join(e.message for e in report.errors),
                    }
                )

        raise ValidationError(
            layer="json",
            field="max_retry",
            message=f"stage1 failed after {self._max_retry} retries",
        )

    # ------------------------------------------------------------------
    # Stage 2
    # ------------------------------------------------------------------

    async def _run_stage2(
        self,
        input_data: dict[str, Any],
        stage1: Stage1Diagnosis,
        *,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        trade_temperament: str | None = None,
    ) -> tuple[Stage2Decision, dict[str, int], list[dict], str, bool]:
        """Stage2 决策重试循环（跨阶段 diagnosis_summary 锚定防篡改）。

        返回 (result, usage, prompt, raw, repaired)：repaired 语义同 stage1。
        """
        base_user = self._build_stage2_user_prompt(
            input_data, stage1, trade_temperament=trade_temperament
        )
        feedback: str | None = None

        for _attempt in range(1, self._max_retry + 1):
            if on_event is not None:
                await on_event({"type": "stage2_start", "attempt": _attempt})
            user_content = base_user if feedback is None else base_user + feedback
            messages = [
                LLMMessage(role="system", content=self._system_prompt),
                LLMMessage(role="user", content=user_content),
            ]
            prompt = [{"role": m.role, "content": m.content} for m in messages]
            content, usage = await self._call_llm(
                messages, stage="stage2", on_event=on_event
            )
            try:
                parsed, repaired = normalize_llm_json(content, "strict")
            except json.JSONDecodeError as strict_exc:
                try:
                    parsed, repaired = normalize_llm_json(content, "lenient")
                except json.JSONDecodeError:
                    diag = diagnose_json_failure(content)
                    feedback = (
                        f"\n\n【上次错误】stage2 输出非合法 JSON（{diag['kind']}）: "
                        f"{strict_exc.msg}。{diag['hint']}"
                    )
                    if on_event is not None:
                        await on_event(
                            {
                                "type": "stage2_retry",
                                "attempt": _attempt,
                                "message": f"非合法 JSON（{diag['kind']}）",
                            }
                        )
                    continue

            try:
                result = Stage2Decision.model_validate(parsed)
            except Exception as exc:
                feedback = f"\n\n【上次错误】stage2 契约校验失败: {exc}"
                if on_event is not None:
                    await on_event(
                        {"type": "stage2_retry", "attempt": _attempt, "message": "契约校验失败"}
                    )
                continue

            report = validate_stage2(result, stage1=stage1, config=self._validator_config)
            if report.is_valid:
                if on_event is not None:
                    await on_event(
                        {"type": "stage2_done", "attempt": _attempt, "usage": usage}
                    )
                return result, usage, prompt, content, repaired

            _raise_terminal(report.errors, repaired=repaired)
            feedback = "\n\n【上次错误】" + "; ".join(e.message for e in report.errors)
            if on_event is not None:
                await on_event(
                    {
                        "type": "stage2_retry",
                        "attempt": _attempt,
                        "message": "; ".join(e.message for e in report.errors),
                    }
                )

        raise ValidationError(
            layer="json",
            field="max_retry",
            message=f"stage2 failed after {self._max_retry} retries",
        )

    # ------------------------------------------------------------------
    # 合成 / 提示词
    # ------------------------------------------------------------------

    def _synth_wait_record(self, stage1: Stage1Diagnosis) -> Stage2Decision:
        """entry_gate != proceed 时的合成 wait 记录（可重放过校验器）。

        trade_setup 带扁平 decision_path（G2/G4：wait 短路路径同样可前端渲染）。
        """
        return Stage2Decision(
            decision="no_trade",
            dispatch_playbooks=[],
            decision_summary={
                "short_circuited": True,
                "entry_gate": stage1.entry_gate,
                "diagnosis_summary": stage1.diagnosis_summary,
            },
            evidence=[],
            next_bar_forecast=None,
            next_cycle_forecast=None,
            trade_setup=TradeSetup(
                direction=None,
                decision_path=[f"gate:{stage1.entry_gate}", "gate 短路", "no_trade"],
            ),
        )

    @staticmethod
    def _extract_indicator_baseline(input_data: dict[str, Any]) -> dict[str, Any]:
        """确定性输入锚定：预计算指标（bar_features）+ 诊断 regime 不在其中。

        bar_features 由预计算得出，LLM 无权修改；缺失时为空（跳过锚定）。
        """
        bf = input_data.get("bar_features")
        if isinstance(bf, dict):
            return {"bar_features": bf}
        return {}

    def _build_stage1_user_prompt(
        self,
        input_data: dict[str, Any],
        prior: dict[str, Any] | None = None,
        *,
        injection: str = "",
        history_context: str = "",
    ) -> str:
        lines = [
            "【输入 K 线与指标】",
            json.dumps(input_data, ensure_ascii=False, indent=2),
            "",
        ]
        if injection:
            lines += [
                "【历史同类案例（仅参考）】",
                injection,
                "",
            ]
        if prior:
            lines += [
                "【上次结论（复用，仅参考）】",
                json.dumps(prior, ensure_ascii=False, indent=2),
                "",
            ]
        if history_context:
            lines += [
                "【历史记录对比（仅参考）】",
                history_context,
                "",
            ]
        lines += [
            "请输出 Stage 1 诊断 JSON：{market_regime, entry_gate, signal_patterns[], "
            "trend_context, bar_features(原样保留), evidence[], diagnosis_summary}。",
            "必须严格匹配以下 JSON Schema（字段名、枚举取值、嵌套结构均以 Schema 为准，不得发明新值）：",
            json.dumps(Stage1Diagnosis.model_json_schema(), ensure_ascii=False, indent=2),
            "entry_gate 判定规则：proceed 表示本窗口存在可执行的明确方向信号——",
            "无论方向，trend_up 有看多信号、trend_down 有看空信号、range 出现方向突破均可 proceed；",
            "wait 表示信号不明确（震荡无突破、方向不明、信号互相矛盾）；unknown 表示信息不足以判断。",
            "注意：方向不是 gate 的依据，信号清晰度才是——不要因市场下跌或震荡就一律 wait，",
            "也不要只对上涨放行；market_regime=spike 时禁止 proceed。",
            "格式约定：kline_range 必须用 'K{n}-K{m}' 形式，下标从 1 起，n<m；"
            f"本窗口共 {len(input_data.get('klines', []))} 根 K 线，整个窗口写 'K1-K{len(input_data.get('klines', []))}'，"
            "局部子区间写 'K{a}-K{b}'（如 'K10-K25'）；不得用单个 K 值、时间戳或空值；"
            "evidence 的 statement 须具体描述依据、不得用模板化短语；"
            "statement 中若引用 K 下标，必须是该条 evidence 的 kline_range 内的下标；"
            "若想引用范围外的 K 线，请扩大该条 kline_range 使其包含它；"
            "不确定具体 K 编号时用描述性语言（如 '近期回落'、'回调低点'），不要编造 K 编号。",
        ]
        return "\n".join(lines)

    def _build_stage2_user_prompt(
        self,
        input_data: dict[str, Any],
        stage1: Stage1Diagnosis,
        *,
        trade_temperament: str | None = None,
    ) -> str:
        lines = [
            "【输入 K 线与指标】",
            json.dumps(input_data, ensure_ascii=False, indent=2),
            "",
            "【Stage 1 诊断】",
            json.dumps(stage1.diagnosis_summary, ensure_ascii=False, indent=2),
            "",
        ]
        if trade_temperament:
            lines += [
                "【交易倾向】",
                _TEMPERAMENT_LABELS.get(trade_temperament, trade_temperament),
                "",
            ]
        lines += [
            "请输出 Stage 2 决策 JSON：{decision, dispatch_playbooks[], "
            "decision_summary(含原样 diagnosis_summary), evidence[], "
            "next_bar_forecast, next_cycle_forecast, trade_setup}。",
            "trade_setup 规则：仅当 decision 为 limit/breakout/market 时输出完整交易设置"
            "（direction/entry_price/stop_loss_price/take_profit_price/"
            "take_profit_price_2/estimated_win_rate/confidence/risk_reward_ratio/"
            "trader_equation_passed/decision_path）；decision 为 no_trade 时 "
            "trade_setup 必须为 null。若计划分批止盈，用 take_profit_price_2 给出第二"
            "止盈位（须与 take_profit_price 同向、更远）；单档止盈时省略该键。",
            "交易设置必须自洽：long 需 stop_loss < entry < take_profit，short 反向；"
            "风险回报比须 ≥1.0（止盈距离 ≥ 止损距离）；胜率须高于盈亏平衡 1/(1+RR)；"
            "不满足就降级为 no_trade，不要硬凑交易。decision_path 用简短扁平步骤标签"
            "（如 [\"gate:proceed\", \"趋势确认\", \"入场\", \"止损设置\"]）。",
            "必须严格匹配以下 JSON Schema（字段名、枚举取值、嵌套结构均以 Schema 为准，不得发明新值）：",
            json.dumps(Stage2Decision.model_json_schema(), ensure_ascii=False, indent=2),
            "格式约定：kline_range 同 Stage 1（'K{n}-K{m}'，本窗口 "
            f"{len(input_data.get('klines', []))} 根 K 线，整窗 'K1-K{len(input_data.get('klines', []))}'，"
            "不得用单个 K 值或时间戳）；"
            "evidence 的 statement 须具体、不得模板化，K 引用在 kline_range 内；"
            "若想引用范围外的 K 线，请扩大该条 kline_range 使其包含它；"
            "不确定具体 K 编号时用描述性语言，不要编造 K 编号；"
            "decision_summary['diagnosis_summary'] 必须与上面【Stage 1 诊断】的 diagnosis_summary "
            "逐字段完全相同：复制其全部字段与值，任何字段或值不同都会判为篡改报错。",
        ]
        return "\n".join(lines)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _merge_usage(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    out = dict(a)
    for k, v in b.items():
        out[k] = out.get(k, 0) + v
    return out


def _raise_terminal(errors, *, repaired: bool = False) -> None:
    """校验错误中的终态错误立即抛出（cheat / semantic 不可重试）。

    ``repaired=True``（lenient JSON 修复成功）时，semantic 层错误降级为
    可重试反馈（不抛），避免"修复后语义可疑"误伤为终态；cheat 语义不变。
    """
    cheat = [e for e in errors if e.cheat_detected]
    if cheat:
        raise CheatError(field=cheat[0].field, message=cheat[0].message)
    semantic = [e for e in errors if e.layer == "semantic"]
    if semantic and not repaired:
        raise SemanticError(message=semantic[0].message)
