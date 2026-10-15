# selene_space_decision_workflow

Two-stage AI decision workflow engine. See `docs/DECISION_WORKFLOW_METHODOLOGY.md`
for the methodology (credit/AML/investment/compliance scenarios).

## Scope (v1, library-only)

- **Parser**: `.txt` decision tree → `DecisionTreeGraph` (4 branch target kinds)
- **Validator**: 3 layers (JSON schema, consistency, semantic)
- **Orchestrator**: `TwoStageWorkflow.analyze()` — Stage 1 diagnosis + Stage 2 decision,
  gate short-circuit, retry, cheat detection
- **LLMProvider**: Protocol + `StubLLMProvider` (Phase 2 plugs in real LLM)
- **Persistence**: Protocol + `StubPersistence`

## Quick sketch

```python
from selene_space_decision_workflow.parser import parse_decision_tree
from selene_space_decision_workflow.orchestrator import TwoStageWorkflow
from selene_space_decision_workflow.llm_provider import StubLLMProvider
from selene_space_decision_workflow.persistence import StubPersistence

graph = parse_decision_tree(open("credit_approval.txt").read(),
                            allowed_terminals=frozenset({"approve","reject","wait"}))
wf = TwoStageWorkflow(llm=StubLLMProvider([json1, json2]),
                     parser_graph=graph,
                     validator_config=ValidatorConfig(),
                     routing_table=CREDIT_ROUTING_TABLE,
                     program_registry=ProgramNodeRegistry(),
                     persistence=StubPersistence())
record = wf.analyze(input_data={"credit_score": 720}, input_id="app-001", session_id="s1")
```

## Pydantic scope note (§18 of plan)

Pydantic is introduced here for the first time in selene-merged alpha/decision
packages — scoped to LLM contract classes only (`models.py`). Internal DTOs
remain `@dataclass(frozen=True)`. Do NOT cite this as precedent for spreading
Pydantic into other alpha-core sub-packages.

## Out of scope for v1

FastAPI endpoint, real LLM integration, streaming, experience library,
visualization, demo mode replay. See plan §15.
