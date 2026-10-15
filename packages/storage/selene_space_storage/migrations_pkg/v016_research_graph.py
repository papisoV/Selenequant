"""Research graph — 八个研究对象 + 关系边,一次落地(v016)。

设计依据 the design spec
(§1-§15 冻结版)。这一迁移的全部意义是让研究图谱**成为事实**:
在此之前 Question/Episode/Hypothesis/Evidence/Experiment/Decision/Revision
在代码里一个都不存在(packages/ 全树 grep 零匹配)。

三条纪律直接落成 schema 而不是靠调用方自觉:
- 状态轴可逆但历史不可丢 → research_questions.status 是当前值,
  research_question_events 是只增的状态事件流(§2 三轴)
- 回答轴只增 → research_revisions 只 INSERT,(question_id, seq) 唯一(§2 三轴)
- Episode 无 state_version → 它只有一个当时的样子,后来变的是观察(§8③)

表名统一 research_ 前缀:experiments 已被 v010 占用,user_decisions 已被
v009 占用,不能复用。ISO-8601 UTC 字符串存储,字典序即时间序。
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration

# §2 四终态 + open。状态轴可逆,所以这不是生命周期终点,只是当前值。
_QUESTION_STATUSES = (
    "'open'",
    "'sufficiently_answered'",
    "'falsified'",
    "'parked'",
    "'superseded'",
)

_QUESTIONS_DDL = f"""
CREATE TABLE IF NOT EXISTS research_questions (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    body                TEXT NOT NULL DEFAULT '',
    anchor_json         TEXT NOT NULL DEFAULT '{{}}',
    status              TEXT NOT NULL CHECK (status IN ({", ".join(_QUESTION_STATUSES)})),
    parent_question_id  TEXT,
    opened_at           TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    closed_at           TEXT,
    created_by          TEXT NOT NULL DEFAULT 'director'
);
"""

_QUESTION_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS research_question_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id TEXT NOT NULL REFERENCES research_questions (id),
    occurred_at TEXT NOT NULL,
    actor       TEXT NOT NULL CHECK (actor IN ('selene','user','system')),
    kind        TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}'
);
"""

_REVISIONS_DDL = """
CREATE TABLE IF NOT EXISTS research_revisions (
    id              TEXT PRIMARY KEY,
    question_id     TEXT NOT NULL REFERENCES research_questions (id),
    seq             INTEGER NOT NULL,
    content         TEXT NOT NULL,
    rationale_json  TEXT NOT NULL DEFAULT '[]',
    cited_json      TEXT NOT NULL DEFAULT '[]',
    actor           TEXT NOT NULL CHECK (actor IN ('selene','user','system')),
    created_at      TEXT NOT NULL,
    UNIQUE (question_id, seq)
);
"""

# 边是一等公民(有 id)不是因为洁癖,是撤销机制倒逼:revokes 边必须能指向
# 被撤销的边本身。edge_class 只有 fact / judgment 两种,没有第三种(§7)。
_EDGES_DDL = """
CREATE TABLE IF NOT EXISTS research_edges (
    id              TEXT PRIMARY KEY,
    from_type       TEXT NOT NULL,
    from_id         TEXT NOT NULL,
    to_type         TEXT NOT NULL,
    to_id           TEXT NOT NULL,
    kind            TEXT NOT NULL,
    edge_class      TEXT NOT NULL CHECK (edge_class IN ('fact','judgment')),
    rationale       TEXT,
    provenance_json TEXT NOT NULL DEFAULT '[]',
    actor           TEXT NOT NULL CHECK (actor IN ('selene','user','system')),
    created_at      TEXT NOT NULL,
    UNIQUE (from_type, from_id, kind, to_type, to_id)
);
"""

# §8③ Episode 不可改:没有任何 state_version 列,也不会有 update 方法。
# 它记录"当时发生了什么",后来变化的是挂在它下面的 Evidence。
_EPISODES_DDL = """
CREATE TABLE IF NOT EXISTS research_episodes (
    id              TEXT PRIMARY KEY,
    subject_type    TEXT NOT NULL,
    subject_id      TEXT NOT NULL,
    title           TEXT NOT NULL,
    boundary_start  TEXT NOT NULL,
    boundary_end    TEXT NOT NULL,
    recorded_json   TEXT NOT NULL DEFAULT '{}',
    occurred_at     TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
"""

# 同一 Episode 可挂多条不同时点的观察 —— 这就是"当时 vs 现在"从 UI 问题
# 变成数据模型问题的地方(§8③)。
_EVIDENCE_DDL = """
CREATE TABLE IF NOT EXISTS research_evidence (
    id           TEXT PRIMARY KEY,
    episode_id   TEXT NOT NULL REFERENCES research_episodes (id),
    mode         TEXT NOT NULL CHECK (mode IN ('RECORDED','RECONSTRUCTED','AFTER_THE_FACT')),
    payload_json TEXT NOT NULL DEFAULT '{}',
    source_ref   TEXT NOT NULL DEFAULT '',
    observed_at  TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
"""

# 跨 Mission 存活:不挂在 mission 上(§6)。
_HYPOTHESES_DDL = """
CREATE TABLE IF NOT EXISTS research_hypotheses (
    id          TEXT PRIMARY KEY,
    statement   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'proposed'
                CHECK (status IN ('proposed','testing','supported','refuted','retired')),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""

_EXPERIMENTS_DDL = """
CREATE TABLE IF NOT EXISTS research_experiments (
    id            TEXT PRIMARY KEY,
    hypothesis_id TEXT REFERENCES research_hypotheses (id),
    kind          TEXT NOT NULL,
    config_json   TEXT NOT NULL DEFAULT '{}',
    ran_at        TEXT,
    created_at    TEXT NOT NULL
);
"""

# Decision 由 Director 正式产生,Mission 只能产出 Candidate Decision(§6)。
# outcome_status 的 never 防线:它必须配一个明确的观察窗口,否则"没等够"
# 会被当成"永远不会发生"(§8④)。
_DECISIONS_DDL = """
CREATE TABLE IF NOT EXISTS research_decisions (
    id             TEXT PRIMARY KEY,
    question_id    TEXT NOT NULL REFERENCES research_questions (id),
    verdict        TEXT NOT NULL,
    rationale      TEXT NOT NULL DEFAULT '',
    decided_by     TEXT NOT NULL CHECK (decided_by IN ('director')),
    outcome_status TEXT NOT NULL DEFAULT 'outcome_pending'
                   CHECK (outcome_status IN ('outcome_pending','outcome_observed','outcome_never')),
    window_start   TEXT,
    window_end     TEXT,
    decided_at     TEXT NOT NULL
);
"""

_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_research_questions_status "
    "ON research_questions (status, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_question_events_q "
    "ON research_question_events (question_id, occurred_at, id)",
    "CREATE INDEX IF NOT EXISTS idx_research_revisions_q "
    "ON research_revisions (question_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_research_edges_from "
    "ON research_edges (from_type, from_id, kind)",
    "CREATE INDEX IF NOT EXISTS idx_research_edges_to "
    "ON research_edges (to_type, to_id, kind)",
    "CREATE INDEX IF NOT EXISTS idx_research_episodes_subject "
    "ON research_episodes (subject_type, subject_id, occurred_at)",
    "CREATE INDEX IF NOT EXISTS idx_research_evidence_episode "
    "ON research_evidence (episode_id, observed_at)",
    "CREATE INDEX IF NOT EXISTS idx_research_decisions_q "
    "ON research_decisions (question_id, decided_at DESC)",
)


async def _apply(conn: aiosqlite.Connection) -> None:
    for ddl in (
        _QUESTIONS_DDL,
        _QUESTION_EVENTS_DDL,
        _REVISIONS_DDL,
        _EDGES_DDL,
        _EPISODES_DDL,
        _EVIDENCE_DDL,
        _HYPOTHESES_DDL,
        _EXPERIMENTS_DDL,
        _DECISIONS_DDL,
    ):
        await conn.execute(ddl)
    for index in _INDEX_DDL:
        await conn.execute(index)


migration = Migration(
    id="v016_research_graph",
    version=16,
    description="Add research graph tables (questions/revisions/edges/episodes/evidence/hypotheses/experiments/decisions)",
    apply=_apply,
)
