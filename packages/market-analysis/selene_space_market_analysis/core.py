"""市场无关的编排原语（retry_loop / gate_merge / cheat_anchor / record_assembly）。

这些原语只操作普通 dict 与鸭子类型 report，不 import 任何市场模型或决策树类型，
可供 market-analysis 与后续其他两阶段流程复用。
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, TypeVar

from .exceptions import CheatError, SemanticError, ValidationError

ResultT = TypeVar("ResultT")


# ---------------------------------------------------------------------------
# retry_loop — 通用重试包装
# ---------------------------------------------------------------------------


def retry_loop(
    call_llm: Callable[[], dict],
    validator: Callable[[dict], tuple[Any, ResultT | None]],
    *,
    max_retry: int = 3,
    stage_label: str = "stage",
    on_json_error: Callable[[str], None] | None = None,
) -> tuple[ResultT, dict]:
    """带分层校验的重试循环（市场无关）。

    - ``call_llm``：产出原始 JSON-parsed dict。
    - ``validator``：接收 raw dict，返回 ``(report, result-or-None)``。
      report 为鸭子类型：须有 ``.is_valid``、``.errors``（每项含 ``.layer``、
      ``.cheat_detected``、``.message``、``.field``）。

    行为：
    - report 通过 → 返回 ``(result, raw)``。
    - cheat 标志（report.errors 中任意 ``cheat_detected``）→ 抛 CheatError。
    - semantic 层错误 → 抛 SemanticError（不可重试）。
    - 其余错误重试（重试间由调用方构造结构化反馈）；超限抛 ValidationError。
    """
    for _attempt in range(1, max_retry + 1):
        raw = call_llm()
        report, result = validator(raw)

        if report.is_valid and result is not None:
            return result, raw

        if any(e.cheat_detected for e in report.errors):
            first = next(e for e in report.errors if e.cheat_detected)
            raise CheatError(field=first.field, message=first.message)

        semantic_errs = [e for e in report.errors if e.layer == "semantic"]
        if semantic_errs:
            raise SemanticError(message=semantic_errs[0].message)

    raise ValidationError(
        layer="json",
        field="max_retry",
        message=f"{stage_label} failed after {max_retry} retries",
    )


# ---------------------------------------------------------------------------
# gate_merge — gate 短路合并
# ---------------------------------------------------------------------------


def gate_merge(
    entry_gate: str,
    stage2: Any | None,
    *,
    synth_factory: Callable[[str], Any],
) -> Any:
    """按 entry_gate 合并出有效 Stage 2 结果。

    - ``entry_gate == "proceed"``：要求提供 stage2；返回原值。
    - 否则（wait/unknown 短路）：要求 stage2 为空；用 ``synth_factory(gate)``
      合成一条 wait 记录返回。

    参数不匹配（proceed 却无 stage2 / 短路却带 stage2）抛 ValueError，
    防止编排层静默产生不一致记录。
    """
    if entry_gate == "proceed":
        if stage2 is None:
            raise ValueError("entry_gate='proceed' 但缺少 stage2")
        return stage2
    if stage2 is not None:
        raise ValueError(
            f"entry_gate='{entry_gate}'（短路）不应携带 stage2"
        )
    return synth_factory(entry_gate)


# ---------------------------------------------------------------------------
# cheat_anchor — 不可变字段锚定
# ---------------------------------------------------------------------------


def cheat_anchor(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    immutable_fields: tuple[str, ...],
) -> None:
    """跨重试比对不可变字段（基于确定性输入锚定）。

    ``baseline`` 来自确定性输入（如预计算指标），``candidate`` 为 LLM 输出。
    任一不可变字段在 candidate 中与 baseline 不一致 → 抛 CheatError（字段可能被
    LLM 篡改）。baseline 为空则跳过（无锚定基线）。
    """
    if not baseline:
        return
    for field in immutable_fields:
        if candidate.get(field) != baseline.get(field):
            raise CheatError(
                field=field,
                message=(
                    f"不可变字段 '{field}' 与确定性输入基线不一致"
                    "（疑似被篡改）"
                ),
            )


def cheat_anchor_between_attempts(
    prev: dict[str, Any] | None,
    candidate: dict[str, Any],
    anchor_fields: tuple[str, ...],
) -> None:
    """跨重试比对不可变字段（尝试 N vs 尝试 N-1）。

    ``prev`` 为上一次已解析的 LLM 输出，``candidate`` 为本次输出。任一锚定
    字段在两次重试间翻转 → 抛 CheatError（模型改口诊断，而非修正格式）。
    ``prev`` 为空（首次尝试）则跳过。
    """
    if prev is None:
        return
    for field in anchor_fields:
        if candidate.get(field) != prev.get(field):
            raise CheatError(
                field=field,
                message=(
                    f"不可变字段 '{field}' 在重试间由 {prev.get(field)!r} "
                    f"变为 {candidate.get(field)!r}（疑似改口诊断，而非修正格式）"
                ),
            )


# ---------------------------------------------------------------------------
# record_assembly — 审计记录装配
# ---------------------------------------------------------------------------


def record_assembly(
    *,
    meta: dict[str, Any],
    input_data: dict[str, Any],
    stage1: dict[str, Any],
    stage2: dict[str, Any] | None,
    dispatch_playbooks: list[str],
    usage_total: dict[str, int],
) -> dict[str, Any]:
    """装配一份审计记录 dict（市场无关）。

    stage1/stage2 为已 model_dump 的结构化 JSON；dispatch_playbooks 为命中的
    playbook 列表；usage_total 汇总两阶段 token 用量。
    """
    return {
        "meta": meta,
        "input_data": input_data,
        "stage1": stage1,
        "stage2": stage2,
        "dispatch_playbooks": list(dispatch_playbooks),
        "usage_total": dict(usage_total),
    }


def make_meta(*, input_id: str, session_id: str) -> dict[str, str]:
    """生成 record meta（市场无关）。"""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "input_id": input_id,
        "session_id": session_id,
    }


def parse_json_response(content: str) -> dict:
    """解析 LLM 响应为 dict；非 JSON 抛 json.JSONDecodeError。"""
    parsed = json.loads(content)
    return parsed if isinstance(parsed, dict) else {}


# ---------------------------------------------------------------------------
# normalize_llm_json — LLM 输出 JSON 归一化（strict / lenient）
# ---------------------------------------------------------------------------

_JSON_FENCE_RE = re.compile(r"^```(?:json)?[ \t]*([\s\S]*?)[ \t]*```$")
# CJK 全角标点与智能引号 → 半角等价物（lenient 修复用）。
_QUOTE_FIX = str.maketrans(
    {
        "“": '"',  # 左双引号 “
        "”": '"',  # 右双引号 ”
        "‘": "'",  # 左单引号 ‘
        "’": "'",  # 右单引号 ’
        "：": ":",  # 全角冒号 ：
        "；": ",",  # 全角分号 ；
        "，": ",",  # 全角逗号 ，
    }
)
_OPEN_TO_CLOSE = {"{": "}", "[": "]", "(": ")"}


def _strip_code_fence(content: str) -> str:
    """去除首尾代码围栏（```json ... ```）与空白。"""
    text = content.strip()
    m = _JSON_FENCE_RE.match(text)
    return m.group(1) if m else text


def _escape_unescaped_quotes(text: str) -> str:
    """把字符串内部未转义的引号补为转义（best-effort，仅结构引号保留）。

    启发式：字符串内出现的引号若后随 JSON 结构边界（``, ] } :``、再一个
    引号或结尾）视为闭合引号；否则视为内容引号补 ``\\`` 转义。
    """
    out: list[str] = []
    in_string = False
    escaped = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if escaped:
            out.append(ch)
            escaped = False
            i += 1
            continue
        if ch == "\\":
            out.append(ch)
            escaped = True
            i += 1
            continue
        if ch == '"':
            if in_string:
                j = i + 1
                while j < n and text[j].isspace():
                    j += 1
                nxt = text[j] if j < n else None
                if nxt not in ('"', ",", "}", "]", ":", None):
                    out.append("\\")
                    out.append(ch)
                    i += 1
                    continue
            in_string = not in_string
            out.append(ch)
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _complete_truncated_json(text: str) -> str:
    """补全截断的未闭合字符串与括号（best-effort，注入缺失尾部）。"""
    stack: list[str] = []
    in_string = False
    escaped = False
    for ch in text:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{([":
            stack.append(ch)
        elif ch in "}])":
            if stack:
                stack.pop()
    tail = '"' if in_string else ""
    for op in reversed(stack):
        tail += _OPEN_TO_CLOSE[op]
    return text + tail


def diagnose_json_failure(content: str) -> dict[str, Any]:
    """分类一次 JSON 解析失败为可操作诊断（供重试反馈 / 日志）。

    - ``truncated``：内容在结构上未闭合（未闭合字符串/括号）→ 输出被截断，
      通常是 max_tokens / 思考长度限制所致。
    - ``syntax``：结构完整但存在语法错误 → 模型输出格式问题。

    以原内容为输入（而非 lenient 修复后），反映"为何解析失败"的首要原因。
    """
    if _complete_truncated_json(content) != content:
        return {
            "kind": "truncated",
            "detail": "输出在结构上未闭合（字符串或括号被截断）",
            "hint": "输出疑似被截断，请压缩 JSON（删除多余注释/字段）并补全结构后重试",
        }
    return {
        "kind": "syntax",
        "detail": "输出结构完整但存在 JSON 语法错误",
        "hint": "请检查 JSON 语法（引号、逗号、括号配对）后重新输出",
    }


def normalize_llm_json(content: str, mode: str) -> tuple[dict, bool]:
    """归一化解析 LLM 原始输出（市场无关纯函数）。

    - ``mode="strict"``：去除代码围栏与首尾空白后 ``json.loads``；
      失败原样抛出（不做任何修复）。返回 ``(parsed, False)``。
    - ``mode="lenient"``：在 strict 基础上做 best-effort 修复
      （智能引号/全角标点/未转义引号/补全截断括号与缺失尾部）后解析；
      成功返回 ``(parsed, True)``；修复失败仍原样抛出。

    调用约定：先 strict 后 lenient；``repaired=True`` 表示 lenient 修复成功，
    调用方应据此打 ``meta.json_repaired`` 审计标记并保守对待后续校验结果。
    """
    if mode == "strict":
        parsed = json.loads(_strip_code_fence(content))
        return (parsed if isinstance(parsed, dict) else {}), False
    if mode != "lenient":
        raise ValueError(f"normalize_llm_json mode 仅支持 strict/lenient，收到 {mode!r}")

    text = _strip_code_fence(content)
    text = text.translate(_QUOTE_FIX)
    text = _escape_unescaped_quotes(text)
    text = _complete_truncated_json(text)
    parsed = json.loads(text)
    return (parsed if isinstance(parsed, dict) else {}), True


__all__ = [
    "retry_loop",
    "gate_merge",
    "cheat_anchor",
    "cheat_anchor_between_attempts",
    "record_assembly",
    "make_meta",
    "parse_json_response",
    "normalize_llm_json",
    "diagnose_json_failure",
]
