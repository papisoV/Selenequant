"""研究账本召回:anchor 识别 + 渲染 + 异步桥接回归。

对应缺陷:对话只读三条例子的 seed JSON,存储层积累的研究账本从未进入
对话。这里锁住三件事 —— 认得出可解释的锚点、认不出时不猜测、脏数据
不会把整段 prompt 弄坏。
"""

from __future__ import annotations

import pytest

from selene_space_agent.memory.research_ledger import (
    ResearchLedgerRecall,
    infer_anchor,
)


class TestInferAnchor:
    def test_finds_strategy_interval_regime(self):
        anchor = infer_anchor("帮我看看 bb_v3 在 BTC 1h 的震荡市表现")
        assert anchor["strategy_ids"] == ["bb_v3"]
        assert anchor["intervals"] == ["1h"]
        assert anchor["regimes"] == ["sideways"]

    def test_no_anchor_on_small_talk(self):
        # 认不出就不要召回 —— 返回空 dict,而不是编一个锚点。
        assert infer_anchor("你好,今天天气怎么样") == {}
        assert infer_anchor("") == {}

    def test_token_boundary_keeps_ids_apart(self):
        # ``bb`` 不该命中 ``bb_v3`` 这行里,否则"看下 bb_v3"会漏掉策略。
        anchor = infer_anchor("跑一下 bb_v3")
        assert anchor["strategy_ids"] == ["bb_v3"]

    def test_interval_does_not_match_inside_longer_number(self):
        # ``1d`` 不能命中 ``21d``(那是天数,不是周期锚点)。
        assert "intervals" not in infer_anchor("21d 内涨了多少")

    def test_chinese_aliases(self):
        anchor = infer_anchor("日线上牛市里的表现")
        assert "1d" in anchor["intervals"]
        assert "bull" in anchor["regimes"]

    def test_multiple_of_one_class(self):
        anchor = infer_anchor("bb_v3 和 rsi_ma_delta 都跑 4h 和 1d")
        assert anchor["strategy_ids"] == ["bb_v3", "rsi_ma_delta"]
        assert anchor["intervals"] == ["1d", "4h"]


def _candidate(**over):
    base = {
        "id": "rev-1",
        "kind": "revision",
        "text": "bb_v3 在 1h 震荡市里胜率显著高于趋势市",
        "question_id": "q-1",
        "score": 2,
        "matched_on": ["interval:1h", "regime:sideways"],
        "recall_class": "learned_experience",
        "superseded": False,
        "evidence_chain": [],
        "created_at": "2026-09-30T12:00:00",
    }
    base.update(over)
    return base


class TestRender:
    @pytest.mark.asyncio
    async def test_renders_class_and_audit_trail(self):
        async def fake_recall(db, **kwargs):
            assert kwargs["purpose"] == "investigate"
            assert kwargs["anchor"]["intervals"] == ["1h"]
            return [_candidate()]

        recall = ResearchLedgerRecall(recall=fake_recall, db_getter=lambda: object())
        out = await recall.recall("bb_v3 在 1h 震荡市怎么样")

        assert out.startswith("- ")
        # 分层与命中理由必须留在可见文本里,消费方据此区分知识/线索。
        assert "learned_experience" in out
        assert "interval:1h" in out
        assert "2026-09-30" in out

    @pytest.mark.asyncio
    async def test_empty_when_no_anchor(self):
        called = False

        async def fake_recall(db, **kwargs):
            nonlocal called
            called = True
            return []

        recall = ResearchLedgerRecall(recall=fake_recall, db_getter=lambda: object())
        assert await recall.recall("你好") == ""
        # 没有锚点就不该打库 —— 不能靠"查了但没命中"来凑效果。
        assert called is False

    @pytest.mark.asyncio
    async def test_recall_failure_does_not_break_chat(self):
        async def boom(db, **kwargs):
            raise RuntimeError("db gone")

        recall = ResearchLedgerRecall(recall=boom, db_getter=lambda: object())
        assert await recall.recall("bb_v3 1h") == ""

    @pytest.mark.asyncio
    async def test_superseded_is_flagged(self):
        async def fake_recall(db, **kwargs):
            return [_candidate(superseded=True, text="旧结论")]

        recall = ResearchLedgerRecall(recall=fake_recall, db_getter=lambda: object())
        out = await recall.recall("bb_v3 1h")
        assert "已被证伪/退役" in out

    @pytest.mark.asyncio
    async def test_long_text_is_clipped(self):
        async def fake_recall(db, **kwargs):
            return [_candidate(text="x" * 5000)]

        recall = ResearchLedgerRecall(recall=fake_recall, db_getter=lambda: object())
        out = await recall.recall("bb_v3 1h")
        assert len(out) < 400
        assert out.endswith("…")
