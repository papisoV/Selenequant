"""Node-override self-report wiring (orchestrator).

Background: ``NodeOverride`` / ``node_overrides`` / ``new_answer`` were declared
on the Stage1/Stage2 pydantic models and rendered by the frontend (pa-panel's
"改判" badge) but had **no producer** — the LLM was never told the field existed,
and the orchestrator never persisted it (stage 1 stores only its inner
``diagnosis_summary`` dict). This suite pins the two halves of the fix:

  1. the prompt clause that tells the LLM it MAY report overrides, and
  2. the orchestrator path that validates reported overrides with
     ``ProgramOverrideValidator`` (drops invalid ones) and merges the survivors
     into the persisted record WITHOUT disturbing existing keys.

Overrides are evidence-only: the orchestrator never re-applies them to the
trace. These tests assert persistence/validation, not any decision change.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[3]
for _p in (_ROOT / "packages" / "decision-workflow", _ROOT / "services" / "local-api"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from selene_space_decision_workflow.llm_provider import StubLLMProvider  # noqa: E402
from selene_space_decision_workflow.orchestrator import (  # noqa: E402
    _NODE_OVERRIDES_INSTRUCTION,
)
from selene_space_decision_workflow.models import (  # noqa: E402
    NodeOverride,
)
from selene_space_decision_workflow.parser import parse_decision_tree  # noqa: E402
from selene_space_decision_workflow.persistence import InMemoryPersistence  # noqa: E402
from selene_space_decision_workflow.program_nodes import (  # noqa: E402
    ProgramOverrideValidator,
)
from selene_space_decision_workflow.trading_pipeline import (  # noqa: E402
    TERMINAL_RE,
    TRADING_TREE_PATH,
    build_trading_workflow,
)


def _graph():
    tree_text = TRADING_TREE_PATH.read_text(encoding="utf-8")
    return parse_decision_tree(
        tree_text, allowed_terminals=frozenset(TERMINAL_RE.findall(tree_text))
    )


def _service_synth():
    """Reuse the demo service's synthesizers to build validation-passing JSON."""
    from app.core.decision_workflow_service import DecisionWorkflowService

    return DecisionWorkflowService


# --------------------------------------------------------------------------
# Prompt clause: the field must be NAMED or the LLM cannot populate it.
# --------------------------------------------------------------------------


def test_prompt_clause_names_the_field() -> None:
    assert "node_overrides" in _NODE_OVERRIDES_INSTRUCTION
    for fld in ("node_id", "new_answer", "reason", "bar_range"):
        assert fld in _NODE_OVERRIDES_INSTRUCTION


def test_user_prompts_carry_the_clause() -> None:
    """Both stage prompts append the clause (each stage has its own overrides)."""
    src = (
        _ROOT
        / "packages"
        / "decision-workflow"
        / "selene_space_decision_workflow"
        / "orchestrator.py"
    ).read_text(encoding="utf-8")
    # one append in _build_stage1_user_prompt, one in _build_stage2_user_prompt
    assert src.count("lines.append(_NODE_OVERRIDES_INSTRUCTION)") == 2


# --------------------------------------------------------------------------
# Validator: the previously-uncalled ProgramOverrideValidator now gates input.
# --------------------------------------------------------------------------


def test_validator_rejects_immutable_boilerplate_and_unknown() -> None:
    graph = _graph()
    nodes = list(graph.nodes)
    v = ProgramOverrideValidator(graph, immutable_nodes=frozenset({nodes[0]}))
    assert v.validate(
        NodeOverride(
            node_id=nodes[0], new_answer="否", reason="具体理由", bar_range="K3-1"
        )
    ), "immutable node must be rejected"
    assert v.validate(
        NodeOverride(node_id=nodes[1], new_answer="否", reason="通过", bar_range="K3-1")
    ), "boilerplate reason must be rejected"
    assert v.validate(
        NodeOverride(node_id="NOPE", new_answer="否", reason="具体理由", bar_range=None)
    ), "unknown node must be rejected"


def test_validator_accepts_a_substantive_override() -> None:
    graph = _graph()
    nodes = list(graph.nodes)
    v = ProgramOverrideValidator(graph)
    assert (
        v.validate(
            NodeOverride(
                node_id=nodes[1],
                new_answer="否",
                reason="末三根 bar 实为缺口未回补，人工复核后改判",
                bar_range="K3-1",
            )
        )
        == []
    )


# --------------------------------------------------------------------------
# End-to-end: analyze() validates + persists overrides, keeps existing keys.
# --------------------------------------------------------------------------


def _run_with_overrides(overrides: list[dict]) -> dict:
    graph = _graph()
    svc = _service_synth()
    stage1_json = json.loads(svc._synthesize_stage1_response(graph))
    stage1_json["node_overrides"] = overrides
    # Synthesize stage2 from the CLEAN stage1 (no overrides) so the cross-stage
    # diagnosis_summary cheat check sees identical bytes.
    clean_stage1 = svc._synthesize_stage1_response(graph)
    stage2_json = svc._synthesize_stage2_response(graph, clean_stage1)

    persist = InMemoryPersistence()
    llm = StubLLMProvider([json.dumps(stage1_json, ensure_ascii=False), stage2_json])
    wf, _meta = build_trading_workflow(
        llm=llm, persistence=persist, symbol="BTC/USDT", interval="1h"
    )
    record = wf.analyze(
        {"symbol": "BTC/USDT", "interval": "1h", "summary": {"atr14": 5.0}},
        input_id="t1",
        session_id="s1",
    )
    return record.stage1.diagnosis_or_decision


def test_valid_override_is_persisted() -> None:
    persisted = _run_with_overrides(
        [
            {
                "node_id": "1.2",
                "new_answer": "否",
                "reason": "末三根 bar 实为缺口未回补，人工复核后改判",
                "bar_range": "K3-1",
            }
        ]
    )
    kept = persisted.get("node_overrides")
    assert kept and len(kept) == 1
    assert kept[0]["node_id"] == "1.2"
    assert kept[0]["new_answer"] == "否"


def test_invalid_overrides_are_dropped_not_raised() -> None:
    """Unknown node + boilerplate reason must be silently dropped (logged)."""
    persisted = _run_with_overrides(
        [
            {
                "node_id": "1.2",
                "new_answer": "否",
                "reason": "末三根 bar 实为缺口未回补",
                "bar_range": "K3-1",
            },
            {
                "node_id": "NOPE",
                "new_answer": "否",
                "reason": "具体理由",
                "bar_range": "K3-1",
            },
            {
                "node_id": "2.1",
                "new_answer": "否",
                "reason": "通过",
                "bar_range": "K3-1",
            },
        ]
    )
    kept = persisted.get("node_overrides") or []
    assert [o["node_id"] for o in kept] == ["1.2"], "only the valid override survives"


def test_existing_keys_survive_the_merge() -> None:
    """The additive merge must not drop state / gate_result etc. that evals read."""
    persisted = _run_with_overrides([])
    # state is always present in the synthesized stage1 summary
    assert "state" in persisted
    # no overrides reported → key simply absent (frontend renders nothing)
    assert not persisted.get("node_overrides")


def test_no_overrides_key_when_none_reported() -> None:
    persisted = _run_with_overrides([])
    assert "node_overrides" not in persisted
