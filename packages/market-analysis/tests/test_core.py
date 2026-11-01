"""A3/A4 支撑 — 编排原语单测（retry_loop / gate_merge / cheat_anchor / record_assembly）。

覆盖 US-003 验收标准：
- retry_loop 超限抛 ValidationError（A3 校验重试支撑）
- cheat_anchor 突变抛 CheatError（A4 cheat 检测支撑）
- gate_merge 短路合并语义
- record_assembly 审计记录装配
- US-201 normalize_llm_json（strict/lenient 两档归一化）
"""
from __future__ import annotations

import json

import pytest

from selene_space_market_analysis.core import (
    cheat_anchor,
    cheat_anchor_between_attempts,
    diagnose_json_failure,
    gate_merge,
    make_meta,
    normalize_llm_json,
    parse_json_response,
    record_assembly,
    retry_loop,
)
from selene_space_market_analysis.exceptions import (
    CheatError,
    SemanticError,
    ValidationError,
)
from selene_space_market_analysis.models import (
    MarketValidationReport,
    ValidationErrorModel,
)


def _report(*, valid: bool, errors: tuple[ValidationErrorModel, ...] = ()) -> MarketValidationReport:
    return MarketValidationReport(
        errors=errors,
        is_valid=valid,
        cheat_fields=tuple(e.field for e in errors if e.cheat_detected),
    )


def _err(layer: str, *, cheat: bool = False, field: str = "x") -> ValidationErrorModel:
    return ValidationErrorModel(
        layer=layer, field=field, message="test", cheat_detected=cheat
    )


# ---------------------------------------------------------------------------
# retry_loop
# ---------------------------------------------------------------------------


def test_retry_loop_returns_on_valid() -> None:
    calls = 0

    def call_llm() -> dict:
        nonlocal calls
        calls += 1
        return {"market_regime": "range", "entry_gate": "wait"}

    def validator(raw: dict):
        return _report(valid=True), raw

    result, raw = retry_loop(call_llm, validator, stage_label="stage1")
    assert result["entry_gate"] == "wait"
    assert calls == 1


def test_retry_loop_exhaustion_raises_validation_error() -> None:
    """重试超限 → ValidationError（A3 支撑）。"""
    calls = 0

    def call_llm() -> dict:
        nonlocal calls
        calls += 1
        return {"bad": True}

    def validator(raw: dict):
        return _report(valid=False), None

    with pytest.raises(ValidationError) as exc:
        retry_loop(call_llm, validator, max_retry=3, stage_label="stage2")
    assert exc.value.layer == "json"
    assert exc.value.field == "max_retry"
    assert calls == 3


def test_retry_loop_cheat_raises_cheat_error() -> None:
    def call_llm() -> dict:
        return {}

    def validator(raw: dict):
        return _report(valid=False, errors=(_err("consistency", cheat=True),)), None

    with pytest.raises(CheatError):
        retry_loop(call_llm, validator, max_retry=3)


def test_retry_loop_semantic_raises_semantic_error() -> None:
    def call_llm() -> dict:
        return {}

    def validator(raw: dict):
        return _report(valid=False, errors=(_err("semantic"),)), None

    with pytest.raises(SemanticError):
        retry_loop(call_llm, validator, max_retry=3)


# ---------------------------------------------------------------------------
# cheat_anchor
# ---------------------------------------------------------------------------


def test_cheat_anchor_no_baseline_passes() -> None:
    """无锚定基线（首次重试）→ 跳过，不抛。"""
    cheat_anchor({}, {"bar_features": {"adx": 28.5}}, ("bar_features",))


def test_cheat_anchor_consistent_fields_pass() -> None:
    """不可变字段与基线一致 → 不抛。"""
    baseline = {"bar_features": {"adx": 28.5}, "market_regime": "trend_up"}
    candidate = {"bar_features": {"adx": 28.5}, "market_regime": "trend_up"}
    cheat_anchor(baseline, candidate, ("bar_features", "market_regime"))


def test_cheat_anchor_mutation_raises_cheat_error() -> None:
    """不可变字段突变 → CheatError（A4 支撑）。"""
    baseline = {"bar_features": {"adx": 28.5}, "market_regime": "trend_up"}
    candidate = {"bar_features": {"adx": 99.9}, "market_regime": "trend_up"}
    with pytest.raises(CheatError) as exc:
        cheat_anchor(baseline, candidate, ("bar_features", "market_regime"))
    assert exc.value.field == "bar_features"


# ---------------------------------------------------------------------------
# cheat_anchor_between_attempts — 跨重试（尝试 N vs N-1）
# ---------------------------------------------------------------------------


def test_cheat_anchor_between_attempts_no_prev_passes() -> None:
    """无上次尝试（首次）→ 跳过，不抛。"""
    cheat_anchor_between_attempts(None, {"market_regime": "trend_up"}, ("market_regime",))


def test_cheat_anchor_between_attempts_stable_passes() -> None:
    """锚定字段在重试间保持 → 不抛。"""
    prev = {"market_regime": "trend_up"}
    cheat_anchor_between_attempts(prev, {"market_regime": "trend_up"}, ("market_regime",))


def test_cheat_anchor_between_attempts_flip_raises_cheat_error() -> None:
    """锚定字段在重试间翻转（改口诊断）→ CheatError。"""
    prev = {"market_regime": "trend_up"}
    candidate = {"market_regime": "trend_down"}
    with pytest.raises(CheatError) as exc:
        cheat_anchor_between_attempts(prev, candidate, ("market_regime",))
    assert exc.value.field == "market_regime"


# ---------------------------------------------------------------------------
# gate_merge
# ---------------------------------------------------------------------------


def test_gate_merge_proceed_keeps_stage2() -> None:
    stage2 = {"decision": "limit", "dispatch_playbooks": []}
    out = gate_merge("proceed", stage2, synth_factory=lambda g: {})
    assert out is stage2


def test_gate_merge_short_circuit_synthesizes() -> None:
    out = gate_merge(
        "wait",
        None,
        synth_factory=lambda g: {"decision": "no_trade", "gate": g},
    )
    assert out == {"decision": "no_trade", "gate": "wait"}


def test_gate_merge_proceed_without_stage2_raises() -> None:
    with pytest.raises(ValueError):
        gate_merge("proceed", None, synth_factory=lambda g: {})


def test_gate_merge_short_circuit_with_stage2_raises() -> None:
    with pytest.raises(ValueError):
        gate_merge("wait", {"decision": "limit"}, synth_factory=lambda g: {})


# ---------------------------------------------------------------------------
# record_assembly / make_meta / parse_json_response
# ---------------------------------------------------------------------------


def test_record_assembly_builds_flat_record() -> None:
    meta = make_meta(input_id="BTCUSDT-1d", session_id="s1")
    record = record_assembly(
        meta=meta,
        input_data={"symbol": "BTCUSDT"},
        stage1={"market_regime": "range", "entry_gate": "wait"},
        stage2=None,
        dispatch_playbooks=[],
        usage_total={"prompt_tokens": 10, "completion_tokens": 5},
    )
    assert record["meta"]["input_id"] == "BTCUSDT-1d"
    assert record["stage1"]["entry_gate"] == "wait"
    assert record["stage2"] is None
    assert record["usage_total"]["completion_tokens"] == 5


def test_parse_json_response() -> None:
    assert parse_json_response('{"a": 1}') == {"a": 1}
    with pytest.raises(Exception):
        parse_json_response("not json")


# ---------------------------------------------------------------------------
# US-201 — normalize_llm_json（strict / lenient）
# ---------------------------------------------------------------------------


def test_normalize_strict_parses_clean_json() -> None:
    parsed, repaired = normalize_llm_json('{"a": 1}', "strict")
    assert parsed == {"a": 1}
    assert repaired is False


def test_normalize_strict_strips_code_fence() -> None:
    content = '```json\n{"market_regime": "range"}\n```'
    parsed, repaired = normalize_llm_json(content, "strict")
    assert parsed == {"market_regime": "range"}
    assert repaired is False


def test_normalize_strict_failure_raises_unrepaired() -> None:
    """strict 档失败原样抛（不修复）。"""
    with pytest.raises(json.JSONDecodeError):
        normalize_llm_json('{"a": 1, "b":', "strict")


def test_normalize_lenient_fixes_smart_quotes() -> None:
    parsed, repaired = normalize_llm_json('{"key": “value”}', "lenient")
    assert parsed == {"key": "value"}
    assert repaired is True


def test_normalize_lenient_fixes_fullwidth_semicolon() -> None:
    parsed, repaired = normalize_llm_json('{"a": 1；"b": 2}', "lenient")
    assert parsed == {"a": 1, "b": 2}
    assert repaired is True


def test_normalize_lenient_escapes_unescaped_quotes() -> None:
    parsed, repaired = normalize_llm_json('{"statement": "他说"好的"结论"}', "lenient")
    assert parsed == {"statement": '他说"好的"结论'}
    assert repaired is True


def test_normalize_lenient_completes_truncated_brackets() -> None:
    parsed, repaired = normalize_llm_json('{"a": 1, "b": [1, 2, 3', "lenient")
    assert parsed == {"a": 1, "b": [1, 2, 3]}
    assert repaired is True


def test_normalize_lenient_completes_truncated_string_then_bracket() -> None:
    parsed, repaired = normalize_llm_json('{"a": "unclosed', "lenient")
    assert parsed == {"a": "unclosed"}
    assert repaired is True


def test_normalize_lenient_still_raises_on_hopeless_input() -> None:
    with pytest.raises(json.JSONDecodeError):
        normalize_llm_json("not json at all", "lenient")


def test_normalize_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError):
        normalize_llm_json('{"a": 1}', "bogus")


# ---------------------------------------------------------------------------
# diagnose_json_failure — 双失败时分类截断 / 语法
# ---------------------------------------------------------------------------


def test_diagnose_truncated_unterminated_string() -> None:
    diag = diagnose_json_failure('{"a": "unclosed')
    assert diag["kind"] == "truncated"
    assert "截断" in diag["hint"]


def test_diagnose_truncated_unclosed_bracket() -> None:
    diag = diagnose_json_failure('{"a": 1, "b": [1, 2')
    assert diag["kind"] == "truncated"
    assert diag["hint"]


def test_diagnose_syntax_balanced_but_invalid() -> None:
    diag = diagnose_json_failure('{"a": 1,,}')
    assert diag["kind"] == "syntax"
    assert "语法" in diag["hint"]


def test_diagnose_syntax_hard_garbage() -> None:
    diag = diagnose_json_failure("not json at all")
    assert diag["kind"] == "syntax"
