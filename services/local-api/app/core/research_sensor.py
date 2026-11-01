"""① 传感器:把外部世界的观测挂到**已经有**的问题上。

**它补的是五步环最前面那一环。** 到 2026-10-26 为止,库里所有研究证据都是
"人去点调查 → council 自己找材料"产生的;没有任何东西在没人点按钮的时候
观察世界。于是问题不会因为世界变了而被重新提起,只能等人想起来。

反面教材就在同一个目录:``pa_mission_sensor.sync_diagnosis_to_mission``
把每条心跳诊断投影成一条 mission,靠 ``find_open_by_question`` 去重。结果
38 条 mission 里 23 条是 BTC 的 parked 心跳诊断 —— **"每条观测都开一个新
案子"这个设计本身就是垃圾来源**。本模块反过来:

三条硬规则(违反任一条等于把垃圾换个名字):

1. **绝不新开 question,绝不新开 mission。** 只往 ``research_evidence``
   写一行 + 一条 ``addresses`` 边,指向 anchor 里声明了同一标的的**已有
   开放问题**。验收判据:跑 N 轮后 ``research_missions`` 行数不变。
2. **幂等键在证据层。** ``source_ref`` 里带窗口终点,重放是 no-op。
3. **和验证器共用同一个写入器。** 走 ``EvidenceRepository`` /
   ``ResearchEdgeRepository``(与 ``counterfactual_settlement`` 同一套),
   不新造第三张证据表 —— 现在 ``mission_evidence`` 与 ``research_evidence``
   已经互不读了,再加一个只会更难对账。

## 只做一种触发源,并且说清为什么

计划里列了四个触发源。逐个实测之后的结论:

| 触发源 | 实测 | 本轮是否落地 |
|---|---|---|
| 行情异动 | ``klines`` 有 BTC/ETH/SOL 的 1h/4h/1d,1h 最长 18,363 根 | **是** |
| 策略退化 | ``backtests`` 里同 (策略,标的,周期) 的最多 12 行,但 ``config_snapshot_json`` **全空**、窗口还都停在 2026-05 —— 无从断言"同一配置随时间退化" | 否 |
| 宏观事件 | 依赖 ``macrodata`` provider;阶段二刚接上,尚无一次成功取数记录 | 否 |
| 研究假设 | 其实是 ``investigate_council._parse_new_questions`` 已做的事,不是传感器 | 否(已存在) |

只写能用数据支撑的那一条。给另外三条写个返回空列表的桩,读起来像"已支持
但没触发",比不写更糟 —— 那正是"看着通其实是黑洞"。

## 阈值:用这个序列自己的历史分位,不用拍脑门的百分比

固定百分比在不同周期上是几种完全不同的频率。实测 BTC/USDT(1h,24 根窗口)
最大回撤:1% 分位 7.58%,5% 分位 4.25%,2% 分位 28.76% —— 也就是"2%"这种
看起来保守的数字每小时有近三成会命中,一天刷七次,不叫异动叫噪声。

所以阈值 = **该 (标的, 周期) 全历史 |horizon 根收益| 的 99 分位**。这句话
本身就回答了"这算不算罕见":它按构造落在该序列波动最大的 1% 里。实测
p99: BTC 1h 7.58% / 1d 18.33%;ETH 10.33% / 25.16%;SOL 11.71% / 37.68%。

还有一道地板 ``MIN_MOVE_PCT``:历史太短或长期横盘的序列 p99 可能小到
没有意义(0.8% 的"前 1%"不是新闻),此时不触发并如实计数。

horizon 直接用 ``counterfactual_settlement.HORIZON_FOR_INTERVAL`` —— 传感器
报的窗口与结算判定的窗口是同一个跨度,两条腿的数字才可比。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from selene_space_core.kline_paths import default_klines_db_path
from selene_space_storage.repositories.research_graph import (
    EpisodeRepository,
    EvidenceRepository,
    ResearchEdgeRepository,
    ResearchQuestionRepository,
)

from app.core.counterfactual_settlement import HORIZON_FOR_INTERVAL, DEFAULT_HORIZON

logger = logging.getLogger(__name__)

#: 本轮实现的唯一触发源。名字进 ``source_ref``,所以改了等于换一套幂等键。
KIND_MARKET_MOVE = "market_move"

#: 取数只认 Binance 合约 —— 与 ``counterfactual_settlement.load_forward_bars``
#: 同一口径。``klines`` 的主键含 exchange + market_type,按 symbol+interval
#: 裸查会静默混掉现货/合约四个源,时间戳还看起来连续。
EXCHANGE = "binance"
MARKET_TYPE = "futures"

#: 分位阈值:99 分位 = 该序列最极端的 1%。
PERCENTILE = 0.99
#: 历史窗口数下限。低于它算不出有意义的 p99(20 个样本的"99 分位"就是最大值)。
MIN_SAMPLES = 200
#: 触发地板:低于这个幅度的"异动"不值得写进证据。
MIN_MOVE_PCT = 2.0

#: 每轮最多扫多少条开放问题。传感器不花钱,但也不能一轮扫全表。
_DEFAULT_QUESTION_LIMIT = 200


@dataclass(frozen=True)
class MoveWindow:
    """一条 (标的, 周期) 上「最极端窗口」的判定结果。"""

    symbol: str
    interval: str
    horizon: int
    window_end_ms: int
    window_start_ms: int
    move_pct: float
    direction: str
    threshold_pct: float
    samples: int


def _interval_horizon(interval: str) -> int:
    return HORIZON_FOR_INTERVAL.get(interval, DEFAULT_HORIZON)


def _closes(db_path: Path, symbol: str, interval: str) -> tuple[list[int], list[float]]:
    """该序列的 (时间戳, 收盘价),按时间升序。只读连接。

    ``exchange`` / ``market_type`` **必须**钉死(见 ``EXCHANGE`` 注释)。
    读不到(路径不对/表不存在/被写者锁住)不抛给上层 —— 传感器每轮都要
    跑,一条序列读不了只该少一条观测。
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            """
            SELECT timestamp, close FROM klines
            WHERE symbol = ? AND interval = ? AND exchange = ? AND market_type = ?
            ORDER BY timestamp ASC
            """,
            (symbol, interval, EXCHANGE, MARKET_TYPE),
        ).fetchall()
    finally:
        con.close()
    stamps = [int(r[0]) for r in rows]
    closes = [float(r[1]) for r in rows]
    return stamps, closes


def percentile_threshold(moves: list[float], pct: float = PERCENTILE) -> float | None:
    """``moves`` 的 ``pct`` 分位(最近秩法)。空列表 → None。"""
    if not moves:
        return None
    ordered = sorted(moves)
    idx = min(len(ordered) - 1, max(0, int(round(pct * (len(ordered) - 1)))))
    return ordered[idx]


def _moves(closes: list[float], horizon: int) -> list[float]:
    """相邻 ``horizon`` 根之间的 |收益率|(% *)10。"""
    return [
        abs(closes[i] / closes[i - horizon] - 1.0) * 100.0
        for i in range(horizon, len(closes))
    ]


def inspect_series(
    db_path: Path, symbol: str, interval: str
) -> tuple[MoveWindow | None, str]:
    """判一条序列最新窗口是否异动。返回 ``(window, reason)``,reason 空 = 成功。

    "最新窗口" = 库里**最后一根已收盘 bar** 往前 horizon 根。库不新鲜时
    这里报的仍是同一个窗口,于是幂等键不变、不会重复入账 —— 陈旧数据不会
    被伪装成新事件,也不会被重复写成旧事件。
    """
    horizon = _interval_horizon(interval)
    try:
        stamps, closes = _closes(db_path, symbol, interval)
    except Exception as exc:  # noqa: BLE001 — 读不了就是这一条没有观测
        logger.warning(
            "sensor: klines unreadable for %s %s", symbol, interval, exc_info=True
        )
        return None, f"K 线读取失败:{type(exc).__name__}: {exc}"[:200]

    if len(closes) <= horizon:
        return None, f"{interval} 只有 {len(closes)} 根,不足 horizon={horizon}"
    moves = _moves(closes, horizon)
    if len(moves) < MIN_SAMPLES:
        return None, (
            f"{interval} 可用窗口 {len(moves)} 个,不足 {MIN_SAMPLES},算不出分位阈值"
        )
    threshold = percentile_threshold(moves)
    if threshold is None:
        return None, "分位阈值为空"
    if threshold < MIN_MOVE_PCT:
        return None, (
            f"该序列 {PERCENTILE:.0%} 分位只有 {threshold:.2f}%,"
            f"低于地板 {MIN_MOVE_PCT:.1f}%,异动无意义"
        )

    latest = moves[-1]
    end_ms = stamps[-1]
    start_ms = stamps[-1 - horizon]
    if latest < threshold:
        return None, f"最新窗口 {latest:.2f}% 未达阈值 {threshold:.2f}%"

    return (
        MoveWindow(
            symbol=symbol,
            interval=interval,
            horizon=horizon,
            window_end_ms=end_ms,
            window_start_ms=start_ms,
            move_pct=latest,
            direction="up" if closes[-1] >= closes[-1 - horizon] else "down",
            threshold_pct=threshold,
            samples=len(moves),
        ),
        "",
    )


def source_ref_of(kind: str, symbol: str, interval: str, window_end_ms: int) -> str:
    """幂等键。

    **比计划里多带一个 interval**:1d 收盘与 1h 收盘可以落在同一个时间戳
    (都是 UTC 00:00),不带周期时两个不同窗口会撞同一个键,第二条被误判
    成"已入账"而静默丢掉。
    """
    return f"sensor:{kind}:{symbol}:{interval}:{window_end_ms}"


async def _already_recorded(db: Any, source_ref: str) -> bool:
    rows = await db.execute(
        "SELECT 1 FROM research_evidence WHERE source_ref = ? LIMIT 1", (source_ref,)
    )
    return bool(rows)


async def _write_observation(
    db: Any,
    window: MoveWindow,
    question_ids: list[str],
    *,
    source_ref: str,
) -> str:
    """一行 episode + 一行 evidence + 每个问题一条 addresses 边。

    一条观测可以服务多个问题(同一标的上的两个问题看到的是同一件事实),
    所以 episode/evidence 只写一次,边写 N 条 —— 反过来按问题各写一份会让
    同一件事在账上出现 N 次,``compare_modes`` 之类按 episode 读的地方就乱了。
    """
    end_date = datetime.fromtimestamp(window.window_end_ms / 1000, UTC).date()
    start_date = datetime.fromtimestamp(window.window_start_ms / 1000, UTC).date()
    episode_id = await EpisodeRepository(db).create(
        subject_type="market",
        subject_id=window.symbol,
        title=f"行情异动 {window.symbol} {window.interval} @ {end_date.isoformat()}",
        boundary_start=start_date.isoformat(),
        boundary_end=end_date.isoformat(),
        recorded={
            "kind": "sensor_market_move",
            "symbol": window.symbol,
            "interval": window.interval,
            "horizon": window.horizon,
            "move_pct": round(window.move_pct, 4),
            "direction": window.direction,
            "threshold_pct": round(window.threshold_pct, 4),
            "percentile": PERCENTILE,
            "samples": window.samples,
        },
    )
    evidence_id = await EvidenceRepository(db).create(
        episode_id=episode_id,
        mode="RECORDED",
        payload={
            "kind": KIND_MARKET_MOVE,
            "symbol": window.symbol,
            "interval": window.interval,
            "horizon": window.horizon,
            "window_start_ms": window.window_start_ms,
            "window_end_ms": window.window_end_ms,
            "move_pct": round(window.move_pct, 4),
            "direction": window.direction,
            "threshold_pct": round(window.threshold_pct, 4),
        },
        source_ref=source_ref,
        observed_at=datetime.fromtimestamp(window.window_end_ms / 1000, UTC).isoformat(
            timespec="seconds"
        ),
    )
    edges = ResearchEdgeRepository(db)
    for question_id in question_ids:
        try:
            await edges.add_fact_edge(
                ("evidence", evidence_id),
                ("question", question_id),
                "addresses",
                actor="system",
            )
        except Exception:  # noqa: BLE001 — UNIQUE 冲突 = 边已存在,不是失败
            logger.debug("sensor: addresses edge already exists for %s", question_id)
    return str(evidence_id)


def _anchor_targets(
    question: dict[str, Any],
) -> tuple[list[tuple[str, str]], str]:
    """问题 anchor → ``[(symbol, interval), ...]``。空列表 + 原因 = 不猜。

    只认 anchor **自己声明**的标的与周期。一个问题声明了两个周期就查两条
    序列;缺周期不默认成 1h —— ``counterfactual_settlement`` 里 ``interval``
    默认 1h 是因为它读的是记录自带的字段,而 anchor 的周期是人的意图,
    猜一个就把"看 1d 的研究"悄悄换成"看 1h 的研究"。
    """
    anchor = question.get("anchor") or {}
    symbols = anchor.get("symbols")
    symbol = ""
    if isinstance(symbols, (list, tuple)) and symbols:
        symbol = str(symbols[0]).strip()
    elif isinstance(anchor.get("symbol"), str):
        symbol = anchor["symbol"].strip()
    if not symbol:
        return [], "anchor 未声明标的"

    intervals = anchor.get("intervals")
    if not isinstance(intervals, (list, tuple)) or not intervals:
        return [], "anchor 未声明周期"
    pairs: list[tuple[str, str]] = []
    for raw in intervals:
        interval = str(raw).strip()
        if interval:
            pairs.append((symbol, interval))
    if not pairs:
        return [], "anchor 的周期列表为空"
    return pairs, ""


async def market_move_round(
    db: Any,
    *,
    db_path: Path | None = None,
    question_limit: int = _DEFAULT_QUESTION_LIMIT,
) -> dict[str, int]:
    """扫一轮:开放问题 → anchor 里的 (标的, 周期) → 异动判定 → 写证据。

    只扫 ``status='open'`` 的问题。parked 是"先放下,别再推进它" —— 往
    暂停的问题里继续堆证据,等于用另一种方式不尊重那个决定。

    返回计数器;单条失败只计数,不拖垮整轮(与 ``settle_decisions`` 同语义)。
    """
    counters = {
        "questions": 0,
        "series": 0,
        "fired": 0,
        "written": 0,
        "duplicate": 0,
        "no_symbol": 0,
        "no_interval": 0,
        "not_anomalous": 0,
        "unavailable": 0,
        "failed": 0,
    }
    path = db_path or default_klines_db_path()
    questions = await ResearchQuestionRepository(db).list_questions(
        status="open", limit=question_limit
    )
    counters["questions"] = len(questions)

    # 同一个 (标的, 周期) 可能被多个问题锚定 —— 先聚合,一条观测写一次。
    by_series: dict[tuple[str, str], list[str]] = {}
    for question in questions:
        pairs, reason = _anchor_targets(question)
        if not pairs:
            counters["no_symbol" if "标的" in reason else "no_interval"] += 1
            continue
        for pair in pairs:
            by_series.setdefault(pair, []).append(str(question["id"]))

    for (symbol, interval), question_ids in by_series.items():
        counters["series"] += 1
        try:
            window, reason = inspect_series(path, symbol, interval)
        except Exception:  # noqa: BLE001 — 一条坏序列不得拖垮整轮
            counters["failed"] += 1
            logger.warning(
                "sensor: inspect failed for %s %s", symbol, interval, exc_info=True
            )
            continue
        if window is None:
            if "未达阈值" in reason:
                counters["not_anomalous"] += 1
            else:
                counters["unavailable"] += 1
                logger.debug("sensor: %s %s skipped — %s", symbol, interval, reason)
            continue
        counters["fired"] += 1

        source_ref = source_ref_of(
            KIND_MARKET_MOVE, symbol, interval, window.window_end_ms
        )
        try:
            if await _already_recorded(db, source_ref):
                counters["duplicate"] += 1
                continue
            await _write_observation(db, window, question_ids, source_ref=source_ref)
            counters["written"] += 1
        except Exception:  # noqa: BLE001
            counters["failed"] += 1
            logger.warning(
                "sensor: write failed for %s %s", symbol, interval, exc_info=True
            )
    return counters


_DEFAULT_LOOP_INTERVAL = 3600.0


def _read_config(settings_service: Any) -> dict[str, Any]:
    """读 ``research_sensor`` 段,缺键按默认(**关闭**)处理。"""
    try:
        section = settings_service.get_research_sensor()
    except Exception:  # noqa: BLE001 — 读不到设置 = 保持关闭
        logger.warning("could not read research sensor settings", exc_info=True)
        return _SENSOR_DEFAULTS
    return {
        "enabled": bool(section.get("enabled", False)),
        "interval_minutes": int(section.get("interval_minutes", 60)),
        "question_limit": int(section.get("question_limit", _DEFAULT_QUESTION_LIMIT)),
    }


_SENSOR_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "interval_minutes": 60,
    "question_limit": _DEFAULT_QUESTION_LIMIT,
}


async def research_sensor_loop(
    state: dict,
    *,
    default_interval: float = _DEFAULT_LOOP_INTERVAL,
) -> None:
    """周期扫描。每轮重读设置(关闭后无需重启即生效)。

    单轮异常不连坐:记 warning 继续下一轮。间隔下限夹在 300 秒 —— 传感器
    只是读 K 线比对阈值,调到几秒只会空转,不会更早发现异动。

    db 取法与 ``autorenew_loop`` 一致:``research_db`` 优先。
    """
    while True:
        interval = default_interval
        try:
            db = state.get("research_db") or state.get("db_manager")
            settings_service = state.get("settings_service")
            if db is not None and settings_service is not None:
                cfg = _read_config(settings_service)
                if cfg["enabled"]:
                    interval = max(300.0, float(cfg["interval_minutes"]) * 60.0)
                    counts = await market_move_round(
                        db,
                        question_limit=int(cfg["question_limit"]),
                    )
                    if any(
                        counts[k] for k in ("fired", "written", "failed", "unavailable")
                    ):
                        logger.info(
                            "research sensor: questions=%d series=%d fired=%d "
                            "written=%d duplicate=%d not_anomalous=%d "
                            "unavailable=%d failed=%d",
                            counts["questions"],
                            counts["series"],
                            counts["fired"],
                            counts["written"],
                            counts["duplicate"],
                            counts["not_anomalous"],
                            counts["unavailable"],
                            counts["failed"],
                        )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("research sensor round failed", exc_info=True)
        await asyncio.sleep(interval)


__all__ = [
    "KIND_MARKET_MOVE",
    "MIN_MOVE_PCT",
    "MIN_SAMPLES",
    "PERCENTILE",
    "MoveWindow",
    "inspect_series",
    "market_move_round",
    "percentile_threshold",
    "research_sensor_loop",
    "source_ref_of",
]
