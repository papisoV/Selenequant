"""``usage_total`` must be the SUM of every LLM call, not the last one.

Background (the defect this suite pins): ``orchestrator._aggregate_usage()``
was a v1 stub that always returned ``{}`` — it received the stage *messages*
(inputs), never the ``LLMResponse`` objects (outputs), so it had nothing to
sum. The API layer papered over the empty field by overwriting ``usage_total``
with ``adapter.last_usage`` — the usage of the SINGLE last call. A run makes
>=2 real calls (stage 1, stage 2) plus up to ``max_retry`` attempts each, so a
field named ``total`` was systematically under-reporting spend (≈2x or worse).

Fix: the stage loops accumulate ``response.usage`` across every attempt and
hand the sums back; ``analyze()`` sums the two stages. These tests assert the
sum — and specifically that it is NOT the last call — so a regression that
reinstates "last masquerading as total" goes RED.

Package precedent: ``market-analysis/flow.py`` already does exactly this
(``_merge_usage(usage1, usage2)``); these tests port that discipline to the
decision-workflow line that had been left behind.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[3]
for _p in (_ROOT / "packages" / "decision-workflow", _ROOT / "services" / "local-api"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from selene_space_decision_workflow.llm_provider import (  # noqa: E402
    LLMMessage,
    LLMResponse,
)
from selene_space_decision_workflow.parser import parse_decision_tree  # noqa: E402
from selene_space_decision_workflow.persistence import InMemoryPersistence  # noqa: E402
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


def _synth_stage1() -> str:
    from app.core.decision_workflow_service import DecisionWorkflowService

    return DecisionWorkflowService._synthesize_stage1_response(_graph())


def _synth_stage2(stage1_json: str) -> str:
    from app.core.decision_workflow_service import DecisionWorkflowService

    return DecisionWorkflowService._synthesize_stage2_response(_graph(), stage1_json)


class _UsageProvider:
    """Returns queued ``(content, usage)`` pairs; usage differs per call.

    Unlike ``StubLLMProvider`` (which pins usage to zeros), this lets a test
    give each LLM call a distinct, non-zero usage dict — the only way to tell
    a real sum apart from the last-call value.
    """

    last_messages: list[LLMMessage] | None = None

    def __init__(self, responses: list[tuple[str, dict[str, int]]]) -> None:
        self._queue = list(responses)
        self.calls = 0
        self.last_messages = None

    def call(self, messages: list[LLMMessage]) -> LLMResponse:
        if not self._queue:
            raise RuntimeError("_UsageProvider queue exhausted — test setup issue")
        content, usage = self._queue.pop(0)
        self.calls += 1
        self.last_messages = list(messages)
        return LLMResponse(content=content, reasoning_content=None, usage=dict(usage))


def _run(provider: _UsageProvider, *, max_retry: int = 4):
    wf, _meta = build_trading_workflow(
        llm=provider,
        persistence=InMemoryPersistence(),
        symbol="BTC/USDT",
        interval="1h",
        max_retry=max_retry,
    )
    return wf.analyze(
        {"symbol": "BTC/USDT", "interval": "1h", "summary": {"atr14": 5.0}},
        input_id="u1",
        session_id="s1",
    )


_U1 = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
_U2 = {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}


def test_usage_total_sums_both_stages() -> None:
    """Two-stage run: usage_total = stage1 + stage2, NOT the last call."""
    s1 = _synth_stage1()
    provider = _UsageProvider([(s1, _U1), (_synth_stage2(s1), _U2)])
    record = _run(provider)

    assert provider.calls == 2, "sanity: exactly one call per stage"
    assert record.usage_total == {
        "prompt_tokens": 30,
        "completion_tokens": 13,
        "total_tokens": 43,
    }
    # The load-bearing distinction — this is what the old code got wrong.
    assert record.usage_total != _U2, "usage_total must not equal the last call"


def test_usage_total_counts_retry_attempts() -> None:
    """A failed attempt still spent tokens: the sum spans every call made.

    Stage 1's first response is invalid JSON (retry), second is valid, then
    Stage 2 — three billed calls, so usage_total must be the sum of all three.
    """
    s1 = _synth_stage1()
    provider = _UsageProvider(
        [
            ("{ not valid json", {"prompt_tokens": 1, "completion_tokens": 0}),
            (s1, _U1),
            (_synth_stage2(s1), _U2),
        ]
    )
    record = _run(provider)

    assert provider.calls == 3, "sanity: bad attempt + good stage1 + stage2"
    assert record.usage_total == {
        "prompt_tokens": 31,
        "completion_tokens": 13,
        "total_tokens": 43,
    }


def test_usage_total_stage1_only_on_short_circuit() -> None:
    """Gate != proceed → Stage 2 never runs → total is Stage 1's usage alone."""
    s1 = json.loads(_synth_stage1())
    s1["gate_result"] = "wait"
    provider = _UsageProvider([(json.dumps(s1, ensure_ascii=False), _U1)])
    record = _run(provider)

    assert provider.calls == 1, "short-circuit must not call the LLM for stage 2"
    assert record.usage_total == _U1


def test_usage_total_empty_is_not_faked() -> None:
    """A provider that reports no usage yields {} — honest, never a fabricated total."""
    s1 = _synth_stage1()
    provider = _UsageProvider([(s1, {}), (_synth_stage2(s1), {})])
    record = _run(provider)
    assert record.usage_total == {}


def test_service_does_not_overwrite_usage_total_with_last_call() -> None:
    """The API layer must no longer clobber the aggregate with ``last_usage``.

    Regression guard for the removed patch: overwriting the field with a single
    call's usage is exactly the defect. Asserted at source level (the service
    path needs a live adapter + bars to exercise end-to-end).
    """
    src = (
        _ROOT
        / "services"
        / "local-api"
        / "app"
        / "core"
        / "decision_workflow_service.py"
    ).read_text(encoding="utf-8")
    assert '"usage_total": dict(last_usage)' not in src, (
        "usage_total is overwritten with last_usage again — the aggregate would "
        "silently collapse back to a single call"
    )
