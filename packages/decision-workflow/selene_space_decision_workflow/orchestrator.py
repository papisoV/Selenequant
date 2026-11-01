"""Two-stage workflow orchestrator. See docs/DECISION_WORKFLOW_METHODOLOGY.md §2.1."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .exceptions import CheatError, SemanticError, ValidationError
from .llm_provider import LLMProvider, LLMMessage, build_system_prompt
from .models import (
    AnalysisRecord,
    NodeAnswer,
    NodeOverride,
    RecordMeta,
    Stage1Result,
    Stage2Result,
    StageRecord,
)
from .parser import DecisionTreeGraph
from .persistence import RecordPersistence
from .program_nodes import ProgramNodeRegistry, ProgramOverrideValidator
from .retry import detect_cheat
from .routing import (
    RoutingTable,
    route_overlay_files,
    route_strategy_files,
)
from .validator import ValidatorConfig, validate_stage1, validate_stage2

logger = logging.getLogger(__name__)

# Trading stance tiers → Stage 2 user-prompt line. Module-level constant,
# mirroring market-analysis flow.py's temperament table (package precedent).
# Self-chosen word: trading_stance (cleanroom AST wordlist zero-hit).
_TRADING_STANCE_LABELS: dict[str, str] = {
    "conservative": "保守型：优先风险控制，仅高置信度信号才进场",
    "balanced": "均衡型：风险与收益并重，按信号置信度正常建仓",
    "aggressive": "激进型：偏好更多交易机会与更大仓位，容忍更高回撤",
    "very_aggressive": "极度激进型：愿意承担高风险以追求高收益，交易频率显著提高",
}

# Canonical stance choice list (CLI argparse choices + tests share this
# single source; the labels dict above stays the display mapping).
TRADING_STANCES: tuple[str, ...] = tuple(_TRADING_STANCE_LABELS)

# Optional node-override self-report clause appended to BOTH stage user prompts
# (same constant → identical bytes in each, keeping the two prompts symmetric).
# Overrides are EVIDENCE ONLY: the orchestrator validates them via
# ProgramOverrideValidator and drops invalid ones; it never re-applies them to
# the trace. Naming the field in the prompt is what lets the LLM populate
# ``node_overrides`` / ``new_answer`` at all — without it the pydantic fields
# are unreachable.
_NODE_OVERRIDES_INSTRUCTION = (
    "\n【可选:节点改判存证】若你在诊断/决策中认为某个已答节点的答案应当改判,"
    "可在输出里附 ``node_overrides`` 数组(字段 node_id / new_answer / reason / "
    "bar_range)。这是**留痕存证**,系统不会据此改写你的 trace;无改判则省略该键。"
    "reason 必须给出具体理由(禁止'是/否/通过'等模板词)。"
)


class TwoStageWorkflow:
    """Two-stage AI decision workflow (methodology §2.1).

    Stage 1 (Diagnosis): LLM produces a structured diagnosis + gate_result.
    Stage 2 (Decision): only if gate_result == "proceed"; LLM produces the
    final decision / order / trace.

    Both stages share an identical system prompt (KV cache optimization, §5.1)
    and run through the same 3-layer validator with a retry loop that flags
    cheat (immutable-field mutation) and non-retryable semantic errors.
    """

    def __init__(
        self,
        *,
        llm: LLMProvider,
        parser_graph: DecisionTreeGraph,
        validator_config: ValidatorConfig,
        routing_table: RoutingTable,
        program_registry: ProgramNodeRegistry,
        persistence: RecordPersistence,
        required_nodes: frozenset[str] = frozenset(),
        tree_text: str = "",
        global_rules: list[str] | None = None,
        max_retry: int = 3,
        trading_stance: str | None = None,
        htf_summary: dict | None = None,
        overlay_patterns: dict | None = None,
        direction_signal: dict | None = None,
    ) -> None:
        self.llm = llm
        self.parser_graph = parser_graph
        self.validator_config = validator_config
        self.routing_table = routing_table
        self.program_registry = program_registry
        self.persistence = persistence
        self.required_nodes = frozenset(required_nodes)
        self.tree_text = tree_text
        self.global_rules = list(global_rules) if global_rules else []
        self.max_retry = max_retry
        # Personalization lives in Stage 2 user prompt only — Stage 1
        # diagnosis must stay stance-neutral, and the shared system prompt
        # bytes stay identical (KV reuse §5.1).
        self.trading_stance = trading_stance
        # Higher-timeframe context: same routing as stance — Stage 2 user
        # prompt only, system prompt bytes untouched.
        self.htf_summary = htf_summary
        # B4 overlay: deterministic pattern hits (from overlays.py) —
        # Stage 2 user prompt only, same routing as stance/htf.
        self.overlay_patterns = overlay_patterns
        # Program-owned direction call (compute_direction_signal) — a
        # ctor-scoped attribute, never a mutation, so concurrent requests
        # cannot race. Rendered into the Stage 2 user prompt and stamped on
        # the record (all three writer arms). Never placed inside
        # input_data: Stage 1's json.dumps(input_data) would leak it.
        self.direction_signal = direction_signal
        # Built once — identical bytes for Stage 1 and Stage 2 (§5.1 KV reuse).
        self._system_prompt = build_system_prompt(self.tree_text, self.global_rules)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self, input_data: dict, *, input_id: str, session_id: str
    ) -> AnalysisRecord:
        """Run the two-stage workflow end-to-end and persist the audit record."""
        system_prompt = self._system_prompt

        # --- Stage 1: Diagnosis ---
        stage1, stage1_messages, stage1_raw, stage1_usage = self._run_stage1(
            input_data=input_data, system_prompt=system_prompt
        )

        # --- Gate short-circuit ---
        if stage1.gate_result != "proceed":
            short_circuit_summary: dict = {
                "short_circuited": True,
                "gate": stage1.gate_result,
            }
            # The program direction call is independent of the LLM gate: a
            # wait record still carries a scorable direction. The block lives
            # in decision_summary so the scorer's *existing* stage-2 read path
            # finds it, and carries source="program_signal" so it is attributed
            # to the program arm, not mislabelled as an LLM Stage-2 call (T-D).
            if self.direction_signal is not None:
                short_circuit_summary["next_period"] = {
                    "direction": self.direction_signal.get("direction"),
                    "source": "program_signal",
                }
            stage2 = Stage2Result(
                decision="wait",
                terminal_outcome="wait",
                order=None,
                trace=[],
                decision_summary=short_circuit_summary,
            )
            strategy_files: list[str] = []
            stage2_messages: list[LLMMessage] | None = None
            stage2_raw: dict | None = None
            usage_total = dict(stage1_usage)
        else:
            stage2, stage2_messages, stage2_raw, stage2_usage = self._run_stage2(
                input_data=input_data,
                stage1=stage1,
                system_prompt=system_prompt,
            )
            usage_total = _merge_usage(stage1_usage, stage2_usage)
            strategy_files = route_strategy_files(
                stage1.diagnosis_summary, self.routing_table
            )

        # --- Merge into AnalysisRecord ---
        meta = RecordMeta(
            timestamp=datetime.now(timezone.utc).isoformat(),
            input_id=input_id,
            session_id=session_id,
        )
        # Validate LLM self-reported node overrides (evidence-only; never
        # re-applied to the trace). Stage 1 persists only its inner
        # diagnosis_summary dict, so the overrides are merged in additively —
        # existing keys (state / gate_result / next_period) that downstream
        # eval scripts read stay untouched.
        stage1_persisted = dict(stage1.diagnosis_summary)
        stage1_overrides = self._validate_overrides(list(stage1.node_overrides))
        if stage1_overrides:
            stage1_persisted["node_overrides"] = [
                o.model_dump() for o in stage1_overrides
            ]
        stage1_record = StageRecord(
            messages=_messages_to_dicts(stage1_messages),
            response=stage1_raw,
            diagnosis_or_decision=stage1_persisted,
            trace=stage1.trace,
        )
        stage2_record: StageRecord | None = None
        if stage2 is not None:
            # Stage 2's diagnosis_or_decision holds the full decision result
            # (decision / terminal_outcome / order / decision_summary) so
            # downstream consumers can read the terminal decision directly.
            # Overwrite node_overrides with the validated subset.
            stage2_dump = stage2.model_dump()
            stage2_dump["node_overrides"] = [
                o.model_dump()
                for o in self._validate_overrides(list(stage2.node_overrides))
            ]
            stage2_record = StageRecord(
                messages=_messages_to_dicts(stage2_messages) if stage2_messages else [],
                response=stage2_raw or {},
                diagnosis_or_decision=stage2_dump,
                trace=stage2.trace,
            )
        record = AnalysisRecord(
            meta=meta,
            input_data=input_data,
            stage1=stage1_record,
            stage2=stage2_record,
            strategy_files_used=strategy_files,
            usage_total=usage_total,
            direction_signal=self.direction_signal,
        )

        # --- Persist ---
        self.persistence.save(record.model_dump())
        return record

    # ------------------------------------------------------------------
    # Stage 1
    # ------------------------------------------------------------------

    def _run_stage1(
        self,
        *,
        input_data: dict,
        system_prompt: str,
    ) -> tuple[Stage1Result, list[LLMMessage], dict, dict[str, int]]:
        """Run the Stage 1 diagnosis loop with retry + cheat detection.

        Returns ``(result, messages, raw, usage)`` where ``usage`` sums the
        per-call usage across **every** attempt — a schema/JSON retry still
        billed tokens, so the total must span the whole loop, not just the
        call that finally validated.
        """
        prefill = self.program_registry.prefill(
            node_ids=list(self.parser_graph.nodes.keys()),
            input_data=input_data,
        )
        base_user = self._build_stage1_user_prompt(
            input_data=input_data, prefill=prefill
        )

        feedback: str | None = None
        previous: Stage1Result | None = None
        last_messages: list[LLMMessage] = []
        last_raw: dict = {}
        usage_acc: dict[str, int] = {}

        for attempt in range(self.max_retry):
            user_content = base_user if feedback is None else base_user + feedback
            messages = [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_content),
            ]
            response = self.llm.call(messages)
            usage_acc = _merge_usage(usage_acc, response.usage)
            last_messages = messages

            try:
                parsed = json.loads(response.content)
            except json.JSONDecodeError as exc:
                # Treat as a json-layer validation error; retry with feedback.
                feedback = (
                    f"\n\n【上次错误】stage1 response was not valid JSON: {exc.msg}"
                )
                continue
            last_raw = parsed if isinstance(parsed, dict) else {}

            try:
                result = Stage1Result.model_validate(parsed)
            except Exception as exc:  # Pydantic ValidationError
                feedback = f"\n\n【上次错误】stage1 schema validation failed: {exc}"
                continue

            report = validate_stage1(
                result,
                graph=self.parser_graph,
                required_nodes=self.required_nodes,
                config=self.validator_config,
            )
            if report.is_valid:
                # Cross-retry cheat check on immutable fields.
                cheats = detect_cheat(previous, result)
                if cheats:
                    raise CheatError(
                        field=cheats[0],
                        message=(
                            f"stage1 immutable field(s) {cheats} mutated between "
                            f"retries (attempt {attempt + 1})"
                        ),
                    )
                return result, last_messages, last_raw, usage_acc

            # Cheat flag from validator → raise immediately.
            cheat_errs = [e for e in report.errors if e.cheat_detected]
            if cheat_errs:
                first = cheat_errs[0]
                raise CheatError(field=first.field, message=first.message)

            # Semantic errors are NOT retryable (§7.2 line 277).
            semantic_errs = [e for e in report.errors if e.layer == "semantic"]
            if semantic_errs:
                first = semantic_errs[0]
                raise SemanticError(message=first.message, node_id=first.node_id)

            # Cross-retry cheat detection (bar_range / program_prefill_nodes /
            # diagnosis_summary mutation between attempts).
            cheats = detect_cheat(previous, result)
            if cheats:
                raise CheatError(
                    field=cheats[0],
                    message=(
                        f"stage1 immutable field(s) {cheats} mutated between "
                        f"retries (attempt {attempt + 1})"
                    ),
                )

            previous = result
            feedback = "\n\n【上次错误】" + "; ".join(e.message for e in report.errors)

        raise ValidationError(
            layer="json",
            field="max_retry",
            message=f"stage1 failed after {self.max_retry} retries",
        )

    def _validate_overrides(self, overrides: list[NodeOverride]) -> list[NodeOverride]:
        """Validate LLM-reported node overrides; return only the valid subset.

        Overrides are EVIDENCE-ONLY (never re-applied to the trace), but a
        malformed one (unknown node, immutable node, boilerplate reason) is
        still worth dropping so the persisted evidence is trustworthy. Invalid
        ones are logged, not raised — a bad self-report must not fail an
        otherwise-valid analysis run.
        """
        if not overrides:
            return []
        validator = ProgramOverrideValidator(
            self.parser_graph,
            high_risk_nodes=self.validator_config.high_risk_nodes,
            immutable_nodes=self.validator_config.immutable_nodes,
        )
        kept: list[NodeOverride] = []
        for override in overrides:
            errors = validator.validate(override)
            if errors:
                logger.warning(
                    "dropping invalid node_override for %s: %s",
                    override.node_id,
                    "; ".join(errors),
                )
                continue
            kept.append(override)
        return kept

    def _build_stage1_user_prompt(
        self,
        *,
        input_data: dict,
        prefill: dict[str, NodeAnswer],
    ) -> str:
        lines: list[str] = []
        lines.append("【输入数据】")
        lines.append(json.dumps(input_data, ensure_ascii=False, indent=2))
        if prefill:
            lines.append("")
            lines.append("【程序预填充】")
            for node_id, ans in prefill.items():
                lines.append(f"§{node_id} answer={ans.answer} reason={ans.reason}")
        lines.append("")
        lines.append(
            "请按照决策树进行 Stage 1 诊断，输出 JSON（含 state / gate_result / "
            "trace / diagnosis_summary）。"
        )
        lines.append(_NODE_OVERRIDES_INSTRUCTION)
        lines.append(
            "必须严格匹配以下 JSON Schema（字段名、枚举取值、嵌套结构均以 Schema "
            "为准，不得发明新值）："
        )
        lines.append(
            json.dumps(Stage1Result.model_json_schema(), ensure_ascii=False, indent=2)
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Stage 2
    # ------------------------------------------------------------------

    def _run_stage2(
        self,
        *,
        input_data: dict,
        stage1: Stage1Result,
        system_prompt: str,
    ) -> tuple[Stage2Result, list[LLMMessage], dict, dict[str, int]]:
        """Run the Stage 2 decision loop with retry + cheat detection.

        Returns ``(result, messages, raw, usage)`` — ``usage`` sums per-call
        usage across every attempt, same rationale as Stage 1.
        """
        strategy_files = route_strategy_files(
            stage1.diagnosis_summary, self.routing_table
        )
        base_user = self._build_stage2_user_prompt(
            input_data=input_data,
            diagnosis_summary=stage1.diagnosis_summary,
            strategy_files=strategy_files,
        )

        feedback: str | None = None
        last_messages: list[LLMMessage] = []
        last_raw: dict = {}
        usage_acc: dict[str, int] = {}

        for attempt in range(self.max_retry):
            user_content = base_user if feedback is None else base_user + feedback
            messages = [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_content),
            ]
            response = self.llm.call(messages)
            usage_acc = _merge_usage(usage_acc, response.usage)
            last_messages = messages

            try:
                parsed = json.loads(response.content)
            except json.JSONDecodeError as exc:
                feedback = (
                    f"\n\n【上次错误】stage2 response was not valid JSON: {exc.msg}"
                )
                continue
            last_raw = parsed if isinstance(parsed, dict) else {}

            try:
                result = Stage2Result.model_validate(parsed)
            except Exception as exc:
                feedback = f"\n\n【上次错误】stage2 schema validation failed: {exc}"
                continue

            report = validate_stage2(
                result,
                stage1=stage1,
                graph=self.parser_graph,
                required_nodes=self.required_nodes,
                config=self.validator_config,
                expected_direction=(
                    self.direction_signal.get("direction")
                    if self.direction_signal
                    else None
                ),
            )
            if report.is_valid:
                # Cross-stage cheatError: the validator already checks
                # decision_summary['diagnosis_summary'] == stage1.diagnosis_summary
                # (§2.4 line 262) via its own cheat_detected flag. detect_cheat()
                # compares the full summary dicts directly, which is correct for
                # same-stage retries (Stage 1 attempt N vs N+1) but would
                # false-positive cross-stage (diagnosis_summary vs
                # decision_summary have different shapes). Filter to the
                # trace-level immutable fields only — the summary check stays
                # the validator's job.
                cheats = [
                    f
                    for f in detect_cheat(stage1, result)
                    if f in ("bar_range", "program_prefill_nodes")
                ]
                if cheats:
                    raise CheatError(
                        field=cheats[0],
                        message=(
                            f"stage2 vs stage1 immutable field(s) {cheats} "
                            f"differ (attempt {attempt + 1})"
                        ),
                    )
                return result, last_messages, last_raw, usage_acc

            cheat_errs = [e for e in report.errors if e.cheat_detected]
            if cheat_errs:
                first = cheat_errs[0]
                raise CheatError(field=first.field, message=first.message)

            semantic_errs = [e for e in report.errors if e.layer == "semantic"]
            if semantic_errs:
                first = semantic_errs[0]
                raise SemanticError(message=first.message, node_id=first.node_id)

            # Same filter as above — trace-level immutable fields only.
            cheats = [
                f
                for f in detect_cheat(stage1, result)
                if f in ("bar_range", "program_prefill_nodes")
            ]
            if cheats:
                raise CheatError(
                    field=cheats[0],
                    message=(
                        f"stage2 vs stage1 immutable field(s) {cheats} "
                        f"differ (attempt {attempt + 1})"
                    ),
                )

            feedback = "\n\n【上次错误】" + "; ".join(e.message for e in report.errors)

        raise ValidationError(
            layer="json",
            field="max_retry",
            message=f"stage2 failed after {self.max_retry} retries",
        )

    def _build_stage2_user_prompt(
        self,
        *,
        input_data: dict,
        diagnosis_summary: dict,
        strategy_files: list[str],
    ) -> str:
        lines: list[str] = []
        lines.append("【输入数据】")
        lines.append(json.dumps(input_data, ensure_ascii=False, indent=2))
        lines.append("")
        lines.append("【Stage 1 诊断摘要】")
        lines.append(json.dumps(diagnosis_summary, ensure_ascii=False, indent=2))
        lines.append("")
        lines.append("【可用策略文件】")
        if strategy_files:
            # Routing tables reference files relative to decision_trees/.
            base = Path(__file__).resolve().parent / "decision_trees"
            for f in strategy_files:
                path = base / f if not Path(f).is_absolute() else Path(f)
                if path.is_file():
                    lines.append(f"--- {f} ---")
                    lines.append(path.read_text(encoding="utf-8"))
                else:
                    lines.append(f"{f} (标注:文件不存在,仅有路径提示)")
        else:
            lines.append("(无)")
        lines.append("")
        if self.trading_stance:
            label = _TRADING_STANCE_LABELS.get(self.trading_stance)
            if label:
                lines.append("【交易倾向】")
                lines.append(label)
                lines.append("")
        if self.htf_summary:
            lines.append("【高周期背景】")
            lines.append(json.dumps(self.htf_summary, ensure_ascii=False, indent=2))
            lines.append("")
        if self.overlay_patterns:
            overlay_files = route_overlay_files(self.overlay_patterns)
            if overlay_files:
                lines.append("【叠加物料】(确定性检测器命中的形态,按此处理)")
                lines.append("【检测命中】")
                lines.append(
                    json.dumps(self.overlay_patterns, ensure_ascii=False, indent=2)
                )
                lines.append("")
                base = Path(__file__).resolve().parent / "decision_trees"
                for f in overlay_files:
                    path = base / f if not Path(f).is_absolute() else Path(f)
                    if path.is_file():
                        lines.append(f"--- {f} ---")
                        lines.append(path.read_text(encoding="utf-8"))
                        lines.append("")
        if self.direction_signal:
            lines.append("【程序方向信号】")
            lines.append(
                json.dumps(self.direction_signal, ensure_ascii=False, indent=2)
            )
            lines.append(
                "以上方向由确定性程序从原始 K 线计算,作为独立参照。你可以不同意,"
                "但必须在 decision_summary.direction 中显式陈述你自己的方向,"
                "不同意时须在理由中说明依据。"
            )
            lines.append("")
        lines.append(
            "请基于 Stage 1 诊断进行 Stage 2 决策，输出 JSON（含 decision / "
            "terminal_outcome / order / trace / decision_summary）。"
        )
        lines.append(_NODE_OVERRIDES_INSTRUCTION)
        lines.append(
            "必须严格匹配以下 JSON Schema（字段名、枚举取值、嵌套结构均以 Schema "
            "为准，不得发明新值）："
        )
        lines.append(
            json.dumps(Stage2Result.model_json_schema(), ensure_ascii=False, indent=2)
        )
        return "\n".join(lines)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _messages_to_dicts(messages: list[LLMMessage]) -> list[dict]:
    return [{"role": m.role, "content": m.content} for m in messages]


def _merge_usage(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    """Sum two per-call usage dicts key-wise (``prompt_tokens`` etc.).

    Same helper as ``market-analysis/flow.py:_merge_usage`` — the decision
    workflow's ``usage_total`` is the sum of every LLM call made during a run
    (both stages, every retry attempt), never a single call's usage. A missing
    key on either side contributes 0, so mixing providers that report different
    token keys stays correct.
    """
    out = dict(a)
    for k, v in b.items():
        out[k] = out.get(k, 0) + v
    return out
