"""调查会行情材料装配模块。

anchor.symbols 命中时从本地 klines.db(1d K线)与 derivatives.db(资金费率)
拼装有界摘要,供调查会注入 prompt;经 7 天门控决定是否落 Episode(market_snapshot)
+ Evidence(RECORDED) + addresses 边。任何异常降级返回 None,绝不 crash council。
"""

from __future__ import annotations

import logging
import math
import sqlite3
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MATERIAL_MAX_CHARS = 1500
GATE_DAYS = 7
# 同 research_missions.py L236 的解析方式:本文件在 app/services/ 下,
# parents[2]=local-api, parents[4]=仓库根
KLINE_DB = Path(__file__).resolve().parents[2] / "data" / "klines.db"
DERIV_DB = Path(__file__).resolve().parents[4] / "data" / "derivatives.db"


def _normalize_symbol(raw: str) -> str:
    s = raw.strip().upper()
    return s if "/" in s else f"{s}/USDT"


def _ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat(timespec="seconds")


def _load_daily_closes(symbol: str, limit: int = 30) -> list[tuple[int, float]]:
    if not KLINE_DB.exists():
        return []
    conn = sqlite3.connect(str(KLINE_DB))
    try:
        rows = conn.execute(
            "SELECT timestamp, close FROM klines "
            "WHERE symbol=? AND exchange='binance' AND interval='1d' "
            "AND market_type='futures' ORDER BY timestamp DESC LIMIT ?",
            (symbol, limit),
        ).fetchall()
    finally:
        conn.close()
    return [(int(r[0]), float(r[1])) for r in reversed(rows)]


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


def _build_summary(
    symbol: str, closes: list[tuple[int, float]], funding: dict[str, Any] | None
) -> str:
    n = len(closes)
    last_ts, last_close = closes[-1]
    lines = [f"行情快照 {symbol} (binance futures, 1d K线)"]
    lines.append(f"- 最新收盘: {last_close:g} ({_ms_to_iso(last_ts)[:10]})")
    if n >= 2:
        lines.append(f"- 1日涨跌: {last_close / closes[-2][1] - 1:+.2%}")
    if n >= 8:
        lines.append(f"- 7日涨跌: {last_close / closes[-8][1] - 1:+.2%}")
    if n >= 30:
        lines.append(f"- {n - 1}日涨跌(窗口): {last_close / closes[0][1] - 1:+.2%}")
    if n >= 21 and min(c for _, c in closes[-21:]) > 0:
        rets = [math.log(closes[i][1] / closes[i - 1][1]) for i in range(n - 20, n)]
        vol = statistics.stdev(rets)
        lines.append(
            f"- 20日波动率: {vol:.4f} (日log收益std, 年化约 {vol * math.sqrt(365):.1%})"
        )
    if n < 30:
        lines.append(f"- 注意: 仅 {n} 根日线(不足30), 指标按可得部分计算")
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


async def assemble_market_material(
    db: Any, anchor: dict | None, question_id: str
) -> str | None:
    try:
        symbols = (anchor or {}).get("symbols")
        if not symbols:
            return None
        symbol = _normalize_symbol(str(symbols[0]))
        closes = _load_daily_closes(symbol)
        if not closes:
            logger.warning("market_material: no klines for %s", symbol)
            return None
        summary = _build_summary(symbol, closes, _load_funding(symbol))
        # 7 天基准取 episode created_at(写入时刻)而非 occurred_at/boundary_end:
        # 门控语义是"该问题近期已注入过行情材料,避免重复写";occurred_at 是行情
        # 数据时间,K线滞后时会误放行导致同题重复落图。
        age = await _recent_snapshot_age(db, question_id)
        if age is not None and age < GATE_DAYS:
            return summary
        await _write_snapshot(db, question_id, symbol, closes, summary)
        return summary
    except Exception:
        logger.warning("market_material: assembly failed", exc_info=True)
        return None
