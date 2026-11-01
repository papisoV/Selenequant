"""P5 前向验证 recorder —— 把已成熟的分析结论对照前向行情,落进 research graph。

阶段 C 的执行体(方案 ``internal-plan``)。职责:
扫出**已成熟**的 v009 分析记录 → 取前向日线 → 调纯函数
(:mod:`selene_space_backtest.forward_validation`) → 按固定顺序写图。

为什么住在这里(而不是 storage 或 daily-cycle):
- ``daily-cycle`` **不依赖 storage**(pyproject 无 selene-space-storage)——故它
  只收一个可选注入 callable(方案 §4)。
- ``storage`` **不依赖 backtest-engine**——故纯函数与写图不能同居一包。
- 本模块同时 import 两者,只有 local-api 的装配层(与既有
  ``decision_workflow_service.py`` 同层)能同时看见它们。

**写入顺序(ADR 契约,方案 §8)**:evidence INSERT **完成且可重读**后,
才调 ``observe_outcome``——所以崩溃在两者之间时,重跑能补上转换且不重复
evidence(靠 ``source_ref`` 的 partial unique 索引兜底)。

**不变量**:本模块**永不**调 ``mark_never``(方案契约 4);不写 ``observed_from``
真边(它是列派生);``actor='selene'`` 的 judgment 边是 **advisory**(契约 1)。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from selene_space_backtest.forward_validation import (
    EVAL_COMPLETED,
    DEFAULT_EVAL_WINDOW_DAYS,
    DEFAULT_NEUTRAL_BAND_PCT,
    analysis_bar_date,
    evaluate_forward,
    window_end_of,
)
from selene_space_storage.repositories.analysis_records import (
    AnalysisRecordRepository,
)
from selene_space_storage.repositories.research_graph import (
    DecisionRepository,
    EpisodeRepository,
    EvidenceRepository,
    ResearchEdgeRepository,
)
from selene_space_storage.repositories.research_graph._shared import _is_past
from selene_space_storage.repositories.research_graph.objects import OUTCOME_PENDING

logger = logging.getLogger(__name__)

# 成熟闸 / 观测跨度(DSA config 默认:min_age=14 交易日, eval_window=10)。
# 交易日 ≈ 自然日的 7/5 倍,但**边界判定一律用实测 bar 时间戳**(方案 §6 议题6):
# 这里只用它们做"是否值得去取 bar"的粗筛,不做日历运算。
DEFAULT_MATURITY_DAYS = 14

# 单次运行最多处理多少条(方案 Pre-mortem 1:首轮洪水靠 cap + unique 索引挡)。
DEFAULT_MAX_PER_RUN = 50

SUBJECT_TYPE = "dsa_analysis"


class ForwardValidationRecorder:
    """扫成熟分析 → 前向验证 → 写 research graph 的执行体。

    ``db`` 是共享的 :class:`~selene_space_storage.sqlite.AsyncSQLiteManager`;
    ``fetch_daily`` 是 ``symbol → (rows, source)`` 的同步取数器(默认接
    FetcherManager.fetch_daily,调用方以 ``asyncio.to_thread`` 包裹)。
    """

    def __init__(
        self,
        db: Any,
        *,
        fetch_daily: Callable[[str], tuple[list[dict[str, Any]], str]],
        eval_window_days: int = DEFAULT_EVAL_WINDOW_DAYS,
        neutral_band_pct: float = DEFAULT_NEUTRAL_BAND_PCT,
        maturity_days: int = DEFAULT_MATURITY_DAYS,
        max_per_run: int = DEFAULT_MAX_PER_RUN,
    ) -> None:
        self._db = db
        self._fetch_daily = fetch_daily
        self._eval_window_days = int(eval_window_days)
        self._neutral_band_pct = float(neutral_band_pct)
        self._maturity_days = int(maturity_days)
        self._max_per_run = int(max_per_run)

    async def run(self) -> dict[str, int]:
        """一轮前向验证。返回 counters(方案 §9 验收 7)。"""
        counters = {
            "created": 0,
            "skipped_no_linkage": 0,
            "already_observed": 0,
            "insufficient_skipped": 0,
            "error_skipped": 0,
        }
        records_repo = AnalysisRecordRepository(self._db)
        candidates = await self._matured_candidates(records_repo)
        for record in candidates:
            try:
                await self._process_one(record, counters)
            except Exception:  # noqa: BLE001 — 一条坏记录不得拖垮整轮
                counters["error_skipped"] += 1
                logger.warning(
                    "forward validation: record failed input_id=%s",
                    (record.get("meta") or {}).get("input_id"),
                    exc_info=True,
                )
        return counters

    # -- 候选:已成熟 + 无既有 outcome 观测 -------------------------------

    async def _matured_candidates(
        self, records_repo: AnalysisRecordRepository
    ) -> list[dict[str, Any]]:
        """取成熟候选。成熟 = 分析锚点距今 ≥ maturity_days(粗筛)。"""
        rows = await records_repo.list_records(limit=self._max_per_run * 4)
        cutoff = datetime.now(UTC) - timedelta(days=self._maturity_days)
        out: list[dict[str, Any]] = []
        for record in rows:
            if record.get("exception"):
                continue  # 坏记录不评估
            anchor = analysis_bar_date(record)
            if anchor is None:
                continue
            if not _before(anchor, cutoff.date().isoformat()):
                continue
            out.append(record)
            if len(out) >= self._max_per_run:
                break
        return out

    # -- 单条处理 -------------------------------------------------------

    async def _process_one(
        self, record: dict[str, Any], counters: dict[str, int]
    ) -> None:
        meta = record.get("meta") or {}
        analysis_id = str(meta.get("input_id") or meta.get("record_id") or "")
        if not analysis_id:
            counters["error_skipped"] += 1
            return

        input_data = record.get("input_data") or {}
        symbol = str(input_data.get("symbol") or "")
        if not symbol:
            counters["skipped_no_linkage"] += 1
            return

        anchor_date = analysis_bar_date(record)
        if anchor_date is None:
            counters["insufficient_skipped"] += 1
            return

        # 取前向 bar(FetcherManager 同步 → 线程池)。任何失败 = 不写。
        try:
            rows, _source = await asyncio.to_thread(self._fetch_daily, symbol)
        except Exception:  # noqa: BLE001 — 取数失败不得写任何东西
            counters["insufficient_skipped"] += 1
            return

        forward = _bars_after(rows, anchor_date)
        result = evaluate_forward(
            record,
            forward,
            eval_window_days=self._eval_window_days,
            neutral_band_pct=self._neutral_band_pct,
        )
        if result.get("eval_status") != EVAL_COMPLETED:
            # insufficient_data / error → 不写任何东西(方案 §9 验收 4)
            counters["insufficient_skipped"] += 1
            return

        window_end = window_end_of(result)
        if window_end is None:
            counters["insufficient_skipped"] += 1
            return

        source_ref = f"dsa_eval:{analysis_id}:{window_end}"

        # 幂等:同一 (analysis_id, window_end) 已有观测 → 干净 skip。
        already = await self._db.execute(
            "SELECT 1 FROM research_evidence WHERE source_ref = ? LIMIT 1",
            (source_ref,),
        )
        if already:
            counters["already_observed"] += 1
            return

        # 链接解析(方案 §3 议题4 顺序 a→b→c)。
        linkage = await self._resolve_linkage(record, symbol)
        if linkage is None:
            counters["skipped_no_linkage"] += 1
            return
        question_id = linkage

        await self._write_observation(
            record=record,
            analysis_id=analysis_id,
            symbol=symbol,
            anchor_date=anchor_date,
            source_ref=source_ref,
            result=result,
            question_id=question_id,
        )
        counters["created"] += 1

    async def _resolve_linkage(self, record: dict[str, Any], symbol: str) -> str | None:
        """解析该分析该挂到哪个 question(顺序 a→b→c)。

        (a) 已成熟决策经 ``led_to`` 边回指的问题 —— 现库 led_to 零写入者
            (Director 流程未落地,方案 §3 议题4 A1),故 (a) 实战恒空。
        (b) question 中介:锚在该 symbol 上的问题(``addresses`` 边的 hub)。
        (c) 都无 → None(调用方 skip + surface)。

        返回 question_id;None = 不可链接。

        反证条件(方案 §0):若 (b) 回退误解析 → 收紧为 led_to-only。
        """
        rows = await self._db.execute(
            "SELECT id FROM research_questions WHERE anchor_json LIKE ? "
            "ORDER BY opened_at DESC LIMIT 1",
            (f'%"{symbol}"%',),
        )
        if rows:
            return str(rows[0]["id"])
        return None

    async def _matured_pending_decisions(self, question_id: str) -> list[str]:
        """该 question 下**观测窗口已走完**的 pending 决策 id(升序)。

        窗口权威 = Decision 自己的 ``window_end``(方案契约 3)。窗口未到者
        **绝不**翻转(否则把"没等够"记成"已观测")。recorder 只翻 pending;
        已 observed/never 的静默跳过(one-shot 由 observe_outcome 的 WHERE
        守卫兜底)。
        """
        rows = await self._db.execute(
            "SELECT id, window_end FROM research_decisions "
            "WHERE question_id = ? AND outcome_status = ?",
            (question_id, OUTCOME_PENDING),
        )
        now = datetime.now(UTC).isoformat(timespec="seconds")
        out: list[str] = []
        for r in rows:
            end = r["window_end"]
            if end and _is_past(str(end), now=now):
                out.append(str(r["id"]))
        return out

    # -- 写图(固定顺序) ------------------------------------------------

    async def _write_observation(
        self,
        *,
        record: dict[str, Any],
        analysis_id: str,
        symbol: str,
        anchor_date: str,
        source_ref: str,
        result: dict[str, Any],
        question_id: str,
    ) -> None:
        episodes = EpisodeRepository(self._db)
        evidence = EvidenceRepository(self._db)
        edges = ResearchEdgeRepository(self._db)

        # (1) OUTCOME Episode(subject_type='dsa_analysis')
        episode_id = await episodes.create(
            subject_type=SUBJECT_TYPE,
            subject_id=analysis_id,
            title=f"前向验证 {symbol} @ {anchor_date}",
            boundary_start=anchor_date,
            boundary_end=str(
                result.get("bar_window", [anchor_date])[-1] or anchor_date
            ),
            recorded={
                "kind": "forward_validation",
                "symbol": symbol,
                "eval_window_days": result.get("eval_window_days"),
                "outcome": result.get("outcome"),
                "direction_expected": result.get("direction_expected"),
                "direction_correct": result.get("direction_correct"),
                "stock_return_pct": result.get("stock_return_pct"),
            },
        )
        await edges.add_fact_edge(
            from_ref=("episode", episode_id),
            to_ref=("question", question_id),
            kind="addresses",
        )

        # (2) AFTER_THE_FACT evidence(source_ref 幂等基板)
        evidence_id = await evidence.create(
            episode_id=episode_id,
            mode="AFTER_THE_FACT",
            payload={
                "eval_status": result.get("eval_status"),
                "outcome": result.get("outcome"),
                "direction_expected": result.get("direction_expected"),
                "direction_correct": result.get("direction_correct"),
                "stock_return_pct": result.get("stock_return_pct"),
                "start_price": result.get("start_price"),
                "end_close": result.get("end_close"),
                "bar_window": result.get("bar_window"),
            },
            source_ref=source_ref,
        )
        await edges.add_fact_edge(
            from_ref=("evidence", evidence_id),
            to_ref=("question", question_id),
            kind="addresses",
        )

        # (3) judgment 边(advisory,完备性规则:应得则发,绝不只发 contradicts)
        await self._emit_judgment_edge(evidence_id, result, question_id)

        # (4) 已成熟的 pending 决策:led_to 边 + 翻转 outcome_status。
        #     顺序:先证据(上方已落),再边,再转换 —— 与 ADR 契约一致。
        decisions = DecisionRepository(self._db)
        for decision_id in await self._matured_pending_decisions(question_id):
            await edges.add_fact_edge(
                from_ref=("decision", decision_id),
                to_ref=("episode", episode_id),
                kind="led_to",
            )
            # 既有方法(非新建);本模块永不调 mark_never(契约 4)。
            try:
                await decisions.observe_outcome(decision_id)
            except (KeyError, ValueError):
                # 竞态:已被并发解决 —— 边已写,转换留给幂等重跑。
                logger.debug(
                    "forward validation: observe_outcome no-op for %s", decision_id
                )

    async def _emit_judgment_edge(
        self, evidence_id: str, result: dict[str, Any], question_id: str
    ) -> None:
        """按 eval 应得发 advisory judgment 边(方案 §5 完备性规则)。

        本实现只发到 question hub(无 hypothesis 锚定信息时的合法落点);
        方向判对 → ``supports``,判错 → ``contradicts``,中性 → ``qualifies``。
        恒 ``actor='selene'`` + ``rationale`` + ``provenance=[evidence_id]``。
        """
        correct = result.get("direction_correct")
        outcome = result.get("outcome")
        if correct is True:
            kind = "supports"
        elif correct is False:
            kind = "contradicts"
        else:
            kind = "qualifies"
        rationale = (
            f"前向验证:期望方向 {result.get('direction_expected')},"
            f"实际收益 {result.get('stock_return_pct'):.2f}%,判定 {outcome}"
            if isinstance(result.get("stock_return_pct"), (int, float))
            else f"前向验证:判定 {outcome}(收益数据不足)"
        )
        await ResearchEdgeRepository(self._db).add_judgment_edge(
            from_ref=("evidence", evidence_id),
            to_ref=("question", question_id),
            kind=kind,
            rationale=rationale,
            provenance=[evidence_id],
            actor="selene",
        )


# --- 内部 -----------------------------------------------------------------


def _bars_after(rows: list[dict[str, Any]], anchor_date: str) -> list[dict[str, Any]]:
    """取 date 严格晚于锚点的 bar,升序(全序字符串比较,date 为 ISO)。"""
    out = [r for r in rows if str(r.get("date") or "") > anchor_date]
    out.sort(key=lambda r: str(r.get("date") or ""))
    return out


def _before(iso_date: str, cutoff_date: str) -> bool:
    """锚点日期是否早于(含)cutoff——ISO 日期串可直接比较。"""
    return iso_date <= cutoff_date


__all__ = ["ForwardValidationRecorder", "SUBJECT_TYPE"]
