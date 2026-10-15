# Absorbed from daily_stock_analysis (MIT License)
# Copyright (c) 2026 ZhuLinsen
# Source: https://github.com/ZhuLinsen/daily_stock_analysis
# Adapted for SeleneQuant (AGPL-3.0-only); upstream ran persistence, forward
# scoring and stats inside one ~4000-line service. Selene splits them across
# the dependency graph (pure scorer -> storage facade -> this orchestrator),
# because storage and backtest-engine cannot see each other.
"""决策信号 outcome 编排 —— 扫候选 → 取前向 bar → 分类 → 落账(v028/📋8)。

职责:把**持久化的决策信号**(``decision_signals``)与**它后来兑现了没有**
(``decision_signal_outcomes``)接起来。判定本身是纯函数
(:mod:`selene_space_backtest.signal_outcome`);本模块只管取数、调函数、写库。

为什么住在这里(而不是 storage 或 backtest-engine):
- ``storage`` **不依赖 backtest-engine** —— 故纯函数与写库不能同居一包。
- ``backtest-engine`` **不依赖 storage** —— 故它拿不到 signal 行。
- 本模块同时 import 两者,只有 local-api 的装配层(与既有
  ``forward_validation_recorder.py`` 同层、同理由)能同时看见它们。

**诚实拒答(本模块最硬的契约)**:数据不足 / 无锚价 / 非方向 action →
``eval_status='unable'`` + ``unable_reason``,**绝不**写 ``miss``。把"没等够"
记成"判错"会污染命中率,是本批吸收最不能出的错。v028 的 CHECK 在库层再兜一层
(``unable`` ⇔ ``outcome IS NULL``)。

与 P5(:mod:`forward_validation_recorder`)的分工:P5 评的是 v009 分析记录、
落 research graph(evidence/episode/edge);本模块评的是决策信号、落
``decision_signal_outcomes`` 关系表。两条链**互不写入**对方的数据。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from selene_space_backtest.signal_outcome import (
    DEFAULT_NEUTRAL_BAND_PCT,
    EVAL_COMPLETED,
    RETRYABLE_UNABLE_REASONS,
    SIGNAL_OUTCOME_ENGINE_VERSION,
    SIGNAL_OUTCOME_HORIZONS,
    direction_for_action,
    evaluate_signal,
)
from selene_space_backtest.skill_weight import (
    MAX_SKILL_WEIGHT_FACTOR,
    MIN_SKILL_OUTCOME_SAMPLE_SIZE,
    MIN_SKILL_WEIGHT_FACTOR,
    NEUTRAL_SKILL_WEIGHT_FACTOR,
    compute_skill_weight_factors,
)
from selene_space_storage.repositories.decision_signals import (
    DEFAULT_STATS_STATUSES,
    DecisionSignalRepository,
)

logger = logging.getLogger(__name__)

# 单轮最多处理多少条(防首轮洪水)。与 P5 同思路:cap 是粗闸,幂等是兜底。
DEFAULT_MAX_PER_RUN = 50

# 可评的 holding_state;不在其中的一律记 unknown(同 DSA HOLDING_STATES)。
_HOLDING_STATES = frozenset({"holding", "empty", "unknown"})


class DecisionSignalOutcomeService:
    """扫待评决策信号 → 逐 horizon 判定 → 落 ``decision_signal_outcomes``。

    ``db`` 是共享的 :class:`~selene_space_storage.sqlite.AsyncSQLiteManager`;
    ``fetch_daily`` 是 ``symbol → bars`` 的同步取数器(与 P5 同签名,调用方以
    ``asyncio.to_thread`` 包裹 —— 取数是阻塞 IO)。``fetch_daily`` 返回的 bar
    是升序 dict,至少含 ``date`` / ``close``(``high`` / ``low`` 可选)。
    """

    def __init__(
        self,
        db: Any,
        *,
        fetch_daily: Callable[[str], Sequence[Mapping[str, Any]]],
        neutral_band_pct: float = DEFAULT_NEUTRAL_BAND_PCT,
        max_per_run: int = DEFAULT_MAX_PER_RUN,
        maturity_days: int = 0,
    ) -> None:
        self._db = db
        self._fetch_daily = fetch_daily
        self._neutral_band_pct = float(neutral_band_pct)
        self._max_per_run = int(max_per_run)
        # 锚点距今至少多少"自然日"才值得去取 bar(粗筛,不做日历运算 —— 是否
        # 真的评得动一律由实测 bar 时间戳判定,同 P5 议题6)。0 = 不过滤。
        self._maturity_days = int(maturity_days)
        self._repo = DecisionSignalRepository(db)

    async def run(self, *, statuses: Sequence[str] | None = None) -> dict[str, int]:
        """一轮评估。返回 counters。"""
        counters = {
            "signals_scanned": 0,
            "completed": 0,
            "unable": 0,
            "retried": 0,
            "skipped_not_mature": 0,
            "skipped_no_data": 0,
            "error_skipped": 0,
        }
        rows = await self._repo.list_candidate_signals(
            statuses=tuple(statuses or DEFAULT_STATS_STATUSES),
            limit=self._max_per_run,
        )
        for signal in rows:
            counters["signals_scanned"] += 1
            try:
                await self._process_signal(signal, counters)
            except Exception:  # noqa: BLE001 — 一条坏 signal 不得拖垮整轮
                counters["error_skipped"] += 1
                logger.warning(
                    "decision signal outcome: signal failed id=%s",
                    signal.get("id"),
                    exc_info=True,
                )
        return counters

    # -- 单条 signal 的全部 horizon -----------------------------------------

    async def _process_signal(
        self, signal: Mapping[str, Any], counters: dict[str, int]
    ) -> None:
        signal_id = int(signal["id"])
        horizons = _horizons_for_signal(signal)
        if not horizons:
            return

        existing = {
            row["horizon"]: row
            for row in await self._repo.list_outcomes_for_signals(
                [signal_id], SIGNAL_OUTCOME_ENGINE_VERSION
            )
        }

        for horizon in horizons:
            current = existing.get(horizon)
            if current is not None and not _needs_recompute(current):
                continue
            if current is not None:
                counters["retried"] += 1
            await self._evaluate_one(signal, horizon, counters)

    async def _evaluate_one(
        self, signal: Mapping[str, Any], horizon: str, counters: dict[str, int]
    ) -> None:
        # 非方向 action(watch / alert)先于一切取数判掉:DSA 同此
        # (``_evaluate_signal_horizon`` 第一件事就是查方向)。这类信号无论
        # 行情如何都不可判,去取行情纯属浪费,且会让"watch 不该读价"的断言失败。
        if direction_for_action(signal.get("action")) is None:
            await self._write_unable(
                signal, horizon, "non_directional_action", counters=counters
            )
            return

        anchor_date = _anchor_date(signal)
        if anchor_date is None:
            await self._write_unable(
                signal, horizon, "missing_anchor_date", counters=counters
            )
            return
        if self._maturity_days and not _is_mature(anchor_date, self._maturity_days):
            counters["skipped_not_mature"] += 1
            return

        symbol = str(signal.get("stock_code") or "")
        if not symbol:
            counters["skipped_no_data"] += 1
            return

        try:
            rows = await asyncio.to_thread(self._fetch_daily, symbol)
        except Exception:  # noqa: BLE001 — 取数失败 = 本轮不判,不写任何东西
            counters["skipped_no_data"] += 1
            return

        bars = _bars_ascending(rows)
        anchor_iso = anchor_date.isoformat()
        start_price = _close_on_or_before(bars, anchor_iso)
        forward = _bars_after(bars, anchor_iso)

        outcome = _classify(
            action=signal.get("action"),
            horizon=horizon,
            start_price=start_price,
            forward_bars=forward,
            neutral_band_pct=self._neutral_band_pct,
        )
        fields = _snapshot_fields(signal, horizon)
        fields.update(outcome)
        fields["anchor_date"] = anchor_iso
        await self._repo.upsert_outcome(int(signal["id"]), fields)
        if outcome["eval_status"] == EVAL_COMPLETED:
            counters["completed"] += 1
        else:
            counters["unable"] += 1

    async def _write_unable(
        self,
        signal: Mapping[str, Any],
        horizon: str,
        reason: str,
        *,
        counters: dict[str, int],
    ) -> None:
        fields = _snapshot_fields(signal, horizon)
        fields.update({"eval_status": "unable", "unable_reason": reason, "outcome": None})
        await self._repo.upsert_outcome(int(signal["id"]), fields)
        counters["unable"] += 1

    # -- 统计 -----------------------------------------------------------------

    async def stats(self, *, engine_version: str = SIGNAL_OUTCOME_ENGINE_VERSION) -> dict[str, Any]:
        """全库命中率聚合(口径同 DSA ``_aggregate``)。"""
        rows = await self._repo.stats_rows(engine_version=engine_version)
        return _aggregate(rows)

    async def skill_weights(
        self,
        *,
        skill_ids: Sequence[str] | None = None,
        horizons: Sequence[str] | None = None,
        engine_version: str = SIGNAL_OUTCOME_ENGINE_VERSION,
    ) -> dict[str, Any]:
        """每个 Skill(``source_agent``)的有界表现因子(📋3)。

        三层在此汇合:``storage`` 出**原始计数**(不知阈值),``backtest-engine``
        的纯函数出**公式**(不知数据库),本装配层把两者接起来 —— 与 B2 同一条
        接缝,同一条理由(storage 与 backtest-engine 互不可见,只有本层同时看得见)。

        ``sample_sufficient`` 在**这里**判定并显式回传给纯函数:阈值常量
        (:data:`MIN_SKILL_OUTCOME_SAMPLE_SIZE`)住在 backtest-engine,storage
        不该知道它。故 ``skill_weight_rows`` 只出计数,本层补上"够不够"的结论。

        返回形状对**每个 Skill** 都给一条记录(含 counts + factor),而不仅是
        被请求的那些 —— 未出场的 Skill 不发明,出场的即使没到样本量也如实列出
        (带 ``sample_sufficient=False`` 与中性因子),让调用方看得见"为什么它还是 1.0"。
        """
        rows = await self._repo.skill_weight_rows(
            engine_version=engine_version,
            skill_ids=skill_ids,
            horizons=horizons,
        )
        buckets: list[dict[str, Any]] = []
        for row in rows:
            evaluated = int(row.get("evaluated") or 0)
            buckets.append(
                {
                    "skill_id": row.get("skill_id"),
                    "horizon": row.get("horizon"),
                    "engine_version": row.get("engine_version"),
                    "total": int(row.get("total") or 0),
                    "evaluated": evaluated,
                    "hit": int(row.get("hit") or 0),
                    "miss": int(row.get("miss") or 0),
                    "observational": int(row.get("observational") or 0),
                    "unable": int(row.get("unable") or 0),
                    "sample_sufficient": evaluated >= MIN_SKILL_OUTCOME_SAMPLE_SIZE,
                }
            )

        requested = _requested_skill_ids(skill_ids, buckets)
        factors = compute_skill_weight_factors(
            skill_ids=requested,
            buckets=buckets,
            engine_version=engine_version,
        )
        by_skill: dict[str, dict[str, Any]] = {
            skill_id: {
                "skill_id": skill_id,
                "factor": factors.get(skill_id, NEUTRAL_SKILL_WEIGHT_FACTOR),
                "buckets": [],
            }
            for skill_id in requested
        }
        for bucket in buckets:
            bucket_skill = str(bucket.get("skill_id") or "")
            entry = by_skill.get(bucket_skill)
            if entry is not None:
                entry["buckets"].append(bucket)

        return {
            "engine_version": engine_version,
            "minimum_evaluated_sample_size": MIN_SKILL_OUTCOME_SAMPLE_SIZE,
            "max_weight_factor": MAX_SKILL_WEIGHT_FACTOR,
            "min_weight_factor": MIN_SKILL_WEIGHT_FACTOR,
            "skills": [by_skill[skill_id] for skill_id in requested],
        }


# -- 纯函数(可离线测) ---------------------------------------------------------


def _horizons_for_signal(signal: Mapping[str, Any]) -> list[str]:
    """该 signal 要评哪些 horizon(同 DSA ``_horizons_for_signal``)。

    显式给了 horizon 就只评那一档;没给就评全部四档。
    """
    horizon = str(signal.get("horizon") or "").strip().lower()
    if horizon:
        return [horizon] if horizon in SIGNAL_OUTCOME_HORIZONS else []
    return list(SIGNAL_OUTCOME_HORIZONS)


def _requested_skill_ids(
    skill_ids: Sequence[str] | None,
    buckets: Sequence[Mapping[str, Any]],
) -> list[str]:
    """哪些 Skill 该出现在权重结果里:显式请求的 ∪ 聚合中实际出现的。

    调用方点名了哪些就至少有哪些(哪怕该 Skill 还没样本 —— 它应得到一个
    中性因子而非"查无此人");没点名时以库里有的为准。并集而非交集:点名了一
    个库中没有的 Skill,如实回一条中性因子记录,比 404 更有用(前端要画一行)。
    """
    ordered: list[str] = []
    for value in skill_ids or ():
        skill_id = str(value or "").strip()
        if skill_id and skill_id not in ordered:
            ordered.append(skill_id)
    for bucket in buckets:
        skill_id = str(bucket.get("skill_id") or "").strip()
        if skill_id and skill_id not in ordered:
            ordered.append(skill_id)
    return ordered


def _needs_recompute(outcome: Mapping[str, Any]) -> bool:
    """既有 outcome 该不该重评(同 DSA ``_should_recompute_outcome``)。

    只有"unable 且原因是数据类"才重评 —— 数据到齐后结论会变;终态 unable
    (如 non_directional_action)重试一万次也是同一结论。
    """
    return (
        outcome.get("eval_status") == "unable"
        and outcome.get("unable_reason") in RETRYABLE_UNABLE_REASONS
    )


def _anchor_date(signal: Mapping[str, Any]) -> date | None:
    """锚点日:优先 metadata 里的 session_date,回退 created_at(同 DSA)。"""
    metadata = _json_loads(signal.get("metadata_json"))
    if isinstance(metadata, Mapping):
        summary = metadata.get("market_phase_summary")
        if isinstance(summary, Mapping):
            parsed = _parse_date(summary.get("session_date"))
            if parsed is not None:
                return parsed
    return _parse_date(signal.get("created_at"))


def _snapshot_fields(signal: Mapping[str, Any], horizon: str) -> dict[str, Any]:
    """写时快照维度:signal 的字段后来可能被改,但"当时按什么口径评的"必须留痕。"""
    return {
        "horizon": horizon,
        "engine_version": SIGNAL_OUTCOME_ENGINE_VERSION,
        "action": signal.get("action"),
        "market": signal.get("market"),
        "market_phase": signal.get("market_phase"),
        "source_type": signal.get("source_type"),
        "source_agent": signal.get("source_agent"),
        "plan_quality": signal.get("plan_quality"),
        "data_quality_level": _data_quality_level(signal),
        "holding_state": _holding_state(signal),
    }


def _holding_state(signal: Mapping[str, Any]) -> str:
    metadata = _json_loads(signal.get("metadata_json"))
    if isinstance(metadata, Mapping):
        value = str(metadata.get("holding_state") or "").strip().lower()
        if value in _HOLDING_STATES:
            return value
    return "unknown"


def _data_quality_level(signal: Mapping[str, Any]) -> str:
    """显式数据质量档;**绝不**从"字段齐不齐"反推质量(同 DSA 明示)。"""
    raw = signal.get("data_quality_summary_json")
    level: Any = None
    if raw and str(raw).strip():
        parsed = _json_loads(raw)
        if isinstance(parsed, Mapping):
            level = parsed.get("level") or parsed.get("quality_level")
            if level in (None, ""):
                nested = parsed.get("data_quality")
                if isinstance(nested, Mapping):
                    level = nested.get("level")
        elif isinstance(parsed, str):
            level = parsed
    if level in (None, ""):
        metadata = _json_loads(signal.get("metadata_json"))
        if isinstance(metadata, Mapping):
            level = metadata.get("data_quality_level")
    text = str(level or "").strip().lower()
    return text[:24] or "unknown"


def _classify(
    *,
    action: Any,
    horizon: str,
    start_price: float | None,
    forward_bars: Sequence[Mapping[str, Any]],
    neutral_band_pct: float,
) -> dict[str, Any]:
    """调纯函数分类,并补齐库表要求的字段(unable ⇒ outcome=None)。"""
    result = evaluate_signal(
        action=action,
        horizon=horizon,
        start_price=start_price,
        forward_bars=forward_bars,
        neutral_band_pct=neutral_band_pct,
    )
    outcome: dict[str, Any] = {
        "eval_status": result.get("eval_status"),
        "outcome": result.get("outcome"),
        "direction_expected": result.get("direction_expected"),
        "direction_correct": _as_bool_int(result.get("direction_correct")),
        "unable_reason": result.get("unable_reason"),
        "eval_window_days": result.get("eval_window_days"),
        "start_price": result.get("start_price"),
        "end_close": result.get("end_close"),
        "max_high": result.get("max_high"),
        "min_low": result.get("min_low"),
        "stock_return_pct": result.get("stock_return_pct"),
    }
    return outcome


def _aggregate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """命中率聚合(口径同 DSA):hit_rate = hit/(hit+miss);neutral/unable 单列。

    ``stats_rows`` 只喂 completed 行,故 unable 恒为 0 —— 保留该字段是为了口径
    与 DSA 一致,调用方不必猜"这里为什么没有 unable"。
    """
    total = len(rows)
    hit = sum(1 for r in rows if r.get("outcome") == "hit")
    miss = sum(1 for r in rows if r.get("outcome") == "miss")
    neutral = sum(1 for r in rows if r.get("outcome") == "neutral")
    denominator = hit + miss
    returns = [
        float(r["stock_return_pct"])
        for r in rows
        if r.get("stock_return_pct") is not None
    ]
    return {
        "total": total,
        "completed": total,
        "unable": 0,
        "hit": hit,
        "miss": miss,
        "neutral": neutral,
        "hit_rate_pct": round(hit / denominator * 100, 2) if denominator else None,
        "avg_stock_return_pct": (
            round(sum(returns) / len(returns), 4) if returns else None
        ),
    }


# -- 小工具 --------------------------------------------------------------------


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _is_mature(anchor: date, maturity_days: int) -> bool:
    return anchor <= (datetime.now(UTC).date() - timedelta(days=maturity_days))


def _bars_ascending(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(
        (r for r in rows if isinstance(r, Mapping)),
        key=lambda r: str(r.get("date") or ""),
    )


def _bars_after(bars: Sequence[Mapping[str, Any]], anchor_iso: str) -> list[Mapping[str, Any]]:
    """严格晚于锚点的 bar,升序(全序字符串比较 —— date 为 ISO)。

    调用方给的 bar 未必有序,而窗口取"前 N 根"依赖升序,故这里排一次;
    :func:`_close_on_or_before` 取"锚点前最近一根"同样依赖有序。
    """
    later = [b for b in bars if str(b.get("date") or "") > anchor_iso]
    later.sort(key=lambda b: str(b.get("date") or ""))
    return later


def _close_on_or_before(
    bars: Sequence[Mapping[str, Any]], anchor_iso: str
) -> float | None:
    """锚点当日(或之前最近一日)的收盘价,作为前向收益的起点。

    精确等于锚点日优先;没有则取锚点前最近一根(节假日/停牌的容错),都没有
    则 None → 上层记 unable(missing_anchor_price),绝不拿一个瞎猜的价格当锚。
    """
    on_or_before = [b for b in bars if str(b.get("date") or "") <= anchor_iso]
    if not on_or_before:
        return None
    exact = [b for b in on_or_before if str(b.get("date") or "") == anchor_iso]
    chosen = exact[-1] if exact else on_or_before[-1]
    return _as_float(chosen.get("close"))


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool_int(value: Any) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


def _json_loads(value: Any) -> Any:
    if not value:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


__all__ = ["DecisionSignalOutcomeService", "DEFAULT_MAX_PER_RUN"]
