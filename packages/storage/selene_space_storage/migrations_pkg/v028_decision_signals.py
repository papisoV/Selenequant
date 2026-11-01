# Absorbed from daily_stock_analysis (MIT License)
# Copyright (c) 2026 ZhuLinsen
# Source: https://github.com/ZhuLinsen/daily_stock_analysis
# Adapted for SeleneQuant (AGPL-3.0-only); upstream used two SQLAlchemy tables
# plus caller-side discipline. Rebuilt here on the Selene SQLite convention:
# ISO-8601 UTC text timestamps and CHECK constraints that make the
# "unable => no verdict" contract structural rather than advisory.
"""决策信号持久化 —— decision_signals + decision_signal_outcomes 两表(v028)。

吸收 daily_stock_analysis 的 DecisionSignal 资产(§📋8)。上游是两张 SQLAlchemy
表(``src/storage.py`` 的 ``DecisionSignalRecord`` / ``DecisionSignalOutcomeRecord``),
此处按 Selene 的 SQLite 惯例重建:ISO-8601 UTC 文本时间戳(字典序即时间序,
同 v016)、CHECK 约束落到 schema 而不是靠调用方自觉。

**为什么是两张新表而不是复用 research graph**:signal 是**先验承诺**(下注前写下
"我认为会怎样"),outcome 是**事后观测**(P5 已把这类观测写进 research_evidence
的 AFTER_THE_FACT 模式)。二者是两个不同物种:research_evidence 的幂等键是
``source_ref``(按窗口),而 signal outcome 的幂等键是
``(signal_id, horizon, engine_version)``(按信号×周期×评分版本)—— 后者要能在
评分逻辑升级后对同一 signal 重算出历史,这是 evidence 行无法表达的。故新表。

**幂等基板**:``UNIQUE (signal_id, horizon, engine_version)``。重跑命中 =
先查后插走干净 skip;唯一约束是并发下的最后一道保险丝(同 v027 的心智模型)。

**只加表与索引**,无破坏性 SQL;重跑靠 ``IF NOT EXISTS`` 与迁移 runner 的
``_migrations`` 台账双重幂等(v027 同)。
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration

# 八态 action(📋1 词汇层)。CHECK 落到 schema:非法动作进不了库,不必等
# 应用层校验漏网。
_ACTIONS = ("buy", "add", "hold", "reduce", "sell", "watch", "avoid", "alert")

# 信号状态。active 之外的终态各自有语义:expired(自然到期)/
# invalidated(被反向信号取代)/closed(人工了结)/archived(归档出统计)。
_SIGNAL_STATUSES = ("active", "expired", "invalidated", "closed", "archived")

# outcome 评估状态。completed = 有判定;unable = 数据不足,诚实拒答
# (绝不把"没等够"记成"判错")。二者互斥,没有第三态。
_EVAL_STATUSES = ("completed", "unable")

# 判定结果。neutral = 落在中性带内,既不算对也不算错。
_OUTCOMES = ("hit", "miss", "neutral")


def _sql_list(values: tuple[str, ...]) -> str:
    """Render a tuple of tokens as a quoted SQL IN-list: 'a', 'b', 'c'."""
    return ", ".join(f"'{value}'" for value in values)


_SIGNALS_DDL = f"""
CREATE TABLE IF NOT EXISTS decision_signals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code          TEXT NOT NULL,
    stock_name          TEXT,
    market              TEXT NOT NULL,
    source_type         TEXT NOT NULL,
    source_agent        TEXT,
    source_report_id    TEXT,
    trace_id            TEXT,
    decision_profile    TEXT,
    market_phase        TEXT,
    trigger_source      TEXT NOT NULL,
    action              TEXT NOT NULL CHECK (action IN ({_sql_list(_ACTIONS)})),
    action_label        TEXT,
    confidence          REAL,
    score               INTEGER,
    horizon             TEXT,
    entry_low           REAL,
    entry_high          REAL,
    stop_loss           REAL,
    target_price        REAL,
    invalidation        TEXT,
    watch_conditions    TEXT,
    reason              TEXT,
    risk_summary        TEXT,
    catalyst_summary    TEXT,
    evidence_json       TEXT,
    data_quality_summary_json TEXT,
    plan_quality        TEXT NOT NULL DEFAULT 'unknown',
    status              TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ({_sql_list(_SIGNAL_STATUSES)})),
    expires_at          TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    metadata_json       TEXT
);
"""

# outcome 维度列(action/market/...)是**写时快照**:signal 的字段后来可能被改,
# 但"当时按什么口径评的"必须留痕,否则历史命中率统计会随 signal 漂移。
_OUTCOMES_DDL = f"""
CREATE TABLE IF NOT EXISTS decision_signal_outcomes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id           INTEGER NOT NULL REFERENCES decision_signals (id),
    horizon             TEXT NOT NULL,
    engine_version      TEXT NOT NULL,
    eval_status         TEXT NOT NULL DEFAULT 'unable'
                        CHECK (eval_status IN ({_sql_list(_EVAL_STATUSES)})),
    outcome             TEXT CHECK (outcome IS NULL OR outcome IN ({_sql_list(_OUTCOMES)})),
    direction_expected  TEXT,
    direction_correct   INTEGER,
    unable_reason       TEXT,
    anchor_date         TEXT,
    eval_window_days    INTEGER,
    start_price         REAL,
    end_close           REAL,
    max_high            REAL,
    min_low             REAL,
    stock_return_pct    REAL,
    action              TEXT,
    market              TEXT,
    market_phase        TEXT,
    source_type         TEXT,
    source_agent        TEXT,
    plan_quality        TEXT,
    data_quality_level  TEXT,
    holding_state       TEXT NOT NULL DEFAULT 'unknown',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE (signal_id, horizon, engine_version),
    -- 诚实拒答的**结构**保证:没判(unable)就不能有判定值,判了(completed)
    -- 就必须有判定值。没有这条,一次手滑的 INSERT 就能把"数据没到齐"写成
    -- "判错了",而那是整个吸收批次最不能出的错。
    CHECK (
        (eval_status = 'unable' AND outcome IS NULL)
        OR (eval_status = 'completed' AND outcome IS NOT NULL)
    )
);
"""

_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_decision_signals_stock_status_time "
    "ON decision_signals (stock_code, status, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_decision_signals_market_status_time "
    "ON decision_signals (market, status, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_decision_signals_action_horizon "
    "ON decision_signals (action, horizon, status)",
    # 候选扫描:按 status 找待评信号,created_at 定序(先老后新)。
    "CREATE INDEX IF NOT EXISTS idx_decision_signals_status_created "
    "ON decision_signals (status, created_at)",
    # 统计主查询:按 engine_version × action × horizon 聚合命中率。
    "CREATE INDEX IF NOT EXISTS idx_decision_signal_outcome_stats_action "
    "ON decision_signal_outcomes (engine_version, action, horizon)",
    "CREATE INDEX IF NOT EXISTS idx_decision_signal_outcome_stats_market "
    "ON decision_signal_outcomes (engine_version, market, horizon)",
    # 单 signal 的全部 horizon。
    "CREATE INDEX IF NOT EXISTS idx_decision_signal_outcome_signal "
    "ON decision_signal_outcomes (signal_id)",
    # 可重试 unable 的翻扫(数据到齐后重评)。
    "CREATE INDEX IF NOT EXISTS idx_decision_signal_outcome_retry "
    "ON decision_signal_outcomes (eval_status, unable_reason, updated_at)",
)


async def _apply(conn: aiosqlite.Connection) -> None:
    await conn.execute(_SIGNALS_DDL)
    await conn.execute(_OUTCOMES_DDL)
    for index in _INDEX_DDL:
        await conn.execute(index)


migration = Migration(
    id="v028_decision_signals",
    version=28,
    description=(
        "Add decision_signals + decision_signal_outcomes tables (persisted "
        "decision signals and their forward outcomes; unique key "
        "(signal_id, horizon, engine_version)). Tables and indexes only."
    ),
    apply=_apply,
)
