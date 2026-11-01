"""Counterfactual settlement —— 给"没有可回测策略"的 mission 一条真判定腿。

**为什么需要它。** ``mission_verifier`` 只在 mission 能解析出一个策略时才回测。
实测 11 条 open mission 里只有 1 条带策略;其余的 PA 心跳诊断、系统验证条目
既没有策略也没有 K 线数组,于是 ``verify_mission`` 早退成 ``ran=False``,
worker 走 ``untested`` 分支 —— 每一轮都写"测不了",永远不会有结论。

但库里的记录其实已经许下了可判定的承诺:``stage1.diagnosis_or_decision.
next_period.direction``(实测 37 条里 6 条有)。这条腿把它们对照**之后**的
真实收盘价,给每个 mission 一个 hit / miss / neutral。

**判定权在代码。** 方向词表、horizon、动量基线全部写死在下面的常量里;
LLM 不参与"这算不算判对"。这与 ``mission_verifier`` 的阈值纪律同源。

**三条诚实规则:**
1. 数据不够就 ``untested``,绝不把"还没走完"记成"没判对";
2. 方向词不认识就 ``unscorable``,不猜;
3. 基线(近期动量)也命中的预测只算 ``mildly_confirming`` —— 与"顺势猜对"
   没有区别,不能当独立证据。

**取数只读本地 klines.db**(与 ``mission_verifier._load_frame`` 同源),
不走 ``FetcherManager``:后者只认 A 股 6 位代码,对 ``BTC/USDT`` 直接抛错
—— 这正是 P5 前向验证器 46 行证据里 0 行 ``AFTER_THE_FACT`` 的原因之一。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from selene_space_core.kline_paths import default_klines_db_path
from selene_space_decision_workflow.eval_counterfactual import (
    next_period_of,
    score_record,
)
from selene_space_decision_workflow.trading_pipeline import load_bars
from selene_space_storage.repositories._mission_thesis_vocab import SIGNALS
from selene_space_storage.repositories.research_graph._shared import _is_past
from selene_space_storage.repositories.analysis_records import (
    AnalysisRecordRepository,
)
from selene_space_storage.repositories.mission_thesis import MissionThesisRepository
from selene_space_storage.repositories.research_graph import (
    EpisodeRepository,
    EvidenceRepository,
    ResearchEdgeRepository,
)

logger = logging.getLogger(__name__)

#: 判定 horizon(根)。与决策记录观察的周期同量级:1h 上 24 根 ≈ 一天,
#: 4h 上 6 根 ≈ 一天。写死在这里 = "跑之前就定好了",改它要留 diff。
HORIZON_FOR_INTERVAL: dict[str, int] = {
    "1m": 60,
    "5m": 48,
    "15m": 24,
    "30m": 24,
    "1h": 24,
    "4h": 6,
    "8h": 6,
    "12h": 6,
    "1d": 5,
}
DEFAULT_HORIZON = 24

#: 与本判定一致的中性带:单根收盘落在 ±2% 内视为"没走出方向"。
#: 但 ``score_record`` 不接受中性带(它按符号判 hit),所以这里只在叙事里用。
NEUTRAL_BAND_PCT = 2.0

#: 认定"与动量同向"的回看根数。3 根是 ``eval_next_bar.momentum_predictor``
#: 的默认窗口,沿用而不新发明。
MOMENTUM_WINDOW = 3

_INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}

_SUBJECT_TYPE_ANALYSIS = "dsa_analysis"


@dataclass(frozen=True)
class CounterfactualOutcome:
    """一次 counterfactual 结算的完整结果。

    字段名与 ``mission_verifier.VerificationOutcome`` 对齐(``ran`` / ``signal`` /
    ``evidence_id`` / ``fact`` / ``interpretation``),worker 因此无需分支 ——
    但它不 import 那个类,避免与 verifier 形成循环依赖。
    """

    mission_id: str
    ran: bool
    signal: str = "untested"
    reason: str = ""
    evidence_id: str | None = None
    pillar_id: str | None = None
    verdict: dict[str, Any] = field(default_factory=dict)
    fact: str = ""
    interpretation: str = ""

    def to_detail(self) -> dict[str, Any]:
        return {
            "ran": self.ran,
            "signal": self.signal,
            "reason": self.reason,
            "evidence_id": self.evidence_id,
            "pillar_id": self.pillar_id,
            "verdict": self.verdict,
            "leg": "counterfactual",
        }


def horizon_for(interval: str) -> int:
    return HORIZON_FOR_INTERVAL.get((interval or "").strip().lower(), DEFAULT_HORIZON)


# ---------------------------------------------------------------------------
# 取记录:mission → 分析记录(不猜)
# ---------------------------------------------------------------------------


async def resolve_mission_record(
    db: Any, mission: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    """找出这条 mission 谈的那条分析记录。返回 ``(record, analysis_id)``。

    三条路径,都要求记录**确实属于**这条 mission:

    a) ``context.record`` —— 手建 mission 把完整记录内联进来了;
    b) ``context.latest_diagnosis.record_id`` / ``context.diagnoses[*]`` ——
       PA 心跳写的是指针,``record_id`` 就是 ``analysis_records.input_id``;
    c) 都没有 → ``(None, None)``。**不**按 symbol 扫最新记录:那会把 mission
       从未看过的记录当成它的判断,审计链上比"没结论"更糟。
    """
    ctx = mission.get("context") or {}

    inline = ctx.get("record")
    if isinstance(inline, dict) and _has_forecast(inline):
        return inline, _analysis_id_of(inline)

    record_ids: list[str] = []
    latest = ctx.get("latest_diagnosis")
    if isinstance(latest, dict) and latest.get("record_id"):
        record_ids.append(str(latest["record_id"]))
    for diag in reversed(ctx.get("diagnoses") or []):
        if isinstance(diag, dict) and diag.get("record_id"):
            rid = str(diag["record_id"])
            if rid not in record_ids:
                record_ids.append(rid)

    if not record_ids:
        return None, None

    repo = AnalysisRecordRepository(db)
    for rid in record_ids:
        try:
            record = await repo.get_by_input_id(rid)
        except Exception:  # noqa: BLE001 — 读失败只是这条判定不做
            logger.warning(
                "counterfactual: analysis record lookup failed for %s",
                rid,
                exc_info=True,
            )
            continue
        if record is not None and _has_forecast(record):
            return record, rid
    return None, None


def _analysis_id_of(record: dict[str, Any]) -> str | None:
    meta = record.get("meta") or {}
    candidate = meta.get("input_id") or meta.get("record_id")
    return str(candidate) if candidate else None


def _has_forecast(record: dict[str, Any]) -> bool:
    return _forecast_of(record) is not None


def _forecast_of(record: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
    """返回 ``(可被 score_record 读的记录, 方向来源名)``。

    第一优先 ``stage1/stage2.diagnosis_or_decision.next_period``(实测 6 条);
    没有时退到 ``input_data.direction_signal.direction``(实测 2 条),
    以"注入一个最小 next_period"的方式复用同一套评分,来源写进 verdict。
    """
    if next_period_of(record) is not None:
        return record, "next_period"
    signal = (record.get("input_data") or {}).get("direction_signal")
    if isinstance(signal, dict):
        raw = signal.get("direction")
        if isinstance(raw, str) and raw.strip():
            patched = {
                **record,
                "stage1": {
                    "diagnosis_or_decision": {
                        "next_period": {"direction": raw, "source": "direction_signal"}
                    }
                },
            }
            return patched, "direction_signal"
    return None


# ---------------------------------------------------------------------------
# 锚点与前向 bar
# ---------------------------------------------------------------------------


def _summary_of(record: dict[str, Any]) -> dict[str, Any]:
    summary = (record.get("input_data") or {}).get("summary")
    return summary if isinstance(summary, dict) else {}


def anchor_ms_of(record: dict[str, Any]) -> int | None:
    """决策锚点的 bar 时间戳(epoch ms)。

    ``summary.window_end_iso`` 名字里有 iso,实测存的是 **epoch ms**
    (2026-08-22 = 1787425200000);``input_data.klines[*].timestamp`` 也是 ms。
    两个都认,再退到 ``meta.timestamp`` 的 ISO 串。
    """
    raw = _summary_of(record).get("window_end_iso")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return int(raw)
    klines = (record.get("input_data") or {}).get("klines")
    if isinstance(klines, list) and klines:
        last = klines[-1]
        if isinstance(last, dict):
            ts = last.get("timestamp")
            if isinstance(ts, (int, float)) and not isinstance(ts, bool):
                return int(ts)
    stamp = (record.get("meta") or {}).get("timestamp")
    if isinstance(stamp, str) and stamp:
        try:
            return int(datetime.fromisoformat(stamp).timestamp() * 1000)
        except ValueError:
            return None
    return None


def _with_anchor_close(record: dict[str, Any]) -> dict[str, Any]:
    """补 ``summary.last_close``:``score_record`` 只从这里读锚点价。

    带 ``input_data.klines`` 的记录(28 条 BTC)没有 summary,但最后一根
    K 线的 close 就是同一个价。补齐而不是改评分器。
    """
    summary = _summary_of(record)
    last_close = summary.get("last_close")
    if isinstance(last_close, (int, float)) and not isinstance(last_close, bool):
        return record
    klines = (record.get("input_data") or {}).get("klines")
    if not isinstance(klines, list) or not klines:
        return record
    close = klines[-1].get("close") if isinstance(klines[-1], dict) else None
    if not isinstance(close, (int, float)) or isinstance(close, bool):
        return record
    return {
        **record,
        "input_data": {
            **(record.get("input_data") or {}),
            "summary": {**summary, "last_close": float(close)},
        },
    }


def load_forward_bars(
    symbol: str,
    interval: str,
    anchor_ms: int,
    *,
    horizon: int,
    market_type: str = "futures",
    db_path: Any = None,
) -> tuple[list[dict[str, Any]] | None, str]:
    """取锚点**之后**的 horizon 根 K 线(升序)。返回 ``(bars, reason)``。

    ``reason`` 为空串表示成功;否则是**具体**原因 —— "库里没那么多根"与
    "库读不了(被写者锁住/路径不对)"是完全不同的两件事,上层绝不能都
    报成"前向 K 线不足"。实测生产库是 ``journal_mode=delete``,后端在跑时
    读会被短暂锁住(``sqlite3.OperationalError: database is locked``)。

    ``load_bars`` 的 ``after_ms`` 分支是 ``timestamp >= after_ms``,锚点那根
    本身会被算进第一根 —— 所以偏移一个周期,否则 horizon 少算一根。
    """
    step = _INTERVAL_MS.get((interval or "").strip().lower())
    if step is None:
        return None, f"未知周期 {interval!r},无法确定前向 K 线间隔"
    path = db_path or default_klines_db_path()
    try:
        bars = load_bars(
            path,
            symbol,
            interval,
            horizon,
            market_type=market_type,
            min_bars=0,
            after_ms=anchor_ms + step,
        )
    except Exception as exc:  # noqa: BLE001 — 读不到要说清是哪一种
        logger.warning(
            "counterfactual: forward bars unreadable for %s %s",
            symbol,
            interval,
            exc_info=True,
        )
        return None, f"前向 K 线读取失败:{type(exc).__name__}: {exc}"[:200]
    if len(bars) < horizon:
        return None, (
            f"锚点之后只有 {len(bars)} 根 {interval} K 线,不足 horizon={horizon}"
        )
    return bars[:horizon], ""


def momentum_direction(record: dict[str, Any]) -> str | None:
    """记录自己看到的窗口里的近期动量方向(up/down/flat)。

    用 ``summary.bars``(120 根,键名 c/o/h/l/v/k)—— 这正是模型当时能看到的
    信息,拿它做基线不需要再读一次库。窗口不足返回 None。
    """
    bars = _summary_of(record).get("bars")
    if not isinstance(bars, list) or len(bars) <= MOMENTUM_WINDOW:
        return None
    try:
        closes = [float(b["c"]) for b in bars if isinstance(b, dict)]
    except (KeyError, TypeError, ValueError):
        return None
    if len(closes) <= MOMENTUM_WINDOW:
        return None
    delta = closes[-1] - closes[-1 - MOMENTUM_WINDOW]
    if delta > 0:
        return "up"
    if delta < 0:
        return "down"
    return "flat"


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------

_BULLISH = frozenset({"up", "long"})
_BEARISH = frozenset({"down", "short"})
_NEUTRAL = frozenset({"neutral", "flat"})


def signal_for(verdict: dict[str, Any], baseline: str | None) -> str:
    """verdict + 动量基线 → ``SIGNALS`` 里的一个值。

    - 未结算 / 方向不可判 → ``untested``(不是"没判对")
    - neutral → ``neutral``(断言没有方向性 edge,记下但不计命中)
    - 幅度落在中性带内 → ``neutral``(**不是**命中)
    - 判对但方向与动量基线一致 → ``mildly_confirming``(顺势猜对,不算独立证据)
    - 判对且逆着动量 → ``confirming``
    - 判错 → ``weakening``

    ⚠️ 中性带是**必须**的:``score_record`` 只按收益符号判 hit,所以
    "-0.03% 的做空"在它那里算命中。实测 ``mis-49a4c5fc8d91`` 就落在这一点
    (BTC short 24 根后 -0.03%)—— 把横盘记成"判对了"会让信号自欺。
    """
    if not verdict.get("settled"):
        return "untested"
    direction = verdict.get("direction")
    if direction is None or direction in _NEUTRAL:
        return "neutral"
    pnl = verdict.get("pnl_pct")
    if isinstance(pnl, (int, float)) and not isinstance(pnl, bool):
        if abs(pnl) * 100.0 < NEUTRAL_BAND_PCT:
            return "neutral"
    hit = verdict.get("hit")
    if hit is False:
        return "weakening"
    if hit is not True:
        return "untested"
    if baseline is not None and _same_side(direction, baseline):
        return "mildly_confirming"
    return "confirming"


def _same_side(direction: str, baseline: str) -> bool:
    if direction in _BULLISH:
        return baseline == "up"
    if direction in _BEARISH:
        return baseline == "down"
    return baseline == "flat"


def render_fact(
    *,
    symbol: str,
    interval: str,
    verdict: dict[str, Any],
    baseline: str | None,
    source: str,
) -> str:
    """一行可审计的事实:期望方向、锚点价、结算价、收益、基线。"""
    direction = verdict.get("direction")
    anchor = verdict.get("decision_close")
    settled = verdict.get("settled_close")
    horizon = verdict.get("horizon")
    pnl = verdict.get("pnl_pct")
    parts = [f"{symbol} {interval} 的记录承诺 next_period={direction or '不可判'}"]
    if anchor is not None and settled is not None and pnl is not None:
        parts.append(
            f"锚点收盘 {anchor:.2f} → {horizon} 根后 {settled:.2f}({pnl * 100:+.2f}%)"
        )
    elif anchor is not None:
        parts.append(f"锚点收盘 {anchor:.2f},前向 K 线不足 {horizon} 根")
    parts.append(f"动量基线 {baseline or '不可得'}")
    parts.append(f"方向来源 {source}")
    return ";".join(parts)


def render_interpretation(verdict: dict[str, Any], signal: str) -> str:
    if not verdict.get("settled"):
        return (
            "这条判定**不构成证据**:锚点之后的前向 K 线还没走满 horizon。"
            "未结算 ≠ 判错,下一轮再判。"
        )
    if verdict.get("unscorable"):
        return (
            "这条判定**不构成证据**:记录里的方向词不在已知词表内"
            "(up/long/down/short/neutral/flat),系统不猜它的含义。"
        )
    if signal == "neutral":
        return "记录判断为中性:它没有主张方向性 edge。结果按中性记下,不计入命中率。"
    if signal == "weakening":
        return (
            "实际走势与记录承诺的方向相反,该判断**被削弱**。"
            "单次判错不等于支柱被推翻,但连续反向就该重审。"
        )
    if signal == "mildly_confirming":
        return (
            "方向判对,但同期动量基线给出同一方向 —— 这可能是顺势猜对,"
            "**不足以单独视为证据**。"
        )
    return (
        "方向判对且逆着同期动量基线,该判断**获得支持**。"
        "信号由阈值表确定,不由模型措辞决定。"
    )


# ---------------------------------------------------------------------------
# 写账
# ---------------------------------------------------------------------------


async def _write_graph_evidence(
    db: Any,
    *,
    analysis_id: str,
    symbol: str,
    anchor_ms: int,
    verdict: dict[str, Any],
    horizon: int,
    question_id: str,
    source: str,
    baseline: str | None,
) -> str | None:
    """写 OUTCOME episode + AFTER_THE_FACT evidence + addresses 边。"""
    anchor_date = datetime.fromtimestamp(anchor_ms / 1000, UTC).date().isoformat()
    episode_id = await EpisodeRepository(db).create(
        subject_type=_SUBJECT_TYPE_ANALYSIS,
        subject_id=analysis_id,
        title=f"反事实结算 {symbol} @ {anchor_date}",
        boundary_start=anchor_date,
        boundary_end=anchor_date,
        recorded={
            "kind": "counterfactual_settlement",
            "symbol": symbol,
            "horizon": horizon,
            "direction": verdict.get("direction"),
            "direction_source": source,
            "momentum_baseline": baseline,
            "pnl_pct": verdict.get("pnl_pct"),
            "hit": verdict.get("hit"),
            "settled": verdict.get("settled"),
        },
    )
    edges = ResearchEdgeRepository(db)
    await edges.add_fact_edge(
        from_ref=("episode", episode_id),
        to_ref=("question", question_id),
        kind="addresses",
    )
    evidence_id = await EvidenceRepository(db).create(
        episode_id=episode_id,
        mode="AFTER_THE_FACT",
        payload={
            "kind": "counterfactual",
            "direction": verdict.get("direction"),
            "direction_source": source,
            "decision_close": verdict.get("decision_close"),
            "settled_close": verdict.get("settled_close"),
            "pnl_pct": verdict.get("pnl_pct"),
            "hit": verdict.get("hit"),
            "settled": verdict.get("settled"),
            "horizon": horizon,
            "momentum_baseline": baseline,
        },
        source_ref=f"counterfactual:{analysis_id}:{horizon}",
    )
    await edges.add_fact_edge(
        from_ref=("evidence", evidence_id),
        to_ref=("question", question_id),
        kind="addresses",
    )
    if verdict.get("settled") and verdict.get("hit") is not None:
        rationale = (
            f"反事实结算:方向 {verdict.get('direction')},"
            f"收益 {(verdict.get('pnl_pct') or 0) * 100:+.2f}%,"
            f"{'命中' if verdict.get('hit') else '未命中'}"
        )
        await edges.add_judgment_edge(
            from_ref=("evidence", evidence_id),
            to_ref=("question", question_id),
            kind="supports" if verdict.get("hit") else "contradicts",
            rationale=rationale,
            provenance=[evidence_id],
            actor="selene",
        )
    return str(evidence_id)


async def _already_settled(
    db: Any, mission_id: str, analysis_id: str, horizon: int
) -> bool:
    """同一份判定是否已经入过账。

    两张表都要查:mission 证据(``mission_evidence.source_ref``)是**一定有**的,
    graph 证据(``research_evidence.source_ref``)只在 mission 有归属问题时才写。
    只看后者会让没绑问题的 mission 每轮重复入账。
    """
    source_ref = f"counterfactual:{analysis_id}:{horizon}"
    for sql, params in (
        (
            "SELECT 1 FROM mission_evidence WHERE mission_id = ? AND source_ref = ? LIMIT 1",
            (mission_id, source_ref),
        ),
        (
            "SELECT 1 FROM research_evidence WHERE source_ref = ? LIMIT 1",
            (source_ref,),
        ),
    ):
        if await db.execute(sql, params):
            return True
    return False


async def settle_mission(
    db: Any,
    mission: dict[str, Any],
    *,
    symbol: str,
    interval: str,
    pillar_id: str | None = None,
    question_id: str | None = None,
    db_path: Any = None,
) -> CounterfactualOutcome:
    """对一条无策略 mission 跑一次反事实结算。

    任何一步不来就不写任何东西,``ran=False`` 且写清原因 —— 与
    ``verify_mission`` 的失败语义一致(一次失败不拖垮整轮扫描)。
    """
    mission_id = str(mission.get("id") or "")
    record, analysis_id = await resolve_mission_record(db, mission)
    if record is None or analysis_id is None:
        return CounterfactualOutcome(
            mission_id,
            False,
            signal="untested",
            reason="这条 mission 没有带方向承诺的记录(既无 context.record,也无诊断指针)",
        )

    prepared, source = _forecast_of(record) or (None, "")
    if prepared is None:
        return CounterfactualOutcome(
            mission_id, False, reason="记录里没有可判定的方向承诺"
        )
    prepared = _with_anchor_close(prepared)

    anchor_ms = anchor_ms_of(record)
    if anchor_ms is None:
        return CounterfactualOutcome(
            mission_id, False, reason="记录缺少锚点时间戳,无法取前向 K 线"
        )

    horizon = horizon_for(interval)
    forward, bars_reason = load_forward_bars(
        symbol, interval, anchor_ms, horizon=horizon, db_path=db_path
    )
    if forward is None:
        # 取不到前向 bar 时**不下判定**:既不能把"库读不到"说成"判错",
        # 也不能误报成"还没走完"。原因原样上抛,不写任何证据。
        return CounterfactualOutcome(mission_id, False, reason=bars_reason)
    verdict = score_record(prepared, forward, horizon=horizon)
    if verdict is None:
        return CounterfactualOutcome(
            mission_id, False, reason="记录不在反事实可判总体内"
        )

    # 未结算 / 方向不可判 → **不写任何东西**。写一行 untested 会用掉幂等键
    # ``counterfactual:{aid}:{horizon}``,等 K 线走完时反而被判成"已结算过",
    # 永远补不上。这一轮就诚实地什么都不说,下一轮再来。
    if verdict.get("unscorable"):
        return CounterfactualOutcome(
            mission_id,
            False,
            reason=f"方向词不在已知词表内,不可判定(verdict={verdict})",
            verdict=verdict,
        )
    if not verdict.get("settled"):
        return CounterfactualOutcome(
            mission_id,
            False,
            reason=(f"锚点之后的前向 K 线不足 {horizon} 根,尚未结算(未结算 ≠ 判错)"),
            verdict=verdict,
        )

    if await _already_settled(db, mission_id, analysis_id, horizon):
        return CounterfactualOutcome(
            mission_id,
            False,
            reason=f"该记录已在 horizon={horizon} 上结算过(幂等跳过)",
            verdict=verdict,
        )

    baseline = momentum_direction(record)
    signal = signal_for(verdict, baseline)
    if signal not in SIGNALS:  # 防御:词表漂移时不要写脏值
        signal = "untested"

    fact = render_fact(
        symbol=symbol,
        interval=interval,
        verdict=verdict,
        baseline=baseline,
        source=source,
    )
    interpretation = render_interpretation(verdict, signal)

    evidence_id: str | None = None
    thesis_repo = MissionThesisRepository(db)
    resolved_pillar = pillar_id
    if resolved_pillar is None:
        pillars = [
            p
            for p in await thesis_repo.list_pillars(mission_id)
            if p.get("superseded_by") is None and p.get("status") != "retired"
        ]
        resolved_pillar = str(pillars[0]["id"]) if pillars else None
    if resolved_pillar is None:
        # 反事实判定描述的是"这条记录的方向承诺准不准",正好可证伪。
        resolved_pillar = await thesis_repo.add_pillar(
            mission_id,
            f"{symbol} {interval} 上该记录的 next_period 方向承诺成立",
            "core",
            confirm_threshold=(
                f"方向命中且逆于 {MOMENTUM_WINDOW} 根动量;horizon={horizon} 根"
            ),
            break_threshold="方向相反(判错);未结算不算反驳",
            threshold_provenance="draft",
            provenance="draft",
            model_line="counterfactual_settlement 自动生成",
        )

    try:
        evidence_id = await thesis_repo.append_evidence(
            mission_id,
            resolved_pillar,
            occurred_at=_now(),
            source_type="counterfactual",
            fact=fact,
            interpretation=interpretation,
            signal=signal,
            source_ref=f"counterfactual:{analysis_id}:{horizon}",
            follow_up=None if verdict.get("settled") else "前向 K 线未走满 horizon",
            owner="selene",
        )
    except (ValueError, KeyError) as exc:
        logger.warning("counterfactual evidence rejected for %s: %s", mission_id, exc)
        return CounterfactualOutcome(
            mission_id,
            False,
            reason=f"证据写入被拒:{exc}",
            pillar_id=resolved_pillar,
            verdict=verdict,
        )

    if question_id:
        try:
            await _write_graph_evidence(
                db,
                analysis_id=analysis_id,
                symbol=symbol,
                anchor_ms=anchor_ms,
                verdict=verdict,
                horizon=horizon,
                question_id=question_id,
                source=source,
                baseline=baseline,
            )
        except Exception:  # noqa: BLE001 — 图写入失败不该收走 mission 证据
            logger.warning(
                "counterfactual: graph evidence write failed for %s",
                mission_id,
                exc_info=True,
            )

    return CounterfactualOutcome(
        mission_id,
        True,
        signal=signal,
        reason=interpretation,
        evidence_id=evidence_id,
        pillar_id=resolved_pillar,
        verdict=verdict,
        fact=fact,
        interpretation=interpretation,
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 问题侧扫描(⑤ 翻账的前置)
# ---------------------------------------------------------------------------


async def settle_decisions(
    db: Any,
    *,
    limit: int = 50,
    db_path: Any = None,
) -> dict[str, int]:
    """扫窗口已过、仍 ``outcome_pending`` 的决策,给每个补一次前向观测。

    **为什么不做成 mission 驱动。** mission 会停在 ``needs_input`` 等人拍板,
    结案后的决策则挂在问题上、与 mission 生命周期无关。P5 recorder 只在
    mission 侧触发,于是 ``dec-*`` 这些决策永远等不到观测 —— 这就是
    ``dec-1ded3e5a8478`` 卡在 ``outcome_pending`` 的原因。

    本函数只做两件事:为该问题找到**最近一次**带方向承诺的分析记录、结算它。
    真正的 ``outcome_pending → outcome_observed`` 翻转仍由
    ``observe_outcome`` 负责(且它要求证据已落库),这里不越权。

    返回 counters;单条失败只计数,不拖垮整轮。
    """
    counters = {"scanned": 0, "settled": 0, "no_record": 0, "not_due": 0, "failed": 0}
    rows = await db.execute(
        """
        SELECT id, question_id, window_end FROM research_decisions
        WHERE outcome_status = 'outcome_pending' AND window_end IS NOT NULL
        ORDER BY window_end ASC LIMIT ?
        """,
        (limit,),
    )
    now = _now()
    for row in rows:
        counters["scanned"] += 1
        if not _is_past(row["window_end"], now):
            counters["not_due"] += 1
            continue
        question_id = str(row["question_id"])
        try:
            settled = await _settle_question_once(
                db, question_id=question_id, db_path=db_path
            )
        except Exception:  # noqa: BLE001 — 一条坏问题不得拖垮整轮
            counters["failed"] += 1
            logger.warning(
                "counterfactual: decision scan failed for question=%s",
                question_id,
                exc_info=True,
            )
            continue
        counters["settled" if settled else "no_record"] += 1
    return counters


async def _settle_question_once(
    db: Any, *, question_id: str, db_path: Any = None
) -> bool:
    """给一个问题找最近一条带方向承诺的记录并结算。返回是否真的落了一条观测。"""
    record, analysis_id, symbol, interval = await _latest_record_for_question(
        db, question_id
    )
    if record is None or analysis_id is None or not symbol:
        return False
    prepared, _source = _forecast_of(record) or (None, "")
    if prepared is None:
        return False
    prepared = _with_anchor_close(prepared)
    anchor_ms = anchor_ms_of(record)
    if anchor_ms is None:
        return False
    horizon = horizon_for(interval)
    forward, _reason = load_forward_bars(
        symbol, interval, anchor_ms, horizon=horizon, db_path=db_path
    )
    if forward is None:
        return False
    verdict = score_record(prepared, forward, horizon=horizon)
    if verdict is None or not verdict.get("settled") or verdict.get("unscorable"):
        return False
    if await _already_settled(db, "", analysis_id, horizon):
        return False
    baseline = momentum_direction(record)
    await _write_graph_evidence(
        db,
        analysis_id=analysis_id,
        symbol=symbol,
        anchor_ms=anchor_ms,
        verdict=verdict,
        horizon=horizon,
        question_id=question_id,
        source=_source,
        baseline=baseline,
    )
    return True


async def _latest_record_for_question(
    db: Any, question_id: str
) -> tuple[dict[str, Any] | None, str | None, str, str]:
    """该问题上最近一条带方向承诺的分析记录(按锚点时间降序)。

    只读**这个问题的锚定标的**:``research_questions.anchor_json`` 里的
    ``symbols[0]``。anchor 没写标的时返回空 —— 不跨标的不猜。
    """
    rows = await db.execute(
        "SELECT anchor_json FROM research_questions WHERE id = ?", (question_id,)
    )
    if not rows:
        return None, None, "", ""
    import json

    try:
        anchor = json.loads(rows[0]["anchor_json"] or "{}")
    except (TypeError, ValueError):
        return None, None, "", ""
    symbols = anchor.get("symbols")
    symbol = ""
    if isinstance(symbols, list) and symbols:
        symbol = str(symbols[0])
    elif isinstance(anchor.get("symbol"), str):
        symbol = str(anchor["symbol"])
    if not symbol:
        return None, None, "", ""

    candidates = await AnalysisRecordRepository(db).list_records(
        symbol=symbol, limit=50
    )
    best: tuple[int, dict[str, Any], str, str] | None = None
    for record in candidates:
        if _forecast_of(record) is None:
            continue
        anchor_ms = anchor_ms_of(record)
        if anchor_ms is None:
            continue
        input_data = record.get("input_data") or {}
        interval = str(input_data.get("interval") or "1h")
        analysis_id = _analysis_id_of(record) or ""
        if not analysis_id:
            continue
        if best is None or anchor_ms > best[0]:
            best = (anchor_ms, record, analysis_id, interval)
    if best is None:
        return None, None, "", ""
    _ms, record, analysis_id, interval = best
    return record, analysis_id, symbol, interval


__all__ = [
    "CounterfactualOutcome",
    "DEFAULT_HORIZON",
    "HORIZON_FOR_INTERVAL",
    "MOMENTUM_WINDOW",
    "NEUTRAL_BAND_PCT",
    "anchor_ms_of",
    "horizon_for",
    "load_forward_bars",
    "momentum_direction",
    "render_fact",
    "render_interpretation",
    "resolve_mission_record",
    "settle_decisions",
    "settle_mission",
    "signal_for",
]
