"""Stage 2 confidence contract tests (S1, decision_summary top-level channel).

Guarantees:
1. build_global_rules contract text carries the confidence clause (parallel
   wording to the next_period clause, inside the Stage 2 output-contract rule)
2. E2E: LLM returning decision_summary.confidence survives the Pydantic
   narrow mouth (Stage2Result extra='ignore' drops top-level extras; the
   decision_summary dict[str, Any] passes content through untouched)
3. E2E counter-case: confidence nested inside decision_summary["diagnosis_summary"]
   trips the cross-stage deep-equality cheat check → CheatError
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from selene_space_decision_workflow.exceptions import CheatError
from selene_space_decision_workflow.llm_provider import StubLLMProvider
from selene_space_decision_workflow.persistence import InMemoryPersistence
from selene_space_decision_workflow.trading_pipeline import (
    build_global_rules,
    build_trading_workflow,
)

_SYMBOL = "BTC/USDT"
_INTERVAL = "1h"

_STAGE1_SUMMARY = {
    "state": "trend_up",
    "gate_result": "proceed",
    "notes": "多头结构完整",
}

_STAGE1_JSON = json.dumps(
    {
        "state": "trend_up",
        "gate_result": "proceed",
        "trace": [
            {
                "node_id": "1.1",
                "question": "数据完整性检查",
                "answer": "是",
                "bar_range": "K1-K120",
                "reason": "窗口含 K1-K120 共120根K线",
                "branch": "是",
                "program_prefill_nodes": [],
            }
        ],
        "diagnosis_summary": _STAGE1_SUMMARY,
    },
    ensure_ascii=False,
)


def _make_workflow(llm: StubLLMProvider, tmp_path: Path):
    wf, _meta = build_trading_workflow(
        llm=llm,
        persistence=InMemoryPersistence(),
        symbol=_SYMBOL,
        interval=_INTERVAL,
        results_dir=tmp_path,
    )
    return wf


# ------------------------------------------------------------- contract text


def test_contract_text_carries_confidence_clause(tmp_path: Path) -> None:
    rules, _meta = build_global_rules(_SYMBOL, _INTERVAL, results_dir=tmp_path)
    with_confidence = [r for r in rules if "confidence" in r]
    assert len(with_confidence) == 1
    rule = with_confidence[0]
    # Same rule as the next_period precedent and the Stage 2 contract.
    assert "Stage 2 输出契约" in rule
    assert "next_period" in rule
    assert "decision_summary 可选携带 confidence" in rule
    assert "顶层键" in rule
    assert "禁止嵌入 diagnosis_summary" in rule


# ---------------------------------------------------------------- e2e positive


def test_confidence_survives_narrow_mouth_via_decision_summary(
    tmp_path: Path,
) -> None:
    stage2_json = json.dumps(
        {
            "decision": "wait",
            "terminal_outcome": "wait",
            "order": None,
            "trace": [],
            "decision_summary": {
                "diagnosis_summary": _STAGE1_SUMMARY,
                "confidence": 0.72,
            },
            # Illegal top-level placement: must be silently dropped.
            "confidence": 0.99,
        },
        ensure_ascii=False,
    )
    wf = _make_workflow(StubLLMProvider([_STAGE1_JSON, stage2_json]), tmp_path)
    record = wf.analyze(
        input_data={"symbol": _SYMBOL, "interval": _INTERVAL},
        input_id="conf-pass",
        session_id="s1",
    )

    assert record.stage2 is not None
    decision = record.stage2.diagnosis_or_decision
    assert decision["decision_summary"]["confidence"] == pytest.approx(0.72)
    # Top-level extra never reaches the record (Pydantic extra='ignore').
    assert "confidence" not in decision


# ---------------------------------------------------------------- e2e negative


def test_confidence_inside_diagnosis_summary_trips_cheat_check(
    tmp_path: Path,
) -> None:
    tampered = {**_STAGE1_SUMMARY, "confidence": 0.72}
    stage2_json = json.dumps(
        {
            "decision": "wait",
            "terminal_outcome": "wait",
            "order": None,
            "trace": [],
            "decision_summary": {"diagnosis_summary": tampered},
        },
        ensure_ascii=False,
    )
    wf = _make_workflow(StubLLMProvider([_STAGE1_JSON, stage2_json]), tmp_path)
    with pytest.raises(CheatError) as exc_info:
        wf.analyze(
            input_data={"symbol": _SYMBOL, "interval": _INTERVAL},
            input_id="conf-cheat",
            session_id="s1",
        )
    assert exc_info.value.field == "diagnosis_summary"
