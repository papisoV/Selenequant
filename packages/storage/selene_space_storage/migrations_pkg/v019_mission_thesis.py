"""Mission thesis — falsifiable pillars + append-only evidence ledger (v019).

Thesis-tracker 认识论纪律的数据模型转译(external-research-project Apache-2.0 理念,非代码):

- ``mission_pillars``:一个 Mission 的可证伪支柱。实体字段(claim/priority/
  baseline)一经写入禁改;变更走新 pillar 行 + 旧行 ``superseded_by`` 处置
  指针。``latest_evidence_id``/``signal`` 两列是纯缓存,恒等于按
  ``(occurred_at, seq)`` 序的 evidence 账本派生值,可随时重建验证。
- ``mission_evidence``:append-only 证据账本。无 UPDATE/DELETE 路径;更正 =
  追加新行。``seq`` AUTOINCREMENT 保证同 ``occurred_at`` 行的确定性保序
  (随机 ``ev-<hex>`` id 非单调,不承担保序职责)。

CHECK 重建 playbook(SQLite 不可 ALTER CHECK,扩档位时):
    1. CREATE TABLE mission_evidence_new (... 新词表 CHECK ...);
    2. INSERT INTO mission_evidence_new SELECT * FROM mission_evidence
       ORDER BY occurred_at, seq;   -- 保 (occurred_at, seq) 序,append-only 历史不颠倒
    3. DROP TABLE mission_evidence;
    4. ALTER TABLE mission_evidence_new RENAME TO mission_evidence;
    5. 重建索引。
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration
from selene_space_storage.repositories._mission_thesis_vocab import (
    DRIFT_PATTERNS,
    PILLAR_PRIORITIES,
    PILLAR_STATUSES,
    PROVENANCE_LEVELS,
    SIGNALS,
)


def _check(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


_PILLARS_DDL = f"""
CREATE TABLE IF NOT EXISTS mission_pillars (
    id                   TEXT PRIMARY KEY,
    mission_id           TEXT NOT NULL REFERENCES research_missions (id) ON DELETE CASCADE,
    claim                TEXT NOT NULL,
    priority             TEXT NOT NULL CHECK (priority IN ({_check(PILLAR_PRIORITIES)})),
    baseline             TEXT,
    expected_path        TEXT,
    confirm_threshold    TEXT,
    warning_threshold    TEXT,
    break_threshold      TEXT,
    threshold_provenance TEXT NOT NULL DEFAULT 'draft'
                         CHECK (threshold_provenance IN ({_check(PROVENANCE_LEVELS)})),
    latest_evidence_id   TEXT,
    signal               TEXT NOT NULL DEFAULT 'untested'
                         CHECK (signal IN ({_check(SIGNALS)})),
    evidence_quality     TEXT,
    provenance           TEXT NOT NULL DEFAULT 'draft'
                         CHECK (provenance IN ({_check(PROVENANCE_LEVELS)})),
    model_line           TEXT,
    implied_action       TEXT,
    next_proof_point     TEXT,
    next_proof_source    TEXT,
    owner                TEXT NOT NULL DEFAULT 'selene',
    status               TEXT NOT NULL DEFAULT 'untested'
                         CHECK (status IN ({_check(PILLAR_STATUSES)})),
    status_note          TEXT,
    superseded_by        TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
"""

_EVIDENCE_DDL = f"""
CREATE TABLE IF NOT EXISTS mission_evidence (
    seq                INTEGER PRIMARY KEY AUTOINCREMENT,
    id                 TEXT NOT NULL UNIQUE,
    mission_id         TEXT NOT NULL REFERENCES research_missions (id) ON DELETE CASCADE,
    pillar_id          TEXT NOT NULL REFERENCES mission_pillars (id) ON DELETE CASCADE,
    occurred_at        TEXT NOT NULL,
    source_type        TEXT NOT NULL,
    source_ref         TEXT,
    fact               TEXT NOT NULL,
    prior_expectation  TEXT,
    market_expectation TEXT,
    interpretation     TEXT NOT NULL,
    signal             TEXT NOT NULL CHECK (signal IN ({_check(SIGNALS)})),
    evidence_quality   TEXT,
    impact_json        TEXT NOT NULL DEFAULT '{{}}',
    follow_up          TEXT,
    owner              TEXT NOT NULL DEFAULT 'selene',
    created_at         TEXT NOT NULL
);
"""

_PILLARS_MISSION_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_mission_pillars_mission "
    "ON mission_pillars (mission_id, priority, status)"
)
_EVIDENCE_PILLAR_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_mission_evidence_pillar "
    "ON mission_evidence (pillar_id, occurred_at, seq)"
)
_EVIDENCE_MISSION_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_mission_evidence_mission "
    "ON mission_evidence (mission_id, occurred_at)"
)


async def _apply(conn: aiosqlite.Connection) -> None:
    await conn.execute(_PILLARS_DDL)
    await conn.execute(_EVIDENCE_DDL)
    await conn.execute(_PILLARS_MISSION_INDEX_DDL)
    await conn.execute(_EVIDENCE_PILLAR_INDEX_DDL)
    await conn.execute(_EVIDENCE_MISSION_INDEX_DDL)


migration = Migration(
    id="v019_mission_thesis",
    version=19,
    description=(
        "Add mission_pillars + mission_evidence tables "
        "(falsifiable pillars + append-only evidence ledger)"
    ),
    apply=_apply,
)

assert "reason_changed" in DRIFT_PATTERNS  # vocab import sanity
