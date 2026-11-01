"""调查会行情材料装配模块。

anchor.symbols 命中时从本地 klines.db(按 anchor.intervals 指定的周期)与
derivatives.db(资金费率)拼装有界摘要,供调查会注入 prompt;经 7 天门控决定
是否落 Episode(market_snapshot) + Evidence(RECORDED) + addresses 边。

**周期必须听 anchor(2026-10-25 修复)**:此前无论 anchor 要什么周期都只取
``interval='1d'``。实测 49 条 material=ok 的分析里 36 条要的是 1h,拿到的却是
日线 —— 问题问"1h 上 half_delay 的衰减",材料给的是日线涨跌,模型只能回
``untested``。anchor 的 ``intervals`` 是作者写下问题时就确定的观测尺度,装配层
没有资格用默认值覆盖它。指定周期缺数据时**降级到 1d 并在 reason 里说明**,
不静默替换。

**任何路径都返回带状态的 :class:`MarketMaterial`,不返回裸 ``None``。**
旧版异常时静默返回 ``None``,调用方无从区分"问题没挂标的""本地没这根 K 线"
"查询炸了"——界面上三者长得一模一样,都是"什么都没发生"。现在装配结果
(含失败原因)随 ``investigate_finished`` 事件回到前端,日志也带结构化字段。
"""

from __future__ import annotations

import logging
import math
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from selene_space_core.derivatives_paths import default_derivatives_db_path
from selene_space_core.kline_paths import default_klines_db_path

logger = logging.getLogger(__name__)

MATERIAL_MAX_CHARS = 1500
GATE_DAYS = 7
#: 降级目标:anchor 指定的周期本地没数据时退到这里,并在 reason 里说明。
FALLBACK_INTERVAL = "1d"
#: 每个周期取多少根。1d 需要至少 30 根算波动率;1h 取 200 根 ≈ 8 天,
#: 够算短期动量又不至于把 prompt 撑爆(摘要本身有字符上限)。
BARS_PER_INTERVAL: dict[str, int] = {
    "1m": 240,
    "5m": 240,
    "15m": 240,
    "30m": 240,
    "1h": 200,
    "4h": 120,
    "8h": 90,
    "12h": 90,
    "1d": 30,
}
_DEFAULT_BARS = 60
# 单一解析来源(与 kline_paths 同一套规则):不随 CWD 漂移,honor
# SELENE_KLINES_DB / SELENE_DERIVATIVES_DB。模块级常量保留给测试 monkeypatch。
KLINE_DB = default_klines_db_path()
DERIV_DB = default_derivatives_db_path()

MaterialState = Literal[
    "ok",  # 已装配(可能只含 K 线、没有资金费率)
    "no_symbol",  # 问题 anchor 没挂标的
    "klines_missing",  # 本地 K 线库文件不存在
    "klines_empty",  # 库在,但这个标的没有该周期数据
    "error",  # 查询/写入异常
    "replay",  # 回放调查,按设计不装配
]


@dataclass(frozen=True)
class MarketMaterial:
    """装配结果 + 可展示的原因。

    ``text`` 非空才注入 prompt;``reason`` 是给人看的一句话,前端归来条与
    日志都用它,不再出现"明明没行情但界面一声不吭"。
    """

    state: MaterialState
    reason: str
    text: str | None = None
    symbol: str | None = None
    funding_available: bool | None = None
    #: 实际取数的周期。anchor 要 1h 而本地只有 1d 时,这里是 "1d" 且 reason
    #: 会写明降级 —— 读者据此判断这批材料是否对应问题问的尺度。
    interval: str | None = None

    @property
    def injected(self) -> bool:
        return bool(self.text)

    def to_detail(self) -> dict[str, Any]:
        """事件 detail 里的紧凑形态(不含正文——正文在 prompt/账本里)。"""
        return {
            "state": self.state,
            "reason": self.reason,
            "symbol": self.symbol,
            "interval": self.interval,
            "funding_available": self.funding_available,
        }


def _normalize_symbol(raw: str) -> str:
    s = raw.strip().upper()
    return s if "/" in s else f"{s}/USDT"


def _ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat(timespec="seconds")


def _load_closes(
    symbol: str, interval: str, limit: int | None = None
) -> list[tuple[int, float]]:
    """取该标的在该周期的最近 N 根收盘价(升序)。

    ``limit`` 缺省时按周期取 ``BARS_PER_INTERVAL``;显式传值用于摘要里
    需要更长窗口的计算(如 1d 的 30 日波动率)。
    """
    if not KLINE_DB.exists():
        return []
    n = limit if limit is not None else BARS_PER_INTERVAL.get(interval, _DEFAULT_BARS)
    conn = sqlite3.connect(str(KLINE_DB))
    try:
        rows = conn.execute(
            "SELECT timestamp, close FROM klines "
            "WHERE symbol=? AND exchange='binance' AND interval=? "
            "AND market_type='futures' ORDER BY timestamp DESC LIMIT ?",
            (symbol, interval, n),
        ).fetchall()
    finally:
        conn.close()
    return [(int(r[0]), float(r[1])) for r in reversed(rows)]


def _requested_interval(anchor: dict | None) -> str:
    """anchor 声明的观测周期;没有就按 1d(历史默认,不改既有行为)。"""
    raw = (anchor or {}).get("intervals")
    if isinstance(raw, (list, tuple)) and raw:
        candidate = str(raw[0]).strip().lower()
        if candidate:
            return candidate
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()
    return FALLBACK_INTERVAL


def _load_funding(symbol: str) -> dict[str, Any] | None:
    if not DERIV_DB.exists():
        return None
    conn = sqlite3.connect(str(DERIV_DB))
    try:
        cutoff_ms = int(datetime.now(UTC).timestamp() * 1000) - 30 * 86400_000
        rows = conn.execute(
            "SELECT timestamp, funding_rate FROM funding_rates "
            "WHERE exchange='binance' AND symbol=? AND timestamp>=? "
            "ORDER BY timestamp ASC",
            (symbol, cutoff_ms),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return None
    rates = [float(r[1]) for r in rows]
    return {
        "latest": rates[-1],
        "latest_ts": int(rows[-1][0]),
        "mean_30d": statistics.fmean(rates),
    }


def _bars_per_day(interval: str) -> int | None:
    """该周期一天多少根;日历周期(1d 及以上)返回 None。"""
    return {
        "1m": 1440,
        "5m": 288,
        "15m": 96,
        "30m": 48,
        "1h": 24,
        "4h": 6,
        "8h": 3,
        "12h": 2,
    }.get(interval)


def _build_summary(
    symbol: str,
    interval: str,
    closes: list[tuple[int, float]],
    funding: dict[str, Any] | None,
) -> str:
    n = len(closes)
    last_ts, last_close = closes[-1]
    per_day = _bars_per_day(interval)
    lines = [f"行情快照 {symbol} (binance futures, {interval} K线)"]
    lines.append(f"- 最新收盘: {last_close:g} ({_ms_to_iso(last_ts)[:10]})")
    if n >= 2:
        lines.append(f"- 最近1根涨跌: {last_close / closes[-2][1] - 1:+.2%}")
    if per_day is not None:
        # 日内周期:用"多少根 = 1 天 / 7 天"换算,标签写周期单位而不是"日线"。
        for label, bars in (("1天", per_day), ("7天", per_day * 7)):
            if n >= bars:
                lines.append(
                    f"- {label}涨跌({bars} 根 {interval}): "
                    f"{last_close / closes[-bars][1] - 1:+.2%}"
                )
        if n >= 21:
            rets = [math.log(closes[i][1] / closes[i - 1][1]) for i in range(n - 20, n)]
            vol = statistics.stdev(rets)
            lines.append(
                f"- 近20根波动率: {vol:.4f} ({interval} log收益std, "
                f"按 {per_day} 根/天年化约 {vol * math.sqrt(365 * per_day):.1%})"
            )
    else:
        if n >= 8:
            lines.append(f"- 7天涨跌: {last_close / closes[-8][1] - 1:+.2%}")
        if n >= 30:
            lines.append(f"- {n - 1}天涨跌(窗口): {last_close / closes[0][1] - 1:+.2%}")
        if n >= 21 and min(c for _, c in closes[-21:]) > 0:
            rets = [math.log(closes[i][1] / closes[i - 1][1]) for i in range(n - 20, n)]
            vol = statistics.stdev(rets)
            lines.append(
                f"- 20日波动率: {vol:.4f} (日log收益std, "
                f"年化约 {vol * math.sqrt(365):.1%})"
            )
    expected = BARS_PER_INTERVAL.get(interval, _DEFAULT_BARS)
    if n < expected:
        lines.append(
            f"- 注意: 该周期仅 {n} 根 {interval} K线(预期 {expected}),指标按可得部分计算"
        )
    if funding:
        lines.append(
            f"- 资金费率现值: {funding['latest']:.6f} "
            f"({_ms_to_iso(funding['latest_ts'])[:10]}) | "
            f"30日均值: {funding['mean_30d']:.6f}"
        )
    else:
        lines.append("- 资金费率: 无数据")
    lines.append(f"- 数据截止: {_ms_to_iso(last_ts)}")
    lines.append("(本段为行情材料,无证据 id,不进 CITED)")
    text = "\n".join(lines)
    return text[:MATERIAL_MAX_CHARS]


async def _recent_snapshot_age(db: Any, question_id: str) -> float | None:
    rows = await db.execute(
        "SELECT e.created_at AS created FROM research_evidence ev "
        "JOIN research_episodes e ON e.id = ev.episode_id "
        "JOIN research_edges r ON r.from_type='evidence' AND r.from_id=ev.id "
        "WHERE e.subject_type='market_snapshot' AND r.kind='addresses' "
        "AND r.to_type='question' AND r.to_id=? "
        "ORDER BY e.created_at DESC LIMIT 1",
        (question_id,),
    )
    if not rows:
        return None
    created = datetime.fromisoformat(rows[0]["created"])
    return (datetime.now(UTC) - created).total_seconds() / 86400


async def _write_snapshot(
    db: Any,
    question_id: str,
    symbol: str,
    closes: list[tuple[int, float]],
    summary: str,
) -> None:
    from selene_space_storage.repositories.research_graph import (
        EpisodeRepository,
        EvidenceRepository,
        ResearchEdgeRepository,
    )

    start = _ms_to_iso(closes[0][0])
    cutoff = _ms_to_iso(closes[-1][0])
    ep_id = await EpisodeRepository(db).create(
        "market_snapshot",
        symbol,
        f"行情快照 {symbol}",
        start,
        cutoff,
        recorded={"source": "market_material"},
        occurred_at=cutoff,
    )
    ev_id = await EvidenceRepository(db).create(
        ep_id,
        "RECORDED",
        payload={"summary": summary, "cutoff": cutoff, "source": "market_material"},
        source_ref="market_material:council",
    )
    await ResearchEdgeRepository(db).add_fact_edge(
        ("evidence", ev_id), ("question", question_id), "addresses"
    )


def replay_material() -> MarketMaterial:
    """回放调查的显式"不装配"结果(理由见 run_council docstring)。"""
    return MarketMaterial(
        state="replay",
        reason="回放调查以历史认知边界为准,按设计不注入行情材料",
    )


async def assemble_market_material(
    db: Any, anchor: dict | None, question_id: str
) -> MarketMaterial:
    try:
        symbols = (anchor or {}).get("symbols")
        if not symbols:
            return MarketMaterial(
                state="no_symbol", reason="问题未关联标的,本次调查没有行情材料"
            )
        symbol = _normalize_symbol(str(symbols[0]))
        if not KLINE_DB.exists():
            return MarketMaterial(
                state="klines_missing",
                symbol=symbol,
                reason=f"本地 K 线库不存在({KLINE_DB}),本次调查没有行情材料",
            )
        wanted = _requested_interval(anchor)
        closes = _load_closes(symbol, wanted)
        used = wanted
        downgraded = False
        if not closes and wanted != FALLBACK_INTERVAL:
            # 指定周期没数据:降级而不是静默替换。reason 必须让读者知道
            # 这批材料不是问题要的尺度 —— 否则又是一次"看起来有材料"。
            closes = _load_closes(symbol, FALLBACK_INTERVAL)
            used = FALLBACK_INTERVAL
            downgraded = bool(closes)
        if not closes:
            return MarketMaterial(
                state="klines_empty",
                symbol=symbol,
                interval=wanted,
                reason=(
                    f"本地 K 线库没有 {symbol} 的 {wanted} 期货数据"
                    f"(降级到 {FALLBACK_INTERVAL} 也没有),本次没有行情材料"
                ),
            )
        funding = _load_funding(symbol)
        summary = _build_summary(symbol, used, closes, funding)
        # 7 天基准取 episode created_at(写入时刻)而非 occurred_at/boundary_end:
        # 门控语义是"该问题近期已注入过行情材料,避免重复写";occurred_at 是行情
        # 数据时间,K线滞后时会误放行导致同题重复落图。
        age = await _recent_snapshot_age(db, question_id)
        if age is None or age >= GATE_DAYS:
            await _write_snapshot(db, question_id, symbol, closes, summary)
        if downgraded:
            reason = (
                f"已注入 {symbol} {used} 行情快照(问题要求 {wanted},"
                f"本地无该周期数据,已降级)"
            )
        else:
            reason = f"已注入 {symbol} {used} 行情快照"
        if funding is None:
            reason += "(未含资金费率)"
        return MarketMaterial(
            state="ok",
            reason=reason,
            text=summary,
            symbol=symbol,
            interval=used,
            funding_available=funding is not None,
        )
    except Exception as exc:  # noqa: BLE001 — 装配失败不应 crash council
        logger.warning(
            "market_material: assembly failed | question=%s anchor=%s",
            question_id,
            (anchor or {}).get("symbols"),
            exc_info=True,
        )
        return MarketMaterial(
            state="error",
            reason=f"行情装配失败({type(exc).__name__}),本次没有行情材料",
        )
