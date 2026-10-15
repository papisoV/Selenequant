"""v025 question revisit — Question 层主动复访 + Revision 层 horizon 二分。

来源:LangAlpha vs 新定位对比判决(2026-10-03)学习清单①②,
plan internal-plan。

1. research_questions.next_revisit_at TEXT(nullable,NULL=无复访计划)。
   现有九表全是过去时(opened_at/updated_at/closed_at),没有任何字段指向
   未来 —— 召回只能是被动拉。这列是"上一段历史主动找上门来"的物理载体:
   写 pillar 时就要回答"什么证据出现时该重新看它"。
2. research_revisions.horizon TEXT NOT NULL DEFAULT 'structural'
   CHECK IN ('structural','tactical')。thesis-tracker 的双裁决二分:
   structural(月/季寿命,股价波动不证伪)vs tactical(天寿命,随时证伪)。
   挂 Revision 层(OQ1)因为召回单位是 revision/hypothesis,寿命标记必须
   跟着召回物走才能被消费。存量行默认 structural=保守。
3. partial index 只盖有复访计划的行 —— 表小(recall.py:190 既有先例:
   不给不存在的规模买单),索引语义诚实优先。

纯新增:不改既有行。horizon 的 stale 推导规则 v0 不发明,只标记+透传。
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration

_ALTER_QUESTION_REVISIT = (
    "ALTER TABLE research_questions ADD COLUMN next_revisit_at TEXT"
)

_ALTER_REVISION_HORIZON = (
    "ALTER TABLE research_revisions ADD COLUMN horizon TEXT NOT NULL "
    "DEFAULT 'structural' CHECK (horizon IN ('structural','tactical'))"
)

_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_research_questions_revisit "
    "ON research_questions (next_revisit_at) WHERE next_revisit_at IS NOT NULL",
)


async def _apply(conn: aiosqlite.Connection) -> None:
    await conn.execute(_ALTER_QUESTION_REVISIT)
    await conn.execute(_ALTER_REVISION_HORIZON)
    for index in _INDEX_DDL:
        await conn.execute(index)


migration = Migration(
    id="v025_question_revisit",
    version=25,
    description=(
        "Add research_questions.next_revisit_at + "
        "research_revisions.horizon (structural/tactical) for proactive revisit"
    ),
    apply=_apply,
)
