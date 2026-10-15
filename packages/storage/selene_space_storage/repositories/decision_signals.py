# Absorbed from daily_stock_analysis (MIT License)
# Copyright (c) 2026 ZhuLinsen
# Source: https://github.com/ZhuLinsen/daily_stock_analysis
# Adapted for SeleneQuant (AGPL-3.0-only); upstream identity lookup was an
# ORM session query — rebound here to an explicit anchor + 7-dimension
# ``IS ?`` probe over plain SQLite, and the read/write split re-expressed as
# a thin facade with no ORM objects crossing the boundary.
"""决策信号仓储 —— ``decision_signals`` + ``decision_signal_outcomes`` 的瘦门面(v028)。

两部分职责分明:

- **signal 表**:一条"当时说过什么"的持久记录。它**没有**数据库唯一键 ——
  DSA 的幂等是"按 (source_report_id 或 trace_id) + 七维查存",形见
  ``decision_signal_repo.py:347`` ``_find_existing_in_session``。因为同一条报告
  可以对同一只票给出**同向但不同 horizon** 的建议,唯一键会误杀合法行。故
  :meth:`create_if_absent` 走 check-then-insert(单用户本地库,无并发窗口)。
- **outcome 表**:一条"后来判得对不对"的评估。它有真唯一键
  ``(signal_id, horizon, engine_version)`` —— 同一 signal 的同一 horizon 在同一
  引擎版本下只能有一个判定,重评是 UPDATE 不是再插一行,否则命中率会重复计数。

``created_at`` 在 upsert 时保持首插值(快照列语义,同 ``strategy_attributions``):
"这条评估第一次什么时候写的"不该被重评悄悄改掉;重评只推进 ``updated_at``。

**未吸收的行为(诚实登记)**:DSA 的 ``create_if_absent`` 还有两条支路 ——
(a) ``expired → active`` 的复活刷新(``_should_refresh_existing``),
(b) 缺 horizon 时的宽松补维(``_find_relaxed_existing_in_session``)。
两者都依赖 DSA 的到期清扫(``expire_due_signals``)与报告耦合语义,Selene 无对等物,
**不吸收**。故此处的 ``create_if_absent`` 只覆盖"插入"与"重放"两种结局。

连接源:构造接 :class:`~selene_space_storage.sqlite.AsyncSQLiteManager`
(``get_db_manager()``),FK 在 manager 连接上真实强制;禁止用裸连接写本表
(与 ``strategy_attributions`` 同纪律)。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

# 统计默认口径:archived 是"收进柜子不再计分",不是"判错",故默认排除
# (DSA ``DEFAULT_STATS_STATUSES``,decision_signal_service.py:48)。
DEFAULT_STATS_STATUSES: tuple[str, ...] = ("active", "expired", "invalidated", "closed")

_SIGNAL_COLUMNS = (
    "stock_code",
    "stock_name",
    "market",
    "source_type",
    "source_agent",
    "source_report_id",
    "trace_id",
    "decision_profile",
    "market_phase",
    "trigger_source",
    "action",
    "action_label",
    "confidence",
    "score",
    "horizon",
    "entry_low",
    "entry_high",
    "stop_loss",
    "target_price",
    "invalidation",
    "watch_conditions",
    "reason",
    "risk_summary",
    "catalyst_summary",
    "evidence_json",
    "data_quality_summary_json",
    "plan_quality",
    "expires_at",
    "metadata_json",
)

# 查存维度(DSA 的同名八条件去掉锚点本身):同报告同票的**同档**建议不重复落库;
# horizon 在内,故同报告同票的不同 horizon 是合法的两条。
#
# ``source_agent`` 也在内(per-skill 权重的前提,📋3):同一条报告里两个不同的
# Skill(= source_agent)可以对同一只票给出**字面相同**的 action/horizon 建议,
# 但它们是两个来源的判断,各自的命中率要分开累计。若这一维缺席,后写的会把先写的
# 收敛成同一条,另一个 Skill 的观点连同它的 outcome 一起消失 —— per-skill 统计
# 从此对不上账。
_IDENTITY_DIMENSIONS = (
    "source_type",
    "source_agent",
    "stock_code",
    "market",
    "decision_profile",
    "action",
    "horizon",
    "market_phase",
)

_TEXT_JSON_COLUMNS = ("metadata_json", "evidence_json", "data_quality_summary_json")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class DecisionSignalRepository:
    """Facade over ``decision_signals`` + ``decision_signal_outcomes``."""

    def __init__(self, db: Any) -> None:
        self._db = db

    # -- signals ----------------------------------------------------------------

    async def create_if_absent(self, signal: Mapping[str, Any]) -> tuple[int, bool]:
        """插入一条 signal;若同 identity 已存在则返回既有行。

        返回 ``(signal_id, created)``。``created=False`` 表示命中查存,零新行
        —— 调用方据此区分"新信号"与"重放",但**不**据此报错(重放是正常路径)。

        锚点缺失(既无 ``source_report_id`` 又无 ``trace_id``)时**不做查存**:
        DSA 同此(``_find_existing_in_session`` 在该分支直接 return None)——
        手工信号没有可比对的锚,每次都是新行。
        """
        existing = await self._find_by_identity(signal)
        if existing is not None:
            return int(existing["id"]), False

        now = _now()
        values: dict[str, Any] = {}
        for column in _SIGNAL_COLUMNS:
            value = signal.get(column)
            if column in _TEXT_JSON_COLUMNS:
                value = _as_json(value)
            values[column] = value
        # 调用方给了 created_at 就用它(回填历史信号、以及锚点日全靠它);
        # 没给才是"现在"。
        values["created_at"] = signal.get("created_at") or now
        values["updated_at"] = now

        columns = ", ".join(values)
        placeholders = ", ".join(f":{key}" for key in values)
        rows = await self._db.execute(
            f"INSERT INTO decision_signals ({columns}) VALUES ({placeholders}) "
            "RETURNING id",
            values,
        )
        return int(rows[0]["id"]), True

    async def _find_by_identity(self, signal: Mapping[str, Any]) -> dict[str, Any] | None:
        anchor_column, anchor_value = _identity_anchor(signal)
        if anchor_column is None:
            return None
        # SQLite 的 IS 是 NULL 安全的等值(与 = 在两边皆非 NULL 时同义),
        # 故可对可空维度(decision_profile / action / horizon / market_phase)
        # 统一用它,不必为 IS NULL 单独分支。
        clauses = [f"{anchor_column} IS ?"]
        params: list[Any] = [anchor_value]
        for dimension in _IDENTITY_DIMENSIONS:
            clauses.append(f"{dimension} IS ?")
            params.append(signal.get(dimension))
        rows = await self._db.execute(
            "SELECT * FROM decision_signals WHERE "
            + " AND ".join(clauses)
            + " ORDER BY id ASC LIMIT 1",
            tuple(params),
        )
        return dict(rows[0]) if rows else None

    async def get(self, signal_id: int) -> dict[str, Any] | None:
        rows = await self._db.execute(
            "SELECT * FROM decision_signals WHERE id = ?", (int(signal_id),)
        )
        return dict(rows[0]) if rows else None

    async def list_signals(
        self,
        *,
        stock_code: str | None = None,
        market: str | None = None,
        action: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for key, value in (
            ("stock_code", stock_code),
            ("market", market),
            ("action", action),
            ("status", status),
        ):
            if value is not None:
                clauses.append(f"{key} = ?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, int(limit)))
        rows = await self._db.execute(
            f"SELECT * FROM decision_signals {where} "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            tuple(params),
        )
        return [dict(r) for r in rows]

    async def list_candidate_signals(
        self, *, statuses: Sequence[str], limit: int
    ) -> list[dict[str, Any]]:
        """按 status 取一页候选(新→旧,与 DSA ``list_candidate_signals`` 同序)。

        返回的是**候选原料**,不是"确定要评的" —— 某个 horizon 是否已判、
        该不该重评,由服务层的 actionability 判定(同 DSA:见
        ``outcome_service.py`` ``_candidate_actionability``)。把这一步留在
        服务层,是为了让 `signal.horizon` 的语义(有 horizon 只评那一档,
        无 horizon 评全部四档)能就近表达。
        """
        if not statuses:
            return []
        status_list = ", ".join(f"'{s}'" for s in statuses)
        rows = await self._db.execute(
            f"SELECT * FROM decision_signals WHERE status IN ({status_list}) "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (max(1, int(limit)),),
        )
        return [dict(r) for r in rows]

    async def set_status(self, signal_id: int, status: str) -> bool:
        """推进信号生命周期(expired / invalidated / closed / archived)。

        返回是否命中一行 —— False 让调用方报 404,而不是静默接受一个不存在的 id。
        ``AsyncSQLiteManager.execute`` 对无描述符的语句返回空行集,拿不到 rowcount,
        故走连接作用域直接读游标(sqlite.py:91)。
        """
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "UPDATE decision_signals SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), int(signal_id)),
            )
            changed = cursor.rowcount
            await cursor.close()
        return bool(changed)

    # -- outcomes ---------------------------------------------------------------

    async def upsert_outcome(self, signal_id: int, outcome: Mapping[str, Any]) -> None:
        """写一条判定;同键重评覆盖内容列,``created_at`` 保持首插值。

        ``outcome`` 需带 ``horizon`` / ``engine_version`` / ``eval_status``。
        写哪些列就覆盖哪些列 —— 调用方若要清掉旧值(如从 unable 转 completed 时
        清 ``unable_reason``),必须显式传该列为 None。这与 DSA 一致:其
        ``_evaluate_signal_horizon`` 恒返回完整字段集(``unable_reason`` 失败为
        None),故重评天然清旧值。
        """
        now = _now()
        payload = dict(outcome)
        payload["signal_id"] = int(signal_id)
        payload.setdefault("holding_state", "unknown")
        payload["created_at"] = now
        payload["updated_at"] = now

        columns = ", ".join(payload)
        placeholders = ", ".join(f":{key}" for key in payload)
        updates = ", ".join(
            f"{key} = excluded.{key}"
            for key in payload
            if key not in {"signal_id", "horizon", "engine_version", "created_at"}
        )
        await self._db.execute(
            f"INSERT INTO decision_signal_outcomes ({columns}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT (signal_id, horizon, engine_version) "
            f"DO UPDATE SET {updates}",
            payload,
        )

    async def get_outcome(
        self, signal_id: int, horizon: str, engine_version: str
    ) -> dict[str, Any] | None:
        rows = await self._db.execute(
            "SELECT * FROM decision_signal_outcomes "
            "WHERE signal_id = ? AND horizon = ? AND engine_version = ?",
            (int(signal_id), horizon, engine_version),
        )
        return dict(rows[0]) if rows else None

    async def list_outcomes(self, signal_id: int) -> list[dict[str, Any]]:
        rows = await self._db.execute(
            "SELECT * FROM decision_signal_outcomes WHERE signal_id = ? "
            "ORDER BY horizon ASC",
            (int(signal_id),),
        )
        return [dict(r) for r in rows]

    async def list_outcomes_for_signals(
        self, signal_ids: Sequence[int], engine_version: str
    ) -> list[dict[str, Any]]:
        if not signal_ids:
            return []
        id_list = ", ".join(str(int(i)) for i in signal_ids)
        rows = await self._db.execute(
            f"SELECT * FROM decision_signal_outcomes "
            f"WHERE signal_id IN ({id_list}) AND engine_version = ?",
            (engine_version,),
        )
        return [dict(r) for r in rows]

    async def count_outcomes(self, signal_id: int) -> int:
        rows = await self._db.execute(
            "SELECT COUNT(*) AS n FROM decision_signal_outcomes WHERE signal_id = ?",
            (int(signal_id),),
        )
        return int(rows[0]["n"]) if rows else 0

    # -- stats ------------------------------------------------------------------

    async def stats_rows(
        self,
        *,
        engine_version: str,
        statuses: Sequence[str] = DEFAULT_STATS_STATUSES,
    ) -> list[dict[str, Any]]:
        """命中率聚合的原料行。

        只取 ``eval_status='completed'`` —— unable 是"没判",进分母就是把
        "没等够"算成"判了但没中"。status 过滤走 signal 表(archived 默认出局)。
        """
        if not statuses:
            return []
        status_list = ", ".join(f"'{s}'" for s in statuses)
        rows = await self._db.execute(
            f"""
            SELECT o.outcome, o.direction_correct, o.action, o.market,
                   o.market_phase, o.horizon, o.plan_quality
            FROM decision_signal_outcomes o
            JOIN decision_signals s ON s.id = o.signal_id
            WHERE o.engine_version = ?
              AND o.eval_status = 'completed'
              AND s.status IN ({status_list})
            """,
            (engine_version,),
        )
        return [dict(r) for r in rows]

    async def skill_weight_rows(
        self,
        *,
        engine_version: str,
        skill_ids: Sequence[str] | None = None,
        horizons: Sequence[str] | None = None,
        statuses: Sequence[str] = DEFAULT_STATS_STATUSES,
    ) -> list[dict[str, Any]]:
        """按 ``(source_agent, horizon)`` 聚合 outcome,喂 per-skill 权重(📋3)。

        ``source_agent`` 就是 Skill 身份 —— B2 已把它作为 **写时快照**存进
        outcome 表,故 per-skill 统计无需新表、无需 migration,聚合既有行即可。

        每行返回**原始计数**,不判定"样本够不够":
        ``sample_sufficient`` 的阈值常量住在 backtest-engine,而 ``storage``
        **不依赖** backtest-engine(三层切分铁律)。够不够由装配层判 —— 正是
        这里只出原料、不下结论的原因。

        计数口径(与 B2 ``_aggregate`` 同):

        - ``evaluated`` = hit + miss —— **方向性判定**,权重公式的分母。
        - ``observational`` = ``neutral``(已判但落在中性带)—— 既不算对也不算错,
          与 DSA 的 observational 同席:它证明"评估跑通了",故冲淡而非抬高
          ``unable_rate``。
        - ``unable`` = ``eval_status='unable'`` —— 诚实拒答,单列不混入分母。

        **只按 source_agent 分组**:没写 source_agent 的 outcome(``NULL`` 或空串)
        不参与 —— 一个没有来源的判定无法归因给任何 Skill,把它摊给全体等于
        凭空发明一个来源。同理,显式传 ``skill_ids`` 时只返回其中有的。
        """
        if not statuses:
            return []
        status_list = ", ".join(f"'{s}'" for s in statuses)
        clauses = [
            "o.engine_version = ?",
            f"s.status IN ({status_list})",
            "o.source_agent IS NOT NULL",
            "o.source_agent != ''",
        ]
        params: list[Any] = [engine_version]
        if skill_ids:
            placeholders = ", ".join("?" for _ in skill_ids)
            clauses.append(f"o.source_agent IN ({placeholders})")
            params.extend(str(s) for s in skill_ids)
        if horizons:
            placeholders = ", ".join("?" for _ in horizons)
            clauses.append(f"o.horizon IN ({placeholders})")
            params.extend(str(h) for h in horizons)

        rows = await self._db.execute(
            f"""
            SELECT o.source_agent AS skill_id,
                   o.horizon AS horizon,
                   o.engine_version AS engine_version,
                   COUNT(*) AS total,
                   SUM(CASE WHEN o.eval_status = 'completed'
                             AND o.outcome IN ('hit', 'miss')
                            THEN 1 ELSE 0 END) AS evaluated,
                   SUM(CASE WHEN o.outcome = 'hit' THEN 1 ELSE 0 END) AS hit,
                   SUM(CASE WHEN o.outcome = 'miss' THEN 1 ELSE 0 END) AS miss,
                   SUM(CASE WHEN o.outcome = 'neutral' THEN 1 ELSE 0 END)
                       AS observational,
                   SUM(CASE WHEN o.eval_status = 'unable' THEN 1 ELSE 0 END)
                       AS unable
            FROM decision_signal_outcomes o
            JOIN decision_signals s ON s.id = o.signal_id
            WHERE {" AND ".join(clauses)}
            GROUP BY o.source_agent, o.horizon
            ORDER BY o.source_agent ASC, o.horizon ASC
            """,
            tuple(params),
        )
        return [dict(r) for r in rows]


def _identity_anchor(signal: Mapping[str, Any]) -> tuple[str | None, Any]:
    """Which column anchors identity, and its value. None column = no dedup.

    DSA prefers ``source_report_id``; ``trace_id`` is the fallback anchor for
    signals that predate a report row. Neither present → the signal is
    unanchored and every write is a new row.
    """
    report_id = signal.get("source_report_id")
    if report_id is not None:
        return "source_report_id", report_id
    trace_id = signal.get("trace_id")
    if trace_id:
        return "trace_id", trace_id
    return None, None


def _as_json(value: Any) -> Any:
    """JSON columns take text; a non-str, non-None value is dumped once here."""
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
