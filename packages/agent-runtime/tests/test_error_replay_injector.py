"""ErrorReplayInjector:假设 id 的两条来路 + 渲染包装。

对应缺陷:注入器从未接线,且接线时 ``message.strip()`` 当 id 的旧约定
会让多行(被 UI 上下文扩充过的)消息永远查不到 —— 看起来接了,实际从不生效。
"""

from __future__ import annotations

import pytest

from selene_space_agent.memory.research_memory import (
    ErrorReplayInjector,
    _extract_hypothesis_id,
)
from selene_space_storage.repositories.research_graph.error_replay import (
    Abandoned,
    ErrorReplayResult,
)


class _FakeHypotheses:
    def __init__(self, rows):
        self._rows = rows
        self.looked_up: list[str] = []

    async def get(self, hypothesis_id):
        self.looked_up.append(hypothesis_id)
        return self._rows.get(hypothesis_id)


class _FakeExperiments:
    db = object()


def _injector(hypotheses, result):
    calls: list[tuple] = []

    async def fake_recall(db, hypothesis_id, **kwargs):
        calls.append((db, hypothesis_id, kwargs))
        return result

    injector = ErrorReplayInjector(
        hypotheses=hypotheses,
        experiments=_FakeExperiments(),
        edges=object(),
        error_types=object(),
        recall=fake_recall,
    )
    return injector, calls


class TestExtractHypothesisId:
    def test_plain_id(self):
        assert _extract_hypothesis_id("hyp-0123456789ab") == "hyp-0123456789ab"

    def test_inside_sentence(self):
        assert (
            _extract_hypothesis_id("看看 hyp-0123456789ab 这个假设还能不能救")
            == "hyp-0123456789ab"
        )

    def test_missing_returns_none(self):
        assert _extract_hypothesis_id("bb_v3 在 1h 怎么样") is None
        assert _extract_hypothesis_id("") is None

    def test_short_fragment_is_not_an_id(self):
        # ``hyp-1`` 是测试/文档里的短写法,不对应真实 id(12 位 hex)。
        assert _extract_hypothesis_id("hyp-1") is None


class TestRecall:
    @pytest.mark.asyncio
    async def test_enriched_multiline_message_still_matches(self):
        # 回归:被 _enrich_message 拼过多行的消息不能整串当 id。
        hyps = _FakeHypotheses({"hyp-0123456789ab": {"statement": "动量有效"}})
        injector, calls = _injector(hyps, ErrorReplayResult())

        message = "[Current view: BTCUSDT 4h]\n看看 hyp-0123456789ab 的失败史"
        out = await injector.recall(message)

        assert hyps.looked_up == ["hyp-0123456789ab"]
        assert out is not None
        assert out.startswith("- [")

    @pytest.mark.asyncio
    async def test_explicit_anchor_wins(self):
        hyps = _FakeHypotheses({"hyp-0123456789ab": {"statement": "x"}})
        injector, _ = _injector(hyps, ErrorReplayResult())

        await injector.recall("随便什么话", hypothesis_id="hyp-0123456789ab")
        assert hyps.looked_up == ["hyp-0123456789ab"]

    @pytest.mark.asyncio
    async def test_no_id_in_message_returns_none_without_lookup(self):
        hyps = _FakeHypotheses({"hyp-0123456789ab": {"statement": "x"}})
        injector, calls = _injector(hyps, ErrorReplayResult())

        assert await injector.recall("bb_v3 表现如何") is None
        assert hyps.looked_up == []
        assert calls == []

    @pytest.mark.asyncio
    async def test_unknown_hypothesis_returns_none(self):
        hyps = _FakeHypotheses({})
        injector, calls = _injector(hyps, ErrorReplayResult())

        assert await injector.recall("hyp-0123456789ab") is None
        assert calls == []

    @pytest.mark.asyncio
    async def test_abandoned_gets_counted_line(self):
        hyps = _FakeHypotheses({"hyp-0123456789ab": {"statement": "x"}})
        injector, _ = _injector(hyps, Abandoned(fail_count=20))

        out = await injector.recall("hyp-0123456789ab")
        assert out is not None
        # ReAct 的"已参考 N 条"只数行首 ``-``;这段也得算进去。
        assert out.startswith("- [")
        assert "熔断" in out

    @pytest.mark.asyncio
    async def test_rendered_body_is_not_mangled(self):
        hyps = _FakeHypotheses({"hyp-0123456789ab": {"statement": "动量有效"}})
        result = ErrorReplayResult(
            former_fails=[{"id": "exp-1", "config": {"code": "def f():\n    pass"}}]
        )
        injector, _ = _injector(hyps, result)

        out = await injector.recall("hyp-0123456789ab")
        assert out is not None
        # 代码块必须原样保留,不能被逐行加前缀。
        assert "def f():" in out
        assert "    pass" in out
