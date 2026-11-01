"""v027 前向验证索引 —— 只加索引,零新表(阶段 C / P5)。

两个索引,各自锁一个查询路径(方案 §7.5):

1. ``ux_research_evidence_dsa_eval`` —— ``research_evidence.source_ref`` 上
   的 **partial unique** 索引,谓词 ``source_ref LIKE 'dsa_eval:%'``。
   前向验证的幂等键是 ``dsa_eval:{analysis_id}:{window_end}``(方案 §3 议题1):
   同一 (分析, 窗口) 只允许一条观测落账。partial 谓词保证**非 dsa_eval 的
   evidence 行不受约束**——它们没有唯一性语义,不能被这条索引误伤。
   SQLite 支持带 LIKE 谓词的 partial index(实测本机 3.x 通过)。

2. ``idx_research_decisions_outcome_window`` —— ``research_decisions
   (outcome_status, window_end)``。P5 扫"已过窗口仍是 pending"的决策走这条;
   也服务 v016 既有的 changes.py tier1 扫描。

**无新表**:P5 全挂在既有 research graph 上(方案 §0 ADR:不 schema 化因果五档)。
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration

# 幂等基板:同一 (analysis_id, window_end) 的 dsa_eval 观测只许一条。
# 重跑命中 = IntegrityError,recorder 先查后插故实战中走干净 skip(方案
# Pre-mortem 1/3);索引是并发下的最后一道保险丝。
_EVIDENCE_DEDUP_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_research_evidence_dsa_eval "
    "ON research_evidence (source_ref) "
    "WHERE source_ref LIKE 'dsa_eval:%'"
)

_DECISION_WINDOW_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_research_decisions_outcome_window "
    "ON research_decisions (outcome_status, window_end)"
)


async def _apply(conn: aiosqlite.Connection) -> None:
    await conn.execute(_EVIDENCE_DEDUP_INDEX)
    await conn.execute(_DECISION_WINDOW_INDEX)


migration = Migration(
    id="v027_forward_validation_indexes",
    version=27,
    description=(
        "Partial unique index on research_evidence.source_ref for dsa_eval "
        "observations (idempotency) + research_decisions(outcome_status, "
        "window_end) index (P5 pending-window scan). Indexes only, no new tables."
    ),
    apply=_apply,
)
